"""Switch platform for Zephyr Connect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the power and clean-air switches for each hood."""
    entities: list[ZephyrEntity] = []
    for coordinator in entry.runtime_data:
        entities.append(ZephyrPowerSwitch(coordinator))
        entities.append(ZephyrCleanAirSwitch(coordinator))
    async_add_entities(entities)


class ZephyrPowerSwitch(ZephyrEntity, SwitchEntity):
    """Master power.

    Validated behaviour: writing 0 turns everything off; writing 1 restores
    the previously running levels (observed restoring fan 6 and light 1
    together). It is NOT a precondition - fan and light can be set directly
    while power reads 0, and the device raises power itself.
    """

    _attr_translation_key = "power"

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def is_on(self) -> bool | None:
        state = self.hood_state
        if state is None or state.power is None:
            # Unknown, not off - the device did not report the field.
            return None
        return bool(state.power)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(self.coordinator.hood.async_set_power(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(self.coordinator.hood.async_set_power(False))


class ZephyrCleanAirSwitch(ZephyrEntity, SwitchEntity):
    """Clean air mode.

    An operating mode, not a setting: enabling it starts the fan at speed 1.
    Deliberately NOT an EntityCategory.CONFIG entity - it actuates the hood,
    so it belongs alongside the fan and light.
    """

    _attr_translation_key = "clean_air"

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "clean_air")

    @property
    def is_on(self) -> bool | None:
        state = self.hood_state
        if state is None or state.set_clean_air_function is None:
            return None
        return bool(state.set_clean_air_function)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(self.coordinator.hood.async_set_clean_air(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(self.coordinator.hood.async_set_clean_air(False))
