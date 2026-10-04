"""The raw-TCP transport for the WiFi firmware, and the ``send`` command's payloads.

A throwaway local server stands in for the board: it reads until the client hangs up,
which is exactly how the sketch frames a message.
"""

from __future__ import annotations

import argparse
import asyncio
import socket
from pathlib import Path

import pytest

from nhl_ticker import __main__ as cli
from nhl_ticker.__main__ import build_message
from nhl_ticker.board.protocol import MAX_VISIBLE_CHARS, Segment, render, visible_length
from nhl_ticker.board.transport import (
    HttpBoardTransport,
    TcpBoardTransport,
    board_transport,
    describe,
)
from nhl_ticker.config import Settings


@pytest.fixture
async def board():
    """A fake board on a free local port. Yields (settings, list of received payloads)."""
    received: list[bytes] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        received.append(await reader.read())
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg = Settings(board_protocol="tcp", board_host="127.0.0.1", board_port=port, board_brightness="30")
    async with server:
        yield cfg, received


async def _settle(received: list[bytes], count: int) -> None:
    for _ in range(50):
        if len(received) >= count:
            return
        await asyncio.sleep(0.01)


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def test_send_writes_payload_bytes_unencoded(board):
    cfg, received = board
    payload = render([Segment("Goal! 3-2 F/OT", "ff0000")], cfg)
    transport = TcpBoardTransport(cfg)

    assert await transport.send(payload)
    await _settle(received, 1)

    # No URL encoding over the socket: spaces, '!' and '/' arrive as themselves.
    assert received == [b"~ff000030Goal! 3-2 F/OT"]
    assert transport.online is True


async def test_unreachable_board_returns_false_without_raising():
    cfg = Settings(board_host="127.0.0.1", board_port=_closed_port(), http_timeout_seconds=1)
    transport = TcpBoardTransport(cfg)

    assert await transport.send("hello") is False
    assert transport.online is False


async def test_clear_blanks_the_visible_window(board):
    cfg, received = board

    assert await TcpBoardTransport(cfg).clear()
    await _settle(received, 1)

    assert received == [b" " * cfg.board_chars]


def test_protocol_setting_picks_the_transport():
    tcp = Settings(board_protocol="tcp", board_host="1.2.3.4", board_port=8080)
    http = Settings(board_protocol="http", board_host="1.2.3.4")

    assert isinstance(board_transport(tcp)._inner, TcpBoardTransport)
    assert isinstance(board_transport(http)._inner, HttpBoardTransport)
    assert describe(tcp) == "tcp://1.2.3.4:8080"
    assert describe(http) == "http://1.2.3.4/arduino"


def test_tcp_is_the_default_protocol():
    assert Settings(_env_file=None).board_protocol == "tcp"


def test_plain_message_gets_a_colour_marker():
    cfg = Settings(board_brightness="30")

    assert build_message("Hello Kent", cfg=cfg) == "~ffffe630Hello Kent"
    assert build_message("Hello Kent", "FF0000", cfg) == "~ff000030Hello Kent"


def test_hand_marked_message_passes_through():
    text = "~ffffe630 Hi ~71afe530UTA~ffffe630 vs ~83001830COL"

    assert build_message(text) == text


def test_long_message_is_capped_to_the_board_buffer():
    assert visible_length(build_message("x" * 400)) == MAX_VISIBLE_CHARS


def _send_args(port: int) -> argparse.Namespace:
    return argparse.Namespace(text="hi", color=None, host="127.0.0.1", port=port, protocol="tcp")


@pytest.mark.parametrize("enabled", [False, True])
def test_send_warns_when_the_ticker_would_not_use_the_board(monkeypatch, capsys, enabled):
    # send reaches the board either way; the note is what stops a working one-off message
    # passing for a wired-up ticker.
    monkeypatch.setattr(cli, "settings", Settings(_env_file=None, board_enabled=enabled, http_timeout_seconds=1))

    cli.send(_send_args(_closed_port()))

    assert ("TICKER_BOARD_ENABLED=true" in capsys.readouterr().out) is not enabled


def test_env_file_is_read_from_engine_dir_not_cwd():
    engine_dir = Path(cli.__file__).resolve().parent.parent

    assert Path(Settings.model_config["env_file"]) == engine_dir / ".env"
