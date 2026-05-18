"""Tests for device state tracking."""

from breathbridge.state import StateTracker


def test_initial_state_is_none():
    tracker = StateTracker()
    assert tracker.state.work_on is None
    assert tracker.state.warehouse_temper is None


def test_update_settings():
    tracker = StateTracker()
    changed = tracker.update({"settings": {"work_on": True, "warehouse_temper": 25}})
    assert "work_on" in changed
    assert "warehouse_temper" in changed
    assert tracker.state.work_on is True
    assert tracker.state.warehouse_temper == 25


def test_no_change_on_same_value():
    tracker = StateTracker()
    tracker.update({"settings": {"warehouse_temper": 25}})
    changed = tracker.update({"settings": {"warehouse_temper": 25}})
    assert len(changed) == 0


def test_custom_temp_maps_to_filament_temp():
    tracker = StateTracker()
    changed = tracker.update({"settings": {"custom_temp": 50, "custom_timer": 12}})
    assert "filament_temp" in changed
    assert "filament_timer" in changed
    assert tracker.state.filament_temp == 50
    assert tracker.state.filament_timer == 12


def test_printer_fields():
    tracker = StateTracker()
    changed = tracker.update({"printer": {"name": "Bambu X1", "state": 3, "ip": "1.2.3.4"}})
    assert "printer_name" in changed
    assert tracker.state.printer_name == "Bambu X1"
    assert tracker.state.printer_state == 3


def test_unknown_fields_ignored():
    tracker = StateTracker()
    changed = tracker.update({"settings": {"unknown_field": 42}})
    assert len(changed) == 0


def test_to_mqtt_payload_switches():
    tracker = StateTracker()
    tracker.update({"settings": {"work_on": True, "isrunning": 0}})
    payload = tracker.to_mqtt_payload()
    assert payload["work_on"] == "ON"
    assert payload["isrunning"] == "OFF"


def test_to_mqtt_payload_selects():
    tracker = StateTracker()
    tracker.update({"settings": {"work_mode": 1, "filament_drying_mode": 2}})
    payload = tracker.to_mqtt_payload()
    assert payload["work_mode"] == "Auto"
    assert payload["filament_drying_mode"] == "PETG-ABS"



def test_to_mqtt_payload_numbers():
    tracker = StateTracker()
    tracker.update({"settings": {"set_temp": 40, "filtertemp": 80, "hotbedtemp": 60}})
    payload = tracker.to_mqtt_payload()
    assert payload["set_temp"] == 40
    assert payload["filtertemp"] == 80
    assert payload["hotbedtemp"] == 60


def test_partial_updates_accumulate():
    tracker = StateTracker()
    tracker.update({"settings": {"work_on": True}})
    tracker.update({"settings": {"warehouse_temper": 30}})
    payload = tracker.to_mqtt_payload()
    assert payload["work_on"] == "ON"
    assert payload["warehouse_temper"] == 30
