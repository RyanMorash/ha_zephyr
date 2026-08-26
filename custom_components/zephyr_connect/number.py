"""Number platform for Zephyr Connect."""

from __future__ import annotations

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.const import UnitOfTime
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
    """Delay-off duration, in seconds.

    SECONDS is now established rather than inferred: the device counts
    `setdelaytimer` down in 60-second steps and the countdown has been
    watched to zero (the library's PROTOCOL.md section 5). This carried no
    unit while that was still an open validation question.

    Still no conversion, though. Seconds is the device's own unit and the
    library's, so passing the value straight through keeps the entity an
    exact mirror of `setdelaytimer` - a minutes display would round a
    90-second timer set from the vendor app or another client, and buy
    nothing the user cannot get from the duration device class.

    ACTUATES: writing a non-zero value starts the fan at speed 1. That is
    validated device behaviour, not a bug - a delay-off timer implies the
    hood should run. The description must say so, because a number entity
    that starts an appliance is otherwise a surprise.

    The vendor app offers only two presets, but the device accepts
    arbitrary values, so this exposes the full range. The upper bound is
    where the evidence stops, not a known device limit: DELAY_TIMER_MAX is
    the largest value proven accepted, and the real ceiling is still
    unprobed.
    """

    _attr_translation_key = "delay_off"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
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
        """Write setdelaytimer only, in seconds.

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
