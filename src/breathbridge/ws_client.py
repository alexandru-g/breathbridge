"""Async WebSocket client for the Panda Breath device."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import websockets
from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)


class PandaBreathWS:
    def __init__(self, url: str, reconnect_interval: float = 5.0) -> None:
        self._url = url
        self._reconnect_interval = reconnect_interval
        self._ws: ClientConnection | None = None
        self._connected = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._ws.close_code is None

    async def connect(self) -> None:
        self._ws = await websockets.connect(self._url, ping_interval=None)
        self._connected.set()
        logger.info("WebSocket connected to %s", self._url)

    async def disconnect(self) -> None:
        self._connected.clear()
        if self._ws:
            await self._ws.close()
            self._ws = None
            logger.info("WebSocket disconnected")

    async def send_command(self, namespace: str, payload: dict) -> None:
        if not self.connected:
            logger.warning("WebSocket not connected, cannot send command")
            return
        message = json.dumps({namespace: payload})
        await self._ws.send(message)
        logger.debug("WS sent: %s", message)

    async def messages(self) -> AsyncIterator[dict]:
        """Yield parsed JSON messages from the WebSocket.

        Reconnects automatically on connection loss.
        """
        while True:
            try:
                if not self.connected:
                    await self.connect()

                async for raw in self._ws:
                    logger.debug("WS recv: %s", raw[:500] if isinstance(raw, str) else raw)
                    try:
                        data = json.loads(raw)
                        yield data
                    except json.JSONDecodeError:
                        logger.warning("Non-JSON WS message: %s", raw[:200])

            except (
                websockets.ConnectionClosed,
                websockets.InvalidURI,
                websockets.InvalidHandshake,
                OSError,
            ) as exc:
                self._connected.clear()
                self._ws = None
                logger.warning("WebSocket connection lost (%s), reconnecting in %ss", exc, self._reconnect_interval)
                await asyncio.sleep(self._reconnect_interval)
