# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A HACS-installable Home Assistant custom integration (`custom_components/zephyr_connect`,
domain `zephyr_connect`) for Zephyr range hoods. The hoods expose **no local API** —
everything is a cloud round trip through AWS IoT device shadows, and the protocol was
reverse-engineered.

All protocol work lives in a separate library, [`pyzephyrconnect`](https://github.com/RyanMorash/pyzephyrconnect)
(pinned in `manifest.json`). This repository is the Home Assistant layer only.

## Commands

Requires **Python 3.12+** — `__init__.py` uses a PEP 695 `type` alias, so 3.11 cannot even
parse it. There is no requirements/lock file in the repo; install the two dependencies
directly:

```bash
pip install pytest-homeassistant-custom-component 'pyzephyrconnect==0.2.0'   # version per manifest.json

pytest                                        # whole suite (asyncio_mode=auto via pytest.ini)
pytest tests/test_fan.py                      # one file
pytest tests/test_init.py::test_setup_starts_each_hood   # one test
pytest -k "reauth"                            # by name

ruff check .                                  # currently clean
```

`ruff format` is **not** the repo's formatting baseline — running it would reformat ~10
existing files. Match surrounding style by hand instead.

CI (`.github/workflows/validate.yml`) runs only **hassfest** and **HACS validation** — the
test suite is not run there, so run it locally before pushing. hassfest checks
`manifest.json`, `strings.json`, and `translations/en.json`; the latter two are currently
byte-identical and must be kept in sync when adding entities.

## Architecture

```
config entry
  └── ZephyrClient            one per account — a single auth/credential lifecycle
        └── Hood[]            one per physical hood on the account
              └── ZephyrCoordinator   one per hood
                    └── ZephyrEntity subclasses (7 platforms)
```

`entry.runtime_data` holds a frozen `ZephyrData(client, coordinators)`. It defines
`__iter__` over the coordinators, so every platform's `async_setup_entry` reads
`for coordinator in entry.runtime_data`. The client is stored *separately* from the
coordinators so unload can stop it even for an account with zero hoods.

**Updates are push-primary.** The library invokes `ZephyrCoordinator._handle_push` whenever
the device reports. The `DataUpdateCoordinator` interval is a *fallback* with three jobs
(see `_async_update_data`): re-read over HTTPS while MQTT is down (`DEGRADED_POLL_INTERVAL_SECONDS`),
a periodic safety-net re-read while connected (`SAFETY_NET_INTERVAL_SECONDS`), and — most
importantly — it is the only path by which a terminal credential/policy failure inside the
library's supervisor surfaces as a reauth prompt. That is why `async_initialise` registers a
permanent no-op listener: it defeats HA's convention of disarming the timer when nothing is
listening, which would otherwise mean a hood with all entities disabled never prompts for reauth.

**Error mapping is load-bearing and easy to get wrong.** `ZephyrAuthError` *and*
`ZephyrPolicyError` are both terminal and both escalate to `ConfigEntryAuthFailed` — in
setup and in `_async_poll`. Only the reauth success path's entry reload rebuilds the
supervisor and re-attaches the IoT policy, so downgrading either to `ConfigEntryNotReady`
or `UpdateFailed` retries forever with no remediation. Both subclass `ZephyrError` directly,
so they must be caught *before* the generic clause. Transient failures stay
`ZephyrTransportError` and take the `NotReady`/`UpdateFailed` path. Write failures go
through `ZephyrEntity._async_write`, which maps `ZephyrError` to `HomeAssistantError`.

Shutdown ordering matters: coordinators are shut down before the shared client, in both
`_release` (partial setup) and `async_unload_entry`. `client.async_stop` is idempotent and
also registered via `entry.async_on_unload`.

## Rules that are not negotiable

- **Never touch the protocol directly.** No importing `paho`, `boto3`, or aiohttp
  transports; no constructing shadow payloads; no MQTT topics. If something is missing,
  the fix belongs in `pyzephyrconnect`.
- **Gate entities on `HoodCapabilities`, never on the model string** — the integration must
  generalise to hoods nobody has seen. `None` from a capability means "not advertised" and
  is treated the same as `0`: no entity.
- **`None` is not `False`.** A device field that was not reported is *unknown*. `bool(None)`
  silently reads as off/0%/no-problem — the exact class of bug the tri-state helpers
  (`_flag`, `_any_flag` in `binary_sensor.py`) and the `is None` guards in every `is_on` /
  `percentage` / `native_value` exist to prevent.
- **PII must stay redacted.** `thingName`, `SN`, `MAC`, `location`, credentials and tokens
  are never logged at INFO or above. `diagnostics.REDACT_KEYS` must cover any new key that
  carries them; `CONF_TOKENS` is named there so the whole token sub-dict is redacted.
  Diagnostics being safe to paste into a public issue is what makes unknown models
  supportable — it is the highest-leverage file here.
- **No network in tests.** `ZephyrClient` is patched; see the `mock_client` fixture in
  `tests/test_init.py`, which deliberately `del`etes the pre-0.2.0 per-thing methods so a
  stale call site raises `AttributeError` instead of passing against a bare `MagicMock`.
- `_attr_has_entity_name = True` on every entity; `entry.runtime_data`, never `hass.data[DOMAIN]`.
- `MQTT_CLIENT_ID_SUFFIX = "-ha"` is passed explicitly to `ZephyrClient.from_credentials` in
  *both* `__init__.py` and `config_flow.py`. The library's default changed in 0.2.0; relying
  on it would move every existing install's MQTT client ID and collide with plain library
  scripts — AWS IoT evicts one of two connections sharing an ID, so both would flap.

## Device behaviour worth knowing before changing anything

Established against real hardware (an AK7400AS, 2026-08-24). `docs/superpowers/plans/2026-08-24-zephyr-connect-ha-integration.md`
carries the full validated protocol section and **supersedes any older document, including
the library's PROTOCOL.md**, where they disagree.

- **Writes go to `state.reported`, not `state.desired`.** AWS accepts `desired` and the
  device silently ignores it. The device echoes its real state ~1.2–1.6 s later via push,
  so no optimistic local update is needed.
- **Every validated `set*` field actuates the hood rather than recording a preference.**
  Enabling clean air or setting a delay timer *starts the fan at speed 1*. Assume any
  untested `set*` field actuates until proven otherwise, and never infer device behaviour
  from the vendor app's UI — the app is consistently more restrictive than the firmware.
- **Fan and light writes do not need `power`.** The device raises `power` itself; writing it
  too would fight the device. `power` on *restores previous levels*, it does not just resume.
- **Units are a trap.** In the same payload: filter counters are **minutes**, the capability
  maxima they divide against are **hours**, and runtime counters are **hours**. Conflating
  any pair is wrong by 60×. Delay timer values are **seconds**, passed through unconverted
  so the entity mirrors `setdelaytimer` exactly.
- **`DELAY_TIMER_MAX = 3600` is where the evidence stops, not a known device limit.** Several
  values in this codebase are like that; the comments say which are measured, which are
  inferred, and which are unprobed. Preserve that distinction rather than flattening it.
- **The grease filter reset is destructive and has never been validated** against hardware —
  verifying it requires actually cleaning a filter. There is no charcoal equivalent in the
  shadow; do not invent one by writing the grease field.
- **`setrecirculating` is deliberately read-only**, and tunable white (TruHue) is
  deliberately unimplemented — the reference hood reports `truHueSupport: 0`, so a
  colour-temperature implementation would ship unvalidated.

## Comment conventions

Comments here explain *why*, and frequently record what was measured versus what was
inferred, or why an obvious alternative is wrong. They are the design record for a
reverse-engineered protocol. When changing behaviour, update the reasoning — don't strip it.
