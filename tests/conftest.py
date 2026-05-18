"""Shared test fixtures."""

import pytest

from breathbridge.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(
        ws_host="192.168.1.50",
        mqtt_host="localhost",
        device_id="panda_breath_test",
        device_name="Test Panda Breath",
    )
