<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/zephyr-logo-white.svg">
  <img src="assets/zephyr-logo-black.svg" alt="Zephyr" width="230">
</picture>

# Zephyr Connect for Home Assistant

Control and monitor Zephyr range hoods from Home Assistant.

These hoods expose no local API — everything is a cloud round trip through
AWS IoT device shadows. The protocol was reverse-engineered; see
[pyzephyrconnect](https://github.com/RyanMorash/pyzephyrconnect).

## Install

[![Open your Home Assistant instance and open a repository in the Marketplace.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=RyanMorash&repository=ha_zephyr&category=integration)

The button opens this repository in the Marketplace on Home Assistant 2026.11
and later, and in HACS on earlier versions. Install **Zephyr Connect**,
restart Home Assistant, then add the integration and sign in with the account
you use in the Zephyr Connect app.

You may not have to go looking for it. Home Assistant recognises a Zephyr hood
by the DHCP lease it takes, so after the restart a card usually appears under
**Settings → Devices & services** on its own — within the hour, or sooner if
the hood renews its lease before that. The card is a shortcut to the same
sign-in, not a local connection: the hood exposes no local API, so its address
on your network is noted and never used.

The integration installs its
[pyzephyrconnect](https://pypi.org/project/pyzephyrconnect/) library from
PyPI. Signing in runs a full login once; afterwards the integration stores
the session tokens in the config entry so restarts reconnect without
re-authenticating.

## Entities

| Entity | Notes |
|---|---|
| Fan | Speeds gated on the model's `maxFanSpeed` |
| Light | Brightness gated on `maxLightLevel` |
| Power switch | Off stops everything; on restores the previous levels |
| Clean air switch | **Starts the fan at speed 1** when enabled |
| Delay off | Seconds. **Setting a value starts the fan at speed 1** |
| Reset grease filter | Zeroes the grease usage counter — see below |
| Reset charcoal filter | Zeroes the charcoal usage counter. Only if the hood advertises one |
| Grease / charcoal filter | Percentage of life remaining |
| Fan / light runtime | Diagnostic, disabled by default |
| Delay remaining | Counts down, updates once a minute |
| Airflow Control Technology | Read-only; ACT is set physically on the hood |
| Fan, filter and fault problem sensors | |

Entities are created from the capabilities your hood reports, so a model
without a charcoal filter simply will not get that sensor or its reset.

## Things worth knowing

**Some controls actuate the hood.** Enabling clean air or setting a delay
timer starts the fan at speed 1. That is how the hood behaves, not a quirk of
this integration.

**The power switch restores, it doesn't just resume.** Turning it off stops
everything; turning it back on restores the fan and light to the levels they
were running at before, rather than starting fresh.

**Delay off is in seconds, up to an hour.** That is now established against
hardware rather than inferred — the device counts the timer down in 60-second
steps and shuts the hood off at zero — so the entity carries the unit instead
of showing a bare number. It is not converted to minutes: seconds is the
device's own unit, and rounding to minutes would misreport a timer set to
something in between from the vendor app.

The Android app writes only off, 5 minutes, or 10 minutes (0, 300, or 600
seconds) and would display a one-hour timer as 10 minutes. That is the app.
The device accepts arbitrary values, so this integration exposes a free-entry
number. Its 3600-second cap is the largest value proven accepted, not a known
device limit — nobody has established where the hood's own ceiling is.

**Both filter resets are destructive, and neither has been watched on
hardware.** Each publishes `1` and is meant to zero a usage counter that
cannot be put back. The charcoal reset is its own field
(`resetcharcoalfilter`), confirmed from Android 1.1.16, not a reuse of the
grease command. Press either only when you have genuinely cleaned or replaced
that filter. A hood that does not advertise a charcoal filter does not get
the charcoal reset.

**Ducted vs recirculating is read-only.** Changing it would start charcoal
filter accounting for a filter that may not be installed. The vendor app
also registers the change with the cloud, which this integration does not
do. Use the vendor app if you genuinely need to change it.

**Light is brightness-only, even on hoods that support tunable white.** The
hood used to build this integration doesn't support TruHue (Zephyr's
color-temperature feature), and Android 1.1.16 has no color control either,
so color-temperature control has never been validated and isn't implemented.
Hoods that do support it will only get brightness control until someone with
that hardware can verify it.

## Reporting problems

Download diagnostics from the device page and attach them to your issue. They
are redacted — no serial, MAC, thing name, coordinates, credentials or
session tokens — which is what makes them safe to attach to a public issue. If you have a model we have not
seen, that download is what lets support for it get added.

## License

GPL-3.0-or-later
