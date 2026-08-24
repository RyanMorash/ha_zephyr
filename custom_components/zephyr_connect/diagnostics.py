"""Diagnostics for Zephyr Connect.

For a reverse-engineered protocol this is the highest-leverage file here:
when someone with a different Zephyr model installs this integration, their
diagnostics download is how unknown fields get characterised - without
asking them to run Python.

That value only holds if the output is safe to paste into a public issue,
so every identifier is redacted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import ZephyrConfigEntry

# Identifies a specific home and its owner. `location` carries precise
# coordinates from the vendor's device list.
REDACT_KEYS = {"thingName", "SN", "MAC", "location", CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ZephyrConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    hoods: list[dict[str, Any]] = []
    for coordinator in entry.runtime_data:
        state = coordinator.data
        hoods.append(
            {
                # Capabilities explain why entities exist or do not, and
                # carry the model's limits.
                "capabilities": async_redact_data(
                    dict(coordinator.capabilities.raw), REDACT_KEYS
                ),
                # The full shadow, including fields this version does not
                # model - that is precisely what makes this useful.
                "state": async_redact_data(
                    dict(state.raw) if state is not None else {}, REDACT_KEYS
                ),
                "connected": coordinator.client.connected,
                "last_update_success": coordinator.last_update_success,
            }
        )

    return {
        "entry": async_redact_data(dict(entry.data), REDACT_KEYS),
        "hoods": hoods,
    }
