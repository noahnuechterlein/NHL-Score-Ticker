"""Local FastAPI server: WebSocket feed for the test UI, plus a few control endpoints.

Binds to 127.0.0.1 by default. Nothing here is exposed publicly and there is no auth,
because there is nothing to authenticate against -- it is a development console for a
device on your desk.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .board.queue import BoardQueue
from .board.transport import (
    FanOutTransport,
    NullTransport,
    board_transport,
    describe,
)
from .config import settings
from .core.league import TEAMS, horn_path
from .nhl.client import NHLClient
from .runner import TickerService
from .sinks.broadcast import BroadcastHub, BroadcastTransport

log = logging.getLogger(__name__)


def build_service() -> TickerService:
    """Wire the object graph, honouring which sinks are switched on."""
    hub = BroadcastHub()

    transports = [BroadcastTransport(hub)]
    if settings.board_enabled:
        transports.append(board_transport(settings))
        log.info("board sink enabled -> %s", describe(settings))
    else:
        transports.append(NullTransport())
        # A warning, not info: `send` reaches the board regardless of this setting, so a
        # working one-off message is easy to mistake for a wired-up ticker.
        log.warning("board disabled: set TICKER_BOARD_ENABLED=true in engine/.env to drive it; emulator only")

    horn = None
    if settings.horn_enabled:
        from .sinks.horn import HornSink

        horn = HornSink(settings)
        log.info("horn sink enabled -> %s", settings.horn_dir)

    queue = BoardQueue(FanOutTransport(*transports), settings)
    service = TickerService(NHLClient(settings), queue, hub, settings, horn=horn)
    # The queue needs to tell the UI what it sent; the service owns that conversation.
    queue.set_message_callback(service.on_board_message)
    return service


@asynccontextmanager
async def lifespan(app: FastAPI):
    service = build_service()
    app.state.service = service
    await service.start()
    try:
        yield
    finally:
        await service.stop()


app = FastAPI(title="NHL Score Ticker", lifespan=lifespan)

# The Vite dev server runs on a different port during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _service() -> TickerService:
    """The running service. Held on app.state so the lifespan owns its lifetime."""
    return app.state.service


@app.get("/api/state")
async def get_state():
    return _service().snapshot()


@app.get("/api/teams")
async def get_teams():
    """Colour and horn availability per team, for the UI's team picker."""
    return [
        {
            "abbrev": t.abbrev,
            "name": t.name,
            "color": f"#{t.color}",
            "hasRealHorn": t.has_real_horn,
            "hornUrl": f"/api/horn/{t.abbrev}",
        }
        for t in sorted(TEAMS.values(), key=lambda t: t.abbrev)
    ]


@app.get("/api/horn/{abbrev}")
async def get_horn(abbrev: str):
    """The scoring team's goal horn, for the browser to play.

    Served by abbreviation so the engine keeps owning the team-to-file mapping, fallbacks
    and all, instead of that table being duplicated in TypeScript. Unknown teams get the
    generic horn, exactly as the board does.

    Independent of ``TICKER_HORN_ENABLED``: that switch is for the engine host's own
    speaker, which is a different machine from whatever is showing the website.
    """
    # Resolved through the league table, never by joining the abbreviation onto a path,
    # so a crafted abbreviation cannot escape the horn directory.
    path = horn_path(abbrev, settings.horn_dir)
    if path is None:
        raise HTTPException(status_code=404, detail=f"no horn available for {abbrev}")
    return FileResponse(
        path,
        media_type="audio/mpeg",
        # Static megabytes; there is no reason to refetch one every goal.
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.post("/api/poll")
async def force_poll():
    """Skip the rest of the current interval and refetch now."""
    _service().request_poll()
    return {"ok": True}


@app.post("/api/fake-goal")
async def fake_goal(gameId: int | None = None, team: str | None = None):
    """Inject a synthetic goal so the board chain can be tested with no live hockey."""
    try:
        return await _service().fake_goal(gameId, team)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/clear")
async def clear_board():
    await _service().clear_board()
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    service = _service()
    await service.hub.connect(websocket)
    await websocket.send_json({"type": "snapshot", **service.snapshot()})
    try:
        while True:
            # The UI does not send commands over the socket; this just detects hangup.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await service.hub.disconnect(websocket)


@app.get("/healthz")
async def healthz():
    """Healthy means both the API is answering and, if enabled, so is the board."""
    service = _service()
    board_online = service.queue.hardware_online
    board_ok = board_online is not False
    ok = service.last_error is None and board_ok
    return JSONResponse(
        {
            "ok": ok,
            "lastError": service.last_error,
            "boardOnline": board_online,
        },
        status_code=200 if ok else 503,
    )


# Serve the built UI when it exists; in development Vite serves it instead.
if settings.ui_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(settings.ui_dist), html=True), name="ui")
