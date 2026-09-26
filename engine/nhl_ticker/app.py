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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .board.queue import BoardQueue
from .board.transport import FanOutTransport, HttpBoardTransport, NullTransport
from .config import settings
from .core.league import TEAMS
from .nhl.client import NHLClient
from .runner import TickerService
from .sinks.broadcast import BroadcastHub, BroadcastTransport

log = logging.getLogger(__name__)


def build_service() -> TickerService:
    """Wire the object graph, honouring which sinks are switched on."""
    hub = BroadcastHub()

    transports = [BroadcastTransport(hub)]
    if settings.board_enabled:
        transports.append(HttpBoardTransport(settings))
        log.info("board sink enabled -> %s", settings.board_url_base)
    else:
        transports.append(NullTransport())
        log.info("board sink disabled; emulator only")

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


def _service(app: FastAPI) -> TickerService:
    return app.state.service


@app.get("/api/state")
async def get_state():
    return _service(app).snapshot()


@app.get("/api/teams")
async def get_teams():
    """Colour and horn availability per team, for the UI's team picker."""
    return [
        {
            "abbrev": t.abbrev,
            "name": t.name,
            "color": f"#{t.color}",
            "hasRealHorn": t.has_real_horn,
        }
        for t in sorted(TEAMS.values(), key=lambda t: t.abbrev)
    ]


@app.post("/api/poll")
async def force_poll():
    """Skip the rest of the current interval and refetch now."""
    _service(app).request_poll()
    return {"ok": True}


@app.post("/api/fake-goal")
async def fake_goal(gameId: int | None = None, team: str | None = None):
    """Inject a synthetic goal so the board chain can be tested with no live hockey."""
    try:
        return _service(app).fake_goal(gameId, team)
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/clear")
async def clear_board():
    await _service(app).clear_board()
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    service = _service(app)
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
    service = _service(app)
    return JSONResponse(
        {"ok": service.last_error is None, "lastError": service.last_error},
        status_code=200 if service.last_error is None else 503,
    )


# Serve the built UI when it exists; in development Vite serves it instead.
if settings.ui_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(settings.ui_dist), html=True), name="ui")
