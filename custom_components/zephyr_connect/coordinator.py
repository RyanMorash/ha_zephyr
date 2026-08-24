"""Coordinator bridging pyzephyrconnect's push updates into Home Assistant."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyzephyrconnect import (
    HoodCapabilities,
    HoodState,
    ZephyrAuthError,
    ZephyrClient,
    ZephyrError,
)

from .const import DEGRADED_POLL_INTERVAL_SECONDS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZephyrCoordinator(DataUpdateCoordinator[HoodState]):
    """One hood.

    Updates arrive by push: the library invokes our listener whenever the
    device reports. The polling interval here is a fallback, not the primary
    path - it refreshes credentials and, when MQTT is down, re-reads state
    over HTTPS so entities degrade instead of going unavailable.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ZephyrClient,
        capabilities: HoodCapabilities,
    ) -> None:
        self.client = client
        self.capabilities = capabilities
        self.thing_name = capabilities.thing_name
        self._unsubscribe: Any = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {capabilities.model}",
            update_interval=timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS),
        )

    @property
    def state(self) -> HoodState | None:
        """Latest known hood state, or None before the first update."""
        return self.data

    async def async_initialise(self) -> None:
        """Open the shadow connection and wire push updates."""
        await self.client.async_start(self.thing_name)
        self._unsubscribe = self.client.add_listener(
            self.thing_name, self._handle_push
        )
        # DataUpdateCoordinator only arms its periodic timer while it has at
        # least one registered listener (see async_add_listener /
        # _schedule_refresh in homeassistant.helpers.update_coordinator).
        # Entities normally provide that listener, but the credential
        # refresh and degraded-poll fallback below must keep running even
        # before any entity has subscribed, so register a permanent no-op
        # listener of our own.
        self.async_add_listener(lambda: None)
        # Seed from whatever the library already cached during setup, so
        # entities have data before the first device report arrives.
        if (cached := self.client.state(self.thing_name)) is not None:
            self.async_set_updated_data(cached)

    @callback
    def _handle_push(self, state: HoodState) -> None:
        """Device reported. Runs on the event loop; the library guarantees
        it never dispatches from paho's network thread."""
        self.async_set_updated_data(state)

    async def async_shutdown(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await super().async_shutdown()

    async def _async_update_data(self) -> HoodState:
        """Fallback tick: refresh credentials, and re-read if push is down."""
        try:
            await self.client.async_refresh_if_needed()
        except ZephyrAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ZephyrError as err:
            raise UpdateFailed(str(err)) from err

        if not self.client.connected:
            _LOGGER.debug("push transport down; reading state over HTTPS")
            try:
                return await self.client.async_poll(self.thing_name)
            except ZephyrAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except ZephyrError as err:
                raise UpdateFailed(str(err)) from err

        cached = self.client.state(self.thing_name)
        if cached is None:
            raise UpdateFailed("no state received yet")
        return cached

    async def async_set_state(self, fields: dict[str, Any]) -> None:
        """Write to the hood. ACTUATES HARDWARE.

        The library writes state.reported - the device ignores state.desired
        entirely. Never build shadow payloads here.

        The device echoes its real state ~1.2-1.6s later via push, so no
        optimistic local update is needed.
        """
        try:
            await self.client.async_set_state(self.thing_name, fields)
        except ZephyrAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ZephyrError as err:
            raise UpdateFailed(f"write failed: {err}") from err
