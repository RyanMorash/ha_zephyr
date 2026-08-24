"""The Zephyr Connect integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyzephyrconnect import ZephyrAuthError, ZephyrClient, ZephyrError

from .const import PLATFORMS
from .coordinator import ZephyrCoordinator

type ZephyrConfigEntry = ConfigEntry[list[ZephyrCoordinator]]


async def async_setup_entry(hass: HomeAssistant, entry: ZephyrConfigEntry) -> bool:
    """Set up Zephyr Connect from a config entry."""
    client = ZephyrClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        async_get_clientsession(hass),
    )

    try:
        capabilities = await client.async_setup()
    except ZephyrAuthError as err:
        await client.async_stop()
        raise ConfigEntryAuthFailed(str(err)) from err
    except ZephyrError as err:
        await client.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    coordinators: list[ZephyrCoordinator] = []
    try:
        for caps in capabilities:
            coordinator = ZephyrCoordinator(hass, entry, client, caps)
            await coordinator.async_initialise()
            coordinators.append(coordinator)
    except ZephyrError as err:
        await client.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = coordinators
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZephyrConfigEntry) -> bool:
    """Unload a config entry, releasing the MQTT connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for coordinator in entry.runtime_data:
            await coordinator.async_shutdown()
        # One client is shared across every hood on the account.
        if entry.runtime_data:
            await entry.runtime_data[0].client.async_stop()
    return unloaded
