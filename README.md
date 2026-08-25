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

Add this repository as a HACS custom repository, install **Zephyr Connect**,
restart Home Assistant, then add the integration and sign in with the account
you use in the Zephyr Connect app.

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
| Delay off | Raw device value, no unit — see below. **Setting a value starts the fan at speed 1** |
| Reset grease filter | Zeroes the usage counter — see below |
| Grease / charcoal filter | Percentage of life remaining |
| Fan / light runtime | Diagnostic, disabled by default |
| Delay remaining | Counts down, updates once a minute |
| Airflow Control Technology | Read-only; ACT is set physically on the hood |
| Fan, filter and fault problem sensors | |

Entities are created from the capabilities your hood reports, so a model
without a charcoal filter simply will not get that sensor.

## Things worth knowing

**Some controls actuate the hood.** Enabling clean air or setting a delay
timer starts the fan at speed 1. That is how the hood behaves, not a quirk of
this integration.

**The power switch restores, it doesn't just resume.** Turning it off stops
everything; turning it back on restores the fan and light to the levels they
were running at before, rather than starting fresh.

**Delay off has no unit yet.** Whether the device reads the field as seconds
or minutes has not been validated against hardware (the vendor app writes 300
for its "5 minutes" preset, which suggests seconds, but the countdown has
never been watched). Until that validation runs, the entity shows and writes
the device's raw value with no unit — presenting one would be a guess dressed
up as a fact. The vendor app only offers two presets, but the device accepts
arbitrary values, so this integration exposes a free-entry number.

**Reset grease filter is destructive and untested.** It zeroes a counter that
cannot be reconstructed, and the write has never been validated against
hardware — doing so requires actually cleaning a filter. Press it only when
you have genuinely cleaned yours.

**Ducted vs recirculating is read-only.** Changing it would start charcoal
filter accounting for a filter that may not be installed. Use the vendor app
if you genuinely need to change it.

**Light is brightness-only, even on hoods that support tunable white.** The
hood used to build this integration doesn't support TruHue (Zephyr's
color-temperature feature), so color-temperature control has never been
validated and isn't implemented. Hoods that do support it will only get
brightness control until someone with that hardware can verify it.

## Reporting problems

Download diagnostics from the device page and attach them to your issue. They
are redacted — no serial, MAC, thing name, coordinates, credentials or
session tokens — which is what makes them safe to attach to a public issue. If you have a model we have not
seen, that download is what lets support for it get added.

## License

GPL-3.0-or-later
