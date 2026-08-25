"""Fan platform for Zephyr Connect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)
from homeassistant.util.scaling import int_states_in_range

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one fan per hood.

    max_fan_speed is None when the model does not advertise one - absent
    means "not advertised", so no fan entity, same as an advertised 0. The
    percentage mapping below needs a real range to be meaningful.
    """
    async_add_entities(
        ZephyrFan(coordinator)
        for coordinator in entry.runtime_data
        if (coordinator.capabilities.max_fan_speed or 0) > 0
    )


class ZephyrFan(ZephyrEntity, FanEntity):
    """The hood blower."""

    _attr_name = None  # the fan IS the device's primary entity
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "fan")
        # The setup gate guarantees a positive maximum; `or 1` only narrows
        # the type for the checker.
        self._range = (1, coordinator.capabilities.max_fan_speed or 1)

    @property
    def speed_count(self) -> int:
        return int_states_in_range(self._range)

    @property
    def percentage(self) -> int | None:
        state = self.hood_state
        if state is None or state.fan is None:
            # None means the device did not report the field - unknown, not
            # off. bool(None) would silently read as 0%.
            return None
        if not state.fan:
            return 0
        return ranged_value_to_percentage(self._range, state.fan)

    @property
    def is_on(self) -> bool | None:
        state = self.hood_state
        if state is None or state.fan is None:
            return None
        return bool(state.fan)

    async def async_set_percentage(self, percentage: int) -> None:
        """Write the fan level only.

        The device raises `power` to 1 on its own when the fan starts, so
        writing power here would be redundant and could fight it.
        """
        if percentage == 0:
            await self._async_write(self.coordinator.hood.async_set_fan(0))
            return
        speed = round(percentage_to_ranged_value(self._range, percentage))
        speed = max(1, min(speed, self._range[1]))
        await self._async_write(self.coordinator.hood.async_set_fan(speed))

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        # No percentage means "just start" - use the quietest speed, which
        # matches how the hood starts itself when a delay timer is armed.
        await self.async_set_percentage(
            percentage if percentage is not None else
            ranged_value_to_percentage(self._range, 1)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(self.coordinator.hood.async_set_fan(0))
