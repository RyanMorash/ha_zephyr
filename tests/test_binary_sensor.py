"""Binary sensors. All PROBLEM class - they signal faults, not states."""

from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.zephyr_connect.binary_sensor import (
    BINARY_SENSORS,
    ZephyrBinarySensor,
)


def _coordinator(**state_kwargs):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.max_charcoal_filter_hours = 200
    caps.urls = {}

    state = MagicMock()
    defaults = {
        "clean_grease_filters": 0,
        "clean_charcoal_filters": 0,
        "alarm_grease_filter": 0,
        "alarm_fan": 0,
        "fan_warning": 0,
        "alarm_fault_code": 0,
        "fault_codes": (),
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
    description = next(d for d in BINARY_SENSORS if d.key == key)
    return ZephyrBinarySensor(_coordinator(**state_kwargs), description)


def test_all_are_problem_class():
    for description in BINARY_SENSORS:
        assert description.device_class is BinarySensorDeviceClass.PROBLEM


def test_grease_filter_clean_is_off():
    assert _sensor("grease_filter_due").is_on is False


def test_grease_filter_due_is_on():
    assert _sensor("grease_filter_due", clean_grease_filters=1).is_on is True


def test_grease_filter_exposes_the_overdue_alarm():
    """cleangreasefilters means due; alarmgreasefilter means overdue. One
    entity, with the severity as an attribute."""
    sensor = _sensor("grease_filter_due", alarm_grease_filter=1)
    assert sensor.extra_state_attributes["overdue"] is True


@pytest.mark.parametrize(
    ("alarm", "warning"), [(1, 0), (0, 1), (1, 1)]
)
def test_fan_fault_triggers_on_either_signal(alarm, warning):
    """alarmfan and fanwarning may differ in severity; either means the fan
    needs attention."""
    assert _sensor("fan_fault", alarm_fan=alarm, fan_warning=warning).is_on is True


def test_fan_fault_clear():
    assert _sensor("fan_fault").is_on is False


def test_fault_reports_codes_as_an_attribute():
    sensor = _sensor("fault", alarm_fault_code=1, fault_codes=("E3", "E7"))
    assert sensor.is_on is True
    assert sensor.extra_state_attributes["fault_codes"] == ["E3", "E7"]


def test_fault_clear_has_empty_codes():
    sensor = _sensor("fault")
    assert sensor.is_on is False
    assert sensor.extra_state_attributes["fault_codes"] == []


def test_returns_none_before_the_first_update():
    coordinator = _coordinator()
    coordinator.data = None
    description = next(d for d in BINARY_SENSORS if d.key == "fan_fault")
    assert ZephyrBinarySensor(coordinator, description).is_on is None
