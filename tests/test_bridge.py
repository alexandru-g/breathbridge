"""Tests for bridge command dispatch."""

from breathbridge.bridge import Bridge
from breathbridge.config import Settings


def _make_bridge() -> Bridge:
    settings = Settings(ws_host="192.168.1.50", mqtt_host="localhost")
    return Bridge(settings)


def test_command_work_on():
    b = _make_bridge()
    result = b._handle_command("work_on", "ON")
    assert result == ("settings", {"work_on": True})
    result = b._handle_command("work_on", "OFF")
    assert result == ("settings", {"work_on": False})


def test_command_work_mode():
    b = _make_bridge()
    result = b._handle_command("work_mode", "Auto")
    assert result == ("settings", {"work_mode": 1})
    result = b._handle_command("work_mode", "Filament Drying")
    assert result == ("settings", {"work_mode": 3})


def test_command_drying_mode():
    b = _make_bridge()
    result = b._handle_command("filament_drying_mode", "PLA")
    assert result == ("settings", {"filament_drying_mode": 1})
    result = b._handle_command("filament_drying_mode", "PETG-ABS")
    assert result == ("settings", {"filament_drying_mode": 2})


def test_command_number():
    b = _make_bridge()
    for key in ("set_temp", "filtertemp", "hotbedtemp", "filament_temp", "filament_timer"):
        result = b._handle_command(key, "42")
        assert result == ("settings", {key: 42})


def test_command_number_float():
    b = _make_bridge()
    result = b._handle_command("set_temp", "42.5")
    assert result == ("settings", {"set_temp": 42})


def test_command_isrunning():
    b = _make_bridge()
    result = b._handle_command("isrunning", "ON")
    assert result == ("settings", {"isrunning": 1})
    result = b._handle_command("isrunning", "OFF")
    assert result == ("settings", {"isrunning": 0})


def test_command_reset():
    b = _make_bridge()
    result = b._handle_command("reset", "PRESS")
    assert result == ("settings", {"reset": 1})


def test_command_climate_mode():
    b = _make_bridge()
    result = b._handle_command("climate_mode", "off")
    assert result == ("settings", {"work_on": False})
    result = b._handle_command("climate_mode", "heat")
    assert result == ("settings", {"work_on": True, "work_mode": 2})


def test_command_climate_temp():
    b = _make_bridge()
    result = b._handle_command("climate_temp", "45.0")
    assert result == ("settings", {"set_temp": 45})


def test_unknown_command():
    b = _make_bridge()
    result = b._handle_command("nonexistent", "value")
    assert result is None


def test_invalid_work_mode():
    b = _make_bridge()
    result = b._handle_command("work_mode", "InvalidMode")
    assert result is None
