"""Button platform for Zephyr Connect."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the filter reset button for each hood."""
    async_add_entities(
        ZephyrResetGreaseFilterButton(coordinator)
        for coordinator in entry.runtime_data
    )


class ZephyrResetGreaseFilterButton(ZephyrEntity, ButtonEntity):
    """Reset the grease filter usage counter after cleaning.

    DESTRUCTIVE AND UNVALIDATED. Pressing this zeroes
    `usegreasefiltertime`, which cannot be reconstructed. The write has
    never been tested against hardware - validating it requires actually
    cleaning the filter, since a test press would destroy the very counter
    it verifies. Ships on that understanding.
    """

    _attr_translation_key = "reset_grease_filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "reset_grease_filter")

    async def async_press(self) -> None:
        await self.coordinator.async_set_state({"resetgreasefilter": 1})
