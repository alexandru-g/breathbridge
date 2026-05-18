"""Async MQTT client with LWT and reconnection."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import aiomqtt

from .config import Settings

logger = logging.getLogger(__name__)


class MQTTClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: aiomqtt.Client | None = None

    def _make_client(self) -> aiomqtt.Client:
        s = self._settings
        return aiomqtt.Client(
            hostname=s.mqtt_host,
            port=s.mqtt_port,
            username=s.mqtt_username,
            password=s.mqtt_password,
            will=aiomqtt.Will(
                topic=s.availability_topic,
                payload="offline",
                qos=1,
                retain=True,
            ),
        )

    async def publish(self, topic: str, payload: str | dict, retain: bool = False, qos: int = 0) -> None:
        if self._client is None:
            logger.warning("MQTT not connected, cannot publish")
            return
        if isinstance(payload, dict):
            payload = json.dumps(payload)
        await self._client.publish(topic, payload, retain=retain, qos=qos)
        logger.debug("MQTT pub %s: %s", topic, payload[:200] if len(payload) > 200 else payload)

    async def publish_online(self) -> None:
        await self.publish(self._settings.availability_topic, "online", retain=True, qos=1)

    async def publish_offline(self) -> None:
        await self.publish(self._settings.availability_topic, "offline", retain=True, qos=1)

    async def publish_state(self, state_payload: dict) -> None:
        await self.publish(self._settings.state_topic, state_payload, retain=True)

    async def publish_discovery(self, configs: list[tuple[str, dict]]) -> None:
        for topic, payload in configs:
            await self.publish(topic, payload, retain=True)
        logger.info("Published %d discovery configs", len(configs))

    async def run(self, command_topic_prefix: str, ha_status_topic: str = "homeassistant/status") -> AsyncIterator[tuple[str, str]]:
        """Connect, subscribe, and yield (topic, payload) for commands and HA birth messages.

        Reconnects automatically on connection loss.
        """
        s = self._settings
        while True:
            try:
                async with self._make_client() as client:
                    self._client = client
                    await client.subscribe(f"{command_topic_prefix}/#")
                    await client.subscribe(ha_status_topic)
                    logger.info("MQTT connected to %s:%d", s.mqtt_host, s.mqtt_port)

                    async for message in client.messages:
                        topic = str(message.topic)
                        payload = message.payload.decode() if isinstance(message.payload, bytes) else str(message.payload)
                        yield (topic, payload)

            except aiomqtt.MqttError as exc:
                self._client = None
                logger.warning("MQTT connection lost (%s), reconnecting in %ss", exc, s.reconnect_interval)
                await asyncio.sleep(s.reconnect_interval)
