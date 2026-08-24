"""Base entity for Zephyr Connect."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyzephyrconnect import HoodState

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
    def hood(self) -> HoodState | None:
        """Latest hood state, or None before the first update."""
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """Available only when our updates work AND the hood reports online.

        These are different failures: last_update_success covers our cloud
        path, isOnline is the device telling the cloud it is reachable.
        """
        if not self.coordinator.last_update_success:
            return False
        state = self.hood
        return state is not None and bool(state.is_online)
