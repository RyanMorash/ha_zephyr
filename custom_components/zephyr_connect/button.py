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
    """Set up filter reset buttons for each hood, gated on capabilities.

    A filter life of None means the model does not advertise one - absent
    means "not advertised", so no button, same as an advertised 0.

    Grease and charcoal are separate counters. Android 1.1.16 publishes
    `resetgreasefilter` = 1 and `resetcharcoalfilter` = 1 from dedicated
    My Hood buttons (the library's PROTOCOL.md section 5). Resetting one
    by writing the other field would zero the wrong counter.
    """
    entities: list[ButtonEntity] = []
    for coordinator in entry.runtime_data:
        if (coordinator.capabilities.max_grease_filter_hours or 0) > 0:
            entities.append(ZephyrResetGreaseFilterButton(coordinator))
        if (coordinator.capabilities.max_charcoal_filter_hours or 0) > 0:
            entities.append(ZephyrResetCharcoalFilterButton(coordinator))
    async_add_entities(entities)


class ZephyrResetGreaseFilterButton(ZephyrEntity, ButtonEntity):
    """Reset the grease filter usage counter after cleaning.

    DESTRUCTIVE, AND THE LIVE EFFECT IS UNWATCHED. Pressing this publishes
    `resetgreasefilter` = 1, the payload Android 1.1.16 sends from its own
    button. It is intended to zero `usegreasefiltertime`, which cannot be
    reconstructed, and that effect has not been watched on hardware -
    validating it requires actually cleaning the filter, since a test press
    would destroy the very counter it verifies.
    """

    _attr_translation_key = "reset_grease_filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "reset_grease_filter")

    async def async_press(self) -> None:
        await self._async_write(self.coordinator.hood.async_reset_grease_filter())


class ZephyrResetCharcoalFilterButton(ZephyrEntity, ButtonEntity):
    """Reset the charcoal filter usage counter after replacing it.

    DESTRUCTIVE, AND THE LIVE EFFECT IS UNWATCHED. Pressing this publishes
    `resetcharcoalfilter` = 1, the payload Android 1.1.16 sends from its
    own button. It is a separate shadow field, not a reuse of the grease
    reset. It is intended to zero `usecharcoalfiltertime`, which cannot be
    reconstructed, and that effect has not been watched on hardware -
    validating it requires actually replacing the filter. The field and
    the payload are known; the counter hitting zero is not.
    """

    _attr_translation_key = "reset_charcoal_filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "reset_charcoal_filter")

    async def async_press(self) -> None:
        await self._async_write(self.coordinator.hood.async_reset_charcoal_filter())
