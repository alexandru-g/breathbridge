#!/bin/sh

# Read all config from /data/options.json and try Supervisor API for MQTT
eval "$(python3 << 'PYEOF'
import json, os, urllib.request

opts = json.load(open("/data/options.json"))

# Always export non-MQTT options
for key, env in [
    ("ws_host", "PB_WS_HOST"),
    ("device_name", "PB_DEVICE_NAME"),
    ("device_id", "PB_DEVICE_ID"),
    ("log_level", "PB_LOG_LEVEL"),
    ("moonraker_enabled", "PB_MOONRAKER_ENABLED"),
    ("moonraker_port", "PB_MOONRAKER_PORT"),
    ("prusalink_host", "PB_PRUSALINK_HOST"),
    ("prusalink_port", "PB_PRUSALINK_PORT"),
    ("prusalink_api_key", "PB_PRUSALINK_API_KEY"),
    ("prusalink_poll_interval", "PB_PRUSALINK_POLL_INTERVAL"),
    ("slicer_watcher_enabled", "PB_SLICER_WATCHER_ENABLED"),
    ("slicer_watcher_poll_interval", "PB_SLICER_WATCHER_POLL_INTERVAL"),
    ("slicer_watcher_tail_bytes", "PB_SLICER_WATCHER_TAIL_BYTES"),
]:
    print(f"export {env}='{opts.get(key, '')}'")

# Try Supervisor API for MQTT credentials (works with Mosquitto add-on)
mqtt = {}
token = os.environ.get("SUPERVISOR_TOKEN", "")
if token:
    try:
        req = urllib.request.Request(
            "http://supervisor/services/mqtt",
            headers={"Authorization": f"Bearer {token}"}
        )
        mqtt = json.load(urllib.request.urlopen(req)).get("data", {})
    except Exception:
        pass

# Use Supervisor MQTT info if available, otherwise fall back to config options
print(f"export PB_MQTT_HOST='{mqtt.get('host') or opts.get('mqtt_host', 'core-mosquitto')}'")
print(f"export PB_MQTT_PORT='{mqtt.get('port') or opts.get('mqtt_port', 1883)}'")
print(f"export PB_MQTT_USERNAME='{mqtt.get('username') or opts.get('mqtt_username', '')}'")
print(f"export PB_MQTT_PASSWORD='{mqtt.get('password') or opts.get('mqtt_password', '')}'")
PYEOF
)"

exec python3 -m breathbridge
