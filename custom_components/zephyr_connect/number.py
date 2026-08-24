"""Number platform for Zephyr Connect."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZephyrConfigEntry
from .const import DELAY_TIMER_MAX_MINUTES
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity

SECONDS_PER_MINUTE = 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the delay-off number for each hood."""
    async_add_entities(
        ZephyrDelayNumber(coordinator) for coordinator in entry.runtime_data
    )


class ZephyrDelayNumber(ZephyrEntity, NumberEntity):
    """Delay-off duration.

    The device stores seconds; this presents minutes because that is how
    users think about it and how the vendor app labels it.

    ACTUATES: writing a non-zero value starts the fan at speed 1. That is
    validated device behaviour, not a bug - a delay-off timer implies the
    hood should run. The description must say so, because a number entity
    that starts an appliance is otherwise a surprise.

    The vendor app offers only 5 and 10 minutes, but the device accepts
    arbitrary values, so this exposes the full range. The upper bound is a
    UI cap, not a device limit - the real ceiling is unknown.
    """

    _attr_translation_key = "delay_off"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 0
    _attr_native_max_value = DELAY_TIMER_MAX_MINUTES
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "delay_off")

    @property
    def native_value(self) -> float | None:
        state = self.hood
        if state is None:
            return None
        return state.set_delay_timer / SECONDS_PER_MINUTE

    async def async_set_native_value(self, value: float) -> None:
        """Write setdelaytimer only.

        The device derives `delaytimer` from this and counts it down itself,
        reporting once a minute. Writing `delaytimer` too would duplicate
        device-managed state.
        """
        await self.coordinator.async_set_state(
            {"setdelaytimer": int(value * SECONDS_PER_MINUTE)}
        )
