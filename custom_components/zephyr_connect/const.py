"""Constants for the Zephyr Connect integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "zephyr_connect"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.FAN,
    Platform.LIGHT,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]

# Config entry key for the persisted ZephyrTokens.as_dict() record. Lets a
# restart skip the rate-limited SRP login. The tokens inside are live
# credentials - diagnostics.REDACT_KEYS must cover this key and its contents.
CONF_TOKENS = "tokens"

# Slow safety-net re-read over HTTPS. Push covers normal operation; this
# catches anything missed while the socket was briefly unhealthy.
SAFETY_NET_INTERVAL_SECONDS = 300

# HTTPS fallback cadence while MQTT is down. discoverdevice still returns
# live state, so entities degrade to slower updates instead of going
# unavailable.
DEGRADED_POLL_INTERVAL_SECONDS = 60

# Identifies THIS consumer in the MQTT client ID, which the library builds as
# `<identity id><suffix>-<thing name>`. AWS IoT treats two live connections
# sharing a client ID as one session and evicts one for the other, so the
# suffix is what stops the vendor phone app, this integration and any other
# pyzephyrconnect consumer on the same account from flapping each other off
# the socket (the library's PROTOCOL.md section 5).
#
# It must be passed explicitly. pyzephyrconnect 0.2.0 made the suffix a
# per-consumer argument and changed its own default from "-ha" to the
# neutral "-py"; relying on that default would both silently change the
# client ID of every existing install and collide with a plain library
# script. "-ha" is the value the library documents for a Home Assistant
# integration, and keeping it preserves the identity 0.1.0 connected under.
MQTT_CLIENT_ID_SUFFIX = "-ha"

# Upper bound offered for the delay-off number, in seconds.
#
# The units are established: `setdelaytimer` is seconds, and the device
# accepts values off the vendor app's two presets (the library's
# PROTOCOL.md section 5). 3600 is the largest value proven accepted, not a
# known device limit - the real ceiling is still unprobed (PROTOCOL.md
# section 7), so this cap is where the evidence stops rather than where the
# hardware does.
DELAY_TIMER_MAX = 3600
