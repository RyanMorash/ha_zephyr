"""Coordinator bridging pyzephyrconnect's push updates into Home Assistant."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyzephyrconnect import (
    Hood,
    HoodState,
    ZephyrAuthError,
    ZephyrError,
    ZephyrPolicyError,
)

from .const import (
    DEGRADED_POLL_INTERVAL_SECONDS,
    DOMAIN,
    SAFETY_NET_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# Every this-many-th connected tick, _async_update_data() performs a real
# poll instead of returning cached state, catching anything missed while
# push was briefly unhealthy. See _async_update_data() and __init__().
SAFETY_NET_TICKS = max(1, SAFETY_NET_INTERVAL_SECONDS // DEGRADED_POLL_INTERVAL_SECONDS)


class ZephyrCoordinator(DataUpdateCoordinator[HoodState]):
    """One hood.

    Updates arrive by push: the library invokes our listener whenever the
    device reports. The polling interval here is a fallback, not the primary
    path - when MQTT is down it re-reads state over HTTPS so entities degrade
    instead of going unavailable, and it is how a terminal credential failure
    inside the library's supervisor surfaces as a reauth prompt (the
    supervisor re-raises it from the next hood.async_poll()).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        hood: Hood,
    ) -> None:
        self.hood = hood
        self.capabilities = hood.capabilities
        self.thing_name = hood.thing_name
        self._unsubscribe: Callable[[], None] | None = None
        # Counts consecutive connected ticks since the last real poll (either
        # a safety-net poll below, or a degraded poll while disconnected).
        # Reaching SAFETY_NET_TICKS triggers a safety-net poll and resets it.
        self._connected_ticks = 0
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {hood.capabilities.model}",
            update_interval=timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS),
        )

    @property
    def state(self) -> HoodState | None:
        """Latest known hood state, or None before the first update."""
        return self.data

    async def async_initialise(self) -> None:
        """Open the shadow connection and wire push updates."""
        await self.hood.async_start()
        self._unsubscribe = self.hood.add_listener(self._handle_push)
        # DataUpdateCoordinator only arms its periodic timer while it has at
        # least one registered listener (see async_add_listener /
        # _schedule_refresh in homeassistant.helpers.update_coordinator).
        # HA's convention is that this is a *feature*: stop polling when
        # nothing is listening, e.g. because the user disabled every entity
        # for this hood. We deliberately override that convention here.
        # The library supervises its own credential lifecycle now, so the
        # tick no longer keeps credentials alive - but it is still the only
        # path a terminal supervisor failure (rejected credentials, missing
        # IoT policy) reaches us: the supervisor stops, disconnects the
        # hoods, and re-raises from the next hood.async_poll(). A consumer
        # that never polls never learns, so with every entity disabled the
        # reauth prompt would simply never appear. Polling must therefore
        # continue regardless of listener count, and we register a permanent
        # no-op listener of our own to force that.
        self.async_add_listener(lambda: None)
        # Seed from whatever the library already cached during setup, so
        # entities have data before the first device report arrives.
        if (cached := self.hood.state) is not None:
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

    async def _async_poll(self) -> HoodState:
        """Read state over HTTPS, mapping library errors to HA's.

        Both TERMINAL supervisor errors escalate to reauth, not just the
        credential one. A terminal error stops the library's supervisor for
        good and every later poll re-raises it, so mapping ZephyrPolicyError
        to UpdateFailed would leave the hood unavailable forever - nothing
        short of an entry reload rebuilds the supervisor. The reauth flow's
        success path IS that reload, and a fresh setup re-runs the identity
        exchange and re-attaches the IoT policy, which is the actual
        remediation for a policy failure (attachments are keyed on the
        identity).

        This cannot fire for a Wi-Fi blip: the library keeps transient
        failures - DNS, timeouts, throttling - as ZephyrTransportError,
        which lands in the UpdateFailed clause below.
        """
        try:
            return await self.hood.async_poll()
        except (ZephyrAuthError, ZephyrPolicyError) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ZephyrError as err:
            raise UpdateFailed(str(err)) from err

    async def _async_update_data(self) -> HoodState:
        """Fallback tick: re-read over HTTPS if push is down or stale.

        Credential refresh is deliberately NOT done here: the library's own
        supervisor renews credentials and rebuilds sockets before expiry.
        """
        if not self.hood.connected:
            _LOGGER.debug("push transport down; reading state over HTTPS")
            self._connected_ticks = 0
            return await self._async_poll()

        self._connected_ticks += 1
        if self._connected_ticks >= SAFETY_NET_TICKS:
            # Safety net: push normally covers everything, but re-read for
            # real every SAFETY_NET_TICKS-th connected tick in case a push
            # was missed while the transport was briefly unhealthy.
            _LOGGER.debug("safety-net re-read: polling despite active push")
            self._connected_ticks = 0
            return await self._async_poll()

        cached = self.hood.state
        if cached is None:
            raise UpdateFailed("no state received yet")
        return cached
