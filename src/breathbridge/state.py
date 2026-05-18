"""Device state model and change detection."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, fields

from .const import WORK_MODE_NAMES, DRYING_MODE_NAMES

logger = logging.getLogger(__name__)


@dataclass
class DeviceState:
    # Settings
    work_on: bool | None = None
    work_mode: int | None = None
    set_temp: int | None = None
    filtertemp: int | None = None
    hotbedtemp: int | None = None
    filament_temp: int | None = None  # custom_temp on the wire
    filament_timer: int | None = None  # custom_timer on the wire
    filament_drying_mode: int | None = None
    isrunning: int | None = None
    remaining_seconds: int | None = None
    warehouse_temper: float | None = None
    fw_version: str | None = None
    language: str | None = None
    printer_type: int | None = None
    ptc_sensor_status: int | None = None
    warehouse_sensor_status: int | None = None
    ptc_heater_status: int | None = None

    # Printer
    printer_name: str | None = None
    printer_state: int | None = None
    printer_ip: str | None = None
    printer_port: int | None = None

    # Slicer watcher (populated by the bridge, not from device).
    # Default ON: if you configured PrusaLink, presumably you want this to act.
    # The retained command from HA's switch persists user changes across restarts.
    gcode_chamber_temp_enabled: bool = True
    gcode_chamber_target: int | None = None
    gcode_print_file: str | None = None


# Map from wire field names to DeviceState field names
_SETTINGS_FIELD_MAP = {
    "custom_temp": "filament_temp",
    "custom_timer": "filament_timer",
}

_PRINTER_FIELD_MAP = {
    "name": "printer_name",
    "state": "printer_state",
    "ip": "printer_ip",
    "port": "printer_port",
}


class StateTracker:
    def __init__(self) -> None:
        self.state = DeviceState()
        self._state_field_names = {f.name for f in fields(DeviceState)}

    def update(self, data: dict) -> set[str]:
        """Update state from a raw WS message. Returns set of changed field names."""
        changed: set[str] = set()

        if "settings" in data:
            self._merge(data["settings"], _SETTINGS_FIELD_MAP, changed)

        if "printer" in data:
            self._merge(data["printer"], _PRINTER_FIELD_MAP, changed)

        return changed

    def _merge(self, raw: dict, field_map: dict[str, str], changed: set[str]) -> None:
        for key, value in raw.items():
            field_name = field_map.get(key, key)
            if field_name not in self._state_field_names:
                continue
            current = getattr(self.state, field_name)
            if current != value:
                setattr(self.state, field_name, value)
                changed.add(field_name)

    def to_mqtt_payload(self) -> dict:
        """Convert current state to the combined MQTT state JSON."""
        s = self.state
        payload: dict = {}

        # Switches as ON/OFF
        if s.work_on is not None:
            payload["work_on"] = "ON" if s.work_on else "OFF"
        if s.isrunning is not None:
            payload["isrunning"] = "ON" if s.isrunning else "OFF"

        # Selects as human-readable strings
        if s.work_mode is not None:
            payload["work_mode"] = WORK_MODE_NAMES.get(s.work_mode, str(s.work_mode))
        if s.filament_drying_mode is not None:
            payload["filament_drying_mode"] = DRYING_MODE_NAMES.get(
                s.filament_drying_mode, str(s.filament_drying_mode)
            )

        # Numbers as-is
        for key in ("set_temp", "filtertemp", "hotbedtemp", "filament_temp", "filament_timer"):
            val = getattr(s, key)
            if val is not None:
                payload[key] = val

        # Sensors
        if s.warehouse_temper is not None:
            payload["warehouse_temper"] = s.warehouse_temper
        if s.remaining_seconds is not None:
            payload["remaining_seconds"] = s.remaining_seconds
        if s.fw_version is not None:
            payload["fw_version"] = s.fw_version


        # Extra info
        if s.ptc_heater_status is not None:
            payload["ptc_heater_status"] = s.ptc_heater_status

        # Slicer watcher fields
        payload["gcode_chamber_temp_enabled"] = "ON" if s.gcode_chamber_temp_enabled else "OFF"
        if s.gcode_chamber_target is not None:
            payload["gcode_chamber_target"] = s.gcode_chamber_target
        if s.gcode_print_file is not None:
            payload["gcode_print_file"] = s.gcode_print_file

        return payload
