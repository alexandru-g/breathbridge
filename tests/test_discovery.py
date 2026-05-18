"""Tests for MQTT Discovery payload generation."""

import json

from breathbridge.discovery import generate_discovery_configs


def test_generates_all_entities(settings):
    configs = generate_discovery_configs(settings)
    assert len(configs) == 16

    # All config topics should be under the discovery prefix
    for topic, payload in configs:
        assert topic.startswith("homeassistant/")
        assert topic.endswith("/config")


def test_all_have_required_fields(settings):
    configs = generate_discovery_configs(settings)
    for topic, payload in configs:
        assert "device" in payload
        assert "unique_id" in payload
        assert "availability_topic" in payload
        assert payload["device"]["identifiers"] == [settings.device_id]
        assert payload["device"]["manufacturer"] == "Biqu"


def test_all_payloads_are_json_serializable(settings):
    configs = generate_discovery_configs(settings)
    for topic, payload in configs:
        json.dumps(payload)  # should not raise


def test_climate_entity(settings):
    configs = generate_discovery_configs(settings)
    climate = next(p for t, p in configs if "climate" in t)
    assert climate["modes"] == ["off", "heat"]
    assert climate["min_temp"] == 0
    assert climate["max_temp"] == 60
    assert "current_temperature_topic" in climate
    assert "temperature_command_topic" in climate
    assert "mode_command_topic" in climate


def test_switch_entities(settings):
    configs = generate_discovery_configs(settings)
    switches = [(t, p) for t, p in configs if "/switch/" in t]
    assert len(switches) == 3
    names = {p["name"] for _, p in switches}
    assert names == {"Power", "Drying", "Auto Chamber Temp from G-code"}


def test_select_entities(settings):
    configs = generate_discovery_configs(settings)
    selects = [(t, p) for t, p in configs if "/select/" in t]
    assert len(selects) == 2
    work_mode = next(p for _, p in selects if p["name"] == "Work Mode")
    assert work_mode["options"] == ["Auto", "Power On", "Filament Drying"]


def test_number_entities(settings):
    configs = generate_discovery_configs(settings)
    numbers = [(t, p) for t, p in configs if "/number/" in t]
    assert len(numbers) == 4


def test_sensor_entities(settings):
    configs = generate_discovery_configs(settings)
    sensors = [(t, p) for t, p in configs if "/sensor/" in t]
    assert len(sensors) == 5
    temp_sensor = next(p for _, p in sensors if p["name"] == "Enclosure Temperature")
    assert temp_sensor["device_class"] == "temperature"



def test_button_entity(settings):
    configs = generate_discovery_configs(settings)
    buttons = [(t, p) for t, p in configs if "/button/" in t]
    assert len(buttons) == 1
    assert buttons[0][1]["device_class"] == "restart"


def test_fw_version_in_device(settings):
    configs = generate_discovery_configs(settings, fw_version="1.2.3")
    _, payload = configs[0]
    assert payload["device"]["sw_version"] == "1.2.3"


def test_tilde_base_topic(settings):
    configs = generate_discovery_configs(settings)
    for _, payload in configs:
        assert payload["~"] == settings.base_topic
        # All topic references should use ~/
        for key, val in payload.items():
            if key.endswith("_topic") and isinstance(val, str):
                assert val.startswith("~/"), f"{key} should use tilde shorthand: {val}"
