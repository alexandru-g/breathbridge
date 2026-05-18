"""Constants, enums, and mappings for the Panda Breath MQTT bridge."""

from enum import IntEnum

MANUFACTURER = "Biqu"
MODEL = "Panda Breath"


class WorkMode(IntEnum):
    AUTO = 1
    POWER_ON = 2
    FILAMENT_DRYING = 3


class FilamentDryingMode(IntEnum):
    PLA = 1
    PETG_ABS = 2
    CUSTOM = 3


class SensorStatus(IntEnum):
    OK = 0
    SHORT_CIRCUIT = 1
    OPEN_CIRCUIT = 2


WORK_MODE_NAMES: dict[int, str] = {
    WorkMode.AUTO: "Auto",
    WorkMode.POWER_ON: "Power On",
    WorkMode.FILAMENT_DRYING: "Filament Drying",
}
WORK_MODE_FROM_NAME: dict[str, int] = {v: k for k, v in WORK_MODE_NAMES.items()}

DRYING_MODE_NAMES: dict[int, str] = {
    FilamentDryingMode.PLA: "PLA",
    FilamentDryingMode.PETG_ABS: "PETG-ABS",
    FilamentDryingMode.CUSTOM: "Custom",
}
DRYING_MODE_FROM_NAME: dict[str, int] = {v: k for k, v in DRYING_MODE_NAMES.items()}
