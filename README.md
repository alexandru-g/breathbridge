# BreathBridge

A polyglot bridge that connects the Biqu Panda Breath chamber heater to your
Home Assistant + 3D printer setup. It speaks every protocol the Panda needs
so its **Auto mode** works even on non-Klipper printers:

- **MQTT Discovery** — exposes Panda controls/sensors as HA entities.
- **Panda Breath WebSocket** — drives the device.
- **Fake Moonraker server** — so the Panda's "Klipper" Auto mode can read your
  printer's bed temperature.
- **PrusaLink REST API** — pulls live bed temp and watches for new prints.
- **PrusaSlicer gcode** — extracts chamber target temp from sliced files.

## Home Assistant Add-on (recommended)

1. Go to **Settings > Add-ons > Add-on Store**
2. Top-right menu > **Repositories**
3. Add: `https://github.com/alexandru-g/breathbridge`
4. Install **BreathBridge**
5. Go to the **Configuration** tab and set `ws_host` to your Panda Breath IP or hostname (e.g. `pandabreath.local`)
6. **MQTT credentials:**
   - **Mosquitto add-on users**: credentials are detected automatically — no extra config needed
   - **External MQTT broker**: fill in `mqtt_host`, `mqtt_username`, and `mqtt_password` in the config
7. Start the add-on

## Standalone

```bash
pip install git+https://github.com/alexandru-g/breathbridge.git

PB_WS_HOST=pandabreath.local PB_MQTT_HOST=localhost breathbridge
```

### Configuration

Set environment variables (prefix `PB_`) or create a `.env` file:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `PB_WS_HOST` | yes | — | Device IP or hostname |
| `PB_MQTT_HOST` | no | `localhost` | MQTT broker address |
| `PB_MQTT_PORT` | no | `1883` | MQTT broker port |
| `PB_MQTT_USERNAME` | no | — | MQTT username |
| `PB_MQTT_PASSWORD` | no | — | MQTT password |
| `PB_DEVICE_ID` | no | `panda_breath` | Unique device ID |
| `PB_DEVICE_NAME` | no | `Panda Breath` | Display name in HA |
| `PB_LOG_LEVEL` | no | `INFO` | Log level |
| `PB_MOONRAKER_ENABLED` | no | `true` | Run the fake Moonraker server for Auto mode |
| `PB_MOONRAKER_PORT` | no | `7125` | Port the fake Moonraker listens on |
| `PB_PRUSALINK_HOST` | no | — | If set, poll PrusaLink for bed temp |
| `PB_PRUSALINK_PORT` | no | `80` | PrusaLink port |
| `PB_PRUSALINK_API_KEY` | no | — | PrusaLink API key |
| `PB_PRUSALINK_POLL_INTERVAL` | no | `5.0` | Seconds between PrusaLink polls |
| `PB_SLICER_WATCHER_ENABLED` | no | `true` | Watch PrusaLink for new prints and extract chamber temp from gcode |
| `PB_SLICER_WATCHER_POLL_INTERVAL` | no | `10.0` | Seconds between job-status checks |
| `PB_SLICER_WATCHER_TAIL_BYTES` | no | `50000` | Bytes to read from end of gcode file when parsing |

### Docker

```bash
docker run -d \
  -e PB_WS_HOST=pandabreath.local \
  -e PB_MQTT_HOST=192.168.1.10 \
  ghcr.io/alexandru-g/breathbridge:latest
```

## Exposed Entities

All entities auto-appear in Home Assistant under **Settings > Devices > MQTT > Panda Breath**:

- **Climate** — thermostat card with current/target temp and on/off
- **Switches** — Power, Drying
- **Selects** — Work Mode, Filament Drying Mode
- **Numbers** — Filter/Heater hotbed temp, Filament drying temp/timer
- **Sensors** — Enclosure temperature, Drying time remaining, Firmware version
- **Button** — Restart device

## Auto mode — feeding bed temperature from a non-Klipper printer

The Panda Breath's **Auto** work mode wants to read the printer's heatbed
temperature live, and only knows how to talk to Klipper/Moonraker. To make
Auto mode work with a Prusa (or any other non-Klipper printer), the bridge
runs a **minimal fake Moonraker server** on port `7125`. Point the Panda
Breath at it under **Settings > Printer > Klipper**.

There are two ways to feed bed temperature into the fake Moonraker:

### 1. Poll PrusaLink directly (recommended for Prusa printers)

Set `PB_PRUSALINK_HOST` + `PB_PRUSALINK_API_KEY` (or the add-on equivalents).
The bridge polls `/api/v1/status` every `PB_PRUSALINK_POLL_INTERVAL`
seconds and pushes `temp_bed` / `target_bed` into the fake Moonraker state.

### 2. Push via MQTT (any temperature source)

If PrusaLink isn't configured, publish to:

- `panda_breath/<device_id>/cmd/bed_temp` — current bed temperature (°C)
- `panda_breath/<device_id>/cmd/bed_target` — target bed temperature (°C)

Example Home Assistant automation that mirrors a PrusaLink sensor into the
fake Moonraker:

```yaml
automation:
  - alias: "Panda Breath: mirror Prusa bed temp"
    trigger:
      - platform: state
        entity_id:
          - sensor.prusa_bed_temperature
          - sensor.prusa_bed_target
    action:
      - service: mqtt.publish
        data:
          topic: "panda_breath/panda_breath/cmd/bed_temp"
          payload: "{{ states('sensor.prusa_bed_temperature') }}"
      - service: mqtt.publish
        data:
          topic: "panda_breath/panda_breath/cmd/bed_target"
          payload: "{{ states('sensor.prusa_bed_target') }}"
```

On the Panda Breath UI: pick **Klipper**, enter the bridge host's IP and
port `7125`, then hit **Scan** first (the firmware needs the scan handshake
to register the printer), and then **Bind**.

## Auto chamber temperature from gcode (optional)

When PrusaLink is configured, the bridge can additionally watch for new print
jobs and extract the chamber target temperature directly from the gcode
file — so you don't have to maintain HA automations that match slicer
settings.

The bridge looks for, in order of precedence:
1. `; chamber_temperature = N` from PrusaSlicer's config block at the end of the file
2. The last `M141 S<n>` in the file

When a value is found and the auto-set switch is **ON**, the bridge writes
that temp into the Panda's `set_temp` setting over its WebSocket.

**Requires bgcode disabled in PrusaSlicer:** Printer Settings → General →
uncheck "Supports binary G-code", then re-slice. The bridge parses plain
text gcode only.

**Two related toggles, different scopes:**

- **Add-on config `slicer_watcher_enabled`** (default ON) — controls whether the
  watcher *task* runs at all. Set to OFF to stop polling PrusaLink for job
  changes entirely. Useful if you don't want any of this feature.
- **HA switch `Auto Chamber Temp from G-code`** (default ON) — controls whether
  detected temps are *actually written* to the Panda. Toggle this from HA
  without restarting the add-on. Your choice is persisted across add-on
  restarts.

Two diagnostic sensors are exposed regardless of the switch state (as long as
the watcher task is running): `G-code Chamber Target` (last parsed value) and
`G-code Print File` (the filename it was parsed from). You can use these to
see what *would* be written if you flipped the switch on.

### Alternative: set chamber temp from filament material

If you prefer not to parse gcode, you can drive the chamber target off the
printer's reported material instead. The Panda Breath climate entity exposed
by this bridge accepts a target temperature directly. Example HA automation
using the Prusa integration's material sensor:

```yaml
alias: Panda Breath Set Target Temp
description: ""
triggers:
  - trigger: state
    entity_id: sensor.prusa_xl_material
    id: material_changed
    for: "00:05:00"
conditions:
  - condition: template
    value_template: >-
      {{ states('sensor.prusa_xl_material') not in ['', 'unknown',
      'unavailable', '---'] }}
actions:
  - action: climate.set_temperature
    target:
      entity_id: climate.panda_breath_climate
    data:
      temperature: >
        {% set material = states('sensor.prusa_xl_material') | upper %}
        {% if 'ASA' in material %} 60
        {% elif 'ABS' in material %} 60
        {% elif 'PA' in material %} 60
        {% elif 'PC' in material %} 60
        {% elif 'PP' in material %} 50
        {% else %} 0
        {% endif %}
```

Replace `sensor.prusa_xl_material` and `climate.panda_breath_climate` with
your own entity IDs. Adjust the per-material thresholds to taste.
