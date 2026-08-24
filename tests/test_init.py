"""Entry lifecycle and coordinator behaviour. ZephyrClient is mocked."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from homeassistant.util import dt as dt_util

from custom_components.zephyr_connect.const import (
    DEGRADED_POLL_INTERVAL_SECONDS,
    DOMAIN,
)
from pyzephyrconnect import ZephyrAuthError, ZephyrError

THING = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"


def _caps():
    caps = MagicMock()
    caps.thing_name = THING
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.max_fan_speed = 6
    caps.max_light_level = 3
    caps.max_grease_filter_hours = 60
    caps.max_charcoal_filter_hours = 200
    caps.supports_tru_hue = False
    caps.urls = {"FAQURL": "https://zephyronline.com/faq"}
    return caps


def _state(**overrides):
    state = MagicMock()
    defaults = {
        "power": 0, "fan": 0, "light": 0, "is_online": True,
        "use_grease_filter_time": 643, "delay_timer": 0,
    }
    for key, value in {**defaults, **overrides}.items():
        setattr(state, key, value)
    return state


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.async_setup = AsyncMock(return_value=[_caps()])
    client.async_start = AsyncMock()
    client.async_stop = AsyncMock()
    client.async_poll = AsyncMock(return_value=_state())
    client.async_refresh_if_needed = AsyncMock(return_value=False)
    client.async_set_state = AsyncMock()
    client.state = MagicMock(return_value=_state())
    client.add_listener = MagicMock(return_value=lambda: None)
    client.connected = True
    with patch(
        "custom_components.zephyr_connect.ZephyrClient", return_value=client
    ):
        yield client


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    e = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "user@example.com", "password": "hunter2"},
        unique_id="us-west-2:00000000-1111-2222-3333-444455556666",
    )
    e.add_to_hass(hass)
    return e


async def test_setup_starts_each_hood(hass, entry, mock_client) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    mock_client.async_setup.assert_awaited_once()
    mock_client.async_start.assert_awaited_once_with(THING)


async def test_setup_registers_a_push_listener(hass, entry, mock_client) -> None:
    """Push is the primary update path; without a listener the integration
    would be silently poll-only."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_client.add_listener.assert_called_once()
    assert mock_client.add_listener.call_args.args[0] == THING


async def test_auth_failure_triggers_reauth(hass, entry, mock_client) -> None:
    mock_client.async_setup.side_effect = ZephyrAuthError("expired")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_transient_failure_retries(hass, entry, mock_client) -> None:
    """A vendor outage must retry, not permanently fail the entry."""
    mock_client.async_setup.side_effect = ZephyrError("vendor down")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_stops_the_client(hass, entry, mock_client) -> None:
    """Leaving the MQTT connection open would leak a paho thread per reload."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    mock_client.async_stop.assert_awaited()


async def test_degraded_poll_runs_when_mqtt_is_down(hass, entry, mock_client) -> None:
    """When the socket dies, HTTPS still returns live state. Entities must
    degrade to slower updates rather than going unavailable."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_poll.reset_mock()

    mock_client.connected = False
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    mock_client.async_poll.assert_awaited()


async def test_refresh_is_attempted_on_the_update_tick(hass, entry, mock_client) -> None:
    """Credentials expire hourly; the library no-ops until inside its margin."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_refresh_if_needed.reset_mock()

    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    mock_client.async_refresh_if_needed.assert_awaited()
