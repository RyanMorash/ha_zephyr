"""The Zephyr Connect integration."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyzephyrconnect import (
    ZephyrAuthError,
    ZephyrClient,
    ZephyrDataError,
    ZephyrError,
    ZephyrTokens,
)

from .const import CONF_TOKENS, PLATFORMS
from .coordinator import ZephyrCoordinator


@dataclass(frozen=True)
class ZephyrData:
    """Runtime state stored on the config entry as `entry.runtime_data`.

    `client` is the single ZephyrClient shared by every hood on the account
    (one auth/credential lifecycle, reused across hoods) - keep a reference
    here so unload can stop it without depending on `coordinators` being
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

    Note: Home Assistant's DataUpdateCoordinator base class registers
    config_entry.async_on_unload(self.async_shutdown) during __init__, so HA
    will shut down coordinators automatically on any failed setup, meaning
    they would be cleaned up regardless. We still do it explicitly here because
    relying on a base-class implementation detail is fragile. More importantly,
    calling async_shutdown directly here guarantees coordinators are shut down
    BEFORE the shared client is stopped, which HA's async_on_unload ordering
    does not promise. client.async_stop() is idempotent, so the extra stop from
    the async_on_unload registration in async_setup_entry is harmless."""
    for coordinator in coordinators:
        await coordinator.async_shutdown()
    await client.async_stop()


async def async_setup_entry(hass: HomeAssistant, entry: ZephyrConfigEntry) -> bool:
    """Set up Zephyr Connect from a config entry."""
    saved = entry.data.get(CONF_TOKENS)
    try:
        tokens = ZephyrTokens.from_dict(saved) if saved else None
    except ZephyrDataError:
        # A corrupted record that survived here would fail much later and
        # far away (as a SECRET_HASH Cognito rejects, or an MQTT client ID
        # AWS IoT silently drops messages for). Discard it - a fresh SRP
        # login rebuilds it through the token updater below.
        tokens = None

    @callback
    def _store_tokens(new_tokens: ZephyrTokens) -> None:
        """Persist refreshed tokens so the next restart skips the SRP login.

        The library invokes the updater on the event loop (login and every
        refresh both run through CredentialsAuth._acquire, an async method),
        so updating the entry directly here is safe.
        """
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_TOKENS: new_tokens.as_dict()}
        )

    client = ZephyrClient.from_credentials(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        async_get_clientsession(hass),
        tokens=tokens,
        token_updater=_store_tokens,
    )
    # Registered BEFORE any hood starts, as the library asks: HA runs
    # on_unload callbacks on unload and on failed setup, and async_stop is
    # the only call that reliably retires the credential supervisor mid-tick
    # and stops every hood in one go. The explicit stops in _release and
    # async_unload_entry keep deterministic ordering (coordinators first);
    # this is belt and braces, and async_stop is idempotent.
    entry.async_on_unload(client.async_stop)

    try:
        hoods = await client.async_setup()
    except ZephyrAuthError as err:
        await _release([], client)
        raise ConfigEntryAuthFailed(str(err)) from err
    except ZephyrError as err:
        await _release([], client)
        raise ConfigEntryNotReady(str(err)) from err

    coordinators: list[ZephyrCoordinator] = []
    try:
        for hood in hoods:
            coordinator = ZephyrCoordinator(hass, entry, hood)
            await coordinator.async_initialise()
            coordinators.append(coordinator)
    except ZephyrAuthError as err:
        # ZephyrAuthError subclasses ZephyrError, so it must be caught here
        # first - otherwise an auth failure raised inside async_initialise()
        # (e.g. from hood.async_start()) falls through to the ZephyrError
        # clause below and gets downgraded to ConfigEntryNotReady, which
        # retries forever instead of prompting the user to reauthenticate.
        await _release(coordinators, client)
        raise ConfigEntryAuthFailed(str(err)) from err
    except ZephyrError as err:
        await _release(coordinators, client)
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = ZephyrData(client=client, coordinators=coordinators)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZephyrConfigEntry) -> bool:
    """Unload a config entry, releasing the MQTT connections."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for coordinator in entry.runtime_data:
            await coordinator.async_shutdown()
        # The client is stored directly on runtime_data (not reached via
        # coordinators[0]) so it is always stopped, even for an account with
        # zero hoods and therefore an empty coordinators list. The
        # async_on_unload registration from setup stops it again afterwards;
        # async_stop is idempotent, and stopping here first guarantees the
        # client dies after its coordinators rather than racing them.
        await entry.runtime_data.client.async_stop()
    return unloaded
