"""Entity base behaviour: identity, device registration, availability."""

from unittest.mock import MagicMock

from custom_components.zephyr_connect.const import DOMAIN
from custom_components.zephyr_connect.entity import ZephyrEntity

THING = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"


def _coordinator(is_online=True, last_update_success=True):
    caps = MagicMock()
    caps.thing_name = THING
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.urls = {"FAQURL": "https://zephyronline.com/faq"}

    state = MagicMock()
    state.is_online = is_online

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = THING
    coordinator.data = state
    coordinator.state = state
    coordinator.last_update_success = last_update_success
    return coordinator


def test_unique_id_combines_thing_and_key():
    entity = ZephyrEntity(_coordinator(), "fan")
    assert entity.unique_id == f"{THING}_fan"


def test_device_info_identifies_the_hood():
    entity = ZephyrEntity(_coordinator(), "fan")
    info = entity.device_info
    assert (DOMAIN, THING) in info["identifiers"]
    assert info["manufacturer"] == "ZEPHYR"
    assert info["model"] == "AK7400AS"
    assert info["serial_number"] == "1234567XYZ"


def test_entity_uses_ha_device_naming():
    """has_entity_name lets HA compose 'Kitchen Hood Fan' rather than each
    entity repeating the device name."""
    assert ZephyrEntity(_coordinator(), "fan")._attr_has_entity_name is True


def test_unavailable_when_the_hood_is_offline():
    """isOnline is the device's own reachability flag - distinct from
    whether OUR transport is healthy."""
    assert ZephyrEntity(_coordinator(is_online=False), "fan").available is False


def test_unavailable_when_updates_are_failing():
    assert ZephyrEntity(
        _coordinator(last_update_success=False), "fan"
    ).available is False


def test_available_when_online_and_updating():
    assert ZephyrEntity(_coordinator(), "fan").available is True


def test_unavailable_before_the_first_update():
    coordinator = _coordinator()
    coordinator.data = None
    coordinator.state = None
    assert ZephyrEntity(coordinator, "fan").available is False
