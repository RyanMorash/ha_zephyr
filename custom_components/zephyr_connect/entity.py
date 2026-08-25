"""Base entity for Zephyr Connect."""

from __future__ import annotations

from collections.abc import Coroutine
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyzephyrconnect import HoodState, ZephyrError

from .const import DOMAIN
from .coordinator import ZephyrCoordinator


class ZephyrEntity(CoordinatorEntity[ZephyrCoordinator]):
    """Common identity, device registration and availability."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ZephyrCoordinator, key: str) -> None:
        super().__init__(coordinator)
        caps = coordinator.capabilities
        self._key = key
        self._attr_unique_id = f"{coordinator.thing_name}_{key}"

        connections = set()
        if caps.mac:
            connections.add((CONNECTION_NETWORK_MAC, caps.mac))

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.thing_name)},
            manufacturer=caps.manufacturer or "ZEPHYR",
            model=caps.model or None,
            serial_number=caps.serial or None,
            connections=connections,
            configuration_url=caps.urls.get("FAQURL"),
        )

    @property
    def hood_state(self) -> HoodState | None:
        """Latest hood state, or None before the first update."""
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """Available only when our updates work AND the hood is not offline.

        These are different failures: last_update_success covers our cloud
        path, is_online is the device telling the cloud it is reachable.

        is_online is None when the latest state came from a payload that
        did not carry it - a full shadow document replaces the cache
        wholesale, and not every reported block includes isOnline - so None
        means "no news", not "offline". A hood that just pushed a shadow
        document is plainly reachable; only an explicit False may mark it
        unavailable. (A stale False also self-heals: the reference shadow
        documents carry isOnline, so the next push overwrites it.)
        """
        if not self.coordinator.last_update_success:
            return False
        state = self.hood_state
        return state is not None and state.is_online is not False

    async def _async_write(self, request: Coroutine[Any, Any, None]) -> None:
        """Await a hood control call, mapping library errors to HA's.

        ACTUATES HARDWARE. The library publishes to state.reported - the
        device ignores state.desired entirely - and the device echoes its
        real state ~1.2-1.6s later via push, so no optimistic local update
        is needed.

        ZephyrNotConnectedError (a write attempted while this hood's shadow
        connection is down) subclasses ZephyrError and lands here too: the
        write was refused before anything was published, so nothing is
        queued to fire later - the user just retries once the hood is back.
        """
        try:
            await request
        except ZephyrError as err:
            raise HomeAssistantError(f"Zephyr write failed: {err}") from err
