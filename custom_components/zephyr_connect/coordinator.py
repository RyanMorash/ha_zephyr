"""Coordinator bridging pyzephyrconnect's push updates into Home Assistant."""

from __future__ import annotations

import asyncio
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
        # A push scheduled for the next loop turn, and whether shutdown has
        # started. See _handle_push for why the update is not applied inline.
        self._push_handle: asyncio.Handle | None = None
        self._accept_push = True
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
        """Library reported a new visible state. Runs on the event loop.

        The library never dispatches from paho's network thread. It does
        call listeners inline, and pyzephyrconnect 0.2.1 does so from a
        write that still holds the hood lock: a successful non-destructive
        publish is applied to the visible cache before this returns.
        Applying that state here would update entities on the same task,
        and a state-changed automation could call back into the hood and
        deadlock on the lock. call_soon runs only after this callback
        returns and the lock is released.

        The callback is handed the whole visible cache, not a delta, so a
        newer report that arrives before the scheduled one runs replaces
        it rather than queueing behind it.
        """
        if not self._accept_push:
            return
        if self._push_handle is not None:
            self._push_handle.cancel()
        self._push_handle = asyncio.get_running_loop().call_soon(
            self._apply_push, state
        )

    @callback
    def _apply_push(self, state: HoodState) -> None:
        """Apply a push scheduled by _handle_push."""
        self._push_handle = None
        if not self._accept_push:
            return
        self.async_set_updated_data(state)

    async def async_shutdown(self) -> None:
        # Before the first await, so a push already queued cannot land on
        # a coordinator Home Assistant is tearing down.
        self._accept_push = False
        if self._push_handle is not None:
            self._push_handle.cancel()
            self._push_handle = None
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
            await self.hood.async_poll()
        except (ZephyrAuthError, ZephyrPolicyError) as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ZephyrError as err:
            raise UpdateFailed(str(err)) from err
        # async_poll's return value is authoritative reported state with
        # pending commands stripped off. pyzephyrconnect 0.2.1 keeps those
        # commands on hood.state, and has already asked listeners to
        # publish that visible cache. Returning the raw value would write
        # it over the top and snap entities back to the pre-command
        # reading until the echo. A successful poll leaves the cache set.
        if (visible := self.hood.state) is None:
            raise UpdateFailed("no state received yet")
        return visible

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
