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
"requirements": ["pyzephyrconnect @ git+https://github.com/<owner>/pyzephyrconnect@main"]
```

Before the HACS release this is pinned to a real version (`pyzephyrconnect==0.1.0`).

## 3. Library architecture

Six modules, each independently testable.

| Module | Responsibility | Dependencies |
|---|---|---|
| `const.py` | AWS and vendor constants | — |
| `presign.py` | SigV4 presigned WebSocket URL builder | stdlib only |
| `auth.py` | SRP -> identity exchange -> AttachPolicy; expiry tracking | `pycognito`, `boto3` |
| `api.py` | `getowndevices`, `discoverdevice` over pinned TLS | `aiohttp` |
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

## 6. TLS pinning

`zephyr-prod-app.gemteks.com` presents a chain whose intermediate omits the
Subject Key Identifier extension, failing OpenSSL 3.x verification. Apple's
Security framework is lenient, so the iOS app is unaffected; Python is not.
`verify=False` is not acceptable.

The library ships a **CA-only** bundle — TWCA Root CA, TWCA Global Root CA, and
TWCA Secure SSL Certification Authority — as package data. Supplying the
intermediate as a trust anchor satisfies verification.

This supersedes PROTOCOL.md §4 and §7.6, which describe pinning the leaf and
warn of a 2026-10-15 expiry. Verified empirically: a CA-only bundle completes
the handshake. Validity moves from **2026-10-15 to 2030**, and the pin survives
vendor leaf rotation.

`api.py` raises a distinct, actionable error on certificate failure rather than
a generic `SSLError`, naming the bundle and its expiry.

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

### 7.3 The `power` decision

Step 3 determines the fan and light entity design:

- **Master switch** (fan/light require `power=1`) — `turn_on` writes `power=1`
  alongside the level. No separate power entity.
- **Derived** (reports 1 when anything is on) — read-only, not exposed as a
  control.
- **Independent standby** — gets its own `switch`.

Implementation assumes *master switch* and is corrected after step 3.

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

### 8.1 Filter percentage units

`usegreasefiltertime: 642` against `maxGreasefilterTimer: 60` reconciles only if
the counter is minutes and the maximum is hours (~10.7 h of 60 h). This is an
inference. The sensor exposes the raw counter as an attribute so a wrong
assumption is visibly wrong rather than silently producing a nonsense
percentage. Confirmed during validation by observing counter movement against
`usefantime`.

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
