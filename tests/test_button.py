"""Filter reset buttons. Destructive, and the live effect is unwatched."""

from unittest.mock import AsyncMock, MagicMock

from custom_components.zephyr_connect.button import (
    ZephyrResetCharcoalFilterButton,
    ZephyrResetGreaseFilterButton,
)


def _coordinator():
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.urls = {}
    caps.max_grease_filter_hours = 60
    # Charcoal is a separate gate. Leave it off here so a grease-only
    # assertion is not tripped by MagicMock's truthy default.
    caps.max_charcoal_filter_hours = 0

    state = MagicMock()
    state.is_online = True

    hood = MagicMock()
    hood.async_reset_grease_filter = AsyncMock()
    hood.async_reset_charcoal_filter = AsyncMock()

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.hood = hood
    return coordinator


async def test_press_resets_the_grease_counter():
    coordinator = _coordinator()
    await ZephyrResetGreaseFilterButton(coordinator).async_press()
    coordinator.hood.async_reset_grease_filter.assert_awaited_once_with()
    coordinator.hood.async_reset_charcoal_filter.assert_not_awaited()


async def test_press_resets_the_charcoal_counter():
    """Charcoal publishes its own field. Reusing the grease reset would
    zero the wrong counter."""
    coordinator = _coordinator()
    await ZephyrResetCharcoalFilterButton(coordinator).async_press()
    coordinator.hood.async_reset_charcoal_filter.assert_awaited_once_with()
    coordinator.hood.async_reset_grease_filter.assert_not_awaited()


def test_both_are_config_entities():
    """Filter maintenance, not a daily control."""
    from homeassistant.helpers.entity import EntityCategory

    for button_cls in (
        ZephyrResetGreaseFilterButton,
        ZephyrResetCharcoalFilterButton,
    ):
        assert button_cls(_coordinator()).entity_category is EntityCategory.CONFIG


def test_unique_ids_are_stable_and_distinct():
    coordinator = _coordinator()
    grease = ZephyrResetGreaseFilterButton(coordinator).unique_id
    charcoal = ZephyrResetCharcoalFilterButton(coordinator).unique_id
    assert grease.endswith("_reset_grease_filter")
    assert charcoal.endswith("_reset_charcoal_filter")
    assert grease != charcoal


async def test_buttons_are_gated_on_the_filter_each_one_resets():
    """A hood with no grease filter must not get a destructive reset
    button for one, and the same for charcoal. None means the model does
    not advertise a filter life - same as 0. The matching sensors are
    already gated this way. The two gates are independent: advertising
    one filter does not invent a reset for the other."""
    from custom_components.zephyr_connect.button import async_setup_entry

    cases = (
        (None, None, set()),
        (0, 0, set()),
        (60, 0, {"_reset_grease_filter"}),
        (0, 200, {"_reset_charcoal_filter"}),
        (60, 200, {"_reset_grease_filter", "_reset_charcoal_filter"}),
    )
    for grease_hours, charcoal_hours, expected in cases:
        coordinator = _coordinator()
        coordinator.capabilities.max_grease_filter_hours = grease_hours
        coordinator.capabilities.max_charcoal_filter_hours = charcoal_hours
        entry = MagicMock()
        entry.runtime_data = [coordinator]
        added = []
        await async_setup_entry(
            MagicMock(), entry, lambda e, added=added: added.extend(e)
        )
        suffixes = {
            entity.unique_id.removeprefix(coordinator.thing_name) for entity in added
        }
        assert suffixes == expected
