"""Entity base behaviour: identity, device registration, availability."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.zephyr_connect.const import DOMAIN
from custom_components.zephyr_connect.entity import ZephyrEntity
from pyzephyrconnect import ZephyrNotConnectedError, ZephyrWriteError

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


def test_available_when_online_status_is_unreported():
    """is_online is None when the latest state came from a source that does
    not carry it: a full shadow document replaces the cache wholesale, and
    isOnline is a discoverdevice field, not a shadow one. A hood that just
    pushed a shadow document is plainly reachable, so None must mean 'no
    news', not 'offline' - otherwise entities would flap unavailable after
    every full shadow read."""
    assert ZephyrEntity(_coordinator(is_online=None), "fan").available is True


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


@pytest.mark.parametrize(
    "error",
    [
        ZephyrNotConnectedError("hood is not connected"),
        ZephyrWriteError("fan must be between 0 and 6 on this hood, got 9"),
    ],
)
async def test_write_helper_maps_library_errors_to_ha(error):
    """Every ZephyrError from a control call - including
    ZephyrNotConnectedError, which used to be a bare RuntimeError in the
    old library - must surface as HomeAssistantError so service calls fail
    visibly in the UI instead of leaking a library type."""
    entity = ZephyrEntity(_coordinator(), "fan")
    request = AsyncMock(side_effect=error)

    with pytest.raises(HomeAssistantError):
        await entity._async_write(request())


async def test_write_helper_passes_success_through():
    entity = ZephyrEntity(_coordinator(), "fan")
    request = AsyncMock()
    await entity._async_write(request())
    request.assert_awaited_once()
