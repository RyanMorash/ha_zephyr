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

# Upper bound offered for the delay-off number, in the device's raw units.
# The field's units are unvalidated (the library's VALIDATION.md, question
# 2): the vendor app writes 300 for its "5 minutes" preset, which suggests
# seconds, but the device has never been watched counting down. 3600 covers
# an hour if that holds. A UI cap, not a device limit - the real ceiling is
# unknown.
DELAY_TIMER_MAX = 3600
