"""light platform for Zephyr Connect.

Placeholder so async_forward_entry_setups can import this platform. The
real implementation arrives in a later task and replaces this file.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """No entities yet."""
