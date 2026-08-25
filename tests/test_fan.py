"""Fan platform. Percentage <-> discrete speed conversion is the risk area."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.zephyr_connect.fan import ZephyrFan


def _coordinator(fan=0, max_speed=6):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.max_fan_speed = max_speed
    caps.urls = {}

    state = MagicMock()
    state.fan = fan
    state.is_online = True

    hood = MagicMock()
    hood.async_set_fan = AsyncMock()

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.hood = hood
    return coordinator


def test_speed_count_comes_from_capabilities():
    """Never hardcode 6 - other Zephyr models differ."""
    assert ZephyrFan(_coordinator(max_speed=4)).speed_count == 4


def test_off_reports_zero_percent():
    assert ZephyrFan(_coordinator(fan=0)).percentage == 0
    assert ZephyrFan(_coordinator(fan=0)).is_on is False


def test_max_speed_reports_one_hundred_percent():
    assert ZephyrFan(_coordinator(fan=6)).percentage == 100
    assert ZephyrFan(_coordinator(fan=6)).is_on is True


def test_unreported_fan_is_unknown_not_off():
    """fan is None when the device did not report it. bool(None) is False,
    so without an explicit check an unknown fan would silently read as off -
    the exact coercion the library's None defaults exist to eliminate."""
    fan = ZephyrFan(_coordinator(fan=None))
    assert fan.percentage is None
    assert fan.is_on is None


@pytest.mark.parametrize(("speed", "expected"), [(1, 16), (3, 50), (5, 83)])
def test_intermediate_speeds_map_to_percentages(speed, expected):
    """Expected values verified against the installed
    homeassistant.util.percentage.ranged_value_to_percentage, which scales
    with floor (integer //) division rather than round-half-up - e.g.
    speed 1 of 6 is 16%, not the 17% naive rounding would suggest."""
    assert ZephyrFan(_coordinator(fan=speed)).percentage == expected


async def test_set_percentage_writes_only_the_fan_field():
    """The device raises power itself. Writing power here would be
    redundant and risks fighting the device."""
    coordinator = _coordinator()
    await ZephyrFan(coordinator).async_set_percentage(50)
    coordinator.hood.async_set_fan.assert_awaited_once_with(3)


async def test_set_percentage_zero_turns_off():
    coordinator = _coordinator(fan=4)
    await ZephyrFan(coordinator).async_set_percentage(0)
    coordinator.hood.async_set_fan.assert_awaited_once_with(0)


async def test_turn_on_without_percentage_uses_lowest_speed():
    """HA may call turn_on with no percentage. Starting at speed 1 matches
    how the hood starts itself when a delay timer is armed."""
    coordinator = _coordinator()
    await ZephyrFan(coordinator).async_turn_on()
    coordinator.hood.async_set_fan.assert_awaited_once_with(1)


async def test_turn_on_with_percentage_uses_it():
    coordinator = _coordinator()
    await ZephyrFan(coordinator).async_turn_on(percentage=100)
    coordinator.hood.async_set_fan.assert_awaited_once_with(6)


async def test_turn_off_writes_zero():
    coordinator = _coordinator(fan=6)
    await ZephyrFan(coordinator).async_turn_off()
    coordinator.hood.async_set_fan.assert_awaited_once_with(0)


async def test_percentage_never_exceeds_the_device_range():
    """A rounding bug that writes 7 to a 0-6 device would now raise
    ZephyrWriteError in the library; keep it from ever getting there."""
    coordinator = _coordinator()
    await ZephyrFan(coordinator).async_set_percentage(99)
    written = coordinator.hood.async_set_fan.call_args.args[0]
    assert 0 <= written <= 6


async def test_fan_is_gated_on_an_advertised_maximum():
    """max_fan_speed is None when the model does not advertise one; the
    percentage mapping needs a real range, so no fan entity - same as an
    advertised 0."""
    from custom_components.zephyr_connect.fan import async_setup_entry

    for max_speed, expected in ((None, 0), (0, 0), (6, 1)):
        entry = MagicMock()
        entry.runtime_data = [_coordinator(max_speed=max_speed)]
        added = []
        await async_setup_entry(
            MagicMock(), entry, lambda e, added=added: added.extend(e)
        )
        assert len(added) == expected
