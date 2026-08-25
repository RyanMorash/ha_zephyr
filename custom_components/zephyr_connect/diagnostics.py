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
from .const import CONF_TOKENS

# Identifies a specific home and its owner. `location` carries precise
# coordinates from the vendor's device list.
#
# CONF_TOKENS names the persisted ZephyrTokens record in entry.data;
# async_redact_data matches key names at every depth, so naming the
# container redacts the whole sub-dict. id_token and refresh_token are
# named individually too, in case they ever appear outside it - a Cognito
# refresh token is valid for ~30 days and on its own is sufficient to take
# over the account. identity_id is not a credential, but it is a stable
# account identifier in the same category as a serial number.
REDACT_KEYS = {
    "thingName",
    "SN",
    "MAC",
    "location",
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_TOKENS,
    "id_token",
    "refresh_token",
    "identity_id",
}


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
                # Per-hood: on a multi-hood account one connection dropping
                # must not report the others as down.
                "connected": coordinator.hood.connected,
                "last_update_success": coordinator.last_update_success,
            }
        )

    return {
        "entry": async_redact_data(dict(entry.data), REDACT_KEYS),
        "hoods": hoods,
    }
