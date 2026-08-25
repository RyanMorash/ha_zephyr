"""Delay-off number. Raw device units - deliberately no unit or conversion.

Whether the device reads setdelaytimer as seconds or minutes is an open
hardware-validation question (the library's VALIDATION.md, question 2), so
the entity presents the raw value and writes exactly what the user enters.
An earlier version displayed minutes and multiplied by 60; these tests
guard against that conversion sneaking back before the units are
established.
"""

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

    hood = MagicMock()
    hood.async_set_delay_timer = AsyncMock()

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.hood = hood
    return coordinator


@pytest.mark.parametrize("raw", [0, 60, 300, 600])
def test_raw_device_value_is_displayed_unconverted(raw):
    """No unit inference: the device's own value, whatever it means."""
    assert ZephyrDelayNumber(_coordinator(set_delay=raw)).native_value == raw


def test_no_unit_is_presented():
    """The field's units are unvalidated; presenting one would be a guess
    dressed up as a fact."""
    assert (
        ZephyrDelayNumber(_coordinator()).native_unit_of_measurement is None
    )


def test_unreported_delay_is_unknown_not_zero():
    """set_delay_timer is None when the device did not report it - unknown,
    not 'timer off'."""
    assert ZephyrDelayNumber(_coordinator(set_delay=None)).native_value is None


@pytest.mark.parametrize("value", [0, 60, 300, 600])
async def test_setting_writes_the_raw_value(value):
    coordinator = _coordinator()
    await ZephyrDelayNumber(coordinator).async_set_native_value(float(value))
    coordinator.hood.async_set_delay_timer.assert_awaited_once_with(value)


async def test_zero_disables_the_timer():
    coordinator = _coordinator(set_delay=300)
    await ZephyrDelayNumber(coordinator).async_set_native_value(0)
    coordinator.hood.async_set_delay_timer.assert_awaited_once_with(0)


def test_is_a_config_entity():
    """Unlike clean air, this is a duration setting rather than a mode, so
    it belongs in the configuration section."""
    from homeassistant.helpers.entity import EntityCategory

    assert (
        ZephyrDelayNumber(_coordinator()).entity_category
        is EntityCategory.CONFIG
    )


async def test_fractional_values_round_rather_than_truncate():
    """HA's number.set_value validates only min/max, not step, so an
    automation can pass a fractional value. int() would truncate 39.7 down
    to 39 instead of rounding to 40."""
    coordinator = _coordinator()
    await ZephyrDelayNumber(coordinator).async_set_native_value(39.7)
    coordinator.hood.async_set_delay_timer.assert_awaited_once_with(40)
