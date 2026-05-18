"""Home Assistant MQTT Discovery payload generation."""

from __future__ import annotations

from .config import Settings
from .const import MANUFACTURER, MODEL, WORK_MODE_NAMES, DRYING_MODE_NAMES


def _device_block(settings: Settings, fw_version: str | None = None) -> dict:
    dev = {
        "identifiers": [settings.device_id],
        "name": settings.device_name,
        "manufacturer": MANUFACTURER,
        "model": MODEL,
        "configuration_url": settings.configuration_url,
    }
    if fw_version:
        dev["sw_version"] = fw_version
    return dev


def _base(settings: Settings) -> dict:
    """Common fields shared by all entities."""
    return {
        "~": settings.base_topic,
        "availability_topic": "~/availability",
        "payload_available": "online",
        "payload_not_available": "offline",
    }


def generate_discovery_configs(
    settings: Settings, fw_version: str | None = None
) -> list[tuple[str, dict]]:
    """Return list of (config_topic, payload) for all entities."""
    prefix = settings.discovery_prefix
    dev_id = settings.device_id
    device = _device_block(settings, fw_version)
    configs: list[tuple[str, dict]] = []

    def _add(component: str, object_id: str, payload: dict) -> None:
        topic = f"{prefix}/{component}/{dev_id}/{object_id}/config"
        payload.update(_base(settings))
        payload["device"] = device
        payload["unique_id"] = f"{dev_id}_{object_id}"
        configs.append((topic, payload))

    # Climate
    _add("climate", "climate", {
        "name": "Climate",
        "current_temperature_topic": "~/state",
        "current_temperature_template": "{{ value_json.warehouse_temper }}",
        "temperature_command_topic": "~/cmd/climate_temp",
        "temperature_state_topic": "~/state",
        "temperature_state_template": "{{ value_json.set_temp }}",
        "mode_command_topic": "~/cmd/climate_mode",
        "mode_state_topic": "~/state",
        "mode_state_template": "{% if value_json.work_on == 'OFF' %}off{% else %}heat{% endif %}",
        "modes": ["off", "heat"],
        "min_temp": 0,
        "max_temp": 60,
        "temp_step": 1,
        "temperature_unit": "C",
    })

    # Switch: Power
    _add("switch", "work_on", {
        "name": "Power",
        "state_topic": "~/state",
        "value_template": "{{ value_json.work_on }}",
        "command_topic": "~/cmd/work_on",
        "payload_on": "ON",
        "payload_off": "OFF",
        "icon": "mdi:power",
    })

    # Switch: Drying
    _add("switch", "isrunning", {
        "name": "Drying",
        "state_topic": "~/state",
        "value_template": "{{ value_json.isrunning }}",
        "command_topic": "~/cmd/isrunning",
        "payload_on": "ON",
        "payload_off": "OFF",
        "icon": "mdi:tumble-dryer",
    })

    # Select: Work Mode
    _add("select", "work_mode", {
        "name": "Work Mode",
        "state_topic": "~/state",
        "value_template": "{{ value_json.work_mode }}",
        "command_topic": "~/cmd/work_mode",
        "options": list(WORK_MODE_NAMES.values()),
        "icon": "mdi:cog",
    })

    # Select: Filament Drying Mode
    _add("select", "filament_drying_mode", {
        "name": "Filament Drying Mode",
        "state_topic": "~/state",
        "value_template": "{{ value_json.filament_drying_mode }}",
        "command_topic": "~/cmd/filament_drying_mode",
        "options": list(DRYING_MODE_NAMES.values()),
        "icon": "mdi:radiator",
    })

    # Number: Filter Hotbed Temp
    _add("number", "filtertemp", {
        "name": "Filter Hotbed Temperature",
        "state_topic": "~/state",
        "value_template": "{{ value_json.filtertemp }}",
        "command_topic": "~/cmd/filtertemp",
        "min": 0,
        "max": 120,
        "step": 1,
        "unit_of_measurement": "°C",
        "icon": "mdi:thermometer",
    })

    # Number: Heater Hotbed Temp
    _add("number", "hotbedtemp", {
        "name": "Heater Hotbed Temperature",
        "state_topic": "~/state",
        "value_template": "{{ value_json.hotbedtemp }}",
        "command_topic": "~/cmd/hotbedtemp",
        "min": 40,
        "max": 120,
        "step": 1,
        "unit_of_measurement": "°C",
        "icon": "mdi:thermometer-high",
    })

    # Number: Filament Drying Temp
    _add("number", "filament_temp", {
        "name": "Filament Drying Temperature",
        "state_topic": "~/state",
        "value_template": "{{ value_json.filament_temp }}",
        "command_topic": "~/cmd/filament_temp",
        "min": 40,
        "max": 60,
        "step": 1,
        "unit_of_measurement": "°C",
        "icon": "mdi:thermometer",
    })

    # Number: Filament Drying Timer
    _add("number", "filament_timer", {
        "name": "Filament Drying Timer",
        "state_topic": "~/state",
        "value_template": "{{ value_json.filament_timer }}",
        "command_topic": "~/cmd/filament_timer",
        "min": 1,
        "max": 24,
        "step": 1,
        "unit_of_measurement": "h",
        "icon": "mdi:timer-outline",
    })

    # Sensor: Enclosure Temperature
    _add("sensor", "warehouse_temper", {
        "name": "Enclosure Temperature",
        "state_topic": "~/state",
        "value_template": "{{ value_json.warehouse_temper }}",
        "device_class": "temperature",
        "unit_of_measurement": "°C",
        "state_class": "measurement",
    })

    # Sensor: Drying Time Remaining
    _add("sensor", "remaining_seconds", {
        "name": "Drying Time Remaining",
        "state_topic": "~/state",
        "value_template": "{{ value_json.remaining_seconds }}",
        "device_class": "duration",
        "unit_of_measurement": "s",
        "icon": "mdi:timer-sand",
    })

    # Sensor: Firmware Version
    _add("sensor", "fw_version", {
        "name": "Firmware Version",
        "state_topic": "~/state",
        "value_template": "{{ value_json.fw_version }}",
        "icon": "mdi:information-outline",
        "entity_category": "diagnostic",
    })


    # Button: Restart
    _add("button", "reset", {
        "name": "Restart",
        "command_topic": "~/cmd/reset",
        "payload_press": "PRESS",
        "device_class": "restart",
        "entity_category": "config",
    })

    # Switch: Auto-set chamber temp from gcode.
    # retain=true so HA persists the user's choice across addon restarts —
    # without it, the bridge would reset to its dataclass default on every reboot.
    _add("switch", "gcode_chamber_temp", {
        "name": "Auto Chamber Temp from G-code",
        "state_topic": "~/state",
        "value_template": "{{ value_json.gcode_chamber_temp_enabled }}",
        "command_topic": "~/cmd/gcode_chamber_temp",
        "payload_on": "ON",
        "payload_off": "OFF",
        "retain": True,
        "icon": "mdi:file-code",
        "entity_category": "config",
    })

    # Sensor: Last parsed chamber target (informational)
    _add("sensor", "gcode_chamber_target", {
        "name": "G-code Chamber Target",
        "state_topic": "~/state",
        "value_template": "{{ value_json.gcode_chamber_target }}",
        "unit_of_measurement": "°C",
        "device_class": "temperature",
        "state_class": "measurement",
        "icon": "mdi:thermometer-auto",
        "entity_category": "diagnostic",
    })

    # Sensor: Last seen print filename (informational)
    _add("sensor", "gcode_print_file", {
        "name": "G-code Print File",
        "state_topic": "~/state",
        "value_template": "{{ value_json.gcode_print_file }}",
        "icon": "mdi:file",
        "entity_category": "diagnostic",
    })

    return configs
