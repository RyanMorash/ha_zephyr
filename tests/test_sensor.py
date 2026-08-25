"""Sensors. The filter-life calculation is the load-bearing one."""

from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfTime

from custom_components.zephyr_connect.sensor import (
    SENSORS,
    ZephyrSensor,
    _filter_remaining,
)


def _coordinator(**state_kwargs):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.max_grease_filter_hours = 60
    caps.max_charcoal_filter_hours = 200
    caps.urls = {
        "GreaseFilterWebstoreURL": "https://store.zephyronline.com/en/baffle"
    }

    state = MagicMock()
    defaults = {
        "use_grease_filter_time": 643,
        "use_charcoal_filter_time": 0,
        "use_fan_time": 1979,
        "use_light_time": 2833,
        "delay_timer": 0,
        "act": "Disabled",
        "set_recirculating": 0,
        "is_online": True,
    }
    for key, value in {**defaults, **state_kwargs}.items():
        setattr(state, key, value)

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    return coordinator


def _sensor(key, **state_kwargs):
    description = next(d for d in SENSORS if d.key == key)
    return ZephyrSensor(_coordinator(**state_kwargs), description)


def test_grease_filter_matches_the_vendor_app():
    """643 minutes against a 60-hour life. The vendor app displays 82%;
    this formula was verified against that exact reading."""
    assert _sensor("grease_filter").native_value == pytest.approx(82.1, abs=0.1)


def test_grease_filter_is_a_percentage():
    sensor = _sensor("grease_filter")
    assert sensor.native_unit_of_measurement == PERCENTAGE


def test_fresh_grease_filter_reads_full():
    assert _sensor("grease_filter", use_grease_filter_time=0).native_value == 100


def test_exhausted_grease_filter_clamps_at_zero():
    """Past its life the raw formula goes negative, which HA would render
    as a nonsensical value."""
    assert _sensor(
        "grease_filter", use_grease_filter_time=999_999
    ).native_value == 0


def test_filter_remaining_handles_an_unadvertised_life():
    """max_*_filter_hours is None when the model omits the key. The
    exists_fn gate keeps such hoods from getting the sensor at all, but the
    formula must not TypeError if it is ever reached."""
    assert _filter_remaining(643, None) is None


@pytest.mark.parametrize("hours", [None, 0])
def test_filter_sensors_are_gated_on_an_advertised_life(hours):
    """None means the model does not advertise a filter life - no sensor,
    same as an advertised 0."""
    caps = MagicMock()
    caps.max_grease_filter_hours = hours
    caps.max_charcoal_filter_hours = hours
    for key in ("grease_filter", "charcoal_filter"):
        description = next(d for d in SENSORS if d.key == key)
        assert description.exists_fn(caps) is False


def test_grease_filter_exposes_the_raw_counter():
    """The unit inference lives in the formula; surfacing the raw minutes
    makes a wrong assumption visible instead of silent."""
    attrs = _sensor("grease_filter").extra_state_attributes
    assert attrs["used_minutes"] == 643
    assert attrs["life_hours"] == 60


def test_grease_filter_links_to_the_replacement_part():
    attrs = _sensor("grease_filter").extra_state_attributes
    assert attrs["store_url"].startswith("https://")


def test_charcoal_filter_reads_full_on_a_ducted_hood():
    """Ducted installs never consume charcoal, so 100% is correct rather
    than misleading."""
    assert _sensor("charcoal_filter").native_value == 100


def test_runtime_sensors_are_hours():
    """Inferred, not measured - see the plan's protocol section. If this
    proves wrong it is a one-line change."""
    sensor = _sensor("fan_runtime")
    assert sensor.native_value == 1979
    assert sensor.native_unit_of_measurement == UnitOfTime.HOURS
    assert sensor.state_class is SensorStateClass.TOTAL_INCREASING


def test_delay_remaining_is_the_raw_countdown():
    """Deliberately unitless, like the delay-off number: whether the timer
    fields hold seconds or minutes is an open hardware-validation question
    (the library's VALIDATION.md, question 2)."""
    sensor = _sensor("delay_remaining", delay_timer=240)
    assert sensor.native_value == 240
    assert sensor.native_unit_of_measurement is None
    assert sensor.device_class is None


def test_unreported_delay_remaining_is_unknown():
    assert _sensor("delay_remaining", delay_timer=None).native_value is None


def test_act_sensor_reports_airflow_control_technology():
    """ACT is Zephyr's Airflow Control Technology - an airflow cap for
    make-up-air code compliance, set physically on the hood. Read-only."""
    assert _sensor("act").native_value == "Disabled"


def test_unreported_act_is_unknown():
    assert _sensor("act", act=None).native_value is None


@pytest.mark.parametrize(
    ("value", "expected"), [(0, "ducted"), (1, "recirculating")]
)
def test_recirculating_is_read_only_text(value, expected):
    """Read-only by design: writing it would start charcoal accounting for
    a filter that may not be physically installed."""
    assert _sensor("recirculating", set_recirculating=value).native_value == expected


def test_unreported_recirculating_is_unknown_not_ducted():
    """None means the device did not report the field. 'ducted' is a
    positive claim about how the hood is installed; unknown must not
    silently become that claim."""
    assert _sensor("recirculating", set_recirculating=None).native_value is None


@pytest.mark.parametrize(
    ("supports_recirculating", "expected"), [(False, False), (True, True)]
)
def test_recirculating_is_gated_on_the_capability(supports_recirculating, expected):
    """`supports_recirculating` exists precisely to gate this sensor."""
    description = next(d for d in SENSORS if d.key == "recirculating")
    caps = MagicMock()
    caps.supports_recirculating = supports_recirculating
    assert description.exists_fn(caps) is expected


def test_sensors_return_none_before_the_first_update():
    coordinator = _coordinator()
    coordinator.data = None
    description = next(d for d in SENSORS if d.key == "grease_filter")
    assert ZephyrSensor(coordinator, description).native_value is None
