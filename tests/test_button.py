"""Reset grease filter button. Destructive and unvalidated."""

from unittest.mock import AsyncMock, MagicMock

from custom_components.zephyr_connect.button import ZephyrResetGreaseFilterButton


def _coordinator():
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.urls = {}

    state = MagicMock()
    state.is_online = True

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.async_set_state = AsyncMock()
    return coordinator


async def test_press_writes_the_reset_flag():
    coordinator = _coordinator()
    await ZephyrResetGreaseFilterButton(coordinator).async_press()
    coordinator.async_set_state.assert_awaited_once_with(
        {"resetgreasefilter": 1}
    )


def test_is_a_config_entity():
    """Filter maintenance, not a daily control."""
    from homeassistant.helpers.entity import EntityCategory

    assert (
        ZephyrResetGreaseFilterButton(_coordinator()).entity_category
        is EntityCategory.CONFIG
    )


def test_unique_id_is_stable():
    button = ZephyrResetGreaseFilterButton(_coordinator())
    assert button.unique_id.endswith("_reset_grease_filter")
