"""The Zephyr Connect integration."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyzephyrconnect import ZephyrAuthError, ZephyrClient, ZephyrError

from .const import PLATFORMS
from .coordinator import ZephyrCoordinator


@dataclass(frozen=True)
class ZephyrData:
    """Runtime state stored on the config entry as `entry.runtime_data`.

    `client` is the single ZephyrClient shared by every hood on the account
    (one MQTT/HTTPS connection, reused across hoods) - keep a reference here
    so unload can stop it without depending on `coordinators` being
    non-empty. `coordinators` holds one ZephyrCoordinator per hood.

    Iterate this object directly to enumerate the per-hood coordinators,
    e.g. `for coordinator in entry.runtime_data: ...`.
    """

    client: ZephyrClient
    coordinators: list[ZephyrCoordinator] = field(default_factory=list)

    def __iter__(self) -> Iterator[ZephyrCoordinator]:
        return iter(self.coordinators)


type ZephyrConfigEntry = ConfigEntry[ZephyrData]


async def _release(
    coordinators: list[ZephyrCoordinator], client: ZephyrClient
) -> None:
    """Release partially-initialised setup state.

    Each initialised coordinator has already armed Home Assistant's periodic
    timer (see the no-op listener in ZephyrCoordinator.async_initialise), so
    shutting them down is not optional - a leaked coordinator keeps firing
    against a stopped client, and setup retries would accumulate more.
    """
    for coordinator in coordinators:
        await coordinator.async_shutdown()
    await client.async_stop()


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
        await _release([], client)
        raise ConfigEntryAuthFailed(str(err)) from err
    except ZephyrError as err:
        await _release([], client)
        raise ConfigEntryNotReady(str(err)) from err

    coordinators: list[ZephyrCoordinator] = []
    try:
        for caps in capabilities:
            coordinator = ZephyrCoordinator(hass, entry, client, caps)
            await coordinator.async_initialise()
            coordinators.append(coordinator)
    except ZephyrAuthError as err:
        # ZephyrAuthError subclasses ZephyrError, so it must be caught here
        # first - otherwise an auth failure raised inside async_initialise()
        # (e.g. from async_start()) falls through to the ZephyrError clause
        # below and gets downgraded to ConfigEntryNotReady, which retries
        # forever instead of prompting the user to reauthenticate.
        await _release(coordinators, client)
        raise ConfigEntryAuthFailed(str(err)) from err
    except ZephyrError as err:
        await _release(coordinators, client)
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = ZephyrData(client=client, coordinators=coordinators)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZephyrConfigEntry) -> bool:
    """Unload a config entry, releasing the MQTT connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for coordinator in entry.runtime_data:
            await coordinator.async_shutdown()
        # The client is stored directly on runtime_data (not reached via
        # coordinators[0]) so it is always stopped, even for an account with
        # zero hoods and therefore an empty coordinators list.
        await entry.runtime_data.client.async_stop()
    return unloaded
