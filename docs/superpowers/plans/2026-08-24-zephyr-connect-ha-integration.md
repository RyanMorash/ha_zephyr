# Zephyr Connect Home Assistant Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `custom_components/zephyr_connect`, a HACS-installable Home Assistant integration exposing full control and monitoring of Zephyr range hoods through the `pyzephyrconnect` library.

**Architecture:** A push-style `DataUpdateCoordinator` wraps one `ZephyrClient` per config entry. The library delivers live shadow updates via a listener callback; the coordinator also drives credential refresh, a slow safety-net re-read, and a degraded HTTPS poll when MQTT is down. Every entity is a `CoordinatorEntity` reading from the cached `HoodState`, and entity creation is gated on `HoodCapabilities` so the integration generalises to Zephyr models we have never seen.

**Tech Stack:** Home Assistant (2024.6+ APIs), `pyzephyrconnect`, `pytest-homeassistant-custom-component`

**Spec:** `../specs/2026-08-23-zephyr-connect-ha-integration-design.md`

**Repo:** `/Users/ryanmorash/Developer/ha_zephyr`

---

## Protocol semantics — VALIDATED AGAINST REAL HARDWARE

Every statement here was established against a physical AK7400AS on
2026-08-24. Several contradict earlier drafts of the design. **Trust this
section over any older document, including PROTOCOL.md.**

### The write path

Publish `{"state": {"reported": {<field>: <value>}}}` to
`$aws/things/<thing>/shadow/update`.

**NOT `state.desired`.** Writing `desired` is accepted by AWS and silently
ignored by the device — no error, no rejection. The library handles this;
call `client.async_set_state(thing_name, {...})` and never construct shadow
payloads in integration code.

Two `update/accepted` messages follow a write: AWS echoes our own value
within ~0.1s, then the DEVICE reports its real state ~1.2–1.6s later.
Only the second is authoritative. The library's cached `HoodState` reflects
both in turn, so a UI may briefly show the optimistic value — acceptable,
and the same behaviour the vendor app exhibits.

`update/delta` is ignored by the library entirely. Never reintroduce it.

### Field semantics

| Field | Writable | Behaviour |
|---|---|---|
| `power` | yes | Master switch. `0` = everything off. `1` = **restore previous levels** (observed restoring fan 6 + light 1). NOT a precondition for other writes. |
| `fan` | yes | `0`–`maxFanSpeed` (6 on reference). Writing a level runs the fan; the device raises `power` to 1 itself. |
| `light` | yes | `0`–`maxLightLevel` (3 on reference). Same: device raises `power` itself. |
| `setdelaytimer` | yes | **Seconds.** Arbitrary values accepted (60 worked; app only offers 300/600). Writing it **starts the fan at speed 1** — an actuating side effect, not a passive setting. |
| `delaytimer` | **no** | Device-managed countdown in seconds. Device derives it from `setdelaytimer` and decrements it, reporting every 60 s. |
| `setcleanairfunction` | yes | Operating mode. `1` = clean air on **and fan to speed 1**. `0` = mode off and hood powers down. |
| `setrecirculating` | **no (v1)** | Installation setting. Read-only diagnostic — see spec §8.1b. |
| `resetgreasefilter` | yes | **Destructive.** Zeroes `usegreasefiltertime`, unrecoverable. Ships untested. |
| `usegreasefiltertime` | no | **Minutes.** Against `maxGreasefilterTimer` in **hours**. |
| `usefantime`, `uselighttime` | no | **Hours** (inferred, see below). Did NOT move during 5 min of fan runtime while `usegreasefiltertime` did. |
| `isOnline` | no | Availability. |
| `act` | no | **Airflow Control Technology (ACT).** Zephyr's airflow cap for make-up-air code compliance. Set PHYSICALLY on the hood - not settable from the cloud at all. `"Disabled"` observed. |

### Filter life — verified against the vendor app

```python
used_fraction = state.use_grease_filter_time / (caps.max_grease_filter_hours * 60)
remaining_pct = 100 * (1 - used_fraction)
```

With `usegreasefiltertime=643` and `maxGreasefilterTimer=60` this yields
82.14%; the vendor app displayed **82%**. Independent confirmation.

### Runtime counters — hours, by inference not measurement

`usefantime` and `uselighttime` are treated as **hours**. This is the one
number in this document not directly measured, so the reasoning is recorded
in full:

- They did not move during five minutes of fan runtime, while
  `usegreasefiltertime` (minutes) advanced. So they are coarser than minutes.
- As hours: 1979 h fan and 2833 h light equal roughly 2.7 and 3.9 hours per
  day over two years — ordinary for a kitchen hood.
- As minutes they would mean 33 h of fan over the unit's entire life, which
  contradicts a device carrying 302,000+ shadow revisions.
- Light exceeding fan matches real use: hood lights get left on.

Confirm cheaply by running the fan for a full hour and checking for a +1, or
by comparing against any runtime figure the vendor app displays — the same
cross-check that verified the filter formula against the app's 82%.

If this proves wrong, it is a one-line change per sensor
(`UnitOfTime.HOURS` -> the correct unit). It affects two diagnostic
sensors, nothing safety-relevant and no control.

### The recurring trap

Every `set*` field validated so far **actuates the hood** rather than
recording a preference. The vendor app's UI is also consistently more
restrictive than the firmware (it hides the delay timer when powered off,
and offers only two presets, neither of which the device enforces). Do not
infer device behaviour from the app's UI, and assume any untested `set*`
field actuates until proven otherwise.

---

## Global Constraints

Every task's requirements implicitly include this section.

- HA integration domain is exactly `zephyr_connect`. Do not rename.
- `manifest.json` must declare `"iot_class": "cloud_push"`, `"config_flow": true`, `"version"`, and `"requirements": ["pyzephyrconnect @ git+https://github.com/RyanMorash/pyzephyrconnect@main"]` during development. Pin to `pyzephyrconnect==0.1.0` before the HACS release.
- Integration code MUST NOT import `paho`, `boto3`, `aiohttp` transports directly, construct shadow payloads, or touch MQTT topics. All protocol work goes through `pyzephyrconnect`'s public API. If something is missing, the fix belongs in the library.
- No blocking I/O in the event loop. The library is already async; do not wrap its calls in executors.
- No test may make a network call. Mock `ZephyrClient`.
- `thingName`, `SN`, `MAC`, `location`, and coordinates are personal data: never logged at INFO or above, and redacted in diagnostics.
- Entity creation is gated on `HoodCapabilities`, never on the model string.
- `_attr_has_entity_name = True` on every entity; HA composes the device name.
- Use `entry.runtime_data` for per-entry state, not `hass.data[DOMAIN]`.

---

## File structure

```
custom_components/zephyr_connect/
├── __init__.py          entry setup/unload, platform forwarding
├── manifest.json        HA metadata + library requirement
├── const.py             DOMAIN, platforms, tunables
├── coordinator.py       ZephyrCoordinator: push wiring, refresh, degraded poll
├── entity.py            ZephyrEntity base: device info, availability
├── fan.py               fan platform
├── light.py             light platform
├── switch.py            power + clean air
├── number.py            delay off
├── button.py            reset grease filter
├── sensor.py            filter life, runtimes, delay remaining, mode, recirculating
├── binary_sensor.py     4 PROBLEM sensors
├── config_flow.py       user + reauth + options
├── diagnostics.py       redacted state dump
└── strings.json         UI copy
hacs.json
tests/
```

---

### Task 1: Integration scaffold and config flow

**Files:**
- Create: `custom_components/zephyr_connect/__init__.py`, `manifest.json`, `const.py`, `config_flow.py`, `strings.json`, `hacs.json`
- Test: `tests/conftest.py`, `tests/test_config_flow.py`

**Interfaces:**
- Consumes: `pyzephyrconnect.ZephyrClient` (including the `identity_id` property, added in library commit `ef13872` specifically so this config flow need not reach into privates), `ZephyrAuthError`, `ZephyrError`
- Produces: `const.DOMAIN = "zephyr_connect"`, `const.PLATFORMS`, `ZephyrConfigFlow`, `async_setup_entry`, `async_unload_entry`, type alias `ZephyrConfigEntry`

- [ ] **Step 1: Create `hacs.json`**

```json
{
  "name": "Zephyr Connect",
  "homeassistant": "2024.6.0",
  "render_readme": true
}
```

- [ ] **Step 2: Create `custom_components/zephyr_connect/manifest.json`**

```json
{
  "domain": "zephyr_connect",
  "name": "Zephyr Connect",
  "codeowners": ["@RyanMorash"],
  "config_flow": true,
  "documentation": "https://github.com/RyanMorash/ha_zephyr",
  "integration_type": "hub",
  "iot_class": "cloud_push",
  "issue_tracker": "https://github.com/RyanMorash/ha_zephyr/issues",
  "requirements": ["pyzephyrconnect @ git+https://github.com/RyanMorash/pyzephyrconnect@main"],
  "version": "0.1.0"
}
```

- [ ] **Step 3: Create `custom_components/zephyr_connect/const.py`**

```python
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
```

- [ ] **Step 4: Write the failing config flow test**

Create `tests/test_config_flow.py`:

```python
"""Config flow tests. No network: ZephyrClient is mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.zephyr_connect.const import DOMAIN
from pyzephyrconnect import ZephyrAuthError, ZephyrError

USER_INPUT = {"username": "user@example.com", "password": "hunter2"}


@pytest.fixture
def mock_client():
    """A ZephyrClient that authenticates and returns one hood."""
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    client = MagicMock()
    client.async_setup = AsyncMock(return_value=[caps])
    client.async_stop = AsyncMock()
    client.identity_id = "us-west-2:00000000-1111-2222-3333-444455556666"
    with patch(
        "custom_components.zephyr_connect.config_flow.ZephyrClient",
        return_value=client,
    ):
        yield client


async def test_user_flow_creates_entry(hass: HomeAssistant, mock_client) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert result["data"] == USER_INPUT


async def test_invalid_auth_is_recoverable(hass: HomeAssistant, mock_client) -> None:
    """A wrong password must re-show the form, not abort the flow."""
    mock_client.async_setup.side_effect = ZephyrAuthError("bad password")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_connection_error_is_recoverable(hass: HomeAssistant, mock_client) -> None:
    mock_client.async_setup.side_effect = ZephyrError("vendor API down")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_account_cannot_be_added_twice(hass: HomeAssistant, mock_client) -> None:
    """Unique ID is the Cognito identity, so the same account aborts."""
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(first["flow_id"], USER_INPUT)
    await hass.async_block_till_done()

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        second["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_flow_releases_the_client_on_failure(hass: HomeAssistant, mock_client) -> None:
    """A validation attempt must not leave an MQTT connection open."""
    mock_client.async_setup.side_effect = ZephyrAuthError("nope")

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    mock_client.async_stop.assert_awaited()


async def test_reauth_updates_the_password(hass: HomeAssistant, mock_client) -> None:
    entry = config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="user@example.com",
        data=USER_INPUT,
        source=config_entries.SOURCE_USER,
        unique_id="us-west-2:00000000-1111-2222-3333-444455556666",
        options={},
        discovery_keys={},
        subentries_data={},
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"password": "new-password"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new-password"
```

- [ ] **Step 5: Create `tests/conftest.py`**

```python
"""Shared fixtures. pytest-homeassistant-custom-component provides `hass`."""

import pytest

pytest_plugins = ["pytest_homeassistant_custom_component"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Without this HA refuses to load anything in custom_components."""
    yield
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `python -m pytest tests/test_config_flow.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zephyr_connect.config_flow'`

- [ ] **Step 7: Write `custom_components/zephyr_connect/config_flow.py`**

```python
"""Config flow for Zephyr Connect."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyzephyrconnect import ZephyrAuthError, ZephyrClient, ZephyrError

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class ZephyrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Authenticate against the vendor cloud and register the account."""

    VERSION = 1

    async def _validate(self, username: str, password: str) -> str:
        """Return the Cognito identity ID, or raise.

        Always releases the client: validation opens an authenticated
        session, and leaking it would leave an MQTT connection open for a
        flow the user may abandon.
        """
        client = ZephyrClient(username, password, async_get_clientsession(self.hass))
        try:
            await client.async_setup()
            return client.identity_id
        finally:
            await client.async_stop()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                identity_id = await self._validate(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except ZephyrAuthError:
                errors["base"] = "invalid_auth"
            except ZephyrError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating Zephyr credentials")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(identity_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME], data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            try:
                await self._validate(
                    entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except ZephyrAuthError:
                errors["base"] = "invalid_auth"
            except ZephyrError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Zephyr reauth")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
        )
```

- [ ] **Step 8: Write `custom_components/zephyr_connect/strings.json`**

```json
{
  "config": {
    "step": {
      "user": {
        "title": "Zephyr Connect",
        "description": "Sign in with the account you use in the Zephyr Connect app.",
        "data": { "username": "Email", "password": "Password" }
      },
      "reauth_confirm": {
        "title": "Re-authenticate",
        "description": "Your Zephyr Connect password is no longer accepted.",
        "data": { "password": "Password" }
      }
    },
    "error": {
      "invalid_auth": "Invalid email or password.",
      "cannot_connect": "Could not reach the Zephyr cloud service.",
      "unknown": "Unexpected error."
    },
    "abort": {
      "already_configured": "This account is already set up.",
      "reauth_successful": "Re-authentication was successful."
    }
  }
}
```

- [ ] **Step 9: Write a minimal `custom_components/zephyr_connect/__init__.py`**

This is expanded in Task 2 once the coordinator exists.

```python
"""The Zephyr Connect integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import PLATFORMS


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Zephyr Connect from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
```

- [ ] **Step 9b: Create no-op stubs for the seven platform modules**

`async_forward_entry_setups` imports every platform listed in `PLATFORMS`.
Those modules are not written until Tasks 4-6, so without stubs any real
entry setup raises `ModuleNotFoundError` and lands in `SETUP_ERROR` - and
Task 2's test, which asserts the entry reaches `LOADED`, would fail outright.

Each later task replaces its own stub wholesale.

```bash
cd /Users/ryanmorash/Developer/ha_zephyr
for platform in binary_sensor button fan light number sensor switch; do
  python3 - "$platform" <<'PYEOF'
import sys, pathlib
name = sys.argv[1]
body = '"""' + name + ''' platform for Zephyr Connect.

Placeholder so async_forward_entry_setups can import this platform. The
real implementation arrives in a later task and replaces this file.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """No entities yet."""
'''
pathlib.Path(f"custom_components/zephyr_connect/{name}.py").write_text(body)
PYEOF
done
ls custom_components/zephyr_connect/*.py
```

- [ ] **Step 10: Install test dependencies and run**

```bash
cd /Users/ryanmorash/Developer/ha_zephyr
python3 -m venv .venv
.venv/bin/pip install pytest pytest-homeassistant-custom-component
.venv/bin/pip install "pyzephyrconnect @ git+https://github.com/RyanMorash/pyzephyrconnect@main"
.venv/bin/python -m pytest tests/test_config_flow.py -v
```

Expected: 6 passed

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "feat: add integration scaffold and config flow"
```

---

### Task 2: Coordinator and entry lifecycle

**Files:**
- Create: `custom_components/zephyr_connect/coordinator.py`
- Modify: `custom_components/zephyr_connect/__init__.py` (replace the Task 1 stub entirely)
- Test: `tests/test_init.py`

**Interfaces:**
- Consumes: `const.DOMAIN`, `const.PLATFORMS`, `const.SAFETY_NET_INTERVAL_SECONDS`, `const.DEGRADED_POLL_INTERVAL_SECONDS`
- Produces: `ZephyrCoordinator(hass, entry, client, capabilities)` with attributes `client`, `capabilities: HoodCapabilities`, `thing_name: str`, property `state: HoodState | None`, and methods `async_initialise()`, `async_shutdown()`, `async_set_state(fields: dict) -> None`; type alias `ZephyrConfigEntry = ConfigEntry[list[ZephyrCoordinator]]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_init.py`:

```python
"""Entry lifecycle and coordinator behaviour. ZephyrClient is mocked."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from homeassistant.util import dt as dt_util

from custom_components.zephyr_connect.const import (
    DEGRADED_POLL_INTERVAL_SECONDS,
    DOMAIN,
)
from pyzephyrconnect import ZephyrAuthError, ZephyrError

THING = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"


def _caps():
    caps = MagicMock()
    caps.thing_name = THING
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.max_fan_speed = 6
    caps.max_light_level = 3
    caps.max_grease_filter_hours = 60
    caps.max_charcoal_filter_hours = 200
    caps.supports_tru_hue = False
    caps.urls = {"FAQURL": "https://zephyronline.com/faq"}
    return caps


def _state(**overrides):
    state = MagicMock()
    defaults = {
        "power": 0, "fan": 0, "light": 0, "is_online": True,
        "use_grease_filter_time": 643, "delay_timer": 0,
    }
    for key, value in {**defaults, **overrides}.items():
        setattr(state, key, value)
    return state


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.async_setup = AsyncMock(return_value=[_caps()])
    client.async_start = AsyncMock()
    client.async_stop = AsyncMock()
    client.async_poll = AsyncMock(return_value=_state())
    client.async_refresh_if_needed = AsyncMock(return_value=False)
    client.async_set_state = AsyncMock()
    client.state = MagicMock(return_value=_state())
    client.add_listener = MagicMock(return_value=lambda: None)
    client.connected = True
    with patch(
        "custom_components.zephyr_connect.ZephyrClient", return_value=client
    ):
        yield client


@pytest.fixture
def entry(hass: HomeAssistant) -> MockConfigEntry:
    e = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "user@example.com", "password": "hunter2"},
        unique_id="us-west-2:00000000-1111-2222-3333-444455556666",
    )
    e.add_to_hass(hass)
    return e


async def test_setup_starts_each_hood(hass, entry, mock_client) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    mock_client.async_setup.assert_awaited_once()
    mock_client.async_start.assert_awaited_once_with(THING)


async def test_setup_registers_a_push_listener(hass, entry, mock_client) -> None:
    """Push is the primary update path; without a listener the integration
    would be silently poll-only."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_client.add_listener.assert_called_once()
    assert mock_client.add_listener.call_args.args[0] == THING


async def test_auth_failure_triggers_reauth(hass, entry, mock_client) -> None:
    mock_client.async_setup.side_effect = ZephyrAuthError("expired")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_transient_failure_retries(hass, entry, mock_client) -> None:
    """A vendor outage must retry, not permanently fail the entry."""
    mock_client.async_setup.side_effect = ZephyrError("vendor down")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_stops_the_client(hass, entry, mock_client) -> None:
    """Leaving the MQTT connection open would leak a paho thread per reload."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    mock_client.async_stop.assert_awaited()


async def test_degraded_poll_runs_when_mqtt_is_down(hass, entry, mock_client) -> None:
    """When the socket dies, HTTPS still returns live state. Entities must
    degrade to slower updates rather than going unavailable."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_poll.reset_mock()

    mock_client.connected = False
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    mock_client.async_poll.assert_awaited()


async def test_refresh_is_attempted_on_the_update_tick(hass, entry, mock_client) -> None:
    """Credentials expire hourly; the library no-ops until inside its margin."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mock_client.async_refresh_if_needed.reset_mock()

    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    mock_client.async_refresh_if_needed.assert_awaited()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_init.py -v`
Expected: FAIL — the coordinator does not exist and `ZephyrClient` is not imported in `__init__.py`

- [ ] **Step 3: Write `custom_components/zephyr_connect/coordinator.py`**

```python
"""Coordinator bridging pyzephyrconnect's push updates into Home Assistant."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from pyzephyrconnect import (
    HoodCapabilities,
    HoodState,
    ZephyrAuthError,
    ZephyrClient,
    ZephyrError,
)

from .const import DEGRADED_POLL_INTERVAL_SECONDS, DOMAIN

_LOGGER = logging.getLogger(__name__)


class ZephyrCoordinator(DataUpdateCoordinator[HoodState]):
    """One hood.

    Updates arrive by push: the library invokes our listener whenever the
    device reports. The polling interval here is a fallback, not the primary
    path - it refreshes credentials and, when MQTT is down, re-reads state
    over HTTPS so entities degrade instead of going unavailable.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ZephyrClient,
        capabilities: HoodCapabilities,
    ) -> None:
        self.client = client
        self.capabilities = capabilities
        self.thing_name = capabilities.thing_name
        self._unsubscribe: Any = None
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {capabilities.model}",
            update_interval=timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS),
        )

    @property
    def state(self) -> HoodState | None:
        """Latest known hood state, or None before the first update."""
        return self.data

    async def async_initialise(self) -> None:
        """Open the shadow connection and wire push updates."""
        await self.client.async_start(self.thing_name)
        self._unsubscribe = self.client.add_listener(
            self.thing_name, self._handle_push
        )
        # Seed from whatever the library already cached during setup, so
        # entities have data before the first device report arrives.
        if (cached := self.client.state(self.thing_name)) is not None:
            self.async_set_updated_data(cached)

    @callback
    def _handle_push(self, state: HoodState) -> None:
        """Device reported. Runs on the event loop; the library guarantees
        it never dispatches from paho's network thread."""
        self.async_set_updated_data(state)

    async def async_shutdown(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await super().async_shutdown()

    async def _async_update_data(self) -> HoodState:
        """Fallback tick: refresh credentials, and re-read if push is down."""
        try:
            await self.client.async_refresh_if_needed()
        except ZephyrAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ZephyrError as err:
            raise UpdateFailed(str(err)) from err

        if not self.client.connected:
            _LOGGER.debug("push transport down; reading state over HTTPS")
            try:
                return await self.client.async_poll(self.thing_name)
            except ZephyrAuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except ZephyrError as err:
                raise UpdateFailed(str(err)) from err

        cached = self.client.state(self.thing_name)
        if cached is None:
            raise UpdateFailed("no state received yet")
        return cached

    async def async_set_state(self, fields: dict[str, Any]) -> None:
        """Write to the hood. ACTUATES HARDWARE.

        The library writes state.reported - the device ignores state.desired
        entirely. Never build shadow payloads here.

        The device echoes its real state ~1.2-1.6s later via push, so no
        optimistic local update is needed.
        """
        try:
            await self.client.async_set_state(self.thing_name, fields)
        except ZephyrAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ZephyrError as err:
            raise UpdateFailed(f"write failed: {err}") from err
```

- [ ] **Step 4: Replace `custom_components/zephyr_connect/__init__.py` entirely**

```python
"""The Zephyr Connect integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pyzephyrconnect import ZephyrAuthError, ZephyrClient, ZephyrError

from .const import PLATFORMS
from .coordinator import ZephyrCoordinator

type ZephyrConfigEntry = ConfigEntry[list[ZephyrCoordinator]]


async def async_setup_entry(hass: HomeAssistant, entry: ZephyrConfigEntry) -> bool:
    """Set up Zephyr Connect from a config entry."""
    client = ZephyrClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        async_get_clientsession(hass),
    )

    try:
        capabilities = await client.async_setup()
    except ZephyrAuthError as err:
        await client.async_stop()
        raise ConfigEntryAuthFailed(str(err)) from err
    except ZephyrError as err:
        await client.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    coordinators: list[ZephyrCoordinator] = []
    try:
        for caps in capabilities:
            coordinator = ZephyrCoordinator(hass, entry, client, caps)
            await coordinator.async_initialise()
            coordinators.append(coordinator)
    except ZephyrError as err:
        await client.async_stop()
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = coordinators
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ZephyrConfigEntry) -> bool:
    """Unload a config entry, releasing the MQTT connection."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        for coordinator in entry.runtime_data:
            await coordinator.async_shutdown()
        # One client is shared across every hood on the account.
        if entry.runtime_data:
            await entry.runtime_data[0].client.async_stop()
    return unloaded
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_init.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add coordinator with push updates and degraded polling"
```

---

### Task 3: Entity base class

**Files:**
- Create: `custom_components/zephyr_connect/entity.py`
- Test: `tests/test_entity.py`

**Interfaces:**
- Consumes: `ZephyrCoordinator`
- Produces: `ZephyrEntity(coordinator, key)` — a `CoordinatorEntity[ZephyrCoordinator]` providing `_attr_device_info`, `_attr_unique_id`, `_attr_has_entity_name`, an `available` property, and a `hood` property returning `HoodState`

- [ ] **Step 1: Write the failing test**

Create `tests/test_entity.py`:

```python
"""Entity base behaviour: identity, device registration, availability."""

from unittest.mock import MagicMock

from custom_components.zephyr_connect.const import DOMAIN
from custom_components.zephyr_connect.entity import ZephyrEntity

THING = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"


def _coordinator(is_online=True, last_update_success=True):
    caps = MagicMock()
    caps.thing_name = THING
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.urls = {"FAQURL": "https://zephyronline.com/faq"}

    state = MagicMock()
    state.is_online = is_online

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = THING
    coordinator.data = state
    coordinator.state = state
    coordinator.last_update_success = last_update_success
    return coordinator


def test_unique_id_combines_thing_and_key():
    entity = ZephyrEntity(_coordinator(), "fan")
    assert entity.unique_id == f"{THING}_fan"


def test_device_info_identifies_the_hood():
    entity = ZephyrEntity(_coordinator(), "fan")
    info = entity.device_info
    assert (DOMAIN, THING) in info["identifiers"]
    assert info["manufacturer"] == "ZEPHYR"
    assert info["model"] == "AK7400AS"
    assert info["serial_number"] == "1234567XYZ"


def test_entity_uses_ha_device_naming():
    """has_entity_name lets HA compose 'Kitchen Hood Fan' rather than each
    entity repeating the device name."""
    assert ZephyrEntity(_coordinator(), "fan")._attr_has_entity_name is True


def test_unavailable_when_the_hood_is_offline():
    """isOnline is the device's own reachability flag - distinct from
    whether OUR transport is healthy."""
    assert ZephyrEntity(_coordinator(is_online=False), "fan").available is False


def test_unavailable_when_updates_are_failing():
    assert ZephyrEntity(
        _coordinator(last_update_success=False), "fan"
    ).available is False


def test_available_when_online_and_updating():
    assert ZephyrEntity(_coordinator(), "fan").available is True


def test_unavailable_before_the_first_update():
    coordinator = _coordinator()
    coordinator.data = None
    coordinator.state = None
    assert ZephyrEntity(coordinator, "fan").available is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_entity.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zephyr_connect.entity'`

- [ ] **Step 3: Write `custom_components/zephyr_connect/entity.py`**

```python
"""Base entity for Zephyr Connect."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyzephyrconnect import HoodState

from .const import DOMAIN
from .coordinator import ZephyrCoordinator


class ZephyrEntity(CoordinatorEntity[ZephyrCoordinator]):
    """Common identity, device registration and availability."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ZephyrCoordinator, key: str) -> None:
        super().__init__(coordinator)
        caps = coordinator.capabilities
        self._key = key
        self._attr_unique_id = f"{coordinator.thing_name}_{key}"

        connections = set()
        if caps.mac:
            connections.add((CONNECTION_NETWORK_MAC, caps.mac))

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.thing_name)},
            manufacturer=caps.manufacturer or "ZEPHYR",
            model=caps.model or None,
            serial_number=caps.serial or None,
            connections=connections,
            configuration_url=caps.urls.get("FAQURL"),
        )

    @property
    def hood(self) -> HoodState | None:
        """Latest hood state, or None before the first update."""
        return self.coordinator.data

    @property
    def available(self) -> bool:
        """Available only when our updates work AND the hood reports online.

        These are different failures: last_update_success covers our cloud
        path, isOnline is the device telling the cloud it is reachable.
        """
        if not self.coordinator.last_update_success:
            return False
        state = self.hood
        return state is not None and bool(state.is_online)
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_entity.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: add entity base with device info and availability"
```

---

### Task 4: Fan and light platforms

The two controls people touch daily. Both write only their own level — the device raises `power` itself, so writing `power` alongside would be redundant and could fight the device.

**Files:**
- Replace (these exist as no-op stubs from Task 1): `custom_components/zephyr_connect/fan.py`, `custom_components/zephyr_connect/light.py`
- Test: `tests/test_fan.py`, `tests/test_light.py`

**Interfaces:**
- Consumes: `ZephyrEntity`, `ZephyrCoordinator.async_set_state`, `ZephyrConfigEntry`
- Produces: `ZephyrFan(coordinator)`, `ZephyrLight(coordinator)`, and `async_setup_entry` in each module

- [ ] **Step 1: Write the failing fan test**

Create `tests/test_fan.py`:

```python
"""Fan platform. Percentage <-> discrete speed conversion is the risk area."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.zephyr_connect.fan import ZephyrFan


def _coordinator(fan=0, max_speed=6):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.max_fan_speed = max_speed
    caps.urls = {}

    state = MagicMock()
    state.fan = fan
    state.is_online = True

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.async_set_state = AsyncMock()
    return coordinator


def test_speed_count_comes_from_capabilities():
    """Never hardcode 6 - other Zephyr models differ."""
    assert ZephyrFan(_coordinator(max_speed=4)).speed_count == 4


def test_off_reports_zero_percent():
    assert ZephyrFan(_coordinator(fan=0)).percentage == 0
    assert ZephyrFan(_coordinator(fan=0)).is_on is False


def test_max_speed_reports_one_hundred_percent():
    assert ZephyrFan(_coordinator(fan=6)).percentage == 100
    assert ZephyrFan(_coordinator(fan=6)).is_on is True


@pytest.mark.parametrize(("speed", "expected"), [(1, 17), (3, 50), (5, 83)])
def test_intermediate_speeds_map_to_percentages(speed, expected):
    assert ZephyrFan(_coordinator(fan=speed)).percentage == expected


async def test_set_percentage_writes_only_the_fan_field():
    """The device raises power itself. Writing power here would be
    redundant and risks fighting the device."""
    coordinator = _coordinator()
    await ZephyrFan(coordinator).async_set_percentage(50)
    coordinator.async_set_state.assert_awaited_once_with({"fan": 3})


async def test_set_percentage_zero_turns_off():
    coordinator = _coordinator(fan=4)
    await ZephyrFan(coordinator).async_set_percentage(0)
    coordinator.async_set_state.assert_awaited_once_with({"fan": 0})


async def test_turn_on_without_percentage_uses_lowest_speed():
    """HA may call turn_on with no percentage. Starting at speed 1 matches
    how the hood starts itself when a delay timer is armed."""
    coordinator = _coordinator()
    await ZephyrFan(coordinator).async_turn_on()
    coordinator.async_set_state.assert_awaited_once_with({"fan": 1})


async def test_turn_on_with_percentage_uses_it():
    coordinator = _coordinator()
    await ZephyrFan(coordinator).async_turn_on(percentage=100)
    coordinator.async_set_state.assert_awaited_once_with({"fan": 6})


async def test_turn_off_writes_zero():
    coordinator = _coordinator(fan=6)
    await ZephyrFan(coordinator).async_turn_off()
    coordinator.async_set_state.assert_awaited_once_with({"fan": 0})


async def test_percentage_never_exceeds_the_device_range():
    """A rounding bug that writes 7 to a 0-6 device is a real risk."""
    coordinator = _coordinator()
    await ZephyrFan(coordinator).async_set_percentage(99)
    written = coordinator.async_set_state.call_args.args[0]["fan"]
    assert 0 <= written <= 6
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_fan.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zephyr_connect.fan'`

- [ ] **Step 2b: Confirm where the scaling helper lives in your HA version**

`int_states_in_range` moved from `homeassistant.util.percentage` to
`homeassistant.util.scaling`. Check before writing the import, rather than
debugging an ImportError later:

```bash
cd /Users/ryanmorash/Developer/ha_zephyr
.venv/bin/python -c "
from homeassistant.util.scaling import int_states_in_range
print('use: homeassistant.util.scaling')
" 2>/dev/null || .venv/bin/python -c "
from homeassistant.util.percentage import int_states_in_range
print('use: homeassistant.util.percentage')
"
```

Use whichever path it prints in the next step. `percentage_to_ranged_value`
and `ranged_value_to_percentage` are in `homeassistant.util.percentage` in
both layouts.

- [ ] **Step 3: Write `custom_components/zephyr_connect/fan.py`**

```python
"""Fan platform for Zephyr Connect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)
from homeassistant.util.scaling import int_states_in_range

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one fan per hood."""
    async_add_entities(
        ZephyrFan(coordinator)
        for coordinator in entry.runtime_data
        if coordinator.capabilities.max_fan_speed > 0
    )


class ZephyrFan(ZephyrEntity, FanEntity):
    """The hood blower."""

    _attr_name = None  # the fan IS the device's primary entity
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED
        | FanEntityFeature.TURN_ON
        | FanEntityFeature.TURN_OFF
    )

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "fan")
        self._range = (1, coordinator.capabilities.max_fan_speed)

    @property
    def speed_count(self) -> int:
        return int_states_in_range(self._range)

    @property
    def percentage(self) -> int | None:
        state = self.hood
        if state is None:
            return None
        if not state.fan:
            return 0
        return ranged_value_to_percentage(self._range, state.fan)

    @property
    def is_on(self) -> bool | None:
        state = self.hood
        return None if state is None else bool(state.fan)

    async def async_set_percentage(self, percentage: int) -> None:
        """Write the fan level only.

        The device raises `power` to 1 on its own when the fan starts, so
        writing power here would be redundant and could fight it.
        """
        if percentage == 0:
            await self.coordinator.async_set_state({"fan": 0})
            return
        speed = round(percentage_to_ranged_value(self._range, percentage))
        speed = max(1, min(speed, self._range[1]))
        await self.coordinator.async_set_state({"fan": speed})

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        # No percentage means "just start" - use the quietest speed, which
        # matches how the hood starts itself when a delay timer is armed.
        await self.async_set_percentage(
            percentage if percentage is not None else
            ranged_value_to_percentage(self._range, 1)
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state({"fan": 0})
```

- [ ] **Step 4: Run the fan tests**

Run: `.venv/bin/python -m pytest tests/test_fan.py -v`
Expected: 11 passed

- [ ] **Step 5: Write the failing light test**

Create `tests/test_light.py`:

```python
"""Light platform. Brightness 0-255 <-> discrete levels 0-3."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.light import ColorMode

from custom_components.zephyr_connect.light import ZephyrLight


def _coordinator(light=0, max_level=3):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.max_light_level = max_level
    caps.supports_tru_hue = False
    caps.urls = {}

    state = MagicMock()
    state.light = light
    state.is_online = True

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.async_set_state = AsyncMock()
    return coordinator


def test_brightness_color_mode():
    """maxLightLevel > 1 means dimmable. truHueSupport is 0 on the reference
    device, so no colour temperature."""
    light = ZephyrLight(_coordinator())
    assert light.color_mode is ColorMode.BRIGHTNESS
    assert light.supported_color_modes == {ColorMode.BRIGHTNESS}


def test_off_state():
    light = ZephyrLight(_coordinator(light=0))
    assert light.is_on is False
    assert light.brightness == 0


def test_max_level_is_full_brightness():
    assert ZephyrLight(_coordinator(light=3)).brightness == 255


@pytest.mark.parametrize(("level", "expected"), [(1, 85), (2, 170), (3, 255)])
def test_levels_map_to_brightness(level, expected):
    assert ZephyrLight(_coordinator(light=level)).brightness == expected


async def test_turn_on_with_brightness_writes_a_level():
    coordinator = _coordinator()
    await ZephyrLight(coordinator).async_turn_on(brightness=170)
    coordinator.async_set_state.assert_awaited_once_with({"light": 2})


async def test_turn_on_without_brightness_uses_max():
    """A bare turn_on should give usable light, not the dimmest setting."""
    coordinator = _coordinator()
    await ZephyrLight(coordinator).async_turn_on()
    coordinator.async_set_state.assert_awaited_once_with({"light": 3})


async def test_turn_off_writes_zero():
    coordinator = _coordinator(light=3)
    await ZephyrLight(coordinator).async_turn_off()
    coordinator.async_set_state.assert_awaited_once_with({"light": 0})


async def test_low_brightness_never_rounds_to_off():
    """Rounding 1/255 down to level 0 would make turn_on silently do
    nothing, which reads as a broken light."""
    coordinator = _coordinator()
    await ZephyrLight(coordinator).async_turn_on(brightness=1)
    assert coordinator.async_set_state.call_args.args[0]["light"] >= 1


async def test_level_never_exceeds_the_device_range():
    coordinator = _coordinator()
    await ZephyrLight(coordinator).async_turn_on(brightness=255)
    assert coordinator.async_set_state.call_args.args[0]["light"] <= 3
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_light.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zephyr_connect.light'`

- [ ] **Step 7: Write `custom_components/zephyr_connect/light.py`**

```python
"""Light platform for Zephyr Connect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one light per hood that has one."""
    async_add_entities(
        ZephyrLight(coordinator)
        for coordinator in entry.runtime_data
        if coordinator.capabilities.max_light_level > 0
    )


class ZephyrLight(ZephyrEntity, LightEntity):
    """The hood work light."""

    _attr_translation_key = "hood_light"
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "light")
        self._range = (1, coordinator.capabilities.max_light_level)

    @property
    def is_on(self) -> bool | None:
        state = self.hood
        return None if state is None else bool(state.light)

    @property
    def brightness(self) -> int | None:
        state = self.hood
        if state is None:
            return None
        if not state.light:
            return 0
        percent = ranged_value_to_percentage(self._range, state.light)
        return round(percent * 255 / 100)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Write the light level only; the device raises power itself."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None:
            level = self._range[1]
        else:
            percent = round(brightness * 100 / 255)
            level = round(percentage_to_ranged_value(self._range, percent))
            # Never round down to 0: turn_on must always produce light.
            level = max(1, min(level, self._range[1]))
        await self.coordinator.async_set_state({"light": level})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state({"light": 0})
```

- [ ] **Step 8: Run the light tests**

Run: `.venv/bin/python -m pytest tests/test_light.py -v`
Expected: 11 passed

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: add fan and light platforms"
```

---

### Task 5: Switch, number and button platforms

Every control here **actuates the hood** rather than storing a preference — validated behaviour, not an assumption. The delay-off number in particular starts the fan when written, which its description must say plainly.

**Files:**
- Replace (no-op stubs from Task 1): `custom_components/zephyr_connect/switch.py`, `number.py`, `button.py`
- Test: `tests/test_switch.py`, `tests/test_number.py`, `tests/test_button.py`

**Interfaces:**
- Consumes: `ZephyrEntity`, `ZephyrCoordinator.async_set_state`, `const.DELAY_TIMER_MAX_MINUTES`, `const.DELAY_TIMER_STEP_SECONDS`
- Produces: `ZephyrPowerSwitch`, `ZephyrCleanAirSwitch`, `ZephyrDelayNumber`, `ZephyrResetGreaseFilterButton`, plus `async_setup_entry` in each module

- [ ] **Step 1: Write the failing switch test**

Create `tests/test_switch.py`:

```python
"""Power and clean-air switches. Both actuate the hood."""

from unittest.mock import AsyncMock, MagicMock

from custom_components.zephyr_connect.switch import (
    ZephyrCleanAirSwitch,
    ZephyrPowerSwitch,
)


def _coordinator(power=0, clean_air=0):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.urls = {}

    state = MagicMock()
    state.power = power
    state.set_clean_air_function = clean_air
    state.is_online = True

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.async_set_state = AsyncMock()
    return coordinator


def test_power_reflects_reported_state():
    assert ZephyrPowerSwitch(_coordinator(power=1)).is_on is True
    assert ZephyrPowerSwitch(_coordinator(power=0)).is_on is False


async def test_power_on_restores_previous_levels():
    """Validated: power=1 restored fan 6 and light 1 together. We just write
    1 and let the device decide what to restore."""
    coordinator = _coordinator()
    await ZephyrPowerSwitch(coordinator).async_turn_on()
    coordinator.async_set_state.assert_awaited_once_with({"power": 1})


async def test_power_off_stops_everything():
    coordinator = _coordinator(power=1)
    await ZephyrPowerSwitch(coordinator).async_turn_off()
    coordinator.async_set_state.assert_awaited_once_with({"power": 0})


def test_clean_air_reflects_reported_state():
    assert ZephyrCleanAirSwitch(_coordinator(clean_air=1)).is_on is True


async def test_clean_air_on_writes_one():
    """Validated side effect: this also starts the fan at speed 1."""
    coordinator = _coordinator()
    await ZephyrCleanAirSwitch(coordinator).async_turn_on()
    coordinator.async_set_state.assert_awaited_once_with(
        {"setcleanairfunction": 1}
    )


async def test_clean_air_off_writes_zero():
    coordinator = _coordinator(clean_air=1)
    await ZephyrCleanAirSwitch(coordinator).async_turn_off()
    coordinator.async_set_state.assert_awaited_once_with(
        {"setcleanairfunction": 0}
    )


def test_clean_air_is_not_a_config_entity():
    """It runs the fan, so it belongs beside the controls rather than
    buried in the configuration section."""
    assert ZephyrCleanAirSwitch(_coordinator()).entity_category is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_switch.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zephyr_connect.switch'`

- [ ] **Step 3: Write `custom_components/zephyr_connect/switch.py`**

```python
"""Switch platform for Zephyr Connect."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the power and clean-air switches for each hood."""
    entities: list[ZephyrEntity] = []
    for coordinator in entry.runtime_data:
        entities.append(ZephyrPowerSwitch(coordinator))
        entities.append(ZephyrCleanAirSwitch(coordinator))
    async_add_entities(entities)


class ZephyrPowerSwitch(ZephyrEntity, SwitchEntity):
    """Master power.

    Validated behaviour: writing 0 turns everything off; writing 1 restores
    the previously running levels (observed restoring fan 6 and light 1
    together). It is NOT a precondition - fan and light can be set directly
    while power reads 0, and the device raises power itself.
    """

    _attr_translation_key = "power"

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "power")

    @property
    def is_on(self) -> bool | None:
        state = self.hood
        return None if state is None else bool(state.power)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state({"power": 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state({"power": 0})


class ZephyrCleanAirSwitch(ZephyrEntity, SwitchEntity):
    """Clean air mode.

    An operating mode, not a setting: enabling it starts the fan at speed 1.
    Deliberately NOT an EntityCategory.CONFIG entity - it actuates the hood,
    so it belongs alongside the fan and light.
    """

    _attr_translation_key = "clean_air"

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "clean_air")

    @property
    def is_on(self) -> bool | None:
        state = self.hood
        return None if state is None else bool(state.set_clean_air_function)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state({"setcleanairfunction": 1})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_state({"setcleanairfunction": 0})
```

- [ ] **Step 4: Run the switch tests**

Run: `.venv/bin/python -m pytest tests/test_switch.py -v`
Expected: 7 passed

- [ ] **Step 5: Write the failing number test**

Create `tests/test_number.py`:

```python
"""Delay-off number. Displayed in minutes, written in seconds."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.zephyr_connect.number import ZephyrDelayNumber


def _coordinator(set_delay=0):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.urls = {}

    state = MagicMock()
    state.set_delay_timer = set_delay
    state.is_online = True

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.async_set_state = AsyncMock()
    return coordinator


@pytest.mark.parametrize(("seconds", "minutes"), [(0, 0), (300, 5), (600, 10), (60, 1)])
def test_seconds_are_displayed_as_minutes(seconds, minutes):
    """The device stores seconds; users think in minutes."""
    assert ZephyrDelayNumber(_coordinator(set_delay=seconds)).native_value == minutes


@pytest.mark.parametrize(("minutes", "seconds"), [(5, 300), (10, 600), (1, 60)])
async def test_setting_minutes_writes_seconds(minutes, seconds):
    coordinator = _coordinator()
    await ZephyrDelayNumber(coordinator).async_set_native_value(float(minutes))
    coordinator.async_set_state.assert_awaited_once_with(
        {"setdelaytimer": seconds}
    )


async def test_zero_disables_the_timer():
    coordinator = _coordinator(set_delay=300)
    await ZephyrDelayNumber(coordinator).async_set_native_value(0)
    coordinator.async_set_state.assert_awaited_once_with({"setdelaytimer": 0})


async def test_only_setdelaytimer_is_written():
    """Validated: the DEVICE derives delaytimer from setdelaytimer and
    counts it down. Writing delaytimer ourselves is unnecessary."""
    coordinator = _coordinator()
    await ZephyrDelayNumber(coordinator).async_set_native_value(5)
    written = coordinator.async_set_state.call_args.args[0]
    assert "delaytimer" not in written


def test_is_a_config_entity():
    """Unlike clean air, this is a duration setting rather than a mode, so
    it belongs in the configuration section."""
    from homeassistant.helpers.entity import EntityCategory

    assert (
        ZephyrDelayNumber(_coordinator()).entity_category
        is EntityCategory.CONFIG
    )
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_number.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zephyr_connect.number'`

- [ ] **Step 7: Write `custom_components/zephyr_connect/number.py`**

```python
"""Number platform for Zephyr Connect."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZephyrConfigEntry
from .const import DELAY_TIMER_MAX_MINUTES
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity

SECONDS_PER_MINUTE = 60


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the delay-off number for each hood."""
    async_add_entities(
        ZephyrDelayNumber(coordinator) for coordinator in entry.runtime_data
    )


class ZephyrDelayNumber(ZephyrEntity, NumberEntity):
    """Delay-off duration.

    The device stores seconds; this presents minutes because that is how
    users think about it and how the vendor app labels it.

    ACTUATES: writing a non-zero value starts the fan at speed 1. That is
    validated device behaviour, not a bug - a delay-off timer implies the
    hood should run. The description must say so, because a number entity
    that starts an appliance is otherwise a surprise.

    The vendor app offers only 5 and 10 minutes, but the device accepts
    arbitrary values, so this exposes the full range. The upper bound is a
    UI cap, not a device limit - the real ceiling is unknown.
    """

    _attr_translation_key = "delay_off"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_native_min_value = 0
    _attr_native_max_value = DELAY_TIMER_MAX_MINUTES
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "delay_off")

    @property
    def native_value(self) -> float | None:
        state = self.hood
        if state is None:
            return None
        return state.set_delay_timer / SECONDS_PER_MINUTE

    async def async_set_native_value(self, value: float) -> None:
        """Write setdelaytimer only.

        The device derives `delaytimer` from this and counts it down itself,
        reporting once a minute. Writing `delaytimer` too would duplicate
        device-managed state.
        """
        await self.coordinator.async_set_state(
            {"setdelaytimer": int(value * SECONDS_PER_MINUTE)}
        )
```

- [ ] **Step 8: Run the number tests**

Run: `.venv/bin/python -m pytest tests/test_number.py -v`
Expected: 9 passed

- [ ] **Step 9: Write the failing button test**

Create `tests/test_button.py`:

```python
"""Reset grease filter button. Destructive and unvalidated."""

from unittest.mock import AsyncMock, MagicMock

from custom_components.zephyr_connect.button import ZephyrResetGreaseFilterButton


def _coordinator():
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.urls = {}

    state = MagicMock()
    state.is_online = True

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.async_set_state = AsyncMock()
    return coordinator


async def test_press_writes_the_reset_flag():
    coordinator = _coordinator()
    await ZephyrResetGreaseFilterButton(coordinator).async_press()
    coordinator.async_set_state.assert_awaited_once_with(
        {"resetgreasefilter": 1}
    )


def test_is_a_config_entity():
    """Filter maintenance, not a daily control."""
    from homeassistant.helpers.entity import EntityCategory

    assert (
        ZephyrResetGreaseFilterButton(_coordinator()).entity_category
        is EntityCategory.CONFIG
    )


def test_unique_id_is_stable():
    button = ZephyrResetGreaseFilterButton(_coordinator())
    assert button.unique_id.endswith("_reset_grease_filter")
```

- [ ] **Step 10: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_button.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zephyr_connect.button'`

- [ ] **Step 11: Write `custom_components/zephyr_connect/button.py`**

```python
"""Button platform for Zephyr Connect."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the filter reset button for each hood."""
    async_add_entities(
        ZephyrResetGreaseFilterButton(coordinator)
        for coordinator in entry.runtime_data
    )


class ZephyrResetGreaseFilterButton(ZephyrEntity, ButtonEntity):
    """Reset the grease filter usage counter after cleaning.

    DESTRUCTIVE AND UNVALIDATED. Pressing this zeroes
    `usegreasefiltertime`, which cannot be reconstructed. The write has
    never been tested against hardware - validating it requires actually
    cleaning the filter, since a test press would destroy the very counter
    it verifies. Ships on that understanding.
    """

    _attr_translation_key = "reset_grease_filter"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: ZephyrCoordinator) -> None:
        super().__init__(coordinator, "reset_grease_filter")

    async def async_press(self) -> None:
        await self.coordinator.async_set_state({"resetgreasefilter": 1})
```

- [ ] **Step 12: Run the button tests**

Run: `.venv/bin/python -m pytest tests/test_button.py -v`
Expected: 3 passed

- [ ] **Step 13: Commit**

```bash
git add -A
git commit -m "feat: add switch, number and button platforms"
```

---

### Task 6: Sensor and binary sensor platforms

The filter-life sensor carries the formula verified against the vendor app's 82%. Get its unit conversion wrong and every user sees a wrong number.

**Files:**
- Replace (no-op stubs from Task 1): `custom_components/zephyr_connect/sensor.py`, `binary_sensor.py`
- Test: `tests/test_sensor.py`, `tests/test_binary_sensor.py`

**Interfaces:**
- Consumes: `ZephyrEntity`, `ZephyrCoordinator`
- Produces: `ZephyrSensor`, `ZephyrSensorDescription`, `ZephyrBinarySensor`, `ZephyrBinarySensorDescription`, plus `async_setup_entry` in each module

- [ ] **Step 1: Write the failing sensor test**

Create `tests/test_sensor.py`:

```python
"""Sensors. The filter-life calculation is the load-bearing one."""

from unittest.mock import MagicMock

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfTime

from custom_components.zephyr_connect.sensor import SENSORS, ZephyrSensor


def _coordinator(**state_kwargs):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.max_grease_filter_hours = 60
    caps.max_charcoal_filter_hours = 200
    caps.urls = {
        "GreaseFilterWebstoreURL": "https://store.zephyronline.com/en/baffle"
    }

    state = MagicMock()
    defaults = {
        "use_grease_filter_time": 643,
        "use_charcoal_filter_time": 0,
        "use_fan_time": 1979,
        "use_light_time": 2833,
        "delay_timer": 0,
        "act": "Disabled",
        "set_recirculating": 0,
        "is_online": True,
    }
    for key, value in {**defaults, **state_kwargs}.items():
        setattr(state, key, value)

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    return coordinator


def _sensor(key, **state_kwargs):
    description = next(d for d in SENSORS if d.key == key)
    return ZephyrSensor(_coordinator(**state_kwargs), description)


def test_grease_filter_matches_the_vendor_app():
    """643 minutes against a 60-hour life. The vendor app displays 82%;
    this formula was verified against that exact reading."""
    assert _sensor("grease_filter").native_value == pytest.approx(82.1, abs=0.1)


def test_grease_filter_is_a_percentage():
    sensor = _sensor("grease_filter")
    assert sensor.native_unit_of_measurement == PERCENTAGE


def test_fresh_grease_filter_reads_full():
    assert _sensor("grease_filter", use_grease_filter_time=0).native_value == 100


def test_exhausted_grease_filter_clamps_at_zero():
    """Past its life the raw formula goes negative, which HA would render
    as a nonsensical value."""
    assert _sensor(
        "grease_filter", use_grease_filter_time=999_999
    ).native_value == 0


def test_grease_filter_exposes_the_raw_counter():
    """The unit inference lives in the formula; surfacing the raw minutes
    makes a wrong assumption visible instead of silent."""
    attrs = _sensor("grease_filter").extra_state_attributes
    assert attrs["used_minutes"] == 643
    assert attrs["life_hours"] == 60


def test_grease_filter_links_to_the_replacement_part():
    attrs = _sensor("grease_filter").extra_state_attributes
    assert attrs["store_url"].startswith("https://")


def test_charcoal_filter_reads_full_on_a_ducted_hood():
    """Ducted installs never consume charcoal, so 100% is correct rather
    than misleading."""
    assert _sensor("charcoal_filter").native_value == 100


def test_runtime_sensors_are_hours():
    """Inferred, not measured - see the plan's protocol section. If this
    proves wrong it is a one-line change."""
    sensor = _sensor("fan_runtime")
    assert sensor.native_value == 1979
    assert sensor.native_unit_of_measurement == UnitOfTime.HOURS
    assert sensor.state_class is SensorStateClass.TOTAL_INCREASING


def test_delay_remaining_is_seconds():
    """The device counts down in seconds, reporting once a minute."""
    sensor = _sensor("delay_remaining", delay_timer=240)
    assert sensor.native_value == 240
    assert sensor.native_unit_of_measurement == UnitOfTime.SECONDS


def test_act_sensor_reports_airflow_control_technology():
    """ACT is Zephyr's Airflow Control Technology - an airflow cap for
    make-up-air code compliance, set physically on the hood. Read-only."""
    assert _sensor("act").native_value == "Disabled"


@pytest.mark.parametrize(
    ("value", "expected"), [(0, "ducted"), (1, "recirculating")]
)
def test_recirculating_is_read_only_text(value, expected):
    """Read-only by design: writing it would start charcoal accounting for
    a filter that may not be physically installed."""
    assert _sensor("recirculating", set_recirculating=value).native_value == expected


def test_sensors_return_none_before_the_first_update():
    coordinator = _coordinator()
    coordinator.data = None
    description = next(d for d in SENSORS if d.key == "grease_filter")
    assert ZephyrSensor(coordinator, description).native_value is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_sensor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zephyr_connect.sensor'`

- [ ] **Step 3: Write `custom_components/zephyr_connect/sensor.py`**

```python
"""Sensor platform for Zephyr Connect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyzephyrconnect import HoodCapabilities, HoodState

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity

MINUTES_PER_HOUR = 60


def _filter_remaining(used_minutes: int, life_hours: int) -> float | None:
    """Percentage of filter life left.

    Verified against the vendor app: 643 minutes against a 60-hour life
    yields 82.1%, and the app displays 82%.

    The counter is in MINUTES and the capability maximum is in HOURS - a
    mismatch that is easy to miss and wrong by 60x if conflated.
    """
    if life_hours <= 0:
        return None
    used_fraction = used_minutes / (life_hours * MINUTES_PER_HOUR)
    # Clamp: past end-of-life the raw value goes negative.
    return round(max(0.0, min(1.0, 1 - used_fraction)) * 100, 1)


@dataclass(frozen=True, kw_only=True)
class ZephyrSensorDescription(SensorEntityDescription):
    """Describes a Zephyr sensor."""

    value_fn: Callable[[HoodState, HoodCapabilities], Any]
    attributes_fn: Callable[[HoodState, HoodCapabilities], dict[str, Any]] | None = None
    exists_fn: Callable[[HoodCapabilities], bool] = lambda _caps: True


SENSORS: tuple[ZephyrSensorDescription, ...] = (
    ZephyrSensorDescription(
        key="grease_filter",
        translation_key="grease_filter",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state, caps: _filter_remaining(
            state.use_grease_filter_time, caps.max_grease_filter_hours
        ),
        attributes_fn=lambda state, caps: {
            "used_minutes": state.use_grease_filter_time,
            "life_hours": caps.max_grease_filter_hours,
            "store_url": caps.urls.get("GreaseFilterWebstoreURL"),
            "video_url": caps.urls.get("GreaseFilterVideoURL"),
        },
        exists_fn=lambda caps: caps.max_grease_filter_hours > 0,
    ),
    ZephyrSensorDescription(
        key="charcoal_filter",
        translation_key="charcoal_filter",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda state, caps: _filter_remaining(
            state.use_charcoal_filter_time, caps.max_charcoal_filter_hours
        ),
        attributes_fn=lambda state, caps: {
            "used_minutes": state.use_charcoal_filter_time,
            "life_hours": caps.max_charcoal_filter_hours,
            "store_url": caps.urls.get("CharcoalFilterWebstoreURL"),
            "video_url": caps.urls.get("CharcoalFilterVideoURL"),
        },
        exists_fn=lambda caps: caps.max_charcoal_filter_hours > 0,
    ),
    ZephyrSensorDescription(
        key="fan_runtime",
        translation_key="fan_runtime",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state, _caps: state.use_fan_time,
    ),
    ZephyrSensorDescription(
        key="light_runtime",
        translation_key="light_runtime",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda state, _caps: state.use_light_time,
    ),
    ZephyrSensorDescription(
        key="delay_remaining",
        translation_key="delay_remaining",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        value_fn=lambda state, _caps: state.delay_timer,
    ),
    ZephyrSensorDescription(
        key="act",
        translation_key="act",
        entity_category=EntityCategory.DIAGNOSTIC,
        # Airflow Control Technology: Zephyr's airflow cap for make-up-air
        # code compliance. Configured PHYSICALLY on the hood and not
        # settable from the cloud, so this is strictly a readout - but a
        # meaningful one, since ACT being enabled explains why the hood's
        # airflow is limited. Enabled by default for that reason.
        value_fn=lambda state, _caps: state.act or None,
    ),
    ZephyrSensorDescription(
        key="recirculating",
        translation_key="recirculating",
        entity_category=EntityCategory.DIAGNOSTIC,
        # Read-only by design: writing it would begin charcoal-filter
        # accounting for a filter that may not be installed.
        value_fn=lambda state, _caps: (
            "recirculating" if state.set_recirculating else "ducted"
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors for each hood, gated on capabilities."""
    async_add_entities(
        ZephyrSensor(coordinator, description)
        for coordinator in entry.runtime_data
        for description in SENSORS
        if description.exists_fn(coordinator.capabilities)
    )


class ZephyrSensor(ZephyrEntity, SensorEntity):
    """A value read from the hood's shadow."""

    entity_description: ZephyrSensorDescription

    def __init__(
        self, coordinator: ZephyrCoordinator, description: ZephyrSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        state = self.hood
        if state is None:
            return None
        return self.entity_description.value_fn(state, self.coordinator.capabilities)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self.hood
        if state is None or self.entity_description.attributes_fn is None:
            return None
        attrs = self.entity_description.attributes_fn(
            state, self.coordinator.capabilities
        )
        return {k: v for k, v in attrs.items() if v is not None}
```

- [ ] **Step 4: Run the sensor tests**

Run: `.venv/bin/python -m pytest tests/test_sensor.py -v`
Expected: 13 passed

- [ ] **Step 5: Write the failing binary sensor test**

Create `tests/test_binary_sensor.py`:

```python
"""Binary sensors. All PROBLEM class - they signal faults, not states."""

from unittest.mock import MagicMock

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

from custom_components.zephyr_connect.binary_sensor import (
    BINARY_SENSORS,
    ZephyrBinarySensor,
)


def _coordinator(**state_kwargs):
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    caps.serial = "1234567XYZ"
    caps.mac = "00:00:5e:00:53:00"
    caps.manufacturer = "ZEPHYR"
    caps.max_charcoal_filter_hours = 200
    caps.urls = {}

    state = MagicMock()
    defaults = {
        "clean_grease_filters": 0,
        "clean_charcoal_filters": 0,
        "alarm_grease_filter": 0,
        "alarm_fan": 0,
        "fan_warning": 0,
        "alarm_fault_code": 0,
        "fault_codes": (),
        "is_online": True,
    }
    for key, value in {**defaults, **state_kwargs}.items():
        setattr(state, key, value)

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = caps.thing_name
    coordinator.data = state
    coordinator.last_update_success = True
    return coordinator


def _sensor(key, **state_kwargs):
    description = next(d for d in BINARY_SENSORS if d.key == key)
    return ZephyrBinarySensor(_coordinator(**state_kwargs), description)


def test_all_are_problem_class():
    for description in BINARY_SENSORS:
        assert description.device_class is BinarySensorDeviceClass.PROBLEM


def test_grease_filter_clean_is_off():
    assert _sensor("grease_filter_due").is_on is False


def test_grease_filter_due_is_on():
    assert _sensor("grease_filter_due", clean_grease_filters=1).is_on is True


def test_grease_filter_exposes_the_overdue_alarm():
    """cleangreasefilters means due; alarmgreasefilter means overdue. One
    entity, with the severity as an attribute."""
    sensor = _sensor("grease_filter_due", alarm_grease_filter=1)
    assert sensor.extra_state_attributes["overdue"] is True


@pytest.mark.parametrize(
    ("alarm", "warning"), [(1, 0), (0, 1), (1, 1)]
)
def test_fan_fault_triggers_on_either_signal(alarm, warning):
    """alarmfan and fanwarning may differ in severity; either means the fan
    needs attention."""
    assert _sensor("fan_fault", alarm_fan=alarm, fan_warning=warning).is_on is True


def test_fan_fault_clear():
    assert _sensor("fan_fault").is_on is False


def test_fault_reports_codes_as_an_attribute():
    sensor = _sensor("fault", alarm_fault_code=1, fault_codes=("E3", "E7"))
    assert sensor.is_on is True
    assert sensor.extra_state_attributes["fault_codes"] == ["E3", "E7"]


def test_fault_clear_has_empty_codes():
    sensor = _sensor("fault")
    assert sensor.is_on is False
    assert sensor.extra_state_attributes["fault_codes"] == []


def test_returns_none_before_the_first_update():
    coordinator = _coordinator()
    coordinator.data = None
    description = next(d for d in BINARY_SENSORS if d.key == "fan_fault")
    assert ZephyrBinarySensor(coordinator, description).is_on is None
```

- [ ] **Step 6: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_binary_sensor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zephyr_connect.binary_sensor'`

- [ ] **Step 7: Write `custom_components/zephyr_connect/binary_sensor.py`**

```python
"""Binary sensor platform for Zephyr Connect."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from pyzephyrconnect import HoodCapabilities, HoodState

from . import ZephyrConfigEntry
from .coordinator import ZephyrCoordinator
from .entity import ZephyrEntity


@dataclass(frozen=True, kw_only=True)
class ZephyrBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Zephyr binary sensor."""

    is_on_fn: Callable[[HoodState], bool]
    attributes_fn: Callable[[HoodState], dict[str, Any]] | None = None
    exists_fn: Callable[[HoodCapabilities], bool] = lambda _caps: True


BINARY_SENSORS: tuple[ZephyrBinarySensorDescription, ...] = (
    ZephyrBinarySensorDescription(
        key="grease_filter_due",
        translation_key="grease_filter_due",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: bool(state.clean_grease_filters),
        # `cleangreasefilters` means due, `alarmgreasefilter` means overdue.
        # One entity with severity as an attribute beats two that would
        # both fire for the same filter.
        attributes_fn=lambda state: {"overdue": bool(state.alarm_grease_filter)},
    ),
    ZephyrBinarySensorDescription(
        key="charcoal_filter_due",
        translation_key="charcoal_filter_due",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: bool(state.clean_charcoal_filters),
        exists_fn=lambda caps: caps.max_charcoal_filter_hours > 0,
    ),
    ZephyrBinarySensorDescription(
        key="fan_fault",
        translation_key="fan_fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # alarmfan and fanwarning may differ in severity - unconfirmed,
        # neither has ever fired on the reference device. Either means the
        # fan needs attention.
        is_on_fn=lambda state: bool(state.alarm_fan or state.fan_warning),
        attributes_fn=lambda state: {
            "alarm": bool(state.alarm_fan),
            "warning": bool(state.fan_warning),
        },
    ),
    ZephyrBinarySensorDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=lambda state: bool(state.alarm_fault_code),
        attributes_fn=lambda state: {"fault_codes": list(state.fault_codes)},
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZephyrConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors for each hood, gated on capabilities."""
    async_add_entities(
        ZephyrBinarySensor(coordinator, description)
        for coordinator in entry.runtime_data
        for description in BINARY_SENSORS
        if description.exists_fn(coordinator.capabilities)
    )


class ZephyrBinarySensor(ZephyrEntity, BinarySensorEntity):
    """A fault or maintenance signal from the hood."""

    entity_description: ZephyrBinarySensorDescription

    def __init__(
        self,
        coordinator: ZephyrCoordinator,
        description: ZephyrBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        state = self.hood
        return None if state is None else self.entity_description.is_on_fn(state)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        state = self.hood
        if state is None or self.entity_description.attributes_fn is None:
            return None
        return self.entity_description.attributes_fn(state)
```

- [ ] **Step 8: Run the binary sensor tests**

Run: `.venv/bin/python -m pytest tests/test_binary_sensor.py -v`
Expected: 11 passed

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: add sensor and binary sensor platforms"
```

---

### Task 7: Diagnostics and translations

For a reverse-engineered protocol, diagnostics is the highest-leverage file in the integration: it's how another Zephyr owner's unknown fields reach you without asking them to run Python.

**Files:**
- Create: `custom_components/zephyr_connect/diagnostics.py`
- Modify: `custom_components/zephyr_connect/strings.json` (add the `entity` section)
- Create: `custom_components/zephyr_connect/translations/en.json`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Consumes: `ZephyrConfigEntry`, `ZephyrCoordinator`
- Produces: `async_get_config_entry_diagnostics(hass, entry) -> dict`

- [ ] **Step 1: Write the failing diagnostics test**

Create `tests/test_diagnostics.py`:

```python
"""Diagnostics must be useful for debugging AND free of personal data."""

import json
from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant

from custom_components.zephyr_connect.diagnostics import (
    async_get_config_entry_diagnostics,
)

THING = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
SERIAL = "1234567XYZ"
MAC = "00:00:5e:00:53:00"


def _entry():
    caps = MagicMock()
    caps.thing_name = THING
    caps.serial = SERIAL
    caps.mac = MAC
    caps.model = "AK7400AS"
    caps.manufacturer = "ZEPHYR"
    caps.max_fan_speed = 6
    caps.max_light_level = 3
    caps.supports_tru_hue = False
    caps.raw = {
        "thingName": THING,
        "SN": SERIAL,
        "MAC": MAC,
        "location": {"lng": "-XX.XXXX", "lat": "YY.YYYY"},
        "modelName": "AK7400AS",
        "maxFanSpeed": 6,
        "truHueSupport": 0,
    }

    state = MagicMock()
    state.raw = {"fan": 0, "light": 1, "power": 1, "somethingNew": 42}

    coordinator = MagicMock()
    coordinator.capabilities = caps
    coordinator.thing_name = THING
    coordinator.data = state
    coordinator.last_update_success = True
    coordinator.client.connected = True

    entry = MagicMock()
    entry.runtime_data = [coordinator]
    entry.data = {"username": "user@example.com", "password": "hunter2"}
    return entry


async def test_no_personal_data_leaks(hass: HomeAssistant) -> None:
    """thingName, serial, MAC and coordinates identify a home and its owner.
    A user pasting diagnostics into a GitHub issue must not expose them."""
    result = await async_get_config_entry_diagnostics(hass, _entry())
    blob = json.dumps(result)

    for secret in (THING, SERIAL, MAC, "-XX.XXXX", "YY.YYYY", "hunter2",
                   "user@example.com"):
        assert secret not in blob, f"{secret!r} leaked into diagnostics"


async def test_unknown_shadow_fields_survive(hass: HomeAssistant) -> None:
    """The whole point: a field we do not model yet must reach the
    maintainer, because that is how new models get characterised."""
    result = await async_get_config_entry_diagnostics(hass, _entry())
    assert result["hoods"][0]["state"]["somethingNew"] == 42


async def test_capabilities_are_included(hass: HomeAssistant) -> None:
    """Capabilities explain why entities exist or do not."""
    caps = (await async_get_config_entry_diagnostics(hass, _entry()))["hoods"][0][
        "capabilities"
    ]
    assert caps["maxFanSpeed"] == 6
    assert caps["truHueSupport"] == 0


async def test_transport_health_is_reported(hass: HomeAssistant) -> None:
    """'Is push working?' is the first question for any stale-data report."""
    result = await async_get_config_entry_diagnostics(hass, _entry())
    assert result["hoods"][0]["connected"] is True
    assert result["hoods"][0]["last_update_success"] is True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_diagnostics.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'custom_components.zephyr_connect.diagnostics'`

- [ ] **Step 3: Write `custom_components/zephyr_connect/diagnostics.py`**

```python
"""Diagnostics for Zephyr Connect.

For a reverse-engineered protocol this is the highest-leverage file here:
when someone with a different Zephyr model installs this integration, their
diagnostics download is how unknown fields get characterised - without
asking them to run Python.

That value only holds if the output is safe to paste into a public issue,
so every identifier is redacted.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import ZephyrConfigEntry

# Identifies a specific home and its owner. `location` carries precise
# coordinates from the vendor's device list.
REDACT_KEYS = {"thingName", "SN", "MAC", "location", CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ZephyrConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    hoods: list[dict[str, Any]] = []
    for coordinator in entry.runtime_data:
        state = coordinator.data
        hoods.append(
            {
                # Capabilities explain why entities exist or do not, and
                # carry the model's limits.
                "capabilities": async_redact_data(
                    dict(coordinator.capabilities.raw), REDACT_KEYS
                ),
                # The full shadow, including fields this version does not
                # model - that is precisely what makes this useful.
                "state": async_redact_data(
                    dict(state.raw) if state is not None else {}, REDACT_KEYS
                ),
                "connected": coordinator.client.connected,
                "last_update_success": coordinator.last_update_success,
            }
        )

    return {
        "entry": async_redact_data(dict(entry.data), REDACT_KEYS),
        "hoods": hoods,
    }
```

- [ ] **Step 4: Run the diagnostics tests**

Run: `.venv/bin/python -m pytest tests/test_diagnostics.py -v`
Expected: 4 passed

- [ ] **Step 5: Add the entity section to `custom_components/zephyr_connect/strings.json`**

Merge this `"entity"` block alongside the existing `"config"` block:

```json
{
  "entity": {
    "light": {
      "hood_light": { "name": "Light" }
    },
    "switch": {
      "power": { "name": "Power" },
      "clean_air": { "name": "Clean air" }
    },
    "number": {
      "delay_off": { "name": "Delay off" }
    },
    "button": {
      "reset_grease_filter": { "name": "Reset grease filter" }
    },
    "sensor": {
      "grease_filter": { "name": "Grease filter" },
      "charcoal_filter": { "name": "Charcoal filter" },
      "fan_runtime": { "name": "Fan runtime" },
      "light_runtime": { "name": "Light runtime" },
      "delay_remaining": { "name": "Delay remaining" },
      "act": { "name": "Airflow Control Technology" },
      "recirculating": { "name": "Ventilation" }
    },
    "binary_sensor": {
      "grease_filter_due": { "name": "Grease filter" },
      "charcoal_filter_due": { "name": "Charcoal filter" },
      "fan_fault": { "name": "Fan" },
      "fault": { "name": "Fault" }
    }
  }
}
```

- [ ] **Step 6: Copy strings to translations**

Home Assistant serves UI copy from `translations/`, while `strings.json` is the source. They must match.

```bash
cd /Users/ryanmorash/Developer/ha_zephyr
mkdir -p custom_components/zephyr_connect/translations
cp custom_components/zephyr_connect/strings.json \
   custom_components/zephyr_connect/translations/en.json
```

- [ ] **Step 7: Verify every translation key referenced in code exists**

```bash
cd /Users/ryanmorash/Developer/ha_zephyr
.venv/bin/python - <<'EOF'
import json, pathlib, re

strings = json.loads(
    pathlib.Path("custom_components/zephyr_connect/strings.json").read_text()
)
declared = {
    key
    for platform in strings.get("entity", {}).values()
    for key in platform
}
used = set()
for path in pathlib.Path("custom_components/zephyr_connect").glob("*.py"):
    used |= set(re.findall(r'translation_key="([^"]+)"', path.read_text()))

missing = used - declared
extra = declared - used
print("missing from strings.json:", sorted(missing) or "none")
print("declared but unused:", sorted(extra) or "none")
assert not missing, f"translation keys used in code but not declared: {missing}"
EOF
```

Expected: `missing from strings.json: none`

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "feat: add redacted diagnostics and entity translations"
```

---

### Task 8: README, HACS packaging and release preparation

**Files:**
- Create: `README.md`
- Modify: `custom_components/zephyr_connect/manifest.json` (pin the library version)
- Create: `.github/workflows/validate.yml`

**Interfaces:**
- Consumes: everything above
- Produces: a HACS-installable repository

- [ ] **Step 1: Write `README.md`**

```markdown
# Zephyr Connect for Home Assistant

Control and monitor Zephyr range hoods from Home Assistant.

These hoods expose no local API — everything is a cloud round trip through
AWS IoT device shadows. The protocol was reverse-engineered; see
[pyzephyrconnect](https://github.com/RyanMorash/pyzephyrconnect).

## Install

Add this repository as a HACS custom repository, install **Zephyr Connect**,
restart Home Assistant, then add the integration and sign in with the account
you use in the Zephyr Connect app.

## Entities

| Entity | Notes |
|---|---|
| Fan | Speeds gated on the model's `maxFanSpeed` |
| Light | Brightness gated on `maxLightLevel` |
| Power switch | Off stops everything; on restores the previous levels |
| Clean air switch | **Starts the fan at speed 1** when enabled |
| Delay off | Minutes. **Setting a value starts the fan at speed 1** |
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

**Reset grease filter is destructive and untested.** It zeroes a counter that
cannot be reconstructed, and the write has never been validated against
hardware — doing so requires actually cleaning a filter. Press it only when
you have genuinely cleaned yours.

**Ducted vs recirculating is read-only.** Changing it would start charcoal
filter accounting for a filter that may not be installed. Use the vendor app
if you genuinely need to change it.

## Reporting problems

Download diagnostics from the device page and attach them to your issue. They
are redacted — no serial, MAC, thing name or coordinates. If you have a model
we have not seen, that download is what lets support for it get added.

## License

GPL-3.0-or-later
```

- [ ] **Step 2: Pin the library version in `manifest.json`**

Change the `requirements` line from the git URL to a released version. HACS-published integrations must depend on real versions, not branches:

```json
  "requirements": ["pyzephyrconnect==0.1.0"],
```

- [ ] **Step 3: Verify the pinned version installs**

```bash
cd /Users/ryanmorash/Developer/ha_zephyr
.venv/bin/pip install "pyzephyrconnect==0.1.0" 2>&1 | tail -2
```

Expected: installs cleanly. If it fails because 0.1.0 is not on PyPI yet, publish the library first — a HACS release depending on an unpublished version will fail for every user at setup.

- [ ] **Step 4: Add HACS and hassfest validation**

Create `.github/workflows/validate.yml`:

```yaml
name: Validate

on:
  push:
  pull_request:
  schedule:
    - cron: "0 0 * * *"

jobs:
  hassfest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: home-assistant/actions/hassfest@master

  hacs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hacs/action@main
        with:
          category: integration
```

- [ ] **Step 5: Run the full suite one final time**

```bash
cd /Users/ryanmorash/Developer/ha_zephyr
.venv/bin/python -m pytest tests/ -q
```

Expected: all pass, no warnings.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: add README, HACS packaging and CI validation"
```

---

## Post-implementation: verify against real hardware

The test suite mocks `ZephyrClient` entirely, so it proves the integration's
logic but never that it talks to a hood. Before tagging a release, install it
in Home Assistant and confirm:

1. Config flow accepts credentials and creates one device.
2. Fan and light respond, and their HA state reflects the device's own report
   ~1.5s later rather than sticking at the optimistic value.
3. Turning the power switch off stops everything; on restores the previous
   levels.
4. Grease filter percentage matches what the vendor app displays.
5. Pulling the hood's power makes entities unavailable, and restoring it
   recovers without a Home Assistant restart.
6. Diagnostics download contains no serial, MAC, thing name or coordinates.

## Known open items

Carried forward deliberately, none blocking:

- **`resetgreasefilter` is untested.** Validating it destroys the counter it
  verifies, so it ships on that understanding.
- **`usefantime` / `uselighttime` units are inferred**, not measured. Shipped
  as hours; a one-line change per sensor if wrong.
- **`setrecirculating` is read-only.** Untestable on ducted hardware, and
  writing it risks corrupting filter accounting.
- **`act` values beyond `"Disabled"` are unknown**, though the field itself
  is understood: Airflow Control Technology, set physically on the hood. The
  enabled-state string is simply unobserved.
- **Whether the hood self-stops at `delaytimer: 0`** was never observed end
  to end.
