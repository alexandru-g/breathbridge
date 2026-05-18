"""Poll PrusaLink /api/v1/status for bed temperature."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import aiohttp

logger = logging.getLogger(__name__)


class PrusaLinkPoller:
    def __init__(
        self,
        url: str,
        api_key: str,
        interval: float,
        on_update: Callable[[float, float], Awaitable[None]],
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._interval = interval
        self._on_update = on_update

    async def run(self, shutdown: asyncio.Event) -> None:
        headers = {"X-Api-Key": self._api_key, "Accept": "application/json"}
        timeout = aiohttp.ClientTimeout(total=self._interval + 5)

        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            while not shutdown.is_set():
                try:
                    await self._poll_once(session)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # pragma: no cover — defensive
                    logger.warning("PrusaLink poll failed: %s", exc)

                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=self._interval)
                except asyncio.TimeoutError:
                    pass

    async def _poll_once(self, session: aiohttp.ClientSession) -> None:
        async with session.get(self._url) as resp:
            if resp.status == 401:
                logger.error("PrusaLink rejected the API key (401)")
                return
            if resp.status >= 400:
                logger.warning("PrusaLink returned HTTP %d", resp.status)
                return
            data = await resp.json()

        printer = data.get("printer") or {}
        temp = printer.get("temp_bed")
        target = printer.get("target_bed")
        if temp is None and target is None:
            logger.debug("PrusaLink status has no bed temp fields: %s", data)
            return

        await self._on_update(
            float(temp) if temp is not None else 0.0,
            float(target) if target is not None else 0.0,
        )
