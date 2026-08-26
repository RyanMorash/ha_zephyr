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

**Delay off is in seconds, up to an hour.** That is now established against
hardware rather than inferred — the device counts the timer down in 60-second
steps and shuts the hood off at zero — so the entity carries the unit instead
of showing a bare number. It is not converted to minutes: seconds is the
device's own unit, and rounding to minutes would misreport a timer set to
something in between from the vendor app.

The vendor app offers only two presets, but the device accepts arbitrary
values, so this integration exposes a free-entry number. Its 3600-second cap
is the largest value proven accepted, not a known device limit — nobody has
established where the hood's own ceiling is.

**Reset grease filter is destructive and untested.** It zeroes a counter that
cannot be reconstructed, and the write has never been validated against
hardware — doing so requires actually cleaning a filter. Press it only when
you have genuinely cleaned yours.

**Ducted vs recirculating is read-only.** Changing it would start charcoal
filter accounting for a filter that may not be installed. Use the vendor app
if you genuinely need to change it.

**There is no charcoal filter reset.** The hood's shadow exposes a grease
filter reset and nothing equivalent for charcoal, so a recirculating hood gets
a charcoal-life reading with no way to zero it from here. Whether the vendor
app has one — and whether it quietly reuses the grease reset — is unknown.

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
