"""Power and clean-air switches. Both actuate the hood."""

from unittest.mock import AsyncMock, MagicMock

from custom_components.zephyr_connect.switch import (
    ZephyrCleanAirSwitch,
    ZephyrPowerSwitch,
)


def _coordinator(power=0, clean_air=0):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.urls = {}

    state = MagicMock()
    state.power = power
    state.set_clean_air_function = clean_air
    state.is_online = True

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.async_set_state = AsyncMock()
    return coordinator


def test_power_reflects_reported_state():
    assert ZephyrPowerSwitch(_coordinator(power=1)).is_on is True
    assert ZephyrPowerSwitch(_coordinator(power=0)).is_on is False


async def test_power_on_restores_previous_levels():
    """Validated: power=1 restored fan 6 and light 1 together. We just write
    1 and let the device decide what to restore."""
    coordinator = _coordinator()
    await ZephyrPowerSwitch(coordinator).async_turn_on()
    coordinator.async_set_state.assert_awaited_once_with({"power": 1})


async def test_power_off_stops_everything():
    coordinator = _coordinator(power=1)
    await ZephyrPowerSwitch(coordinator).async_turn_off()
    coordinator.async_set_state.assert_awaited_once_with({"power": 0})


def test_clean_air_reflects_reported_state():
    assert ZephyrCleanAirSwitch(_coordinator(clean_air=1)).is_on is True


async def test_clean_air_on_writes_one():
    """Validated side effect: this also starts the fan at speed 1."""
    coordinator = _coordinator()
    await ZephyrCleanAirSwitch(coordinator).async_turn_on()
    coordinator.async_set_state.assert_awaited_once_with(
        {"setcleanairfunction": 1}
    )


async def test_clean_air_off_writes_zero():
    coordinator = _coordinator(clean_air=1)
    await ZephyrCleanAirSwitch(coordinator).async_turn_off()
    coordinator.async_set_state.assert_awaited_once_with(
        {"setcleanairfunction": 0}
    )


def test_clean_air_is_not_a_config_entity():
    """It runs the fan, so it belongs beside the controls rather than
    buried in the configuration section."""
    assert ZephyrCleanAirSwitch(_coordinator()).entity_category is None
