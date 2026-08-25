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
    """Set up one light per hood that has one.

    max_light_level is None when the model does not advertise one - absent
    means "not advertised", so no light entity, same as an advertised 0.
    """
    async_add_entities(
        ZephyrLight(coordinator)
        for coordinator in entry.runtime_data
        if (coordinator.capabilities.max_light_level or 0) > 0
    )


class ZephyrLight(ZephyrEntity, LightEntity):
    """The hood work light."""

    _attr_translation_key = "hood_light"
    # HoodCapabilities.supports_tru_hue (Zephyr's tunable-white feature) is
    # deliberately unconsumed here: the reference hood reports
    # truHueSupport: 0, so a color-temperature implementation would ship
    # unvalidated. A hood reporting supports_tru_hue = 1 gets
    # brightness-only control until someone with that hardware can verify
    # a COLOR_TEMP implementation against it.
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "light")
        # The setup gate guarantees a positive maximum; `or 1` only narrows
        # the type for the checker.
        self._range = (1, coordinator.capabilities.max_light_level or 1)

    @property
    def is_on(self) -> bool | None:
        state = self.hood_state
        if state is None or state.light is None:
            # Unknown, not off - the device did not report the field.
            return None
        return bool(state.light)

    @property
    def brightness(self) -> int | None:
        state = self.hood_state
        if state is None or state.light is None:
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
        await self._async_write(self.coordinator.hood.async_set_light(level))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(self.coordinator.hood.async_set_light(0))
