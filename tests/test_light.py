"""Light platform. Brightness 0-255 <-> discrete levels 0-3."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.light import ColorMode

from custom_components.zephyr_connect.light import ZephyrLight


def _coordinator(light=0, max_level=3):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.max_light_level = max_level
    caps.supports_tru_hue = False
    caps.urls = {}

    state = MagicMock()
    state.light = light
    state.is_online = True

    hood = MagicMock()
    hood.async_set_light = AsyncMock()

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.hood = hood
    return coordinator


def test_brightness_color_mode():
    """maxLightLevel > 1 means dimmable. truHueSupport is 0 on the reference
    device, so no colour temperature."""
    light = ZephyrLight(_coordinator())
    assert light.color_mode is ColorMode.BRIGHTNESS
    assert light.supported_color_modes == {ColorMode.BRIGHTNESS}


def test_off_state():
    light = ZephyrLight(_coordinator(light=0))
    assert light.is_on is False
    assert light.brightness == 0


def test_unreported_light_is_unknown_not_off():
    """light is None when the device did not report it - unknown, not off."""
    light = ZephyrLight(_coordinator(light=None))
    assert light.is_on is None
    assert light.brightness is None


def test_max_level_is_full_brightness():
    assert ZephyrLight(_coordinator(light=3)).brightness == 255


@pytest.mark.parametrize(("level", "expected"), [(1, 84), (2, 168), (3, 255)])
def test_levels_map_to_brightness(level, expected):
    """Expected values verified against the installed
    homeassistant.util.percentage.ranged_value_to_percentage, which scales
    with floor (integer //) division rather than round-half-up - e.g.
    level 1 of 3 is 33%, giving brightness 84, not the 85 naive rounding
    would suggest."""
    assert ZephyrLight(_coordinator(light=level)).brightness == expected


async def test_turn_on_with_brightness_writes_a_level():
    coordinator = _coordinator()
    await ZephyrLight(coordinator).async_turn_on(brightness=170)
    coordinator.hood.async_set_light.assert_awaited_once_with(2)


async def test_turn_on_without_brightness_uses_max():
    """A bare turn_on should give usable light, not the dimmest setting."""
    coordinator = _coordinator()
    await ZephyrLight(coordinator).async_turn_on()
    coordinator.hood.async_set_light.assert_awaited_once_with(3)


async def test_turn_off_writes_zero():
    coordinator = _coordinator(light=3)
    await ZephyrLight(coordinator).async_turn_off()
    coordinator.hood.async_set_light.assert_awaited_once_with(0)


async def test_low_brightness_never_rounds_to_off():
    """Rounding 1/255 down to level 0 would make turn_on silently do
    nothing, which reads as a broken light."""
    coordinator = _coordinator()
    await ZephyrLight(coordinator).async_turn_on(brightness=1)
    assert coordinator.hood.async_set_light.call_args.args[0] >= 1


async def test_level_never_exceeds_the_device_range():
    coordinator = _coordinator()
    await ZephyrLight(coordinator).async_turn_on(brightness=255)
    assert coordinator.hood.async_set_light.call_args.args[0] <= 3


async def test_light_is_gated_on_an_advertised_maximum():
    """max_light_level is None when the model does not advertise one - no
    light entity, same as an advertised 0."""
    from custom_components.zephyr_connect.light import async_setup_entry

    for max_level, expected in ((None, 0), (0, 0), (3, 1)):
        entry = MagicMock()
        entry.runtime_data = [_coordinator(max_level=max_level)]
        added = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == expected
