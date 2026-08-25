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

> **Note:** this integration currently installs its `pyzephyrconnect` library
> directly from GitHub rather than from PyPI. A PyPI release is pending; once
> it ships, `manifest.json` will be updated to pin a released version. Until
> then, installing the integration will also pull `pyzephyrconnect` from
> `github.com/RyanMorash/pyzephyrconnect`.

## Entities

| Entity | Notes |
|---|---|
| Fan | Speeds gated on the model's `maxFanSpeed` |
| Light | Brightness gated on `maxLightLevel` |
| Power switch | Off stops everything; on restores the previous levels |
| Clean air switch | **Starts the fan at speed 1** when enabled |
| Delay off | Minutes in the UI (seconds on the device). **Setting a value starts the fan at speed 1** |
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

**Delay off is minutes in Home Assistant, seconds on the device.** The
integration converts for you. The vendor app only offers 5 or 10 minutes, but
the device itself accepts arbitrary values, so this integration exposes a
free-entry number instead of just those two presets.

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
are redacted — no serial, MAC, thing name or coordinates — which is what
makes them safe to attach to a public issue. If you have a model we have not
seen, that download is what lets support for it get added.

## License

GPL-3.0-or-later
