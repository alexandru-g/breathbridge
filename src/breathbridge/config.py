"""Configuration via environment variables (prefix PB_) or .env file."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    model_config = {"env_prefix": "PB_", "env_file": ".env", "env_file_encoding": "utf-8"}

    # WebSocket
    ws_host: str = Field(description="Panda Breath IP or hostname")
    ws_port: int = 80
    ws_path: str = "/ws"

    # MQTT
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None

    # Topics
    mqtt_base_topic: str = "panda_breath"
    discovery_prefix: str = "homeassistant"

    # Device identity
    device_id: str = "panda_breath"
    device_name: str = "Panda Breath"

    # Behavior
    update_interval: int = 30  # seconds, max between full state publishes
    reconnect_interval: int = 5  # seconds between reconnect attempts
    log_level: str = "INFO"

    # Fake Moonraker server — lets the Panda Breath device pull bed temp
    # from us as if we were a Klipper/Moonraker host.
    moonraker_enabled: bool = True
    moonraker_host: str = "0.0.0.0"
    moonraker_port: int = 7125

    # PrusaLink polling — when configured, we pull bed temp directly from
    # a Prusa printer and feed it into the fake Moonraker state.
    # If not configured, bed temp must be pushed via MQTT
    # (panda_breath/<id>/cmd/bed_temp and /cmd/bed_target).
    prusalink_host: str | None = None
    prusalink_port: int = 80
    prusalink_api_key: str | None = None
    prusalink_poll_interval: float = 5.0

    # Slicer watcher — when PrusaLink is configured, optionally watch for new
    # print jobs and extract chamber temp from gcode (M141 / `; chamber_temperature`).
    # Auto-pushes to the Panda only when the MQTT switch "gcode_chamber_temp" is ON.
    # Requires bgcode disabled in PrusaSlicer.
    slicer_watcher_enabled: bool = True
    slicer_watcher_poll_interval: float = 10.0
    slicer_watcher_tail_bytes: int = 50000

    @property
    def ws_url(self) -> str:
        return f"ws://{self.ws_host}:{self.ws_port}{self.ws_path}"

    @property
    def prusalink_status_url(self) -> str | None:
        if not self.prusalink_host:
            return None
        return f"http://{self.prusalink_host}:{self.prusalink_port}/api/v1/status"

    @property
    def base_topic(self) -> str:
        return f"{self.mqtt_base_topic}/{self.device_id}"

    @property
    def availability_topic(self) -> str:
        return f"{self.base_topic}/availability"

    @property
    def state_topic(self) -> str:
        return f"{self.base_topic}/state"

    @property
    def command_topic_prefix(self) -> str:
        return f"{self.base_topic}/cmd"

    @property
    def configuration_url(self) -> str:
        return f"http://{self.ws_host}"
