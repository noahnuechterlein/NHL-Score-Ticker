"""Async client for the NHL public API.

Only one endpoint matters for the ticker: ``/v1/score/now`` returns every game on the
current slate with scores, state, clock, and -- crucially -- an ordered ``goals`` array
carrying the scorer and assists. That single response replaces both the scoreboard scrape
and the per-game boxscore scrape the original project needed.

Note ``/score/now`` answers 307 with a redirect to ``/score/<date>``, so redirects must be
followed.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..config import Settings, settings as default_settings
from .models import Scoreboard

log = logging.getLogger(__name__)


class NHLClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self._settings = settings or default_settings
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._settings.nhl_api_base,
            timeout=self._settings.http_timeout_seconds,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    async def __aenter__(self) -> "NHLClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get_json(self, path: str) -> dict:
        """GET with bounded exponential backoff. Raises the last error if all tries fail."""
        last_exc: Exception | None = None
        for attempt in range(self._settings.http_max_retries):
            try:
                response = await self._client.get(path)
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_exc = exc
                backoff = 2.0**attempt
                log.warning(
                    "GET %s failed (attempt %d/%d): %s; retrying in %.0fs",
                    path,
                    attempt + 1,
                    self._settings.http_max_retries,
                    exc,
                    backoff,
                )
                if attempt < self._settings.http_max_retries - 1:
                    await asyncio.sleep(backoff)
        assert last_exc is not None
        raise last_exc

    async def fetch_scoreboard(self, date: str | None = None) -> Scoreboard:
        """Fetch the slate for ``date`` (YYYY-MM-DD), or the current slate if omitted."""
        path = f"/score/{date}" if date else "/score/now"
        return Scoreboard.model_validate(await self._get_json(path))
