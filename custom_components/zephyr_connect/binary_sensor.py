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


@dataclass(frozen=True, kw_only=True)
class ZephyrBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Zephyr binary sensor."""

    is_on_fn: Callable[[HoodState], bool]
    attributes_fn: Callable[[HoodState], dict[str, Any]] | None = None
    exists_fn: Callable[[HoodCapabilities], bool] = lambda _caps: True


BINARY_SENSORS: tuple[ZephyrBinarySensorDescription, ...] = (
    ZephyrBinarySensorDescription(
        key="grease_filter_due",
        translation_key="grease_filter_due",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: bool(state.clean_grease_filters),
        # `cleangreasefilters` means due, `alarmgreasefilter` means overdue.
        # One entity with severity as an attribute beats two that would
        # both fire for the same filter.
        attributes_fn=lambda state: {"overdue": bool(state.alarm_grease_filter)},
    ),
    ZephyrBinarySensorDescription(
        key="charcoal_filter_due",
        translation_key="charcoal_filter_due",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: bool(state.clean_charcoal_filters),
        exists_fn=lambda caps: caps.max_charcoal_filter_hours > 0,
    ),
    ZephyrBinarySensorDescription(
        key="fan_fault",
        translation_key="fan_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # alarmfan and fanwarning may differ in severity - unconfirmed,
        # neither has ever fired on the reference device. Either means the
        # fan needs attention.
        is_on_fn=lambda state: bool(state.alarm_fan or state.fan_warning),
        attributes_fn=lambda state: {
            "alarm": bool(state.alarm_fan),
            "warning": bool(state.fan_warning),
        },
    ),
    ZephyrBinarySensorDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: bool(state.alarm_fault_code),
        attributes_fn=lambda state: {"fault_codes": list(state.fault_codes)},
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
        state = self.hood
        return None if state is None else self.entity_description.is_on_fn(state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self.hood
        if state is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(state)
