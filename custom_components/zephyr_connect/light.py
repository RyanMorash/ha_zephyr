"""Light platform for Zephyr Connect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one light per hood that has one."""
    async_add_entities(
        ZephyrLight(coordinator)
        for coordinator in entry.runtime_data
        if coordinator.capabilities.max_light_level > 0
    )


class ZephyrLight(ZephyrEntity, LightEntity):
    """The hood work light."""

    _attr_translation_key = "hood_light"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "light")
        self._range = (1, coordinator.capabilities.max_light_level)

    @property
    def is_on(self) -> bool | None:
        state = self.hood
        return None if state is None else bool(state.light)

    @property
    def brightness(self) -> int | None:
        state = self.hood
        if state is None:
            return None
        if not state.light:
            return 0
        percent = ranged_value_to_percentage(self._range, state.light)
        return round(percent * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Write the light level only; the device raises power itself."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None:
            level = self._range[1]
        else:
            percent = round(brightness * 100 / 255)
            level = round(percentage_to_ranged_value(self._range, percent))
            # Never round down to 0: turn_on must always produce light.
            level = max(1, min(level, self._range[1]))
        await self.coordinator.async_set_state({"light": level})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state({"light": 0})
