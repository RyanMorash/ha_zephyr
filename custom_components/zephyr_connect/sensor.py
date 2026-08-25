"""Sensor platform for Zephyr Connect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyzephyrconnect import HoodCapabilities, HoodState

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity

MINUTES_PER_HOUR = 60


def _filter_remaining(used_minutes: int, life_hours: int | None) -> float | None:
    """Percentage of filter life left.

    Verified against the vendor app: 643 minutes against a 60-hour life
    yields 82.1%, and the app displays 82%.

    The counter is in MINUTES and the capability maximum is in HOURS - a
    mismatch that is easy to miss and wrong by 60x if conflated.

    life_hours is None when the model does not advertise a filter life.
    The exists_fn gates already keep such hoods from getting this sensor,
    but comparing None would raise TypeError, so guard here too.
    """
    if life_hours is None or life_hours <= 0:
        return None
    used_fraction = used_minutes / (life_hours * MINUTES_PER_HOUR)
    # Clamp: past end-of-life the raw value goes negative.
    return round(max(0.0, min(1.0, 1 - used_fraction)) * 100, 1)


@dataclass(frozen=True, kw_only=True)
class ZephyrSensorDescription(SensorEntityDescription):
    """Describes a Zephyr sensor."""

    value_fn: Callable[[HoodState, HoodCapabilities], Any]
    attributes_fn: Callable[[HoodState, HoodCapabilities], dict[str, Any]] | None = None
    exists_fn: Callable[[HoodCapabilities], bool] = lambda _caps: True


SENSORS: tuple[ZephyrSensorDescription, ...] = (
    ZephyrSensorDescription(
        key="grease_filter",
        translation_key="grease_filter",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state, caps: _filter_remaining(
            state.use_grease_filter_time, caps.max_grease_filter_hours
        ),
        attributes_fn=lambda state, caps: {
            "used_minutes": state.use_grease_filter_time,
            "life_hours": caps.max_grease_filter_hours,
            "store_url": caps.urls.get("GreaseFilterWebstoreURL"),
            "video_url": caps.urls.get("GreaseFilterVideoURL"),
        },
        # None means the model does not advertise a filter life - no sensor,
        # same as an advertised 0.
        exists_fn=lambda caps: (caps.max_grease_filter_hours or 0) > 0,
    ),
    ZephyrSensorDescription(
        key="charcoal_filter",
        translation_key="charcoal_filter",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state, caps: _filter_remaining(
            state.use_charcoal_filter_time, caps.max_charcoal_filter_hours
        ),
        attributes_fn=lambda state, caps: {
            "used_minutes": state.use_charcoal_filter_time,
            "life_hours": caps.max_charcoal_filter_hours,
            "store_url": caps.urls.get("CharcoalFilterWebstoreURL"),
            "video_url": caps.urls.get("CharcoalFilterVideoURL"),
        },
        exists_fn=lambda caps: (caps.max_charcoal_filter_hours or 0) > 0,
    ),
    ZephyrSensorDescription(
        key="fan_runtime",
        translation_key="fan_runtime",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state, _caps: state.use_fan_time,
    ),
    ZephyrSensorDescription(
        key="light_runtime",
        translation_key="light_runtime",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state, _caps: state.use_light_time,
    ),
    ZephyrSensorDescription(
        key="delay_remaining",
        translation_key="delay_remaining",
        # Deliberately unitless, like the delay-off number: whether the
        # timer fields hold seconds or minutes is an open
        # hardware-validation question (the library's VALIDATION.md,
        # question 2), and a duration device class needs a unit. The raw
        # countdown value still shows the timer running and reaching zero.
        # delay_timer is None when unreported, which a sensor shows as
        # unknown - exactly right.
        value_fn=lambda state, _caps: state.delay_timer,
    ),
    ZephyrSensorDescription(
        key="act",
        translation_key="act",
        entity_category=EntityCategory.DIAGNOSTIC,
        # Airflow Control Technology: Zephyr's airflow cap for make-up-air
        # code compliance. Configured PHYSICALLY on the hood and not
        # settable from the cloud, so this is strictly a readout - but a
        # meaningful one, since ACT being enabled explains why the hood's
        # airflow is limited. Enabled by default for that reason.
        # `or None` folds both None (unreported) and "" into unknown.
        value_fn=lambda state, _caps: state.act or None,
    ),
    ZephyrSensorDescription(
        key="recirculating",
        translation_key="recirculating",
        entity_category=EntityCategory.DIAGNOSTIC,
        # Read-only by design: writing it would begin charcoal-filter
        # accounting for a filter that may not be installed. None means the
        # device did not report the field - unknown, NOT "ducted": ducted
        # is a positive claim about how the hood is installed.
        value_fn=lambda state, _caps: (
            None
            if state.set_recirculating is None
            else ("recirculating" if state.set_recirculating else "ducted")
        ),
        exists_fn=lambda caps: caps.supports_recirculating,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for each hood, gated on capabilities."""
    async_add_entities(
        ZephyrSensor(coordinator, description)
        for coordinator in entry.runtime_data
        for description in SENSORS
        if description.exists_fn(coordinator.capabilities)
    )


class ZephyrSensor(ZephyrEntity, SensorEntity):
    """A value read from the hood's shadow."""

    entity_description: ZephyrSensorDescription

    def __init__(
        self, coordinator: ZephyrCoordinator, description: ZephyrSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        state = self.hood_state
        if state is None:
            return None
        return self.entity_description.value_fn(state, self.coordinator.capabilities)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self.hood_state
        if state is None or self.entity_description.attributes_fn is None:
            return None
        attrs = self.entity_description.attributes_fn(
            state, self.coordinator.capabilities
        )
        return {k: v for k, v in attrs.items() if v is not None}
