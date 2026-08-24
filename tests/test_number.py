"""Delay-off number. Displayed in minutes, written in seconds."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.zephyr_connect.number import ZephyrDelayNumber


def _coordinator(set_delay=0):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.urls = {}

    state = MagicMock()
    state.set_delay_timer = set_delay
    state.is_online = True

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.async_set_state = AsyncMock()
    return coordinator


@pytest.mark.parametrize(("seconds", "minutes"), [(0, 0), (300, 5), (600, 10), (60, 1)])
def test_seconds_are_displayed_as_minutes(seconds, minutes):
    """The device stores seconds; users think in minutes."""
    assert ZephyrDelayNumber(_coordinator(set_delay=seconds)).native_value == minutes


@pytest.mark.parametrize(("minutes", "seconds"), [(5, 300), (10, 600), (1, 60)])
async def test_setting_minutes_writes_seconds(minutes, seconds):
    coordinator = _coordinator()
    await ZephyrDelayNumber(coordinator).async_set_native_value(float(minutes))
    coordinator.async_set_state.assert_awaited_once_with(
        {"setdelaytimer": seconds}
    )


async def test_zero_disables_the_timer():
    coordinator = _coordinator(set_delay=300)
    await ZephyrDelayNumber(coordinator).async_set_native_value(0)
    coordinator.async_set_state.assert_awaited_once_with({"setdelaytimer": 0})


async def test_only_setdelaytimer_is_written():
    """Validated: the DEVICE derives delaytimer from setdelaytimer and
    counts it down. Writing delaytimer ourselves is unnecessary."""
    coordinator = _coordinator()
    await ZephyrDelayNumber(coordinator).async_set_native_value(5)
    written = coordinator.async_set_state.call_args.args[0]
    assert "delaytimer" not in written


def test_is_a_config_entity():
    """Unlike clean air, this is a duration setting rather than a mode, so
    it belongs in the configuration section."""
    from homeassistant.helpers.entity import EntityCategory

    assert (
        ZephyrDelayNumber(_coordinator()).entity_category
        is EntityCategory.CONFIG
    )


async def test_fractional_minutes_round_rather_than_truncate():
    """HA's number.set_value validates only min/max, not step, so an
    automation can pass a fractional minute. int() would truncate
    0.6666666 * 60 == 39.999996 down to 39 instead of rounding to 40."""
    coordinator = _coordinator()
    await ZephyrDelayNumber(coordinator).async_set_native_value(0.6666666)
    assert coordinator.async_set_state.call_args.args[0] == {"setdelaytimer": 40}
