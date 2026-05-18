"""Tests for the gcode chamber-temp parser."""

from __future__ import annotations

from breathbridge.slicer_watcher import SlicerWatcher, parse_chamber_temp


async def _noop(*_args, **_kw):
    return None


def _make_watcher() -> SlicerWatcher:
    return SlicerWatcher(
        base_url="http://printer",
        api_key="key",
        poll_interval=10.0,
        tail_bytes=50000,
        on_detect=_noop,
    )


def test_parse_from_config_block():
    text = """
; some thumbnail noise above
M141 S50 ; set chamber temperature
M191 S40 ; set chamber temperature and wait for it to be reached
; ...lots of stuff...
; prusaslicer_config = begin
; chamber_temperature = 50
; chamber_minimal_temperature = 40
; first_layer_temperature = 215
; prusaslicer_config = end
"""
    assert parse_chamber_temp(text) == 50


def test_parse_falls_back_to_m141_when_no_config_block():
    text = """
M191 S40 ; preheat chamber
G28
M141 S45 ; steady-state chamber target
G1 X10 Y10
"""
    assert parse_chamber_temp(text) == 45


def test_parse_prefers_last_m141():
    text = """
M141 S30
M141 S55
"""
    assert parse_chamber_temp(text) == 55


def test_parse_config_block_wins_over_m141():
    text = """
M141 S30
; chamber_temperature = 55
"""
    assert parse_chamber_temp(text) == 55


def test_parse_returns_none_when_nothing_present():
    text = """
G28 ; home
G1 X10 Y10 Z0.2
M104 S215
"""
    assert parse_chamber_temp(text) is None


def test_parse_treats_zero_as_no_temp():
    text = """
M141 S0
; chamber_temperature = 0
"""
    assert parse_chamber_temp(text) is None


def test_parse_handles_float_value():
    text = "; chamber_temperature = 45.7\n"
    assert parse_chamber_temp(text) == 45


def test_parse_ignores_other_chamber_keys():
    text = """
; chamber_minimal_temperature = 40
; chamber_temperature = 50
"""
    assert parse_chamber_temp(text) == 50


def test_parse_case_insensitive_m141():
    text = "m141 s42\n"
    assert parse_chamber_temp(text) == 42


def test_parse_csv_multi_tool_takes_max_positive():
    """PrusaSlicer on XL: one value per tool slot, zeros for unused tools."""
    text = "; chamber_temperature = 50,0,50,0,0\n"
    assert parse_chamber_temp(text) == 50


def test_parse_csv_picks_highest_when_tools_differ():
    text = "; chamber_temperature = 40,60,50\n"
    assert parse_chamber_temp(text) == 60


def test_parse_csv_all_zeros_means_none():
    text = "; chamber_temperature = 0,0,0\n"
    assert parse_chamber_temp(text) is None


def test_parse_real_xl_gcode_tail():
    """Regression: actual tail from a PrusaSlicer 2.9.4 XL slice (FUSELA~1.GCO).
    Multi-tool CSV form was previously not matched by the regex.
    """
    text = """
; bed_temperature = 95,60,95,100,80
; chamber_minimal_temperature = 40,0,40,0,0
; chamber_temperature = 50,0,50,0,0
; first_layer_bed_temperature = 100,60,100,100,85
; first_layer_temperature = 245,230,245,250,270
; idle_temperature = 70,70,70,100,70
; temperature = 245,230,245,250,270
"""
    assert parse_chamber_temp(text) == 50


# --- URL builder tests ---


def test_url_uses_refs_download_first():
    """The canonical case: refs.download is provided by the server."""
    w = _make_watcher()
    urls = w._build_file_url_candidates({
        "refs": {"download": "/usb/FUSELA~1.GCO"},
        "name": "FUSELA~1.GCO",
        "path": "/usb",
    })
    assert urls[0] == "http://printer/usb/FUSELA~1.GCO"


def test_url_full_path_with_usb_prefix():
    w = _make_watcher()
    urls = w._build_file_url_candidates({"path": "/usb/folder/test.gcode", "origin": "USB"})
    assert urls[0] == "http://printer/usb/folder/test.gcode"
    assert urls[1] == "http://printer/api/v1/files/usb/folder/test.gcode/raw"
    assert urls[2] == "http://printer/api/files/usb/folder/test.gcode/raw"


def test_url_full_path_with_local_prefix():
    w = _make_watcher()
    urls = w._build_file_url_candidates({"path": "/local/test.gcode", "origin": "LOCAL"})
    assert urls[0] == "http://printer/local/test.gcode"


def test_url_storage_root_path_uses_name():
    """The bug case: PrusaLink returns path='/usb' and the real name elsewhere."""
    w = _make_watcher()
    urls = w._build_file_url_candidates({"path": "/usb", "name": "mything.gcode", "origin": "USB"})
    assert urls[0] == "http://printer/usb/mything.gcode"


def test_url_bare_name_uses_origin():
    w = _make_watcher()
    urls = w._build_file_url_candidates({"path": "", "name": "mything.gcode", "origin": "LOCAL"})
    assert urls[0] == "http://printer/local/mything.gcode"


def test_url_path_with_unknown_storage_falls_back_to_origin():
    w = _make_watcher()
    urls = w._build_file_url_candidates({"path": "/foo/bar.gcode", "origin": "USB"})
    assert urls[0] == "http://printer/usb/foo/bar.gcode"


def test_url_returns_empty_when_nothing_useful():
    w = _make_watcher()
    assert w._build_file_url_candidates({"path": "/usb", "name": "", "origin": "USB"}) == []
    assert w._build_file_url_candidates({}) == []


def test_url_quotes_special_chars_but_keeps_slashes():
    w = _make_watcher()
    urls = w._build_file_url_candidates({"path": "/usb/my folder/file.gcode", "origin": "USB"})
    assert urls[0] == "http://printer/usb/my%20folder/file.gcode"
