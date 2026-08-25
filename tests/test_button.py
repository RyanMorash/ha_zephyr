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

    hood = MagicMock()
    hood.async_reset_grease_filter = AsyncMock()

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.hood = hood
    return coordinator


async def test_press_resets_the_counter():
    coordinator = _coordinator()
    await ZephyrResetGreaseFilterButton(coordinator).async_press()
    coordinator.hood.async_reset_grease_filter.assert_awaited_once_with()


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
    button for one. None means the model does not advertise a filter life -
    same as 0. The matching sensor is already gated this way."""
    from custom_components.zephyr_connect.button import async_setup_entry

    for hours, expected in ((None, 0), (0, 0), (60, 1)):
        coordinator = _coordinator()
        coordinator.capabilities.max_grease_filter_hours = hours
        entry = MagicMock()
        entry.runtime_data = [coordinator]
        added = []
        await async_setup_entry(
            MagicMock(), entry, lambda e, added=added: added.extend(e)
        )
        assert len(added) == expected
