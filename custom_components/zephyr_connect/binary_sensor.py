"""Binary sensor platform for Zephyr Connect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyzephyrconnect import HoodCapabilities, HoodState

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


def _flag(value: int | None) -> bool | None:
    """A device flag as a tri-state.

    None means the device did not report the field. bool(None) is False,
    which would silently read "no problem" for a fault whose state is
    actually unknown - the exact failure the library's None defaults exist
    to eliminate. Returning None makes HA show the entity as unknown.
    """
    return None if value is None else bool(value)


def _any_flag(*values: int | None) -> bool | None:
    """Combine device flags: set beats unknown beats clear.

    Any set flag is a problem regardless of the others; otherwise a single
    unreported flag makes the combination unknown, because "no problem"
    would claim knowledge of a field the device never sent.
    """
    if any(values):
        return True
    if any(value is None for value in values):
        return None
    return False


@dataclass(frozen=True, kw_only=True)
class ZephyrBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Zephyr binary sensor."""

    is_on_fn: Callable[[HoodState], bool | None]
    attributes_fn: Callable[[HoodState], dict[str, Any]] | None = None
    exists_fn: Callable[[HoodCapabilities], bool] = lambda _caps: True


BINARY_SENSORS: tuple[ZephyrBinarySensorDescription, ...] = (
    ZephyrBinarySensorDescription(
        key="grease_filter_due",
        translation_key="grease_filter_due",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: _flag(state.clean_grease_filters),
        # `cleangreasefilters` means due, `alarmgreasefilter` means overdue.
        # One entity with severity as an attribute beats two that would
        # both fire for the same filter.
        attributes_fn=lambda state: {"overdue": _flag(state.alarm_grease_filter)},
    ),
    ZephyrBinarySensorDescription(
        key="charcoal_filter_due",
        translation_key="charcoal_filter_due",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: _flag(state.clean_charcoal_filters),
        # None means the model does not advertise a filter life - no sensor,
        # same as an advertised 0.
        exists_fn=lambda caps: (caps.max_charcoal_filter_hours or 0) > 0,
    ),
    ZephyrBinarySensorDescription(
        key="fan_fault",
        translation_key="fan_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # alarmfan and fanwarning may differ in severity - unconfirmed,
        # neither has ever fired on the reference device. Either means the
        # fan needs attention.
        is_on_fn=lambda state: _any_flag(state.alarm_fan, state.fan_warning),
        attributes_fn=lambda state: {
            "alarm": _flag(state.alarm_fan),
            "warning": _flag(state.fan_warning),
        },
    ),
    ZephyrBinarySensorDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: _flag(state.alarm_fault_code),
        attributes_fn=lambda state: {
            "fault_codes": (
                None if state.fault_codes is None else list(state.fault_codes)
            )
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors for each hood, gated on capabilities."""
    async_add_entities(
        ZephyrBinarySensor(coordinator, description)
        for coordinator in entry.runtime_data
        for description in BINARY_SENSORS
        if description.exists_fn(coordinator.capabilities)
    )


class ZephyrBinarySensor(ZephyrEntity, BinarySensorEntity):
    """A fault or maintenance signal from the hood."""

    entity_description: ZephyrBinarySensorDescription

    def __init__(
        self,
        coordinator: ZephyrCoordinator,
        description: ZephyrBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        state = self.hood_state
        return None if state is None else self.entity_description.is_on_fn(state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self.hood_state
        if state is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(state)
