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

# Slow safety-net re-read over MQTT. Push covers normal operation; this
# catches anything missed while the socket was briefly unhealthy.
SAFETY_NET_INTERVAL_SECONDS = 300

# HTTPS fallback cadence while MQTT is down. discoverdevice still returns
# live state, so entities degrade to slower updates instead of going
# unavailable.
DEGRADED_POLL_INTERVAL_SECONDS = 60

# The device reports its countdown once a minute, so anything faster is
# wasted work.
DELAY_TIMER_STEP_SECONDS = 60

# Upper bound offered for the delay-off number, in minutes. The device
# accepts arbitrary values and its real ceiling is unknown; this is a sane
# UI cap, not a device limit.
DELAY_TIMER_MAX_MINUTES = 60
