"""Entry point: ``uv run nhl-ticker`` or ``python -m nhl_ticker``.

With no arguments this serves the ticker. ``nhl-ticker send "text"`` pushes one message
straight to the board and exits -- the engine's replacement for the standalone
"Wifi Message Send" script.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from .board.protocol import Segment, plain_text, render, truncate
from .board.transport import board_transport, describe
from .config import Settings, settings
from .core.league import DEFAULT_TEAM


def build_message(text: str, color: str | None = None, cfg: Settings | None = None) -> str:
    """Payload for a custom message.

    Plain text is coloured like any other board message. Text that already carries
    ``~RRGGBBLL`` markers is taken as hand-written and passed through, so a payload copied
    from the old script still works.
    """
    payload = text if "~" in text else render([Segment(text, color or DEFAULT_TEAM.color)], cfg)
    return truncate(payload)


async def _send(payload: str, cfg: Settings) -> bool:
    transport = board_transport(cfg)
    try:
        return await transport.send(payload)
    finally:
        await transport.aclose()


def send(args: argparse.Namespace) -> int:
    overrides = {
        key: value
        for key, value in (("board_host", args.host), ("board_port", args.port), ("board_protocol", args.protocol))
        if value is not None
    }
    cfg = settings.model_copy(update=overrides)
    payload = build_message(args.text, args.color, cfg)
    if not cfg.board_enabled:
        # send ignores the setting on purpose, but the ticker does not.
        print("Note: the ticker itself won't drive the board until TICKER_BOARD_ENABLED=true in engine/.env.")
    print(f"Sending to {describe(cfg)}: {plain_text(payload)}")
    if asyncio.run(_send(payload, cfg)):
        print("Delivered.")
        return 0
    print("Board did not accept the message.")
    return 1


def serve() -> None:
    import uvicorn

    uvicorn.run(
        "nhl_ticker.app:app",
        host=settings.host,
        port=settings.port,
        log_level="warning",
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="nhl-ticker")
    commands = parser.add_subparsers(dest="command")
    send_cmd = commands.add_parser("send", help="send one message to the board and exit")
    send_cmd.add_argument("text", help="message text; may include ~RRGGBBLL colour markers")
    send_cmd.add_argument("--color", help="six hex digits, e.g. ff0000 (default: the board's warm white)")
    send_cmd.add_argument("--host", help="board IP (default: TICKER_BOARD_HOST)")
    send_cmd.add_argument("--port", type=int, help="TCP port (default: TICKER_BOARD_PORT)")
    send_cmd.add_argument("--protocol", choices=["tcp", "http"], help="default: TICKER_BOARD_PROTOCOL")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    if args.command == "send":
        raise SystemExit(send(args))
    serve()


if __name__ == "__main__":
    main()
