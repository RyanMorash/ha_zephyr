"""Diagnostics must be useful for debugging AND free of personal data."""

import json
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from custom_components.zephyr_connect.diagnostics import (
    async_get_config_entry_diagnostics,
)

THING = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
SERIAL = "1234567XYZ"
MAC = "00:00:5e:00:53:00"


def _entry():
    caps = MagicMock()
    caps.thing_name = THING
    caps.serial = SERIAL
    caps.mac = MAC
    caps.model = "AK7400AS"
    caps.manufacturer = "ZEPHYR"
    caps.max_fan_speed = 6
    caps.max_light_level = 3
    caps.supports_tru_hue = False
    caps.raw = {
        "thingName": THING,
        "SN": SERIAL,
        "MAC": MAC,
        "location": {"lng": "-XX.XXXX", "lat": "YY.YYYY"},
        "modelName": "AK7400AS",
        "maxFanSpeed": 6,
        "truHueSupport": 0,
    }

    state = MagicMock()
    state.raw = {"fan": 0, "light": 1, "power": 1, "somethingNew": 42}

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = THING
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.client.connected = True

    entry = MagicMock()
    entry.runtime_data = [coordinator]
    entry.data = {"username": "user@example.com", "password": "hunter2"}
    return entry


async def test_no_personal_data_leaks(hass: HomeAssistant) -> None:
    """thingName, serial, MAC and coordinates identify a home and its owner.
    A user pasting diagnostics into a GitHub issue must not expose them."""
    result = await async_get_config_entry_diagnostics(hass, _entry())
    blob = json.dumps(result)

    for secret in (THING, SERIAL, MAC, "-XX.XXXX", "YY.YYYY", "hunter2",
                   "user@example.com"):
        assert secret not in blob, f"{secret!r} leaked into diagnostics"


async def test_unknown_shadow_fields_survive(hass: HomeAssistant) -> None:
    """The whole point: a field we do not model yet must reach the
    maintainer, because that is how new models get characterised."""
    result = await async_get_config_entry_diagnostics(hass, _entry())
    assert result["hoods"][0]["state"]["somethingNew"] == 42


async def test_capabilities_are_included(hass: HomeAssistant) -> None:
    """Capabilities explain why entities exist or do not."""
    caps = (await async_get_config_entry_diagnostics(hass, _entry()))["hoods"][0][
        "capabilities"
    ]
    assert caps["maxFanSpeed"] == 6
    assert caps["truHueSupport"] == 0


async def test_transport_health_is_reported(hass: HomeAssistant) -> None:
    """'Is push working?' is the first question for any stale-data report."""
    result = await async_get_config_entry_diagnostics(hass, _entry())
    assert result["hoods"][0]["connected"] is True
    assert result["hoods"][0]["last_update_success"] is True
