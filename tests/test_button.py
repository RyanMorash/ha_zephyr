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
    caps.max_grease_filter_hours = 60

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


async def test_button_is_gated_on_having_a_grease_filter():
    """A hood with no grease filter must not get a destructive reset
    button for one. The matching sensor is already gated this way."""
    from custom_components.zephyr_connect.button import async_setup_entry

    without = _coordinator()
    without.capabilities.max_grease_filter_hours = 0
    with_filter = _coordinator()
    with_filter.capabilities.max_grease_filter_hours = 60

    for coordinator, expected in ((without, 0), (with_filter, 1)):
        entry = MagicMock()
        entry.runtime_data = [coordinator]
        added = []
        await async_setup_entry(MagicMock(), entry, lambda e: added.extend(e))
        assert len(added) == expected
