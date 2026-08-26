"""Delay-off number. Seconds, and no conversion.

setdelaytimer is seconds - established against the reference hood and
recorded in the library's PROTOCOL.md section 5, which is what lets this
entity carry a unit at all. It still writes exactly what the user enters:
seconds is the device's own unit, so a pass-through keeps the entity an
exact mirror of the field. An earlier version displayed minutes and
multiplied by 60; these tests guard against that conversion coming back and
rounding away timers the device can hold.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.number import NumberDeviceClass
from homeassistant.const import UnitOfTime

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


@pytest.mark.parametrize("raw", [0, 60, 90, 300, 600])
def test_raw_device_value_is_displayed_unconverted(raw):
    """Seconds in, seconds out. 90 is the case a minutes display would
    round: the device holds off-preset values, so the entity must show the
    one it is actually holding."""
    assert ZephyrDelayNumber(_coordinator(set_delay=raw)).native_value == raw


def test_the_unit_is_seconds():
    """setdelaytimer is seconds (the library's PROTOCOL.md section 5), so
    the entity says so. It was deliberately unitless while that was still
    an open validation question - a unit then would have been a guess
    dressed up as a fact."""
    number = ZephyrDelayNumber(_coordinator())
    assert number.native_unit_of_measurement == UnitOfTime.SECONDS
    assert number.device_class is NumberDeviceClass.DURATION


def test_unreported_delay_is_unknown_not_zero():
    """set_delay_timer is None when the device did not report it - unknown,
    not 'timer off'."""
    assert ZephyrDelayNumber(_coordinator(set_delay=None)).native_value is None


@pytest.mark.parametrize("value", [0, 60, 90, 300, 600])
async def test_setting_writes_the_raw_value(value):
    """Written in seconds, unmultiplied - the library's
    async_set_delay_timer takes seconds and passes them straight to
    setdelaytimer."""
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


@pytest.mark.parametrize(
    ("value", "written"), [(39.7, 40), (0.5, 1), (2.5, 3), (0.1, 1)]
)
async def test_positive_fractional_values_always_arm_the_timer(value, written):
    """HA's number.set_value validates only min/max, not step, so an
    automation can pass a fractional value. int() would truncate 39.7 down
    to 39; Python's half-to-even round() would turn 0.5 into 0 and 2.5
    into 2; and even half-up turns 0.1 into 0. Asking for ANY positive
    delay must arm the timer rather than silently disable it - the same
    rule the light applies to turn_on never rounding down to off - so
    positive requests clamp up to at least 1."""
    coordinator = _coordinator()
    await ZephyrDelayNumber(coordinator).async_set_native_value(value)
    coordinator.hood.async_set_delay_timer.assert_awaited_once_with(written)
