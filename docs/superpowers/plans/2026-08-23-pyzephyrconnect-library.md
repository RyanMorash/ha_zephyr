# pyzephyrconnect Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `pyzephyrconnect`, a Python library that authenticates to the Zephyr/Gemtek AWS IoT cloud, reads range hood state from the device shadow, and provides a safety-railed probe CLI for mapping the unverified write path.

**Architecture:** Async facade (`client.py`) over four independently testable layers: SigV4 presigning (pure, stdlib), REST (`aiohttp` with a pinned CA bundle), auth (`pycognito`/`boto3` wrapped in `asyncio.to_thread`), and MQTT transport (`paho-mqtt` over a presigned WebSocket). No network in the test suite.

**Tech Stack:** Python 3.12+, `aiohttp`, `pycognito`, `paho-mqtt` 2.x, `pytest`, `pytest-asyncio`

**Spec:** `../specs/2026-08-23-zephyr-connect-ha-integration-design.md`

**Repo:** `/Users/ryanmorash/Developer/pyzephyrconnect` (empty, not yet a git repo)

## Global Constraints

Every task's requirements implicitly include this section.

- `requires-python = ">=3.12"`. Do NOT target the local 3.14 — Home Assistant runs an older interpreter and will refuse to install a package that floors above it.
- Runtime dependencies are exactly: `aiohttp`, `pycognito`, `paho-mqtt>=2.1.0`. `boto3` arrives transitively via `pycognito` and may be used directly for `cognito-identity` and `iot` only.
- `awsiotsdk` and `awscrt` are FORBIDDEN. They are compiled wheels unavailable on 32-bit ARM. This is the reason the transport is hand-built.
- The library exposes an async surface. `pycognito` and `boto3` are blocking and MUST be wrapped in `asyncio.to_thread` inside the library, never pushed onto the caller.
- No test may make a network call. All `aiohttp`, `paho`, `pycognito`, and `boto3` interactions are mocked.
- TLS to `zephyr-prod-app.gemteks.com` uses the bundled CA-only PEM. `verify=False` is FORBIDDEN in all code paths, including tests and the CLI.
- The write path is unverified and actuates a physical fan and light. `ShadowClient.publish_desired` and `ZephyrClient.async_publish_desired` are the plumbing and belong in the library, but **`probe.py` is the only permitted caller** until the validation gate passes. Per spec section 7: no integration code writes to the shadow until the probe has confirmed each field against the real device.
- `thingName`, `SN`, `MAC`, and `location` are personal data. Never log them at INFO or above; redact them in any diagnostic output.
- AWS IoT constants: region `us-west-2`, service name `iotdevicegateway`, endpoint `a1nqxu0hki9zw3-ats.iot.us-west-2.amazonaws.com`, policy `RangeHoodPolicy`.

## Deferred to Plan 2 (the integration)

These spec requirements are deliberately NOT in this plan, because the
library exposes primitives and Home Assistant owns the scheduling:

- The 5-minute safety-net `get` and the 60-second degraded poll (spec
  section 5) are timers. The library provides `request_state()` and
  `async_poll()`; the integration's coordinator drives their cadence.
- The credential refresh timer. The library provides
  `async_refresh_if_needed()`, which is a no-op until inside the margin;
  the integration calls it on its update tick.
- Entity model, config flow, diagnostics, and HACS packaging (spec sections
  8 through 12).

This plan also adds two modules beyond the six in spec section 3:
`models.py` (typed views, kept separate so parsing is testable without any
transport) and `exceptions.py` (a shared hierarchy every layer raises into).

---

### Task 1: Project scaffold, constants, and exceptions

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`
- Create: `src/pyzephyrconnect/__init__.py`, `src/pyzephyrconnect/const.py`, `src/pyzephyrconnect/exceptions.py`
- Test: `tests/test_const.py`

**Interfaces:**
- Consumes: nothing
- Produces: `const.REGION`, `const.USER_POOL`, `const.CLIENT_ID`, `const.CLIENT_SECRET`, `const.IDENTITY_POOL`, `const.IOT_ENDPOINT`, `const.IOT_SERVICE`, `const.POLICY_NAME`, `const.DEVICE_API_BASE`, `const.WRITABLE_FIELDS`; exception classes `ZephyrError`, `ZephyrAuthError`, `ZephyrCertificateError`, `ZephyrPolicyError`, `ZephyrTransportError`

- [ ] **Step 1: Create the source tree**

The repo is ALREADY initialised on `main` (commit `7bc6373`) with
`.gitattributes`, a full Python `.gitignore`, and a GPL-3.0 `LICENSE`. Do
not run `git init`, and do not overwrite any of those three files.

```bash
cd /Users/ryanmorash/Developer/pyzephyrconnect
mkdir -p src/pyzephyrconnect/certs tests/fixtures
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "pyzephyrconnect"
version = "0.1.0"
description = "Python client for Zephyr/Gemtek range hoods via AWS IoT device shadows"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "GPL-3.0-or-later" }
authors = [{ name = "Ryan Morash" }]
dependencies = [
    "aiohttp>=3.9",
    "pycognito>=2024.5.1",
    "paho-mqtt>=2.1.0",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "pytest-cov"]

[project.urls]
Homepage = "https://github.com/RyanMorash/pyzephyrconnect"

[tool.hatch.build.targets.wheel]
packages = ["src/pyzephyrconnect"]

[tool.hatch.build.targets.wheel.force-include]
"src/pyzephyrconnect/certs" = "pyzephyrconnect/certs"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Append project-specific ignores**

The existing `.gitignore` is the standard Python template and already
covers `__pycache__/`, `.venv/`, `dist/`, `*.egg-info/`, `.pytest_cache/`,
`.coverage` and `.env`. Append only what it lacks — do NOT rewrite the file.

```bash
cd /Users/ryanmorash/Developer/pyzephyrconnect
grep -q 'probe-capture' .gitignore || cat >> .gitignore <<'EOF'

# pyzephyrconnect
probe-capture-*.json
.superpowers/
EOF
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_const.py`:

```python
"""Constants must stay pinned - these are reverse-engineered values."""
from pyzephyrconnect import const


def test_aws_constants_are_pinned():
    assert const.REGION == "us-west-2"
    assert const.USER_POOL == "us-west-2_McuoKpkna"
    assert const.IOT_ENDPOINT.endswith(".iot.us-west-2.amazonaws.com")
    assert const.IOT_SERVICE == "iotdevicegateway"
    assert const.POLICY_NAME == "RangeHoodPolicy"


def test_alarm_and_counter_fields_are_not_writable():
    """The probe allowlist is the only thing preventing a write to a
    read-only alarm field. Guard it with a test."""
    forbidden = {
        "alarmfan", "alarmfaultcode", "alarmgreasefilter", "faultCode",
        "fanwarning", "usegreasefiltertime", "usecharcoalfiltertime",
        "uselighttime", "usefantime", "isOnline",
    }
    assert forbidden.isdisjoint(const.WRITABLE_FIELDS)


def test_writable_fields_cover_the_validation_sequence():
    for field in ("light", "power", "fan", "setdelaytimer",
                  "setcleanairfunction", "setrecirculating",
                  "resetgreasefilter"):
        assert field in const.WRITABLE_FIELDS
```

- [ ] **Step 5: Run test to verify it fails**

Run: `python -m pytest tests/test_const.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyzephyrconnect'`

- [ ] **Step 6: Write `src/pyzephyrconnect/const.py`**

```python
"""Constants for the Zephyr/Gemtek cloud.

All values reverse-engineered from the vendor iOS app. See PROTOCOL.md.
"""

REGION = "us-west-2"
USER_POOL = "us-west-2_McuoKpkna"
CLIENT_ID = "5a2qiskdvvu7gre1jvbjnunu20"
# Ships inside the iOS app bundle; provides no security boundary, but SRP
# fails without it because it is needed for SECRET_HASH.
CLIENT_SECRET = "3b085l2fkgph4kt734k5e26tirb9hjasgb4rn8sjpp4mheo5kga"
IDENTITY_POOL = "us-west-2:fb4c1b66-12c2-414b-83a1-a1902f7d98e3"
PROVIDER = f"cognito-idp.{REGION}.amazonaws.com/{USER_POOL}"

IOT_ENDPOINT = "a1nqxu0hki9zw3-ats.iot.us-west-2.amazonaws.com"
IOT_SERVICE = "iotdevicegateway"
POLICY_NAME = "RangeHoodPolicy"

DEVICE_API_BASE = "https://zephyr-prod-app.gemteks.com/prod"
DEVICE_API_LIST = f"{DEVICE_API_BASE}/getowndevices"
DEVICE_API_DISCOVER = f"{DEVICE_API_BASE}/discoverdevice"

# Suffix appended to the Cognito identity ID to form the MQTT client ID, so
# the library can coexist with the phone app instead of evicting it.
CLIENT_ID_SUFFIX = "-ha"

# Credentials last 1 hour. Refresh early enough to rebuild the socket.
REFRESH_MARGIN_SECONDS = 600

# Fields the probe CLI is permitted to write. Everything else in the shadow
# is a counter, an alarm, or device-reported telemetry.
WRITABLE_FIELDS = frozenset({
    "power",
    "light",
    "fan",
    "setdelaytimer",
    "setcleanairfunction",
    "setrecirculating",
    "resetgreasefilter",
})

# Writes that are destructive or change device configuration. The probe
# requires an extra confirmation for these.
DANGEROUS_FIELDS = frozenset({
    "resetgreasefilter",   # zeroes an unrecoverable usage counter
    "setrecirculating",    # changes filter accounting
})
```

- [ ] **Step 7: Write `src/pyzephyrconnect/exceptions.py`**

```python
"""Exception hierarchy.

Each error names the operator action that resolves it. A generic error here
costs hours of debugging, because most failure modes in this protocol are
silent - see PROTOCOL.md section 6.
"""


class ZephyrError(Exception):
    """Base for all library errors."""


class ZephyrAuthError(ZephyrError):
    """Cognito authentication failed. Credentials are wrong or expired."""


class ZephyrCertificateError(ZephyrError):
    """TLS verification failed against the bundled TWCA CA set.

    The vendor's intermediate omits the Subject Key Identifier extension, so
    system trust stores reject it. The library ships its own CA bundle. If
    this fires, the vendor rotated to a chain the bundle does not cover.
    """


class ZephyrPolicyError(ZephyrError):
    """The IoT policy is not attached to this Cognito identity.

    Symptom: connect, subscribe and publish all succeed and every message is
    silently dropped. Call attach_policy() BEFORE connecting - an open
    connection does not pick up newly attached permissions.
    """


class ZephyrTransportError(ZephyrError):
    """MQTT connect, subscribe or publish failed."""
```

- [ ] **Step 8: Write `src/pyzephyrconnect/__init__.py`**

```python
"""Python client for Zephyr/Gemtek range hoods."""

from .exceptions import (
    ZephyrAuthError,
    ZephyrCertificateError,
    ZephyrError,
    ZephyrPolicyError,
    ZephyrTransportError,
)

__version__ = "0.1.0"

__all__ = [
    "ZephyrError",
    "ZephyrAuthError",
    "ZephyrCertificateError",
    "ZephyrPolicyError",
    "ZephyrTransportError",
    "__version__",
]
```

- [ ] **Step 9: Install and run the tests**

```bash
cd /Users/ryanmorash/Developer/pyzephyrconnect
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/test_const.py -v
```

Expected: 3 passed

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "feat: scaffold package with pinned constants and exceptions"
```

---

### Task 2: Capability and state models

**Files:**
- Create: `src/pyzephyrconnect/models.py`
- Create: `tests/fixtures/discoverdevice.json`, `tests/fixtures/shadow_get_accepted.json`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing
- Produces: `HoodCapabilities` (frozen dataclass, classmethod `from_discover(dict) -> HoodCapabilities`, fields `thing_name: str`, `serial: str`, `model: str`, `mac: str`, `manufacturer: str`, `max_fan_speed: int`, `max_light_level: int`, `supports_recirculating: bool`, `supports_tru_hue: bool`, `max_grease_filter_hours: int`, `max_charcoal_filter_hours: int`, `urls: dict[str, str]`); `HoodState` (frozen dataclass, classmethod `from_reported(dict) -> HoodState`, method `merge(dict) -> HoodState`, fields named after shadow keys, plus `raw: dict`)

- [ ] **Step 1: Write the fixtures**

Create `tests/fixtures/discoverdevice.json` — a real capture with identifiers replaced by placeholders:

```json
{
  "power": 0, "act": "Disabled", "light": 0, "fan": 0,
  "delaytimer": 0, "fanwarning": 0,
  "cleangreasefilters": 0, "cleancharcoalfilters": 0,
  "setrecirculating": 0, "setcleanairfunction": 0,
  "usegreasefiltertime": 642, "usecharcoalfiltertime": 0,
  "faultCode": [], "alarmfaultcode": 0,
  "setdelaytimer": 0, "alarmfan": 0,
  "resetgreasefilter": 0, "alarmgreasefilter": 0,
  "uselighttime": 2833, "usefantime": 1979, "isOnline": 1,
  "thingName": "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee",
  "SN": "1234567XYZ", "modelName": "AK7400AS",
  "MAC": "00:00:5e:00:53:00",
  "laborWarranty": "2026/02/10", "partsWarranty": "2028/02/10",
  "Recirculating": 1, "maxFanSpeed": 6, "maxLightLevel": 3,
  "maxCharcoalfilterTimer": 200, "maxGreasefilterTimer": 60,
  "CharcoalFilterVideoURL": "https://youtu.be/example",
  "CharcoalFilterWebstoreURL": "https://store.zephyronline.com/en/charcoal",
  "GreaseFilterVideoURL": "https://youtu.be/example2",
  "GreaseFilterWebstoreURL": "https://store.zephyronline.com/en/baffle",
  "HoodCleanVideoURL": "https://youtu.be/example3",
  "ProductPhotoURL": "https://zephyronline.com/photo.jpg",
  "UserManualURL": "http://docs.zephyronline.com/manual.pdf",
  "companyName": "ZEPHYR",
  "ContactURL": "https://zephyronline.com/contact",
  "FAQURL": "https://zephyronline.com/faq",
  "WarranyRegistrationURL": "https://store.zephyronline.com/en/reg",
  "truHueSupport": 0
}
```

Create `tests/fixtures/shadow_get_accepted.json`:

```json
{
  "state": {
    "reported": {
      "power": 0, "act": "Disabled", "light": 0, "fan": 0,
      "delaytimer": 0, "fanwarning": 0,
      "cleangreasefilters": 0, "cleancharcoalfilters": 0,
      "setrecirculating": 0, "setcleanairfunction": 0,
      "usegreasefiltertime": 642, "usecharcoalfiltertime": 0,
      "faultCode": [], "alarmfaultcode": 0,
      "setdelaytimer": 0, "alarmfan": 0,
      "resetgreasefilter": 0, "alarmgreasefilter": 0,
      "uselighttime": 2833, "usefantime": 1979, "isOnline": 1
    }
  },
  "metadata": { "reported": {} },
  "version": 302664,
  "timestamp": 1787540497
}
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_models.py`:

```python
import json
from pathlib import Path

import pytest

from pyzephyrconnect.models import HoodCapabilities, HoodState

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def discover() -> dict:
    return json.loads((FIXTURES / "discoverdevice.json").read_text())


@pytest.fixture
def shadow() -> dict:
    return json.loads((FIXTURES / "shadow_get_accepted.json").read_text())


def test_capabilities_parse_the_reference_device(discover):
    caps = HoodCapabilities.from_discover(discover)
    assert caps.max_fan_speed == 6
    assert caps.max_light_level == 3
    assert caps.supports_recirculating is True
    assert caps.supports_tru_hue is False
    assert caps.model == "AK7400AS"
    assert caps.manufacturer == "ZEPHYR"
    assert caps.max_grease_filter_hours == 60
    assert caps.max_charcoal_filter_hours == 200


def test_capabilities_collect_vendor_urls(discover):
    caps = HoodCapabilities.from_discover(discover)
    assert caps.urls["GreaseFilterWebstoreURL"].startswith("https://")
    assert "FAQURL" in caps.urls


def test_capabilities_tolerate_a_missing_optional_field(discover):
    """Other Zephyr models will not return every key. Absent capability
    must degrade to a safe default, not raise."""
    del discover["truHueSupport"]
    del discover["maxCharcoalfilterTimer"]
    caps = HoodCapabilities.from_discover(discover)
    assert caps.supports_tru_hue is False
    assert caps.max_charcoal_filter_hours == 0


def test_state_parses_reported_block(shadow):
    state = HoodState.from_reported(shadow["state"]["reported"])
    assert state.power == 0
    assert state.fan == 0
    assert state.act == "Disabled"
    assert state.use_grease_filter_time == 642
    assert state.is_online is True
    assert state.fault_codes == []


def test_state_merge_applies_a_partial_delta(shadow):
    """update/delta carries only changed keys. Merging must preserve the rest."""
    state = HoodState.from_reported(shadow["state"]["reported"])
    merged = state.merge({"fan": 3, "power": 1})
    assert merged.fan == 3
    assert merged.power == 1
    assert merged.use_grease_filter_time == 642, "unchanged keys must survive"
    assert state.fan == 0, "merge must not mutate the original"


def test_state_keeps_unknown_keys_in_raw(shadow):
    """A model we have never seen may report fields we do not model. They
    must survive into raw so diagnostics can surface them."""
    reported = dict(shadow["state"]["reported"])
    reported["somethingNew"] = 42
    state = HoodState.from_reported(reported)
    assert state.raw["somethingNew"] == 42
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyzephyrconnect.models'`

- [ ] **Step 4: Write `src/pyzephyrconnect/models.py`**

```python
"""Typed views over the vendor's untyped JSON.

Both models keep the original payload in `raw`. Field semantics are only
partially understood, so discarding unmodelled keys would destroy the
evidence needed to characterise them later.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

_URL_KEYS = (
    "CharcoalFilterVideoURL",
    "CharcoalFilterWebstoreURL",
    "GreaseFilterVideoURL",
    "GreaseFilterWebstoreURL",
    "HoodCleanVideoURL",
    "ProductPhotoURL",
    "UserManualURL",
    "ContactURL",
    "FAQURL",
    "WarranyRegistrationURL",
)


@dataclass(frozen=True, slots=True)
class HoodCapabilities:
    """What a specific hood can do, from the discoverdevice endpoint.

    Entity creation is gated on these rather than on the model string, so
    the library generalises to Zephyr hoods we have never seen.
    """

    thing_name: str
    serial: str
    model: str
    mac: str
    manufacturer: str
    max_fan_speed: int
    max_light_level: int
    supports_recirculating: bool
    supports_tru_hue: bool
    max_grease_filter_hours: int
    max_charcoal_filter_hours: int
    labor_warranty: str
    parts_warranty: str
    urls: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_discover(cls, payload: dict[str, Any]) -> HoodCapabilities:
        return cls(
            thing_name=str(payload.get("thingName", "")),
            serial=str(payload.get("SN", "")),
            model=str(payload.get("modelName", "")),
            mac=str(payload.get("MAC", "")),
            manufacturer=str(payload.get("companyName", "")),
            max_fan_speed=int(payload.get("maxFanSpeed", 0)),
            max_light_level=int(payload.get("maxLightLevel", 0)),
            supports_recirculating=bool(payload.get("Recirculating", 0)),
            supports_tru_hue=bool(payload.get("truHueSupport", 0)),
            max_grease_filter_hours=int(payload.get("maxGreasefilterTimer", 0)),
            max_charcoal_filter_hours=int(
                payload.get("maxCharcoalfilterTimer", 0)
            ),
            labor_warranty=str(payload.get("laborWarranty", "")),
            parts_warranty=str(payload.get("partsWarranty", "")),
            urls={k: payload[k] for k in _URL_KEYS if payload.get(k)},
            raw=dict(payload),
        )


@dataclass(frozen=True, slots=True)
class HoodState:
    """Current shadow state.

    Field semantics are documented where known. `act` and the exact units of
    the use*time counters are unverified - see PROTOCOL.md section 7.
    """

    power: int = 0
    light: int = 0
    fan: int = 0
    act: str = ""
    delay_timer: int = 0
    set_delay_timer: int = 0
    set_recirculating: int = 0
    set_clean_air_function: int = 0
    clean_grease_filters: int = 0
    clean_charcoal_filters: int = 0
    use_grease_filter_time: int = 0
    use_charcoal_filter_time: int = 0
    use_light_time: int = 0
    use_fan_time: int = 0
    fan_warning: int = 0
    alarm_fan: int = 0
    alarm_fault_code: int = 0
    alarm_grease_filter: int = 0
    is_online: bool = False
    fault_codes: tuple[Any, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_reported(cls, reported: dict[str, Any]) -> HoodState:
        def as_int(key: str) -> int:
            try:
                return int(reported.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0

        return cls(
            power=as_int("power"),
            light=as_int("light"),
            fan=as_int("fan"),
            act=str(reported.get("act", "")),
            delay_timer=as_int("delaytimer"),
            set_delay_timer=as_int("setdelaytimer"),
            set_recirculating=as_int("setrecirculating"),
            set_clean_air_function=as_int("setcleanairfunction"),
            clean_grease_filters=as_int("cleangreasefilters"),
            clean_charcoal_filters=as_int("cleancharcoalfilters"),
            use_grease_filter_time=as_int("usegreasefiltertime"),
            use_charcoal_filter_time=as_int("usecharcoalfiltertime"),
            use_light_time=as_int("uselighttime"),
            use_fan_time=as_int("usefantime"),
            fan_warning=as_int("fanwarning"),
            alarm_fan=as_int("alarmfan"),
            alarm_fault_code=as_int("alarmfaultcode"),
            alarm_grease_filter=as_int("alarmgreasefilter"),
            is_online=bool(as_int("isOnline")),
            fault_codes=tuple(reported.get("faultCode") or ()),
            raw=dict(reported),
        )

    def merge(self, delta: dict[str, Any]) -> HoodState:
        """Return a new state with `delta` applied over the raw payload.

        update/delta and update/accepted carry only changed keys, so a
        replace-the-whole-object approach would silently zero everything the
        device did not mention.
        """
        merged_raw = {**self.raw, **delta}
        return replace(HoodState.from_reported(merged_raw))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_models.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add capability and state models with partial-delta merge"
```

---

### Task 3: SigV4 presigned WebSocket URL

This is the only module with no counterpart in the verified prototype, which used `awsiotsdk` to do this internally. It is therefore the highest-risk code in the library, and is deliberately isolated as a pure function with no network access.

**Files:**
- Create: `src/pyzephyrconnect/presign.py`
- Test: `tests/test_presign.py`

**Interfaces:**
- Consumes: `const.IOT_ENDPOINT`, `const.IOT_SERVICE`, `const.REGION`
- Produces: `build_presigned_url(access_key: str, secret_key: str, session_token: str | None, *, endpoint: str, region: str, now: datetime) -> str` returning a full `wss://` URL

- [ ] **Step 1: Write the failing test**

Create `tests/test_presign.py`:

```python
"""Tests for SigV4 WebSocket presigning.

There is no published AWS test vector for iotdevicegateway WebSocket
presigning, so these tests pin the canonical request (which is
hand-verifiable against the SigV4 specification), determinism, and the
structural invariants that break real connections. End-to-end proof comes
from the live connect in Task 8.
"""

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from pyzephyrconnect.presign import build_presigned_url, canonical_request

ENDPOINT = "a1nqxu0hki9zw3-ats.iot.us-west-2.amazonaws.com"
NOW = datetime(2026, 8, 23, 12, 0, 0, tzinfo=UTC)
KEY = "AKIDEXAMPLE"
SECRET = "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
TOKEN = "SESSIONTOKEN/with+special=chars"


def _url(**kw) -> str:
    params = dict(
        access_key=KEY, secret_key=SECRET, session_token=TOKEN,
        endpoint=ENDPOINT, region="us-west-2", now=NOW,
    )
    params.update(kw)
    return build_presigned_url(**params)


def test_url_shape():
    url = _url()
    parts = urlsplit(url)
    assert parts.scheme == "wss"
    assert parts.netloc == ENDPOINT
    assert parts.path == "/mqtt"


def test_required_query_parameters_present():
    q = parse_qs(urlsplit(_url()).query)
    assert q["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert q["X-Amz-SignedHeaders"] == ["host"]
    assert q["X-Amz-Date"] == ["20260823T120000Z"]
    assert q["X-Amz-Credential"] == [
        f"{KEY}/20260823/us-west-2/iotdevicegateway/aws4_request"
    ]
    assert len(q["X-Amz-Signature"][0]) == 64
    assert q["X-Amz-Security-Token"] == [TOKEN]


def test_security_token_is_excluded_from_the_signature():
    """AWS IoT requires the session token be appended AFTER signing.
    Including it in the canonical query string produces a signature the
    broker rejects, and the failure looks like a generic handshake error."""
    with_token = parse_qs(urlsplit(_url()).query)["X-Amz-Signature"][0]
    without = parse_qs(
        urlsplit(_url(session_token=None)).query
    )["X-Amz-Signature"][0]
    assert with_token == without


def test_signature_is_deterministic():
    assert _url() == _url()


@pytest.mark.parametrize(
    "override",
    [
        {"secret_key": "different-secret"},
        {"access_key": "AKIDOTHER"},
        {"region": "us-east-1"},
        {"now": datetime(2026, 8, 23, 12, 0, 1, tzinfo=UTC)},
    ],
)
def test_signature_changes_when_any_signed_input_changes(override):
    base = parse_qs(urlsplit(_url()).query)["X-Amz-Signature"][0]
    other = parse_qs(urlsplit(_url(**override)).query)["X-Amz-Signature"][0]
    assert base != other


def test_canonical_request_matches_sigv4_specification():
    """Hand-verifiable against the SigV4 spec: method, URI, sorted query,
    canonical headers terminated by a newline, a blank line, signed headers,
    then the SHA-256 of an empty payload."""
    cr = canonical_request(
        access_key=KEY, endpoint=ENDPOINT, region="us-west-2", now=NOW
    )
    lines = cr.split("\n")
    assert lines[0] == "GET"
    assert lines[1] == "/mqtt"
    assert lines[2].startswith("X-Amz-Algorithm=AWS4-HMAC-SHA256&")
    assert "X-Amz-Security-Token" not in lines[2]
    assert lines[3] == f"host:{ENDPOINT}"
    assert lines[4] == ""
    assert lines[5] == "host"
    assert lines[6] == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_query_string_is_sorted():
    """SigV4 requires lexicographically sorted parameters. An unsorted
    canonical query produces a valid-looking but rejected signature."""
    cr = canonical_request(
        access_key=KEY, endpoint=ENDPOINT, region="us-west-2", now=NOW
    )
    qs = cr.split("\n")[2]
    keys = [p.split("=")[0] for p in qs.split("&")]
    assert keys == sorted(keys)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_presign.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyzephyrconnect.presign'`

- [ ] **Step 3: Write `src/pyzephyrconnect/presign.py`**

```python
"""SigV4 presigning for AWS IoT Core WebSocket connections.

Pure and stdlib-only by design: no network, no clock of its own, no
credentials provider. `now` is a parameter so the tests are deterministic.

The one non-obvious rule: X-Amz-Security-Token is appended AFTER the
signature is computed and is NOT part of the canonical query string. Signing
over it yields a signature the broker rejects with an opaque handshake
error.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime
from urllib.parse import quote

ALGORITHM = "AWS4-HMAC-SHA256"
CANONICAL_URI = "/mqtt"
SIGNED_HEADERS = "host"
SERVICE = "iotdevicegateway"
# SHA-256 of the empty string; a presigned GET has no body.
EMPTY_PAYLOAD_HASH = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
# RFC 3986 unreserved characters. urllib's default safe set is "/", which is
# wrong for canonical query encoding.
_SAFE = "-_.~"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret_key: str, datestamp: str, region: str) -> bytes:
    k_date = _hmac(f"AWS4{secret_key}".encode("utf-8"), datestamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, SERVICE)
    return _hmac(k_service, "aws4_request")


def _query_params(access_key: str, region: str, now: datetime) -> dict[str, str]:
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    datestamp = now.strftime("%Y%m%d")
    scope = f"{datestamp}/{region}/{SERVICE}/aws4_request"
    return {
        "X-Amz-Algorithm": ALGORITHM,
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-SignedHeaders": SIGNED_HEADERS,
    }


def _canonical_query(params: dict[str, str]) -> str:
    return "&".join(
        f"{quote(k, safe=_SAFE)}={quote(v, safe=_SAFE)}"
        for k, v in sorted(params.items())
    )


def canonical_request(
    *, access_key: str, endpoint: str, region: str, now: datetime
) -> str:
    """Build the SigV4 canonical request. Exposed for testing."""
    return "\n".join(
        [
            "GET",
            CANONICAL_URI,
            _canonical_query(_query_params(access_key, region, now)),
            f"host:{endpoint}\n",
            SIGNED_HEADERS,
            EMPTY_PAYLOAD_HASH,
        ]
    )


def build_presigned_url(
    access_key: str,
    secret_key: str,
    session_token: str | None,
    *,
    endpoint: str,
    region: str,
    now: datetime,
) -> str:
    """Return a `wss://` URL authorising an MQTT connection to AWS IoT."""
    datestamp = now.strftime("%Y%m%d")
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    scope = f"{datestamp}/{region}/{SERVICE}/aws4_request"

    params = _query_params(access_key, region, now)
    query = _canonical_query(params)

    string_to_sign = "\n".join(
        [
            ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(
                canonical_request(
                    access_key=access_key, endpoint=endpoint,
                    region=region, now=now,
                ).encode("utf-8")
            ).hexdigest(),
        ]
    )

    signature = hmac.new(
        _signing_key(secret_key, datestamp, region),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    query = f"{query}&X-Amz-Signature={signature}"
    if session_token:
        # Appended after signing - see module docstring.
        query = f"{query}&X-Amz-Security-Token={quote(session_token, safe=_SAFE)}"

    return f"wss://{endpoint}{CANONICAL_URI}?{query}"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_presign.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add SigV4 presigned WebSocket URL builder"
```

---

### Task 4: Pinned TLS bundle and REST API client

**Files:**
- Create: `src/pyzephyrconnect/certs/twca.pem` (generated, then committed)
- Create: `src/pyzephyrconnect/api.py`
- Test: `tests/test_api.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `const.DEVICE_API_LIST`, `const.DEVICE_API_DISCOVER`, `exceptions.ZephyrCertificateError`
- Produces: `build_ssl_context() -> ssl.SSLContext`; `ZephyrApi(session: aiohttp.ClientSession, ssl_context: ssl.SSLContext | None = None)` with `async get_own_devices(id_token: str) -> list[dict]` and `async discover_device(id_token: str, thing_name: str) -> dict`

- [ ] **Step 1: Generate the CA-only bundle**

The vendor's intermediate omits the Subject Key Identifier extension, so OpenSSL 3.x rejects the chain. Supplying the CA certificates as trust anchors resolves it. Keep only the TWCA certificates — excluding the leaf is what moves expiry from 2026-10-15 to 2030 and survives vendor leaf rotation.

```bash
cd /Users/ryanmorash/Developer/pyzephyrconnect
SRC=/Users/ryanmorash/Developer/ha_zephyr/gemtek-chain.pem
DEST=src/pyzephyrconnect/certs/twca.pem
rm -f "$DEST" /tmp/zsplit-*.pem
csplit -sz -f /tmp/zsplit- -b '%02d.pem' "$SRC" '/-----BEGIN CERTIFICATE-----/' '{*}'
for f in /tmp/zsplit-*.pem; do
  # The leaf's SUBJECT is GEMTEK; only the CAs have TAIWAN-CA as subject.
  if openssl x509 -in "$f" -noout -subject 2>/dev/null | grep -q "TAIWAN-CA"; then
    openssl x509 -in "$f" >> "$DEST"
  fi
done
rm -f /tmp/zsplit-*.pem
```

- [ ] **Step 2: Verify the bundle**

```bash
grep -c "BEGIN CERTIFICATE" src/pyzephyrconnect/certs/twca.pem
openssl crl2pkcs7 -nocrl -certfile src/pyzephyrconnect/certs/twca.pem \
  | openssl pkcs7 -print_certs -noout
```

Expected: `3`, and three subjects all containing `TAIWAN-CA` — TWCA Root Certification Authority, TWCA Global Root CA, TWCA Secure SSL Certification Authority. No `GEMTEK` subject may appear; if one does, the leaf leaked in and the expiry problem is back.

- [ ] **Step 3: Write the shared test helpers**

Create `tests/conftest.py`:

```python
"""Fake aiohttp objects. No test in this suite touches the network."""

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return json.dumps(self._payload)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Records calls and returns queued responses. `post` returns an async
    context manager, matching aiohttp rather than being a coroutine."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if not self._responses:
            raise AssertionError(f"unexpected POST to {url}")
        return self._responses.pop(0)


@pytest.fixture
def discover_payload() -> dict:
    return json.loads((FIXTURES / "discoverdevice.json").read_text())
```

- [ ] **Step 4: Write the failing test**

Create `tests/test_api.py`:

```python
import ssl

import aiohttp
import pytest

from conftest import FakeResponse, FakeSession
from pyzephyrconnect import const
from pyzephyrconnect.api import ZephyrApi, build_ssl_context
from pyzephyrconnect.exceptions import ZephyrCertificateError

TOKEN = "id-token-value"
THING = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"


def test_ssl_context_loads_the_bundled_cas():
    ctx = build_ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert len(ctx.get_ca_certs()) == 3


def test_ssl_context_cas_outlive_the_old_leaf_pin():
    """The whole point of pinning CAs rather than the leaf: validity must
    extend past 2026-10-15, when the vendor's leaf expires."""
    for cert in build_ssl_context().get_ca_certs():
        assert cert["notAfter"].endswith("2030 GMT")


async def test_get_own_devices_sends_a_bare_token_and_empty_body():
    """The vendor API takes the raw ID token with NO 'Bearer ' prefix and a
    genuinely empty body - not '{}'. Both matter."""
    session = FakeSession(
        FakeResponse({"message": "Success", "devices": [{"thingName": THING}]})
    )
    api = ZephyrApi(session)
    devices = await api.get_own_devices(TOKEN)

    assert devices == [{"thingName": THING}]
    call = session.calls[0]
    assert call["url"] == const.DEVICE_API_LIST
    assert call["headers"]["Authorization"] == TOKEN
    assert not call["headers"]["Authorization"].startswith("Bearer")
    assert call["data"] == b""


async def test_discover_device_posts_the_thing_name():
    session = FakeSession(FakeResponse({"maxFanSpeed": 6}))
    api = ZephyrApi(session)
    result = await api.discover_device(TOKEN, THING)

    assert result == {"maxFanSpeed": 6}
    assert session.calls[0]["url"] == const.DEVICE_API_DISCOVER
    assert session.calls[0]["json"] == {"thingName": THING}


async def test_requests_pass_the_pinned_ssl_context():
    ctx = build_ssl_context()
    session = FakeSession(FakeResponse({"devices": []}))
    await ZephyrApi(session, ctx).get_own_devices(TOKEN)
    assert session.calls[0]["ssl"] is ctx


async def test_certificate_failure_raises_an_actionable_error():
    """A generic SSLError here sends the operator hunting through their
    system trust store. Name the bundle instead."""

    class ExplodingSession:
        def post(self, url, **kwargs):
            raise aiohttp.ClientConnectorCertificateError(
                connection_key=None,
                certificate_error=ssl.SSLCertVerificationError("bad chain"),
            )

    with pytest.raises(ZephyrCertificateError, match="twca.pem"):
        await ZephyrApi(ExplodingSession()).get_own_devices(TOKEN)


async def test_missing_devices_key_returns_empty_list():
    session = FakeSession(FakeResponse({"message": "Success"}))
    assert await ZephyrApi(session).get_own_devices(TOKEN) == []
```

- [ ] **Step 5: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyzephyrconnect.api'`

- [ ] **Step 6: Write `src/pyzephyrconnect/api.py`**

```python
"""Vendor REST endpoints.

The session is injected rather than owned, so Home Assistant can pass its
shared client session. The pinned SSL context is applied per request, which
means a shared session needs no special construction.
"""

from __future__ import annotations

import logging
import ssl
from importlib import resources
from typing import Any

import aiohttp

from . import const
from .exceptions import ZephyrCertificateError, ZephyrError

_LOGGER = logging.getLogger(__name__)

CERT_BUNDLE = "twca.pem"
# Validity of the bundled CA set; surfaced in the error message so an
# operator hitting this in 2030 knows immediately what expired.
CERT_BUNDLE_EXPIRY = "2030"


def build_ssl_context() -> ssl.SSLContext:
    """SSL context trusting the bundled TWCA CA set.

    The vendor's intermediate omits the Subject Key Identifier extension and
    is rejected by OpenSSL 3.x under system trust. Loading the CAs as trust
    anchors satisfies verification without weakening it - verify_mode stays
    CERT_REQUIRED and hostname checking stays on.
    """
    ctx = ssl.create_default_context()
    with resources.as_file(
        resources.files("pyzephyrconnect.certs").joinpath(CERT_BUNDLE)
    ) as path:
        ctx.load_verify_locations(cafile=str(path))
    return ctx


class ZephyrApi:
    """Client for the vendor's two REST endpoints."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._session = session
        self._ssl = ssl_context if ssl_context is not None else build_ssl_context()

    def _headers(self, id_token: str) -> dict[str, str]:
        # Bare token, no "Bearer " prefix - the API rejects the prefixed form.
        return {
            "Authorization": id_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _post(self, url: str, id_token: str, **kwargs: Any) -> Any:
        try:
            async with self._session.post(
                url, headers=self._headers(id_token), ssl=self._ssl, **kwargs
            ) as response:
                if response.status == 403:
                    raise ZephyrError(
                        f"{url} returned 403 - the ID token is rejected or expired"
                    )
                if response.status >= 400:
                    raise ZephyrError(f"{url} returned HTTP {response.status}")
                # The API sends text/plain for some responses.
                return await response.json(content_type=None)
        except aiohttp.ClientConnectorCertificateError as err:
            raise ZephyrCertificateError(
                f"TLS verification failed for {url}. The bundled CA set "
                f"({CERT_BUNDLE}, valid to {CERT_BUNDLE_EXPIRY}) does not "
                "cover the presented chain - the vendor likely rotated CAs. "
                "Recapture the chain; do not disable verification."
            ) from err

    async def get_own_devices(self, id_token: str) -> list[dict[str, Any]]:
        """Return the caller's devices.

        Note: the response includes precise device coordinates. Treat the
        payload as personal data and never log it.
        """
        # Empty body, not "{}" - matches the captured request exactly.
        payload = await self._post(const.DEVICE_API_LIST, id_token, data=b"")
        devices = payload.get("devices") or []
        _LOGGER.debug("getowndevices returned %d device(s)", len(devices))
        return devices

    async def discover_device(
        self, id_token: str, thing_name: str
    ) -> dict[str, Any]:
        """Return capabilities merged with current state for one thing."""
        return await self._post(
            const.DEVICE_API_DISCOVER, id_token, json={"thingName": thing_name}
        )
```

- [ ] **Step 7: Make `conftest` importable and run the tests**

Add to `pyproject.toml` under `[tool.pytest.ini_options]`:

```toml
pythonpath = ["tests"]
```

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: 7 passed

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: add pinned CA bundle and vendor REST client"
```

---

### Task 5: Authentication

**Files:**
- Create: `src/pyzephyrconnect/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `const.*`, `exceptions.ZephyrAuthError`, `exceptions.ZephyrPolicyError`
- Produces: `Credentials` (frozen dataclass: `access_key: str`, `secret_key: str`, `session_token: str`, `expiration: datetime`, property `expired: bool`); `ZephyrAuth(username: str, password: str)` with `async authenticate() -> None`, `async refresh() -> None`, `async attach_policy() -> None`, properties `id_token: str`, `identity_id: str`, `credentials: Credentials`, `mqtt_client_id: str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_auth.py`:

```python
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from pyzephyrconnect import auth as auth_module
from pyzephyrconnect.auth import Credentials, ZephyrAuth
from pyzephyrconnect.exceptions import ZephyrAuthError

IDENTITY = "us-west-2:00000000-1111-2222-3333-444455556666"


def _creds_response(expires_in_seconds=3600):
    return {
        "Credentials": {
            "AccessKeyId": "AKIA",
            # Note: SecretKey, NOT SecretAccessKey. This differs from STS and
            # is a documented trap in PROTOCOL.md section 3.2.
            "SecretKey": "SECRET",
            "SessionToken": "TOKEN",
            "Expiration": datetime.now(UTC)
            + timedelta(seconds=expires_in_seconds),
        }
    }


@pytest.fixture
def fake_aws(monkeypatch):
    """Replace pycognito and boto3 with recording doubles."""
    cognito = MagicMock()
    cognito.id_token = "ID-TOKEN"
    monkeypatch.setattr(auth_module, "Cognito", MagicMock(return_value=cognito))

    identity = MagicMock()
    identity.get_id.return_value = {"IdentityId": IDENTITY}
    identity.get_credentials_for_identity.return_value = _creds_response()

    iot = MagicMock()
    iot.list_attached_policies.return_value = {"policies": []}

    def client(service, **kwargs):
        return {"cognito-identity": identity, "iot": iot}[service]

    monkeypatch.setattr(auth_module.boto3, "client", MagicMock(side_effect=client))
    return {"cognito": cognito, "identity": identity, "iot": iot}


async def test_authenticate_runs_srp_and_exchanges_credentials(fake_aws):
    a = ZephyrAuth("user@example.com", "pw")
    await a.authenticate()

    fake_aws["cognito"].authenticate.assert_called_once_with(password="pw")
    assert a.id_token == "ID-TOKEN"
    assert a.credentials.secret_key == "SECRET"


async def test_user_pool_region_is_passed_explicitly(fake_aws):
    """Without it pycognito falls back to ambient AWS config and raises a
    misleading ResourceNotFoundException."""
    await ZephyrAuth("u", "p").authenticate()
    kwargs = auth_module.Cognito.call_args.kwargs
    assert kwargs["user_pool_region"] == "us-west-2"
    assert kwargs["client_secret"], "SRP fails without the client secret"


async def test_identity_id_keeps_its_region_prefix(fake_aws):
    """The full 'us-west-2:uuid' is what the IoT policy variable resolves to
    and is the correct MQTT client ID base. Stripping it breaks delivery."""
    a = ZephyrAuth("u", "p")
    await a.authenticate()
    assert a.identity_id == IDENTITY
    assert a.identity_id.startswith("us-west-2:")


async def test_mqtt_client_id_is_suffixed(fake_aws):
    """A bare identity ID collides with the phone app and the two sessions
    evict each other in a reconnect loop."""
    a = ZephyrAuth("u", "p")
    await a.authenticate()
    assert a.mqtt_client_id == f"{IDENTITY}-ha"


async def test_attach_policy_is_skipped_when_already_attached(fake_aws):
    fake_aws["iot"].list_attached_policies.return_value = {
        "policies": [{"policyName": "RangeHoodPolicy"}]
    }
    a = ZephyrAuth("u", "p")
    await a.authenticate()
    await a.attach_policy()
    fake_aws["iot"].attach_policy.assert_not_called()


async def test_attach_policy_attaches_when_missing(fake_aws):
    a = ZephyrAuth("u", "p")
    await a.authenticate()
    await a.attach_policy()
    fake_aws["iot"].attach_policy.assert_called_once_with(
        policyName="RangeHoodPolicy", target=IDENTITY
    )


async def test_refresh_renews_without_a_full_srp_round_trip(fake_aws):
    """Re-running SRP costs multiple round trips and the pool may rate-limit."""
    a = ZephyrAuth("u", "p")
    await a.authenticate()
    fake_aws["cognito"].authenticate.reset_mock()

    await a.refresh()

    fake_aws["cognito"].renew_access_token.assert_called_once()
    fake_aws["cognito"].authenticate.assert_not_called()
    # get_id is only valid once; the identity must be reused.
    assert fake_aws["identity"].get_id.call_count == 1


async def test_authentication_failure_is_wrapped(fake_aws):
    fake_aws["cognito"].authenticate.side_effect = Exception("Incorrect username")
    with pytest.raises(ZephyrAuthError):
        await ZephyrAuth("u", "bad").authenticate()


def test_credentials_expire_early_by_the_refresh_margin():
    """Reporting 'valid' until the last second guarantees a mid-flight
    expiry, because rebuilding the socket is not instant."""
    nearly = Credentials(
        "k", "s", "t", datetime.now(UTC) + timedelta(seconds=60)
    )
    plenty = Credentials(
        "k", "s", "t", datetime.now(UTC) + timedelta(seconds=3600)
    )
    assert nearly.expired is True
    assert plenty.expired is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_auth.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyzephyrconnect.auth'`

- [ ] **Step 3: Write `src/pyzephyrconnect/auth.py`**

```python
"""Cognito authentication and AWS IoT policy attachment.

pycognito and boto3 are synchronous. Every blocking call here is wrapped in
asyncio.to_thread so callers get a purely async surface. Auth runs roughly
once an hour, so the thread hop costs nothing.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
from botocore import UNSIGNED
from botocore.config import Config
from pycognito import Cognito

from . import const
from .exceptions import ZephyrAuthError, ZephyrPolicyError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Credentials:
    access_key: str
    secret_key: str
    session_token: str
    expiration: datetime

    @property
    def expired(self) -> bool:
        """True once inside the refresh margin.

        Deliberately pessimistic: rebuilding the MQTT socket takes time, and
        credentials that expire mid-handshake fail opaquely.
        """
        margin = timedelta(seconds=const.REFRESH_MARGIN_SECONDS)
        return datetime.now(UTC) >= (self.expiration - margin)


class ZephyrAuth:
    """Owns the Cognito session and the derived AWS credentials."""

    def __init__(self, username: str, password: str) -> None:
        self._username = username
        self._password = password
        self._user: Cognito | None = None
        self._identity_id: str | None = None
        self._credentials: Credentials | None = None

    @property
    def id_token(self) -> str:
        if self._user is None:
            raise ZephyrAuthError("authenticate() has not been called")
        return self._user.id_token

    @property
    def identity_id(self) -> str:
        if self._identity_id is None:
            raise ZephyrAuthError("authenticate() has not been called")
        return self._identity_id

    @property
    def credentials(self) -> Credentials:
        if self._credentials is None:
            raise ZephyrAuthError("authenticate() has not been called")
        return self._credentials

    @property
    def mqtt_client_id(self) -> str:
        """Identity ID plus a stable suffix.

        The IoT policy pins the client ID to the identity. Using the bare
        identity ID makes this library and the phone app evict each other.
        """
        return f"{self.identity_id}{const.CLIENT_ID_SUFFIX}"

    # -- blocking bodies, run in a worker thread ----------------------

    def _srp_login(self) -> Cognito:
        user = Cognito(
            const.USER_POOL,
            const.CLIENT_ID,
            client_secret=const.CLIENT_SECRET,
            username=self._username,
            # Must be explicit; otherwise pycognito reads ambient AWS config
            # and raises a confusing ResourceNotFoundException.
            user_pool_region=const.REGION,
        )
        user.authenticate(password=self._password)
        return user

    def _exchange(self) -> tuple[str, Credentials]:
        client = boto3.client(
            "cognito-identity",
            region_name=const.REGION,
            config=Config(signature_version=UNSIGNED),
        )
        logins = {const.PROVIDER: self.id_token}
        identity_id = self._identity_id or client.get_id(
            IdentityPoolId=const.IDENTITY_POOL, Logins=logins
        )["IdentityId"]
        raw = client.get_credentials_for_identity(
            IdentityId=identity_id, Logins=logins
        )["Credentials"]
        return identity_id, Credentials(
            access_key=raw["AccessKeyId"],
            # "SecretKey", not "SecretAccessKey" - differs from STS.
            secret_key=raw["SecretKey"],
            session_token=raw["SessionToken"],
            expiration=raw["Expiration"],
        )

    def _attach(self) -> None:
        creds = self.credentials
        client = boto3.client(
            "iot",
            region_name=const.REGION,
            aws_access_key_id=creds.access_key,
            aws_secret_access_key=creds.secret_key,
            aws_session_token=creds.session_token,
        )
        try:
            attached = client.list_attached_policies(target=self.identity_id)
            names = [p["policyName"] for p in attached.get("policies", [])]
            if const.POLICY_NAME in names:
                return
        except Exception:  # noqa: BLE001 - listing is best-effort
            _LOGGER.debug("list_attached_policies failed; attaching anyway")

        try:
            client.attach_policy(
                policyName=const.POLICY_NAME, target=self.identity_id
            )
        except Exception as err:  # noqa: BLE001
            raise ZephyrPolicyError(
                f"Could not attach {const.POLICY_NAME} to {self.identity_id}. "
                "Without it the MQTT connection succeeds but every message is "
                "silently dropped."
            ) from err

    # -- async surface -------------------------------------------------

    async def authenticate(self) -> None:
        try:
            self._user = await asyncio.to_thread(self._srp_login)
        except Exception as err:  # noqa: BLE001
            raise ZephyrAuthError(f"Cognito authentication failed: {err}") from err
        self._identity_id, self._credentials = await asyncio.to_thread(
            self._exchange
        )
        _LOGGER.debug("authenticated; credentials expire %s",
                      self._credentials.expiration)

    async def refresh(self) -> None:
        """Renew tokens and re-exchange. Cheaper than a full SRP login."""
        if self._user is None:
            raise ZephyrAuthError("authenticate() has not been called")
        try:
            await asyncio.to_thread(self._user.renew_access_token)
        except Exception as err:  # noqa: BLE001
            raise ZephyrAuthError(f"Token renewal failed: {err}") from err
        self._identity_id, self._credentials = await asyncio.to_thread(
            self._exchange
        )

    async def attach_policy(self) -> None:
        """Bind the IoT policy to this identity.

        MUST run before connecting. An open MQTT connection does not pick up
        newly attached permissions.
        """
        await asyncio.to_thread(self._attach)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_auth.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add Cognito auth with identity exchange and policy attach"
```

---

### Task 6: MQTT shadow transport

**Files:**
- Create: `src/pyzephyrconnect/shadow.py`
- Test: `tests/test_shadow.py`

**Interfaces:**
- Consumes: `presign.build_presigned_url`, `auth.Credentials`, `exceptions.ZephyrTransportError`, `exceptions.ZephyrPolicyError`
- Produces: `ShadowTopics(thing_name: str)` with properties `get`, `get_accepted`, `get_rejected`, `update`, `update_accepted`, `update_rejected`, `update_delta`, `update_documents`, and `subscriptions: tuple[str, ...]`; `ShadowClient(thing_name: str, client_id: str, on_message: Callable[[str, dict], None], on_connection_change: Callable[[bool], None])` with `async connect(credentials) -> None`, `async disconnect() -> None`, `async request_state() -> None`, `async publish_desired(fields: dict) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_shadow.py`:

```python
import asyncio
import json
from contextlib import nullcontext
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from pyzephyrconnect import shadow as shadow_module
from pyzephyrconnect.auth import Credentials
from pyzephyrconnect.exceptions import ZephyrPolicyError
from pyzephyrconnect.shadow import ShadowClient, ShadowTopics

THING = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
CREDS = Credentials("AKIA", "SECRET", "TOKEN", datetime(2030, 1, 1, tzinfo=UTC))


def test_topics_are_built_from_the_thing_name():
    t = ShadowTopics(THING)
    assert t.get == f"$aws/things/{THING}/shadow/get"
    assert t.update == f"$aws/things/{THING}/shadow/update"
    assert t.update_delta == f"$aws/things/{THING}/shadow/update/delta"


def test_subscription_set_covers_reads_and_rejections():
    subs = ShadowTopics(THING).subscriptions
    assert f"$aws/things/{THING}/shadow/get/accepted" in subs
    assert f"$aws/things/{THING}/shadow/update/delta" in subs
    assert f"$aws/things/{THING}/shadow/update/rejected" in subs
    # The write topic itself is published to, never subscribed.
    assert f"$aws/things/{THING}/shadow/update" not in subs


@pytest.fixture
def fake_paho(monkeypatch):
    client = MagicMock()
    client.subscribe.return_value = (0, 1)
    client.publish.return_value = MagicMock(rc=0)

    def fire_connack(*args, **kwargs):
        # Real paho invokes on_connect from its network thread once CONNACK
        # arrives. Without this the mock never fires it, connect() blocks on
        # its event and every connect test fails on a 15s timeout.
        client.on_connect(client, None, {}, 0, None)

    client.connect_async.side_effect = fire_connack
    monkeypatch.setattr(
        shadow_module.mqtt, "Client", MagicMock(return_value=client)
    )
    return client


def _make(on_message=None):
    return ShadowClient(
        THING, f"{THING}-ha", on_message or MagicMock(), MagicMock()
    )


async def test_connect_uses_a_presigned_websocket_path(fake_paho):
    sc = _make()
    await sc.connect(CREDS)

    path = fake_paho.ws_set_options.call_args.kwargs["path"]
    assert path.startswith("/mqtt?X-Amz-Algorithm=AWS4-HMAC-SHA256")
    assert "X-Amz-Signature=" in path
    assert "X-Amz-Security-Token=" in path
    fake_paho.tls_set.assert_called_once()


async def test_connect_uses_the_suffixed_client_id(fake_paho):
    await _make().connect(CREDS)
    assert shadow_module.mqtt.Client.call_args.kwargs["client_id"] == f"{THING}-ha"


async def test_connect_targets_port_443(fake_paho):
    await _make().connect(CREDS)
    args = fake_paho.connect_async.call_args.args
    assert args[1] == 443


@pytest.mark.parametrize(
    ("is_failure", "expectation"),
    [
        (True, pytest.raises(ZephyrPolicyError, match="attach")),
        (False, nullcontext()),
    ],
    ids=["denied", "granted"],
)
def test_subscribe_grant_is_validated(fake_paho, is_failure, expectation):
    """paho reports success for a subscribe the broker refused. Granted QoS
    128 means denied, and the usual cause is a missing IoT policy. Without
    this check it presents as a working connection that receives nothing."""
    sc = _make()
    code = MagicMock()
    code.is_failure = is_failure
    with expectation:
        sc._on_subscribe(fake_paho, None, 1, [code], None)


async def test_request_state_publishes_an_empty_get(fake_paho):
    sc = _make()
    await sc.connect(CREDS)
    await sc.request_state()

    topic, payload = fake_paho.publish.call_args.args[:2]
    assert topic == f"$aws/things/{THING}/shadow/get"
    assert json.loads(payload) == {}


async def test_publish_desired_wraps_fields_in_state_desired(fake_paho):
    sc = _make()
    await sc.connect(CREDS)
    await sc.publish_desired({"light": 1})

    topic, payload = fake_paho.publish.call_args.args[:2]
    assert topic == f"$aws/things/{THING}/shadow/update"
    assert json.loads(payload) == {"state": {"desired": {"light": 1}}}


async def test_publish_desired_rejects_an_empty_payload(fake_paho):
    sc = _make()
    await sc.connect(CREDS)
    with pytest.raises(ValueError):
        await sc.publish_desired({})


async def test_reconnect_uses_capped_exponential_backoff(fake_paho):
    """paho retries indefinitely at a fixed short interval by default. An
    expired credential would otherwise become a hot reconnect loop against
    AWS IoT."""
    await _make().connect(CREDS)
    kwargs = fake_paho.reconnect_delay_set.call_args.kwargs
    assert kwargs["min_delay"] >= 1
    assert kwargs["max_delay"] <= 300


async def test_incoming_message_is_dispatched_with_parsed_json(fake_paho):
    """Callbacks arrive on paho's thread and are marshalled onto the loop
    with call_soon_threadsafe, so the dispatch needs a loop tick to land."""
    received = []
    sc = _make(on_message=lambda topic, payload: received.append((topic, payload)))
    await sc.connect(CREDS)

    msg = MagicMock()
    msg.topic = f"$aws/things/{THING}/shadow/get/accepted"
    msg.payload = json.dumps({"state": {"reported": {"fan": 2}}}).encode()
    sc._on_message(fake_paho, None, msg)
    await asyncio.sleep(0)

    assert received[0][0].endswith("get/accepted")
    assert received[0][1]["state"]["reported"]["fan"] == 2


async def test_malformed_payload_is_dropped_without_dispatching(fake_paho):
    """A parse error inside a paho callback thread would kill the network
    loop and silently stop all updates. The payload must be dropped, and the
    consumer must not be handed anything."""
    received = []
    sc = _make(on_message=lambda topic, payload: received.append((topic, payload)))
    await sc.connect(CREDS)

    msg = MagicMock()
    msg.topic = f"$aws/things/{THING}/shadow/get/accepted"
    msg.payload = b"not json"
    sc._on_message(fake_paho, None, msg)
    await asyncio.sleep(0)

    assert received == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_shadow.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyzephyrconnect.shadow'`

- [ ] **Step 3: Write `src/pyzephyrconnect/shadow.py`**

```python
"""MQTT device shadow transport over a presigned WebSocket.

paho runs its network loop on a background thread, so every callback here
executes off the event loop. Callbacks marshal onto the loop with
call_soon_threadsafe and swallow their own exceptions - an exception raised
inside a paho callback kills the network thread and updates stop arriving
with no error anywhere.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import paho.mqtt.client as mqtt

from . import const
from .auth import Credentials
from .exceptions import ZephyrPolicyError, ZephyrTransportError
from .presign import build_presigned_url

_LOGGER = logging.getLogger(__name__)


class ShadowTopics:
    """Classic shadow topic names for one thing."""

    def __init__(self, thing_name: str) -> None:
        self._base = f"$aws/things/{thing_name}/shadow"

    @property
    def get(self) -> str:
        return f"{self._base}/get"

    @property
    def get_accepted(self) -> str:
        return f"{self._base}/get/accepted"

    @property
    def get_rejected(self) -> str:
        return f"{self._base}/get/rejected"

    @property
    def update(self) -> str:
        """The write path. Publishing here actuates hardware."""
        return f"{self._base}/update"

    @property
    def update_accepted(self) -> str:
        return f"{self._base}/update/accepted"

    @property
    def update_rejected(self) -> str:
        return f"{self._base}/update/rejected"

    @property
    def update_delta(self) -> str:
        return f"{self._base}/update/delta"

    @property
    def update_documents(self) -> str:
        return f"{self._base}/update/documents"

    @property
    def subscriptions(self) -> tuple[str, ...]:
        return (
            self.get_accepted,
            self.get_rejected,
            self.update_accepted,
            self.update_rejected,
            self.update_delta,
            self.update_documents,
        )


class ShadowClient:
    """One MQTT connection to one thing's shadow."""

    def __init__(
        self,
        thing_name: str,
        client_id: str,
        on_message: Callable[[str, dict[str, Any]], None],
        on_connection_change: Callable[[bool], None],
    ) -> None:
        self.topics = ShadowTopics(thing_name)
        self._client_id = client_id
        self._on_message_cb = on_message
        self._on_connection_cb = on_connection_change
        self._client: mqtt.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._connected = asyncio.Event()

    # -- paho callbacks (background thread) ---------------------------

    def _dispatch(self, fn: Callable[..., None], *args: Any) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(fn, *args)

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            _LOGGER.warning("MQTT connect refused: %s", reason_code)
            return
        for topic in self.topics.subscriptions:
            client.subscribe(topic, qos=1)
        self._dispatch(self._connected.set)
        self._dispatch(self._on_connection_cb, True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None):
        self._dispatch(self._connected.clear)
        self._dispatch(self._on_connection_cb, False)

    def _on_subscribe(self, client, userdata, mid, reason_code_list, properties):
        """Validate the GRANTED QoS.

        paho resolves the subscribe even when the broker refused the topic.
        Granted QoS 128 means denied, and the cause is almost always a
        missing IoT policy on the Cognito identity.
        """
        for code in reason_code_list:
            if getattr(code, "is_failure", False):
                raise ZephyrPolicyError(
                    "AWS IoT denied a shadow subscription (granted QoS 128). "
                    f"Confirm {const.POLICY_NAME} is attached to this identity "
                    "with attach_policy() BEFORE connecting - an open "
                    "connection does not pick up new permissions."
                )

    def _on_message(self, client, userdata, message):
        try:
            payload = json.loads(message.payload)
        except (ValueError, TypeError):
            _LOGGER.warning("Discarding malformed payload on %s", message.topic)
            return
        self._dispatch(self._on_message_cb, message.topic, payload)

    # -- async surface -------------------------------------------------

    async def connect(self, credentials: Credentials, timeout: float = 15.0) -> None:
        self._loop = asyncio.get_running_loop()
        url = build_presigned_url(
            credentials.access_key,
            credentials.secret_key,
            credentials.session_token,
            endpoint=const.IOT_ENDPOINT,
            region=const.REGION,
            now=datetime.now(UTC),
        )
        parts = urlsplit(url)

        client = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            transport="websockets",
            protocol=mqtt.MQTTv311,
        )
        client.ws_set_options(path=f"{parts.path}?{parts.query}")
        # The IoT ATS endpoint chains to Amazon Root CA 1, which system trust
        # stores already carry. Only the vendor REST host needs a pinned CA.
        client.tls_set()
        # paho retries indefinitely at a fixed short interval by default. Cap
        # the backoff so an expired credential does not become a hot loop.
        client.reconnect_delay_set(min_delay=1, max_delay=120)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_subscribe = self._on_subscribe
        client.on_message = self._on_message

        client.connect_async(const.IOT_ENDPOINT, 443, keepalive=30)
        client.loop_start()
        self._client = client

        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
        except TimeoutError as err:
            await self.disconnect()
            raise ZephyrTransportError(
                f"MQTT connection to {const.IOT_ENDPOINT} timed out"
            ) from err

    async def disconnect(self) -> None:
        if self._client is None:
            return
        self._client.loop_stop()
        self._client.disconnect()
        self._client = None
        self._connected.clear()

    def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        if self._client is None:
            raise ZephyrTransportError("not connected")
        info = self._client.publish(topic, json.dumps(payload), qos=1)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ZephyrTransportError(f"publish to {topic} failed: rc={info.rc}")

    async def request_state(self) -> None:
        """Ask for the full shadow. The reply lands on get/accepted."""
        self._publish(self.topics.get, {})

    async def publish_desired(self, fields: dict[str, Any]) -> None:
        """WRITE PATH - actuates hardware.

        Callers are responsible for allowlisting fields. Only the probe CLI
        should reach this until the write path has been validated.
        """
        if not fields:
            raise ValueError("refusing to publish an empty desired state")
        self._publish(self.topics.update, {"state": {"desired": fields}})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_shadow.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add MQTT shadow transport with granted-QoS validation"
```

---

### Task 7: Client facade

**Files:**
- Create: `src/pyzephyrconnect/client.py`
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: `auth.ZephyrAuth`, `api.ZephyrApi`, `shadow.ShadowClient`, `models.HoodCapabilities`, `models.HoodState`
- Produces: `ZephyrClient(username: str, password: str, session: aiohttp.ClientSession)` with `async async_setup() -> list[HoodCapabilities]`, `async async_start(thing_name: str) -> None`, `async async_stop() -> None`, `async async_poll(thing_name: str) -> HoodState`, `async async_refresh_if_needed() -> bool`, `async async_publish_desired(thing_name: str, fields: dict) -> None`, `add_listener(thing_name, callback) -> Callable[[], None]`, `state(thing_name) -> HoodState | None`, `capabilities(thing_name) -> HoodCapabilities | None`, property `connected: bool`

- [ ] **Step 1: Write the failing test**

Create `tests/test_client.py`:

```python
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyzephyrconnect import client as client_module
from pyzephyrconnect.auth import Credentials
from pyzephyrconnect.client import ZephyrClient
from pyzephyrconnect.models import HoodState

FIXTURES = Path(__file__).parent / "fixtures"
THING = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"


@pytest.fixture
def wired(monkeypatch):
    """Replace the three collaborators, recording the order of calls."""
    order: list[str] = []
    discover = json.loads((FIXTURES / "discoverdevice.json").read_text())

    auth = MagicMock()
    auth.authenticate = AsyncMock(side_effect=lambda: order.append("authenticate"))
    auth.attach_policy = AsyncMock(side_effect=lambda: order.append("attach_policy"))
    auth.refresh = AsyncMock()
    auth.id_token = "ID"
    auth.identity_id = "us-west-2:abc"
    auth.mqtt_client_id = "us-west-2:abc-ha"
    auth.credentials = Credentials(
        "k", "s", "t", datetime.now(UTC) + timedelta(hours=1)
    )

    api = MagicMock()
    api.get_own_devices = AsyncMock(return_value=[{"thingName": THING}])
    api.discover_device = AsyncMock(return_value=discover)

    shadow = MagicMock()
    shadow.connect = AsyncMock(side_effect=lambda *a, **k: order.append("connect"))
    shadow.disconnect = AsyncMock()
    shadow.request_state = AsyncMock()
    shadow.publish_desired = AsyncMock()

    monkeypatch.setattr(client_module, "ZephyrAuth", MagicMock(return_value=auth))
    monkeypatch.setattr(client_module, "ZephyrApi", MagicMock(return_value=api))
    monkeypatch.setattr(client_module, "ShadowClient", MagicMock(return_value=shadow))
    return {"auth": auth, "api": api, "shadow": shadow, "order": order}


def _client():
    return ZephyrClient("u", "p", MagicMock())


async def test_setup_returns_parsed_capabilities(wired):
    caps = await _client().async_setup()
    assert len(caps) == 1
    assert caps[0].max_fan_speed == 6
    assert caps[0].thing_name == THING


async def test_policy_is_attached_before_the_socket_opens(wired):
    """Ordering is load-bearing: an already-open connection does not pick up
    newly attached permissions, and the failure is silent."""
    c = _client()
    await c.async_setup()
    await c.async_start(THING)

    order = wired["order"]
    assert order.index("attach_policy") < order.index("connect")
    assert order.index("authenticate") < order.index("attach_policy")


async def test_start_requests_initial_state(wired):
    c = _client()
    await c.async_setup()
    await c.async_start(THING)
    wired["shadow"].request_state.assert_awaited_once()


async def test_get_accepted_populates_state_and_notifies(wired):
    c = _client()
    await c.async_setup()
    await c.async_start(THING)

    seen = []
    c.add_listener(THING, lambda state: seen.append(state))
    c._handle_message(
        THING,
        f"$aws/things/{THING}/shadow/get/accepted",
        {"state": {"reported": {"fan": 4, "power": 1, "isOnline": 1}}},
    )

    assert c.state(THING).fan == 4
    assert seen[-1].power == 1


async def test_delta_merges_without_clearing_untouched_fields(wired):
    c = _client()
    await c.async_setup()
    await c.async_start(THING)
    c._handle_message(
        THING,
        f"$aws/things/{THING}/shadow/get/accepted",
        {"state": {"reported": {"fan": 0, "usegreasefiltertime": 642}}},
    )
    c._handle_message(
        THING, f"$aws/things/{THING}/shadow/update/delta", {"state": {"fan": 3}}
    )

    assert c.state(THING).fan == 3
    assert c.state(THING).use_grease_filter_time == 642


async def test_listener_can_be_removed(wired):
    c = _client()
    await c.async_setup()
    seen = []
    remove = c.add_listener(THING, lambda s: seen.append(s))
    remove()
    c._handle_message(
        THING, f"$aws/things/{THING}/shadow/get/accepted",
        {"state": {"reported": {"fan": 1}}},
    )
    assert seen == []


async def test_listener_exception_does_not_break_other_listeners(wired):
    c = _client()
    await c.async_setup()
    seen = []
    c.add_listener(THING, lambda s: (_ for _ in ()).throw(RuntimeError("boom")))
    c.add_listener(THING, lambda s: seen.append(s))
    c._handle_message(
        THING, f"$aws/things/{THING}/shadow/get/accepted",
        {"state": {"reported": {"fan": 1}}},
    )
    assert len(seen) == 1


async def test_refresh_is_skipped_while_credentials_are_fresh(wired):
    c = _client()
    await c.async_setup()
    assert await c.async_refresh_if_needed() is False
    wired["auth"].refresh.assert_not_awaited()


async def test_expiring_credentials_trigger_refresh_and_reconnect(wired):
    """The presigned URL is derived from credentials, so a refresh must
    rebuild the socket, not just swap the token."""
    c = _client()
    await c.async_setup()
    await c.async_start(THING)
    wired["auth"].credentials = Credentials(
        "k", "s", "t", datetime.now(UTC) + timedelta(seconds=30)
    )

    assert await c.async_refresh_if_needed() is True
    wired["auth"].refresh.assert_awaited_once()
    wired["shadow"].disconnect.assert_awaited()
    assert wired["shadow"].connect.await_count == 2


async def test_publish_desired_requires_a_started_connection(wired):
    c = _client()
    await c.async_setup()
    with pytest.raises(RuntimeError, match="async_start"):
        await c.async_publish_desired(THING, {"light": 1})


async def test_publish_desired_delegates_to_the_shadow(wired):
    c = _client()
    await c.async_setup()
    await c.async_start(THING)
    await c.async_publish_desired(THING, {"light": 1})
    wired["shadow"].publish_desired.assert_awaited_once_with({"light": 1})


async def test_poll_falls_back_to_https(wired):
    """When MQTT is down, discoverdevice still returns live state."""
    c = _client()
    await c.async_setup()
    state = await c.async_poll(THING)
    assert isinstance(state, HoodState)
    assert state.use_grease_filter_time == 642
    assert c.state(THING) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyzephyrconnect.client'`

- [ ] **Step 3: Write `src/pyzephyrconnect/client.py`**

```python
"""Facade tying auth, REST and MQTT into one lifecycle.

Read strategy is hybrid by design: discoverdevice supplies capabilities and
an initial state over plain HTTPS before MQTT exists, MQTT then carries live
push, and discoverdevice remains available as a fallback so consumers degrade
to slower updates instead of going unavailable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from .api import ZephyrApi
from .auth import ZephyrAuth
from .models import HoodCapabilities, HoodState
from .shadow import ShadowClient

_LOGGER = logging.getLogger(__name__)

StateListener = Callable[[HoodState], None]


class ZephyrClient:
    """One authenticated account and the hoods under it."""

    def __init__(
        self, username: str, password: str, session: aiohttp.ClientSession
    ) -> None:
        self._auth = ZephyrAuth(username, password)
        self._api = ZephyrApi(session)
        self._capabilities: dict[str, HoodCapabilities] = {}
        self._states: dict[str, HoodState] = {}
        self._listeners: dict[str, list[StateListener]] = {}
        self._shadows: dict[str, ShadowClient] = {}
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    def capabilities(self, thing_name: str) -> HoodCapabilities | None:
        return self._capabilities.get(thing_name)

    def state(self, thing_name: str) -> HoodState | None:
        return self._states.get(thing_name)

    async def async_setup(self) -> list[HoodCapabilities]:
        """Authenticate and discover every hood on the account."""
        await self._auth.authenticate()
        devices = await self._api.get_own_devices(self._auth.id_token)
        for device in devices:
            thing_name = device["thingName"]
            payload = await self._api.discover_device(
                self._auth.id_token, thing_name
            )
            caps = HoodCapabilities.from_discover(payload)
            self._capabilities[thing_name] = caps
            self._states[thing_name] = HoodState.from_reported(payload)
        return list(self._capabilities.values())

    async def async_start(self, thing_name: str) -> None:
        """Attach the IoT policy, then open the shadow connection.

        The ordering is mandatory. Attaching after connecting produces a
        connection where subscribe and publish succeed and every message is
        silently dropped.
        """
        await self._auth.attach_policy()

        shadow = ShadowClient(
            thing_name,
            self._auth.mqtt_client_id,
            lambda topic, payload: self._handle_message(
                thing_name, topic, payload
            ),
            self._handle_connection_change,
        )
        await shadow.connect(self._auth.credentials)
        self._shadows[thing_name] = shadow
        await shadow.request_state()

    async def async_stop(self) -> None:
        for shadow in self._shadows.values():
            await shadow.disconnect()
        self._shadows.clear()
        self._connected = False

    async def async_poll(self, thing_name: str) -> HoodState:
        """Read current state over HTTPS. Used at setup and while degraded."""
        payload = await self._api.discover_device(self._auth.id_token, thing_name)
        state = HoodState.from_reported(payload)
        self._states[thing_name] = state
        self._notify(thing_name, state)
        return state

    async def async_refresh_if_needed(self) -> bool:
        """Renew credentials and rebuild sockets if inside the margin.

        Returns True when a refresh happened. The presigned WebSocket URL is
        derived from the credentials, so refreshing without reconnecting
        leaves the socket authorised by an expiring signature.
        """
        if not self._auth.credentials.expired:
            return False

        _LOGGER.debug("credentials near expiry; refreshing and reconnecting")
        await self._auth.refresh()
        for thing_name, shadow in list(self._shadows.items()):
            await shadow.disconnect()
            await shadow.connect(self._auth.credentials)
            await shadow.request_state()
        return True

    async def async_publish_desired(
        self, thing_name: str, fields: dict[str, Any]
    ) -> None:
        """WRITE PATH - actuates hardware.

        Callers must allowlist fields themselves. Until the validation gate
        in the plan is complete, the probe CLI is the only permitted caller.
        """
        shadow = self._shadows.get(thing_name)
        if shadow is None:
            raise RuntimeError(f"async_start() has not been called for {thing_name}")
        await shadow.publish_desired(fields)

    def add_listener(
        self, thing_name: str, callback: StateListener
    ) -> Callable[[], None]:
        self._listeners.setdefault(thing_name, []).append(callback)

        def remove() -> None:
            try:
                self._listeners[thing_name].remove(callback)
            except (KeyError, ValueError):
                pass

        return remove

    # -- internals -----------------------------------------------------

    def _handle_connection_change(self, connected: bool) -> None:
        self._connected = connected

    def _handle_message(
        self, thing_name: str, topic: str, payload: dict[str, Any]
    ) -> None:
        """Fold an incoming shadow message into the cached state.

        get/accepted carries a full document; update/accepted and
        update/delta carry only what changed, so both are merged rather than
        replacing the cache.
        """
        if topic.endswith("/rejected"):
            _LOGGER.warning("shadow operation rejected: %s", payload)
            return

        state_block = payload.get("state") or {}
        if topic.endswith("/get/accepted"):
            reported = state_block.get("reported") or {}
            new_state = HoodState.from_reported(reported)
        elif topic.endswith("/update/accepted"):
            reported = state_block.get("reported") or {}
            current = self._states.get(thing_name)
            new_state = (
                current.merge(reported)
                if current
                else HoodState.from_reported(reported)
            )
        elif topic.endswith("/update/delta"):
            # delta carries the changed keys directly under "state".
            current = self._states.get(thing_name)
            new_state = (
                current.merge(state_block)
                if current
                else HoodState.from_reported(state_block)
            )
        else:
            return

        self._states[thing_name] = new_state
        self._notify(thing_name, new_state)

    def _notify(self, thing_name: str, state: HoodState) -> None:
        for callback in list(self._listeners.get(thing_name, [])):
            try:
                callback(state)
            except Exception:  # noqa: BLE001
                # One bad consumer must not stop the others from updating.
                _LOGGER.exception("state listener raised")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_client.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add client facade with hybrid read and refresh handling"
```

---

### Task 8: Probe CLI and documentation

The probe is the only code permitted to write to the shadow. Its rails are the last thing standing between a typo and an unexpected write to a physical appliance, so they are tested as logic rather than trusted as prose.

**Files:**
- Create: `src/pyzephyrconnect/probe.py`, `src/pyzephyrconnect/__main__.py`
- Create: `README.md`
- Move: `PROTOCOL.md` from `ha_zephyr` into this repo
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: `client.ZephyrClient`, `const.WRITABLE_FIELDS`, `const.DANGEROUS_FIELDS`
- Produces: `parse_assignment(text: str) -> tuple[str, int]`; `validate_write(field: str, *, confirmed: bool, forced: bool) -> None`; `diff_states(before: dict, after: dict) -> dict[str, tuple[Any, Any]]`; `async main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing test**

Create `tests/test_probe.py`:

```python
from contextlib import nullcontext

import pytest

from pyzephyrconnect.probe import diff_states, parse_assignment, validate_write


@pytest.mark.parametrize(
    ("text", "expected"),
    [("fan=3", ("fan", 3)), ("light=0", ("light", 0)), ("power=1", ("power", 1))],
)
def test_parse_assignment(text, expected):
    assert parse_assignment(text) == expected


@pytest.mark.parametrize("text", ["fan", "fan=", "=3", "fan=high", "fan=3=4"])
def test_parse_assignment_rejects_malformed_input(text):
    with pytest.raises(ValueError):
        parse_assignment(text)


def test_write_requires_confirmation():
    """--confirm is the deliberate speed bump before actuating hardware."""
    with pytest.raises(PermissionError, match="--confirm"):
        validate_write("light", confirmed=False, forced=False)


def test_readonly_fields_are_refused_even_with_confirm():
    """Counters and alarms are device-reported. Writing them is meaningless
    at best and confusing at worst."""
    for field in ("usegreasefiltertime", "alarmfan", "isOnline", "faultCode"):
        with pytest.raises(PermissionError, match="not writable"):
            validate_write(field, confirmed=True, forced=True)


def test_unknown_fields_are_refused():
    with pytest.raises(PermissionError, match="not writable"):
        validate_write("madeUpField", confirmed=True, forced=True)


def test_dangerous_fields_need_force_as_well_as_confirm():
    """resetgreasefilter zeroes an unrecoverable counter; setrecirculating
    changes filter accounting. --confirm alone must not be enough."""
    for field in ("resetgreasefilter", "setrecirculating"):
        with pytest.raises(PermissionError, match="--force"):
            validate_write(field, confirmed=True, forced=False)


@pytest.mark.parametrize("field", ["light", "fan", "power", "setdelaytimer"])
def test_ordinary_writes_pass_with_confirm_alone(field):
    with nullcontext():
        validate_write(field, confirmed=True, forced=False)


@pytest.mark.parametrize("field", ["resetgreasefilter", "setrecirculating"])
def test_dangerous_writes_pass_with_both_flags(field):
    with nullcontext():
        validate_write(field, confirmed=True, forced=True)


def test_diff_reports_only_changed_keys():
    before = {"fan": 0, "light": 0, "usefantime": 1979}
    after = {"fan": 3, "light": 0, "usefantime": 1979}
    assert diff_states(before, after) == {"fan": (0, 3)}


def test_diff_reports_newly_appearing_keys():
    """A field the device only reports once set is exactly what the
    validation sequence is hunting for."""
    assert diff_states({"fan": 0}, {"fan": 0, "newField": 7}) == {
        "newField": (None, 7)
    }


def test_diff_of_identical_states_is_empty():
    assert diff_states({"fan": 1}, {"fan": 1}) == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyzephyrconnect.probe'`

- [ ] **Step 3: Write `src/pyzephyrconnect/probe.py`**

```python
"""Probe CLI for mapping the shadow write path.

The write path is unverified and actuates a physical fan and light. This
tool exists so field semantics can be established one field at a time, with
the device attended, before any of it reaches an automation platform.

    python -m pyzephyrconnect --watch
    python -m pyzephyrconnect --set light=1 --confirm
    python -m pyzephyrconnect --set resetgreasefilter=1 --confirm --force
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import logging
import os
import sys
from typing import Any

import aiohttp

from . import const
from .client import ZephyrClient

_LOGGER = logging.getLogger(__name__)

# Keys never echoed to the terminal - they identify a home and its owner.
_REDACT = {"thingName", "SN", "MAC", "location"}


def parse_assignment(text: str) -> tuple[str, int]:
    """Parse `field=value`. Values are integers; the shadow has no others
    among the writable fields."""
    if text.count("=") != 1:
        raise ValueError(f"expected field=value, got {text!r}")
    field, _, raw = text.partition("=")
    field, raw = field.strip(), raw.strip()
    if not field or not raw:
        raise ValueError(f"expected field=value, got {text!r}")
    try:
        return field, int(raw)
    except ValueError as err:
        raise ValueError(f"value must be an integer, got {raw!r}") from err


def validate_write(field: str, *, confirmed: bool, forced: bool) -> None:
    """Raise unless this write is permitted. Order matters: report an
    unwritable field before complaining about missing flags."""
    if field not in const.WRITABLE_FIELDS:
        raise PermissionError(
            f"{field!r} is not writable. Allowed: "
            f"{', '.join(sorted(const.WRITABLE_FIELDS))}"
        )
    if not confirmed:
        raise PermissionError(
            f"refusing to write {field!r} without --confirm; this actuates "
            "hardware"
        )
    if field in const.DANGEROUS_FIELDS and not forced:
        raise PermissionError(
            f"{field!r} is destructive or changes device configuration; "
            "pass --force as well as --confirm"
        )


def diff_states(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, tuple[Any, Any]]:
    """Changed keys as {key: (before, after)}. Absent-before reads as None."""
    return {
        key: (before.get(key), after.get(key))
        for key in set(before) | set(after)
        if before.get(key) != after.get(key)
    }


def _redacted(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: ("<redacted>" if k in _REDACT else v) for k, v in payload.items()}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pyzephyrconnect",
        description="Read and probe a Zephyr range hood's device shadow.",
    )
    parser.add_argument("--watch", action="store_true",
                        help="stream shadow updates until interrupted")
    parser.add_argument("--seconds", type=int, default=300,
                        help="how long --watch listens (default: 300)")
    parser.add_argument("--set", dest="assignment", metavar="FIELD=VALUE",
                        help="write one field to the shadow")
    parser.add_argument("--confirm", action="store_true",
                        help="required for any write; actuates hardware")
    parser.add_argument("--force", action="store_true",
                        help="additionally required for destructive writes")
    parser.add_argument("--thing", help="thing name (default: first device)")
    parser.add_argument("--verbose", action="store_true")
    return parser


async def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    field = value = None
    if args.assignment:
        try:
            field, value = parse_assignment(args.assignment)
            validate_write(field, confirmed=args.confirm, forced=args.force)
        except (ValueError, PermissionError) as err:
            print(f"error: {err}", file=sys.stderr)
            return 2

    username = os.environ.get("ZEPHYR_USER") or input("email: ")
    password = os.environ.get("ZEPHYR_PASS") or getpass.getpass("password: ")

    async with aiohttp.ClientSession() as session:
        client = ZephyrClient(username, password, session)
        capabilities = await client.async_setup()
        if not capabilities:
            print("no devices on this account", file=sys.stderr)
            return 1

        caps = next(
            (c for c in capabilities if c.thing_name == args.thing),
            capabilities[0],
        )
        print(f"device: {caps.model} (fan 0-{caps.max_fan_speed}, "
              f"light 0-{caps.max_light_level})")

        await client.async_start(caps.thing_name)
        await asyncio.sleep(2)

        before = dict(client.state(caps.thing_name).raw)
        print("current state:")
        print(json.dumps(_redacted(before), indent=2, sort_keys=True))

        if field is None:
            if args.watch:
                print(f"watching for {args.seconds}s (ctrl-c to stop)")
                try:
                    await asyncio.sleep(args.seconds)
                except asyncio.CancelledError:
                    pass
                after = dict(client.state(caps.thing_name).raw)
                _report(diff_states(before, after))
            await client.async_stop()
            return 0

        print(f"\nWRITING {field}={value} to a physical appliance.")
        await client.async_publish_desired(caps.thing_name, {field: value})

        await asyncio.sleep(5)
        after = dict(client.state(caps.thing_name).raw)
        _report(diff_states(before, after))
        await client.async_stop()
        return 0


def _report(changes: dict[str, tuple[Any, Any]]) -> None:
    if not changes:
        print("\nno reported change. The device may have ignored the write, "
              "or it may not echo this field.")
        return
    print("\nchanged:")
    for key, (old, new) in sorted(changes.items()):
        print(f"  {key}: {old!r} -> {new!r}")


def run() -> int:
    try:
        return asyncio.run(main())
    except KeyboardInterrupt:
        return 130
```

- [ ] **Step 4: Write `src/pyzephyrconnect/__main__.py`**

```python
import sys

from .probe import run

sys.exit(run())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_probe.py -v`
Expected: 15 passed

- [ ] **Step 6: Move the protocol documentation into this repo**

```bash
git -C /Users/ryanmorash/Developer/ha_zephyr mv PROTOCOL.md /Users/ryanmorash/Developer/pyzephyrconnect/PROTOCOL.md 2>/dev/null \
  || mv /Users/ryanmorash/Developer/ha_zephyr/PROTOCOL.md /Users/ryanmorash/Developer/pyzephyrconnect/PROTOCOL.md
```

Then correct the two statements this implementation supersedes. In section 4, replace the TLS caveat's leaf-pinning guidance and, in section 7, remove open item 6:

- Section 4 must state that the bundled trust anchor set is **CA-only** (TWCA Root, TWCA Global Root, TWCA Secure SSL CA), that this is what `pyzephyrconnect` ships as `certs/twca.pem`, and that validity therefore runs to **2030** rather than expiring with the leaf on 2026-10-15.
- Section 7 item 6 ("Cert pin expiry 2026-10-15") is resolved and should be deleted.

- [ ] **Step 7: Write `README.md`**

```markdown
# pyzephyrconnect

Python client for Zephyr / Gemtek range hoods.

These hoods expose no local API. All communication is a cloud round-trip
through AWS IoT Core device shadows. See [PROTOCOL.md](PROTOCOL.md) for how
the protocol was reverse-engineered.

## Install

    pip install pyzephyrconnect

## Read state

```python
import aiohttp
from pyzephyrconnect import ZephyrClient

async with aiohttp.ClientSession() as session:
    client = ZephyrClient("you@example.com", "password", session)
    for caps in await client.async_setup():
        print(caps.model, caps.max_fan_speed)
        await client.async_start(caps.thing_name)
        print(client.state(caps.thing_name))
```

## Probe CLI

The write path actuates a physical fan and light. The CLI writes one field
at a time, refuses anything outside an allowlist, and requires `--confirm`:

    export ZEPHYR_USER=you@example.com
    python -m pyzephyrconnect --watch
    python -m pyzephyrconnect --set light=1 --confirm

Destructive writes need `--force` as well. `resetgreasefilter` zeroes a usage
counter that cannot be reconstructed.

## Status

Read path verified against a Zephyr AK7400AS. Write path is under
validation; field semantics for `act` and the units of the `use*time`
counters are not yet established.

## License

GPL-3.0-or-later
```

- [ ] **Step 8: Run the full suite**

```bash
.venv/bin/python -m pytest -v
```

Expected: all tests pass, no network access, no warnings about unclosed sessions.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: add probe CLI, protocol docs and README"
```

---

## Validation Gate

**This is a manual step. It cannot be automated and it blocks the integration plan.**

With the hood attended, work through the sequence in spec section 7.2, one
field per invocation, recording the reported diff each time:

```bash
export ZEPHYR_USER=you@example.com
python -m pyzephyrconnect --set light=1 --confirm
```

Record the results in `PROTOCOL.md` section 7, replacing the open items.
Three answers gate the integration design:

1. **`power` semantics** (step 3) — master switch, derived, or standby.
   Determines whether `fan.turn_on` writes `power` alongside the level, and
   whether a `switch` entity exists at all.
2. **`setdelaytimer` units and value domain** (step 6) — continuous minutes
   or discrete presets. Determines `number` versus `select`.
3. **Filter counter units** (observed during any fan run) — whether
   `usegreasefiltertime` is minutes against a `maxGreasefilterTimer` in
   hours. Determines the filter-life percentage calculation.

Defer step 9 (`resetgreasefilter`) until you are actually cleaning the
filter. The counter cannot be recovered.

Once these are recorded, the integration plan can be written against known
semantics instead of placeholders.
