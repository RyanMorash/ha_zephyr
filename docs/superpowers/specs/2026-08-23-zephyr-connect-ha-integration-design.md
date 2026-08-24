# Zephyr Connect — Home Assistant integration design

Date: 2026-08-23
Status: approved, pending implementation plan

Protocol reference: [PROTOCOL.md](../../../PROTOCOL.md)

---

## 1. Goal

A HACS-installable Home Assistant integration giving full local-UI control of
Zephyr range hoods (fan, light, settings, filter monitoring) via the vendor's
AWS IoT cloud, plus a reusable Python library for the protocol.

There is no local API. All communication is a cloud round-trip through AWS IoT
Core device shadows. This is a constraint, not a design choice.

### Success criteria

1. Fan speed and light level controllable from HA, reflecting within ~1s.
2. Filter life, usage counters, and alarms visible as entities.
3. Survives the 1-hour credential expiry without user intervention.
4. Degrades to polling rather than going unavailable when MQTT drops.
5. Installable from HACS on any architecture HA supports.

## 2. Repositories and naming

Two repositories.

| Repo | Contents | Visibility |
|---|---|---|
| `pyzephyrconnect` (new) | Protocol library, `PROTOCOL.md`, TWCA CA bundle, probe CLI, library tests | public |
| `ha_zephyr` (existing) | `custom_components/zephyr_connect/`, `hacs.json`, integration tests | public (identifiers scrubbed 2026-08-23) |

- HA domain: `zephyr_connect` (verified unused in HA core)
- PyPI package: `pyzephyrconnect` (verified available; `pyzephyr` is taken)
- Branding follows the vendor's own product name, "Zephyr Connect".

Rationale for the split: the protocol library has value independent of Home
Assistant (CLI, exporters, other automation platforms), and the churn-heavy
discovery work happens entirely inside the library via the probe CLI, so the
repo boundary does not cut across an active edit loop.

During development the component depends on the library by git URL:

```json
"requirements": ["pyzephyrconnect @ git+https://github.com/RyanMorash/pyzephyrconnect@main"]
```

Before the HACS release this is pinned to a real version (`pyzephyrconnect==0.1.0`).

## 3. Library architecture

Six modules, each independently testable.

| Module | Responsibility | Dependencies |
|---|---|---|
| `const.py` | AWS and vendor constants | — |
| `presign.py` | SigV4 presigned WebSocket URL builder | stdlib only |
| `auth.py` | SRP -> identity exchange -> AttachPolicy; expiry tracking | `pycognito`, `boto3` |
| `api.py` | `getowndevices`, `discoverdevice` over supplemented TLS trust | `aiohttp` |
| `shadow.py` | MQTT subscribe/publish, granted-QoS validation | `paho-mqtt` |
| `client.py` | Facade: orchestration, refresh, reconnect, state cache | the above |

### 3.1 Dependency choice

`aiohttp` + `pycognito` + `paho-mqtt`, matching precedent in HA core:

- `pycognito` — used by `pylitterbot` (Whisker/AWS Cognito). Pulls `boto3`
  transitively (~28 MB, pure Python, installs on every architecture).
- `paho-mqtt` — used by `pyeconet` (Rheem/AWS IoT), and already shipped in HA
  core for the MQTT integration.

`awsiotsdk`/`awscrt` is deliberately **not** used despite the working prototype
using it. It is a compiled binary wheel published only for x86_64 and aarch64;
32-bit ARM has no wheel and would attempt a source build. `paho-mqtt` against a
presigned WebSocket URL is equivalent in capability and pure Python.

### 3.2 Blocking calls

`pycognito` and `boto3` are synchronous. The library exposes an async surface
and wraps them in `asyncio.to_thread` internally, so Home Assistant never sees a
blocking call and callers need no executor knowledge. Auth runs roughly once per
hour; thread overhead is irrelevant.

### 3.3 `presign.py` isolation

SigV4 WebSocket presigning is the only component with no counterpart in the
verified prototype. It is a pure function — credentials in, URL out — with no
network access, so it is testable against a known-good vector offline. This
keeps the highest-uncertainty code in the smallest, most-testable unit.

## 4. Authentication and connection lifecycle

Setup ordering is mandatory and derives from PROTOCOL.md §3.3:

1. Cognito User Pool SRP -> ID token (`user_pool_region` passed explicitly).
2. Identity Pool `get_id` + `get_credentials_for_identity` (unsigned).
   Response key is `SecretKey`, not `SecretAccessKey`.
3. `iot:AttachPolicy` binding `RangeHoodPolicy` to the identity.
4. `getowndevices` -> thing names.
5. `discoverdevice` per thing -> capabilities + initial state.
6. MQTT connect, subscribe, publish `{}` to `.../get`.

**Step 3 must precede step 6.** An already-open MQTT connection does not pick up
newly attached permissions. Skipping it produces a connection where CONNECT,
SUBSCRIBE and PUBLISH all succeed and every message is silently dropped, with no
error path. `list_attached_policies` is checked first; `attach_policy` is
idempotent.

### 4.1 Identity and client ID

- `IdentityId` retains its full `us-west-2:uuid` form, region prefix included.
- MQTT client ID is `<identity_id>-ha` — a stable suffix so the integration can
  coexist with the phone app without mutual session takeover (PROTOCOL.md §5).
  Configurable via the options flow for users running more than one HA instance.

### 4.2 Credential refresh

Cognito tokens and AWS credentials both expire at 1 hour, and the presigned
WebSocket URL is time-limited. Refresh is scheduled at ~50 minutes:

1. `renew_access_token()` (not a full SRP re-auth — multiple round trips, and
   the pool may rate-limit).
2. Re-exchange for fresh AWS credentials.
3. Rebuild the presigned URL and reconnect MQTT.

Policy re-attachment is unnecessary on refresh; the binding persists on the
identity.

### 4.3 Reconnect

`on_connection_interrupted` / `on_connection_resumed` equivalents are wired.
On resume, `session_present` is checked and subscriptions are re-established if
false. Backoff is exponential with a cap.

### 4.4 Subscribe validation

`paho` and `awscrt` both report success for a subscribe the broker refused. The
granted QoS is checked explicitly on every subscribe: `0` or `1` is a real
subscription; `128` or absent means denied. A denied subscribe raises an error
naming AttachPolicy as the likely cause rather than presenting as a working but
silent connection.

Note also that AWS IoT closes the connection on a refused subscribe, so
sequential probing of multiple topics on one connection is invalid.

## 5. Sync model — hybrid

| Path | Role | Cadence |
|---|---|---|
| `discoverdevice` (HTTPS) | Capabilities + initial state; works before MQTT is up | setup, then fallback |
| MQTT `update/accepted`, `update/delta`, `get/accepted` | Live push | event-driven |
| MQTT publish `{}` to `.../get` | Safety net for missed messages | every 5 min |
| `discoverdevice` poll | Fallback when MQTT is down | every 60 s while degraded |

State is merged into a single cache in `client.py`, which notifies listeners.
Home Assistant consumes this via a push-style `DataUpdateCoordinator`.

Rationale: MQTT alone leaves entities stale or unavailable when a token refresh
or vendor hiccup breaks the socket. Polling alone adds latency and permanent
vendor API load. The hybrid degrades rather than fails.

## 6. TLS trust supplement

`zephyr-prod-app.gemteks.com` presents a chain whose intermediate omits the
Subject Key Identifier extension, failing OpenSSL 3.x verification. Apple's
Security framework is lenient, so the iOS app is unaffected; Python is not.
`verify=False` is not acceptable.

The library ships the TWCA certificate set — Root CA, Global Root CA, and the
Secure SSL Certification Authority intermediate — as package data, and loads
them as **supplementary** trust anchors on top of the system trust store:

```python
ctx = ssl.create_default_context()        # system CAs stay loaded
ctx.load_verify_locations(cafile=bundle)  # TWCA added alongside
```

This is deliberately **not** certificate pinning. Measured against the live
host:

| Trust configuration | Vendor host | Mainstream-CA host |
|---|---|---|
| System only (191 CAs) | FAIL: `CERTIFICATE_VERIFY_FAILED` | OK |
| TWCA only (3 CAs) | OK | **FAIL** |
| System + TWCA (193 CAs) | OK | OK |

Replacing the trust store would break the integration completely the day
Gemtek rotates to any mainstream CA — a self-inflicted outage on a device
with no local control path. Adding to it fixes the SKI defect and survives
rotation.

Note that the system store already trusts the TWCA *roots*; the cert that
actually resolves the failure is the intermediate, which is precisely the one
missing the SKI extension.

`verify_mode` stays `CERT_REQUIRED` and `check_hostname` stays `True`.

This supersedes PROTOCOL.md §4 and §7.6, which describe pinning the leaf and
warn of a 2026-10-15 expiry. Verified empirically. `api.py` raises a distinct,
actionable error on certificate failure rather than a generic `SSLError`.

## 7. Write path

Format: `{"state":{"desired":{<field>:<value>}}}` published to `.../update`.
The device applies it and echoes on `update/accepted` with `state.reported`
updated.

### 7.1 Probe CLI

Field semantics are unverified. No integration code writes to the shadow until
the probe has confirmed each field against the real device.

```
python -m pyzephyrconnect.probe --watch
python -m pyzephyrconnect.probe --set light=1 --confirm
```

Safety rails:

- One field per invocation.
- Explicit allowlist of writable keys. Alarm and counter fields are never
  writable.
- Prints current state and the intended desired-state diff, then requires
  confirmation.
- Reports the before/after pair from `update/documents`.
- Refuses to run without `--confirm`.

### 7.2 Validation sequence

Device attended. Ordered most-reversible first.

| # | Write | Establishes | Risk |
|---|---|---|---|
| 1 | `light=1` | Write path works; whether `power` gates it | none |
| 2 | `light=2,3` -> `0` | Level range maps to `maxLightLevel` | none |
| 3 | `power=1` -> `0` | Master switch, derived, or standby | low |
| 4 | `fan=1` | Fan actuates | audible |
| 5 | `fan=6` -> `0` | Range matches `maxFanSpeed` | loud |
| 6 | `setdelaytimer=N` | Units, by watching `delaytimer` count down | low |
| 7 | `setcleanairfunction=1` -> `0` | Toggles | low |
| 8 | `setrecirculating` | Ducted <-> recirculating | changes filter accounting; restore after |
| 9 | `resetgreasefilter=1` | Reset works | destructive: zeroes the usage counter |

Step 9 is deferred until the filter is actually being cleaned. The accumulated
counter cannot be reconstructed once reset.

### 7.3 The `power` decision — RESOLVED 2026-08-24

Established against the real device. `power` is a **writable master switch**
that the device also maintains itself:

| Action | Observed |
|---|---|
| Write `power=0` | Device turns everything off (light 1 -> 0, ~1.2s later) |
| Write `light=1` while `power=0` | Light comes on; device raises `power` to 1 on its own |
| Light goes to 0 | Device drops `power` to 0 on its own |

Confirmed symmetric for the fan: writing `fan=N` starts the fan and the
device raises `power` to 1 on its own, exactly as with `light`.

**Resolved:** writing `power=1` from an all-off state RESTORES the previous
levels - observed bringing back `fan=6` and `light=1` together. The device
remembers the last-active levels across a power cycle.

So `power` is a switch with real semantics in both directions:
`turn_off` -> everything off; `turn_on` -> resume what was running. It maps
cleanly to a HA `switch` entity; no off-only workaround needed.

Fan range confirmed 0-6, matching `maxFanSpeed: 6`.

It is **not a precondition**: `fan` and `light` are set directly and the
device manages `power` in response. So `fan.turn_on` / `light.turn_on` write
only the level, never `power` alongside it.

`power` is exposed as its own `switch` entity (master off), and its reported
value is authoritative for whether the hood is running.

An earlier draft of this spec concluded `power` was derived and read-only.
That was wrong: every test behind it used `state.desired`, which this device
ignores entirely (see 7.4), so the absence of a response carried no
information about semantics.

### 7.4 Control mechanism — CORRECTED 2026-08-24

The write path is **not** `state.desired`. Captured from the vendor iOS
app's own MQTT traffic via a wildcard subscription on
`$aws/things/<thing>/shadow/#`:

```
<shadow>/update   {"state": {"reported": {"light": 1}}}
```

The app writes **`state.reported`** to `.../shadow/update`. This is
backwards from the AWS convention (`reported` is normally device-authored),
but it is what the device acts on. Writing `state.desired` is accepted by
AWS and silently ignored by the device - no error, no rejection, and the
desired block accumulates forever generating spurious deltas.

Consequences for the integration:
- `update/delta` must be ignored entirely. A delta is a desired-vs-reported
  difference; since this vendor never writes `desired`, any delta is stale
  or foreign and never represents device state. Folding deltas into cached
  state makes the client report a wish back as if it were a confirmation.
- After a write, AWS echoes `update/accepted` immediately with our own
  value. The device's real confirmation arrives as a SECOND
  `update/accepted` roughly 1.2-1.6s later, typically carrying a fuller
  block. Treat only the device's report as authoritative.

### 7.5 Delay timer — RESOLVED 2026-08-24

Established by observing the vendor app.

**Units are SECONDS, not minutes.** Selecting 5 minutes in the app writes
`300`; 10 minutes writes `600`. An earlier draft assumed minutes - wrong.

**Only `setdelaytimer` needs writing — RESOLVED.** Writing
`setdelaytimer=300` alone caused the DEVICE to set `delaytimer` to 300 in
response. Hypothesis (b) confirmed; the app was never writing both fields.

Consequence: `delaytimer` does not need to be in the probe's writable
allowlist. It was added while (a) was still live and should be removed once
the countdown behaviour below is settled, to keep the hardware-actuating
allowlist as narrow as the evidence requires.

**Countdown behaviour — RESOLVED.** Once armed, `delaytimer` counts down
continuously and the device reports it in 60-second steps
(300 -> 240 -> 180 ...). It is not a per-second push, which is why a
seconds-scale observation appeared static. Units confirmed as real seconds.

`delaytimer` is therefore device-managed status and was removed from the
writable allowlist again (commit 87b0ed0). Clients write `setdelaytimer`
only.

**The app's presets are not device limits.** The picker offers only 5 and
10 minutes, but writing `setdelaytimer=60` (one minute) was accepted. The
device takes arbitrary second values; the app is constraining its own UI.

Entity implication:
- `number` for the delay setting, NOT a `select`. HA can expose finer
  control than the vendor app allows. Displayed in minutes, written in
  seconds (x60). Lower bound 0 (off). **Upper bound unknown** - not worth
  probing exhaustively; pick a sane cap (e.g. 60 minutes) and treat a
  rejected write as the real ceiling if a user ever hits it.
- `sensor` for delay remaining, sourced from `delaytimer`, device_class
  DURATION, unit seconds. Updates about once a minute, so it is genuinely
  useful rather than a static mirror of the setting.

### 7.7 Clean air function — RESOLVED 2026-08-24

`setcleanairfunction` is an operating mode, not a passive setting.

| Write | Observed |
|---|---|
| `setcleanairfunction=1` | Clean air mode on, fan starts at speed 1 |
| `setcleanairfunction=0` | Clean air mode off, hood powers down |

The power-down on `0` is consistent with two readings and the observation
cannot separate them: either turning the mode off explicitly powers the hood
down, or clean air was the only thing running and the device dropped `power`
because nothing remained - which matches the established behaviour in 7.3.
The second is simpler and assumed; if it matters later, test by turning
clean air off while the fan is separately running at speed 6.

Entity implication: a plain `switch`, NOT an `EntityCategory.CONFIG` switch.
It actuates the hood rather than configuring it, so it belongs alongside the
fan and light rather than buried in the config section. Its description must
note that enabling it starts the fan at speed 1.

This is the third control (with the delay timer and `power`) where writing a
value actuates the hood rather than merely recording a preference. Treat
every `set*` field as potentially actuating until proven otherwise.

### 7.6 Pattern: the app constrains more than the firmware

Observed three times now - the delay-timer presets, the rule that the timer
can only be set while power is on (confirmed UI-only), and the preset
values themselves. The vendor app's UI limits are
not evidence of device limits.

Practical consequence: do not infer device constraints from what the app
refuses to offer. Test the device directly. This integration can legitimately
expose capability the vendor app does not, provided each case is verified
against hardware rather than assumed.

**Power precondition — RESOLVED: not device-enforced.** Writing
`setdelaytimer=300` with `power=0` succeeded: both timer values were set.
The app's rule that the timer is only settable while powered is UI-only.

**But writing it POWERS THE HOOD ON.** This is a side effect, not a
configuration change. Functionally coherent for a delay-off feature ("run,
then stop after N seconds"), but it breaks the naive entity mapping.

A HA `number` that starts a fan when adjusted is a bad surprise - especially
for automations that set it expecting a passive change. Options for Plan 2:

  1. `number` that writes through, with the side effect documented in the
     entity description. Simplest, matches device behaviour, but the
     surprise is real.
  2. Local `number` holding the duration without writing, plus a `button`
     ("Run with delay off") that arms it. Decouples configuration from
     actuation, at the cost of the number no longer mirroring device state.
  3. `number` whose writes are only sent while `power == 1`, mimicking the
     app. Avoids the surprise but silently drops user input when off.

Recommendation: option 1, named "Delay off" with an explicit description.
Someone setting a delay-off timer on a range hood most likely does intend it
to run. Option 2 is the fallback if that proves confusing in practice.

**Start speed — RESOLVED: fan speed 1.** Arming the timer from power-off
consistently starts the fan at speed 1, NOT a restore of previous levels.
(Contrast `power=1`, which does restore - fan 6 and the light.) So the two
power-on paths differ: `power=1` resumes, arming the timer starts gently.

This settles the recommendation on option 1. A `number` that starts the hood
on its quietest setting is defensible write-through behaviour; one that
jumped to speed 6 would not have been.

**Still open:** whether the hood actually shuts off when `delaytimer`
reaches 0. This is the one part of the feature never observed end to end,
and it is the behaviour the entity is built around. Verify by arming a short
timer (e.g. `setdelaytimer=60`) and leaving it.

### 7.6 Pattern: the app constrains more than the firmware

Observed three times now - the delay-timer presets, the rule that the timer
can only be set while power is on (confirmed UI-only), and the preset
values themselves. The vendor app's UI limits are
not evidence of device limits.

Practical consequence: do not infer device constraints from what the app
refuses to offer. Test the device directly. This integration can legitimately
expose capability the vendor app does not, provided each case is verified
against hardware rather than assumed.

**Still open:** whether the DEVICE enforces the app's rule that the delay
timer can only be set while power is on, or whether the app merely gates its
own UI. Device-enforced means the entity must go unavailable when
`power == 0`. Test by writing the pair with `power=0` and seeing whether the
values hold.

## 8. Entity model

Entities are gated on `discoverdevice` capabilities rather than hardcoded per
model, so the integration generalizes to other Zephyr hoods.

| Entity | Field(s) | Notes |
|---|---|---|
| `fan` | `fan`, `power` | `speed_count = maxFanSpeed` |
| `light` | `light`, `power` | Brightness from `maxLightLevel`; color temp only if `truHueSupport == 1` |
| `switch` recirculating | `setrecirculating` | Config category; only if `Recirculating == 1` |
| `switch` clean air | `setcleanairfunction` | Config category |
| `number` delay off | `setdelaytimer` | Becomes `select` if step 6 reveals discrete presets |
| `button` reset grease filter | `resetgreasefilter` | Ships in v1 |
| `sensor` grease filter | `usegreasefiltertime` / `maxGreasefilterTimer` | % remaining; raw value and store URL as attributes |
| `sensor` charcoal filter | `usecharcoalfiltertime` / `maxCharcoalfilterTimer` | Only if `maxCharcoalfilterTimer > 0` |
| `sensor` fan runtime | `usefantime` | Diagnostic, `TOTAL_INCREASING` |
| `sensor` light runtime | `uselighttime` | Diagnostic, `TOTAL_INCREASING` |
| `sensor` delay remaining | `delaytimer` | Duration |
| `sensor` mode | `act` | Diagnostic, raw string; semantics unknown |
| `binary_sensor` grease filter | `cleangreasefilters` | `PROBLEM`; `alarmgreasefilter` as attribute |
| `binary_sensor` charcoal filter | `cleancharcoalfilters` | `PROBLEM` |
| `binary_sensor` fan fault | `alarmfan`, `fanwarning` | `PROBLEM` |
| `binary_sensor` fault | `alarmfaultcode` | `PROBLEM`; `faultCode[]` as attribute |

Roughly 16 entities. `isOnline` becomes availability, combined with transport
health — not a sensor.

### 8.1 Filter counter units — RESOLVED 2026-08-24

`usegreasefiltertime` is in **MINUTES**, confirmed by direct observation: it
ticked up while the fan ran. `maxGreasefilterTimer` (60) is in **HOURS**.

Filter life percentage:

```python
used_fraction = use_grease_filter_time / (max_grease_filter_hours * 60)
remaining_pct = 100 * (1 - used_fraction)
```

**Verified against the vendor app.** With `usegreasefiltertime` at 643 the
formula yields 82.14% remaining, and the app displays **82%**. This is
independent confirmation from the vendor's own implementation, not merely
internal consistency.

The rival hypotheses are excluded outright by the same check:
- counter in seconds -> 99.70%, the app would show 100%
- `maxGreasefilterTimer` in minutes -> -972%, nonsensical

`cleangreasefilters` reads 0 (not yet due), also consistent.

Hours is ruled out for the counter: 643 h against a 60 h life would be 10x
overdue while the device reports the filter as fine. Seconds is ruled out on
plausibility - it would make lifetime fan runtime 33 minutes on a hood with
302,688 shadow revisions behind it.

The same conversion applies to `usecharcoalfiltertime` against
`maxCharcoalfilterTimer` (200 h), though that reads 0 on the reference
device, which runs ducted.

**`usefantime` and `uselighttime` are a DIFFERENT unit or granularity -
OPEN.** `usefantime` held at 1980 through five minutes of fan running while
`usegreasefiltertime` advanced. They cannot both be minutes on the same
flush schedule. Most likely hours (1979 h lifetime fan runtime is plausible)
or minutes flushed only rarely.

This does not block the filter sensors, which use `usegreasefiltertime`.
But the runtime diagnostic sensors must NOT be labelled with a unit until
this is settled - shipping `usefantime` as minutes when it is hours would be
wrong by 60x on a user-visible value. Ship them unitless (state_class
TOTAL_INCREASING, no device_class) until measured over a longer window.

### 8.2 Device registry

One HA device per thing:

- `identifiers`: `(DOMAIN, thingName)`
- `manufacturer`: `companyName`
- `model`: `modelName`
- `serial_number`: `SN`
- `connections`: `{(CONNECTION_NETWORK_MAC, MAC)}`
- `configuration_url`: `FAQURL`

Filter store and video URLs from `discoverdevice` attach as attributes on the
corresponding filter sensors, so a "filter due" notification can link straight
to the replacement part.

## 9. Config flow and error handling

- **User step**: email + password. Validated by full auth plus `getowndevices`.
- **Entry scope**: one config entry per account; all returned things become
  devices under it.
- **Unique ID**: Cognito identity ID.
- **Reauth flow**: triggered on `ConfigEntryAuthFailed`.
- **Options flow**: poll interval, MQTT client-ID suffix.

| Condition | Behaviour |
|---|---|
| SRP auth failure | `ConfigEntryAuthFailed` -> reauth |
| Network/transient failure at setup | `ConfigEntryNotReady` |
| Certificate verification failure | Distinct error naming the bundle and expiry |
| Subscribe denied (granted QoS 128) | Error naming AttachPolicy as likely cause |
| `isOnline == 0` | Entities unavailable |
| MQTT down, HTTPS reachable | Entities remain available; degraded polling |

Credentials are stored in the config entry. `getowndevices` returns precise home
coordinates; these are never logged and are redacted in diagnostics.

## 10. Diagnostics

`diagnostics.py` implements `async_get_config_entry_diagnostics`, dumping the
full shadow document and `discoverdevice` response with `thingName`, `SN`,
`MAC`, and `location` redacted.

For a reverse-engineered protocol this is the highest-leverage file in the
integration: when an owner of a different Zephyr model installs this, their
diagnostics download is how unknown fields (`act`, `truHueSupport == 1`,
unseen `faultCode` values) get characterized without asking them to run Python.

## 11. Testing

**Library** (`pytest`):
- `presign.py` against a known-good SigV4 vector, offline.
- State and capability parsing from the captured shadow and `discoverdevice`
  samples.
- Granted-QoS handling, including the denied-subscribe path.
- Credential refresh scheduling with a frozen clock.
- Mocked `aiohttp` and `paho` — no network in the test suite.

**Integration** (`pytest-homeassistant-custom-component`):
- Config flow: success, invalid auth, reauth, duplicate entry.
- Entity creation gated on capabilities (a hood with `Recirculating == 0`
  must not produce the recirculating switch).
- Availability transitions on `isOnline` and transport loss.
- Service calls produce the expected desired-state payloads.

## 12. Distribution

- `hacs.json` at repo root; `manifest.json` with `version`, `iot_class:
  cloud_push`, `config_flow: true`.
- Tagged GitHub releases.
- The vendor app client secret stays embedded. It ships inside the iOS app
  bundle and provides no security boundary; the integration cannot authenticate
  without it. Consistent with how other HA cloud integrations handle vendor app
  credentials.
- `PROTOCOL.md` device identifiers were replaced with placeholders and scrubbed
  from git history on 2026-08-23 before publication.

## 13. Open questions

Resolved during implementation, not blocking the plan:

1. `power` semantics — validation step 3.
2. `setdelaytimer` units and whether values are continuous or preset — step 6.
3. `act` string domain; only `"Disabled"` observed.
4. Charcoal filter reset — no `resetcharcoalfilter` field exists in the shadow.
   May share `resetgreasefilter`, may be app-side, may not exist.
5. Filter counter units — §8.1.
6. Whether `fanwarning` and `alarmfan` differ in meaning or severity.

## 14. Out of scope for v1

- Multiple accounts in one HA instance (multiple devices per account is supported).
- Local control. No local API exists.
- `truHueSupport` color-temperature control — the reference device reports `0`;
  the capability gate is implemented, the feature is not.
- Vendor security disclosure (PROTOCOL.md §8) — tracked separately, unrelated to
  the integration.
