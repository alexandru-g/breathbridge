"""Core bridge logic: glues WebSocket client to MQTT client."""

from __future__ import annotations

import asyncio
import json
import logging
import signal

from .config import Settings
from .const import WORK_MODE_FROM_NAME, DRYING_MODE_FROM_NAME
from .discovery import generate_discovery_configs
from .moonraker_server import MoonrakerServer, MoonrakerState
from .mqtt_client import MQTTClient
from .prusalink_client import PrusaLinkPoller
from .slicer_watcher import SlicerWatcher
from .state import StateTracker
from .ws_client import PandaBreathWS

logger = logging.getLogger(__name__)


class Bridge:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._ws = PandaBreathWS(settings.ws_url, settings.reconnect_interval)
        self._mqtt = MQTTClient(settings)
        self._state = StateTracker()
        self._shutdown = asyncio.Event()
        self._last_publish: dict | None = None

        self._moonraker_state: MoonrakerState | None = None
        self._moonraker_server: MoonrakerServer | None = None
        if settings.moonraker_enabled:
            self._moonraker_state = MoonrakerState()
            self._moonraker_server = MoonrakerServer(
                settings.moonraker_host,
                settings.moonraker_port,
                self._moonraker_state,
            )

    async def _publish_discovery(self) -> None:
        fw = self._state.state.fw_version
        configs = generate_discovery_configs(self._settings, fw)
        await self._mqtt.publish_discovery(configs)

    async def _publish_state(self, force: bool = False) -> None:
        payload = self._state.to_mqtt_payload()
        if not force and payload == self._last_publish:
            return
        self._last_publish = payload
        await self._mqtt.publish_state(payload)

    def _handle_command(self, key: str, payload: str) -> tuple[str, dict] | None:
        """Translate an MQTT command to a WS namespace + payload. Returns None if unknown."""
        match key:
            case "work_on":
                return ("settings", {"work_on": payload == "ON"})
            case "work_mode":
                val = WORK_MODE_FROM_NAME.get(payload)
                if val is not None:
                    return ("settings", {"work_mode": val})
            case "filament_drying_mode":
                val = DRYING_MODE_FROM_NAME.get(payload)
                if val is not None:
                    return ("settings", {"filament_drying_mode": val})
            case "set_temp" | "filtertemp" | "hotbedtemp" | "filament_temp" | "filament_timer":
                try:
                    return ("settings", {key: int(float(payload))})
                except ValueError:
                    logger.warning("Invalid number for %s: %s", key, payload)
            case "isrunning":
                return ("settings", {"isrunning": 1 if payload == "ON" else 0})
            case "reset":
                return ("settings", {"reset": 1})
            case "climate_mode":
                if payload == "off":
                    return ("settings", {"work_on": False})
                else:
                    # Turn on + set to Power On mode
                    return ("settings", {"work_on": True, "work_mode": 2})
            case "climate_temp":
                try:
                    return ("settings", {"set_temp": int(float(payload))})
                except ValueError:
                    logger.warning("Invalid climate temp: %s", payload)
        return None

    async def _ws_reader(self) -> None:
        """Read WS messages and publish state to MQTT."""
        was_connected = False
        async for data in self._ws.messages():
            if self._shutdown.is_set():
                break
            if not was_connected:
                was_connected = True
                await self._mqtt.publish_online()
                await self._publish_discovery()

            changed = self._state.update(data)
            if changed:
                await self._publish_state()

    async def _mqtt_reader(self) -> None:
        """Read MQTT commands and forward to WS."""
        s = self._settings
        ha_status_topic = f"{s.discovery_prefix}/status"

        async for topic, payload in self._mqtt.run(s.command_topic_prefix, ha_status_topic):
            if self._shutdown.is_set():
                break

            # HA birth message — re-publish discovery + state
            if topic == ha_status_topic and payload == "online":
                logger.info("Home Assistant came online, re-publishing discovery")
                await self._publish_discovery()
                await self._mqtt.publish_online()
                await self._publish_state(force=True)
                continue

            # Command messages
            prefix = s.command_topic_prefix + "/"
            if topic.startswith(prefix):
                key = topic[len(prefix):]
                if await self._handle_moonraker_command(key, payload):
                    continue
                if await self._handle_gcode_command(key, payload):
                    continue
                result = self._handle_command(key, payload)
                if result:
                    namespace, ws_payload = result
                    await self._ws.send_command(namespace, ws_payload)
                    # Optimistically update state so HA doesn't flicker
                    # (device doesn't echo back all fields)
                    self._state.update({namespace: ws_payload})
                    await self._publish_state(force=True)
                    logger.info("Command %s=%s -> WS %s", key, payload, ws_payload)
                else:
                    logger.warning("Unknown command: %s=%s", key, payload)

    async def _heartbeat(self) -> None:
        """Periodically re-publish full state."""
        while not self._shutdown.is_set():
            await asyncio.sleep(self._settings.update_interval)
            if self._ws.connected:
                await self._publish_state(force=True)

    async def _handle_gcode_command(self, key: str, payload: str) -> bool:
        if key != "gcode_chamber_temp":
            return False
        enabled = payload == "ON"
        self._state.state.gcode_chamber_temp_enabled = enabled
        await self._publish_state(force=True)
        logger.info("Gcode chamber-temp auto-set: %s", "ON" if enabled else "OFF")
        return True

    async def _on_gcode_chamber_temp(self, filename: str, chamber: int | None) -> None:
        s = self._state.state
        s.gcode_print_file = filename
        s.gcode_chamber_target = chamber
        await self._publish_state(force=True)
        if chamber is None:
            return
        if not s.gcode_chamber_temp_enabled:
            logger.info(
                "Detected chamber temp %d°C in %s (auto-set disabled)",
                chamber,
                filename,
            )
            return
        # Drive the Panda's chamber setpoint directly.
        await self._ws.send_command("settings", {"set_temp": int(chamber)})
        self._state.update({"settings": {"set_temp": int(chamber)}})
        await self._publish_state(force=True)
        logger.info("Auto-set Panda chamber temp from %s: %d°C", filename, chamber)

    async def _on_print_ended(self) -> None:
        """PrusaLink reports no active job — clear gcode-derived state.

        If we previously auto-set the Panda's chamber setpoint, drop it back to
        0 so it doesn't keep heating to the last print's target.
        """
        s = self._state.state
        had_target = s.gcode_chamber_target is not None
        prev_file = s.gcode_print_file
        s.gcode_chamber_target = None
        s.gcode_print_file = None

        if had_target and s.gcode_chamber_temp_enabled:
            await self._ws.send_command("settings", {"set_temp": 0})
            self._state.update({"settings": {"set_temp": 0}})
            logger.info("Print ended (%s) — reset Panda chamber setpoint to 0", prev_file)
        else:
            logger.info("Print ended (%s) — cleared gcode chamber target", prev_file)
        await self._publish_state(force=True)

    async def _slicer_watcher_runner(self) -> None:
        s = self._settings
        if not s.prusalink_host or not s.prusalink_api_key:
            return
        if not s.slicer_watcher_enabled:
            return
        base = f"http://{s.prusalink_host}:{s.prusalink_port}"
        watcher = SlicerWatcher(
            base_url=base,
            api_key=s.prusalink_api_key,
            poll_interval=s.slicer_watcher_poll_interval,
            tail_bytes=s.slicer_watcher_tail_bytes,
            on_detect=self._on_gcode_chamber_temp,
            on_print_ended=self._on_print_ended,
        )
        logger.info("Slicer watcher started against %s", base)
        await watcher.run(self._shutdown)

    async def _handle_moonraker_command(self, key: str, payload: str) -> bool:
        """Route bed_temp / bed_target MQTT commands into the fake Moonraker state.

        Returns True if the key was handled (regardless of success).
        """
        if self._moonraker_state is None:
            return False
        if key not in ("bed_temp", "bed_target"):
            return False
        try:
            value = float(payload)
        except ValueError:
            logger.warning("Invalid number for %s: %s", key, payload)
            return True
        if key == "bed_temp":
            await self._moonraker_state.update(temp=value)
        else:
            await self._moonraker_state.update(target=value)
        logger.debug("Moonraker state updated via MQTT: %s=%s", key, value)
        return True

    async def _moonraker_runner(self) -> None:
        if self._moonraker_server is None:
            return
        await self._moonraker_server.run_forever(self._shutdown)

    async def _prusalink_runner(self) -> None:
        s = self._settings
        url = s.prusalink_status_url
        if self._moonraker_state is None or not url or not s.prusalink_api_key:
            return

        async def on_update(temp: float, target: float) -> None:
            await self._moonraker_state.update(temp=temp, target=target)

        poller = PrusaLinkPoller(
            url=url,
            api_key=s.prusalink_api_key,
            interval=s.prusalink_poll_interval,
            on_update=on_update,
        )
        logger.info("PrusaLink poller started against %s", url)
        await poller.run(self._shutdown)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._shutdown.set)

        s = self._settings
        moonraker_msg = (
            f", Moonraker: {s.moonraker_host}:{s.moonraker_port}"
            if s.moonraker_enabled
            else ""
        )
        prusalink_msg = (
            f", PrusaLink: {s.prusalink_host}:{s.prusalink_port}"
            if s.prusalink_host
            else ""
        )
        logger.info(
            "Starting Panda Breath MQTT bridge (WS: %s, MQTT: %s:%d%s%s)",
            s.ws_url,
            s.mqtt_host,
            s.mqtt_port,
            moonraker_msg,
            prusalink_msg,
        )

        tasks = [
            asyncio.create_task(self._ws_reader(), name="ws_reader"),
            asyncio.create_task(self._mqtt_reader(), name="mqtt_reader"),
            asyncio.create_task(self._heartbeat(), name="heartbeat"),
        ]
        if self._moonraker_server is not None:
            tasks.append(
                asyncio.create_task(self._moonraker_runner(), name="moonraker_server")
            )
        if (
            self._moonraker_state is not None
            and self._settings.prusalink_host
            and self._settings.prusalink_api_key
        ):
            tasks.append(
                asyncio.create_task(self._prusalink_runner(), name="prusalink_poller")
            )
        if (
            self._settings.slicer_watcher_enabled
            and self._settings.prusalink_host
            and self._settings.prusalink_api_key
        ):
            tasks.append(
                asyncio.create_task(self._slicer_watcher_runner(), name="slicer_watcher")
            )

        try:
            # Wait for shutdown or any task to fail
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.exception():
                    logger.error("Task %s failed: %s", task.get_name(), task.exception())
        finally:
            logger.info("Shutting down...")
            self._shutdown.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._mqtt.publish_offline()
            await self._ws.disconnect()
            logger.info("Shutdown complete")
