"""Number platform for Zephyr Connect."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZephyrConfigEntry
from .const import DELAY_TIMER_MAX
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


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
    """Delay-off duration, in the device's raw units.

    Deliberately UNITLESS. Whether the device reads this field as seconds
    or minutes - and whether it snaps to presets - is an open
    hardware-validation question (the library's VALIDATION.md, question 2);
    the vendor app writes 300 for its "5 minutes" preset, which suggests
    seconds, but the device has never been watched counting down. Until
    that validation runs, this presents the raw value the device reports
    and writes exactly what the user enters - no unit, no conversion. An
    earlier draft presented minutes and multiplied by 60; that conversion
    belongs back here only once the units are established.

    ACTUATES: writing a non-zero value starts the fan at speed 1. That is
    validated device behaviour, not a bug - a delay-off timer implies the
    hood should run. The description must say so, because a number entity
    that starts an appliance is otherwise a surprise.

    The vendor app offers only two presets, but the device accepts
    arbitrary values, so this exposes the full range. The upper bound is a
    UI cap, not a device limit - the real ceiling is unknown.
    """

    _attr_translation_key = "delay_off"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_min_value = 0
    _attr_native_max_value = DELAY_TIMER_MAX
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "delay_off")

    @property
    def native_value(self) -> float | None:
        state = self.hood_state
        if state is None or state.set_delay_timer is None:
            # Unknown, not zero - the device did not report the field.
            return None
        return state.set_delay_timer

    async def async_set_native_value(self, value: float) -> None:
        """Write setdelaytimer only.

        The device derives `delaytimer` from this and counts it down itself,
        reporting once a minute. Writing `delaytimer` too would duplicate
        device-managed state.

        HA's number.set_value validates only min/max, not step, so an
        automation can pass a fractional value; round rather than truncate,
        and round HALF-UP rather than with Python's half-to-even round(),
        which would turn 0.5 into 0. int() truncates, which is floor for
        the non-negative values the min/max validation guarantees, so +0.5
        then int() is half-up. A positive request below 0.5 still clamps up
        to 1: asking for ANY positive delay must arm the timer, never
        silently disable it - the same rule the light applies to turn_on
        never rounding down to off. Only an explicit 0 turns the timer off.
        """
        raw = int(value + 0.5)
        if value > 0:
            raw = max(1, raw)
        await self._async_write(self.coordinator.hood.async_set_delay_timer(raw))
