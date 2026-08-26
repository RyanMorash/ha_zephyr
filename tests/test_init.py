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
    CONF_TOKENS,
    DEGRADED_POLL_INTERVAL_SECONDS,
    DOMAIN,
)
from custom_components.zephyr_connect.coordinator import SAFETY_NET_TICKS
from pyzephyrconnect import (
    ZephyrAuthError,
    ZephyrError,
    ZephyrPolicyError,
    ZephyrTokens,
    ZephyrTransportError,
)

THING = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"

TOKENS_RECORD = {
    "username": "user@example.com",
    "id_token": "eyJr.id.token",
    "refresh_token": "eyJr.refresh.token",
    "identity_id": "us-west-2:00000000-1111-2222-3333-444455556666",
    "expires_at": 4_000_000_000.0,
}


def _caps(thing_name=THING):
    caps = MagicMock()
    caps.thing_name = thing_name
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
        "use_grease_filter_time": 643, "use_charcoal_filter_time": 0,
        "use_fan_time": 1979, "use_light_time": 2833,
        "delay_timer": 0, "set_delay_timer": 0,
        "act": "Disabled", "set_recirculating": 0,
        "set_clean_air_function": 0,
        "clean_grease_filters": 0, "clean_charcoal_filters": 0,
        "alarm_grease_filter": 0, "alarm_fan": 0, "fan_warning": 0,
        "alarm_fault_code": 0, "fault_codes": (),
    }
    for key, value in {**defaults, **overrides}.items():
        setattr(state, key, value)
    return state


def _hood(thing_name=THING):
    """A Hood mock: per-thing lifecycle, state and controls live here now."""
    hood = MagicMock()
    hood.capabilities = _caps(thing_name)
    hood.thing_name = thing_name
    hood.state = _state()
    hood.connected = True
    hood.async_start = AsyncMock()
    hood.async_stop = AsyncMock()
    hood.async_poll = AsyncMock(return_value=_state())
    hood.add_listener = MagicMock(return_value=lambda: None)
    return hood


@pytest.fixture
def mock_client():
    """A ZephyrClient built through from_credentials, returning one Hood.

    The per-thing client methods (async_start, async_poll, state,
    add_listener, async_set_state, async_refresh_if_needed) are DELETED
    from the mock, mirroring the 0.1.0 surface: any leftover call site
    raises AttributeError here instead of silently passing against a
    MagicMock and failing only in production.
    """
    client = MagicMock()
    client.async_setup = AsyncMock(return_value=[_hood()])
    client.async_stop = AsyncMock()
    client.connected = True
    del client.async_start
    del client.async_poll
    del client.state
    del client.add_listener
    del client.async_set_state
    del client.async_refresh_if_needed
    with patch(
        "custom_components.zephyr_connect.ZephyrClient"
    ) as client_cls:
        client_cls.from_credentials.return_value = client
        client.mock_from_credentials = client_cls.from_credentials
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


def _the_hood(mock_client):
    """The single Hood the default mock_client returns."""
    return mock_client.async_setup.return_value[0]


async def test_setup_starts_each_hood(hass, entry, mock_client) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    mock_client.async_setup.assert_awaited_once()
    _the_hood(mock_client).async_start.assert_awaited_once_with()


async def test_setup_registers_a_push_listener(hass, entry, mock_client) -> None:
    """Push is the primary update path; without a listener the integration
    would be silently poll-only."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    _the_hood(mock_client).add_listener.assert_called_once()


async def test_setup_without_saved_tokens_passes_none(
    hass, entry, mock_client
) -> None:
    """A fresh entry has no token record; the library then runs a full SRP
    login rather than being handed a fabricated one."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    kwargs = mock_client.mock_from_credentials.call_args.kwargs
    assert kwargs["tokens"] is None
    assert kwargs["token_updater"] is not None


async def test_setup_pins_the_home_assistant_client_id_suffix(
    hass, entry, mock_client
) -> None:
    """The MQTT client ID must identify THIS consumer, explicitly.

    pyzephyrconnect 0.2.0 made the suffix a per-consumer argument and moved
    its own default from "-ha" to "-py". Leaving it defaulted would change
    the client ID every existing install already connects under, and put
    the integration on the same ID as a plain library script - and AWS IoT
    evicts one of two connections sharing an ID, so both would flap.
    """
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    kwargs = mock_client.mock_from_credentials.call_args.kwargs
    assert kwargs["client_id_suffix"] == "-ha"


async def test_setup_restores_saved_tokens(hass, mock_client) -> None:
    """A persisted record must reach the library as a ZephyrTokens, so a
    restart skips the rate-limited SRP login."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "username": "user@example.com",
            "password": "hunter2",
            CONF_TOKENS: TOKENS_RECORD,
        },
        unique_id="us-west-2:00000000-1111-2222-3333-444455556666",
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    tokens = mock_client.mock_from_credentials.call_args.kwargs["tokens"]
    assert isinstance(tokens, ZephyrTokens)
    assert tokens.as_dict() == TOKENS_RECORD


async def test_corrupt_saved_tokens_fall_back_to_fresh_login(
    hass, mock_client
) -> None:
    """ZephyrTokens.from_dict raises ZephyrDataError on a malformed record.
    The correct response is to discard it and log in fresh - NOT to abort
    setup: a corrupted value that survives fails much later and far away,
    as a SECRET_HASH Cognito rejects or an MQTT client ID AWS IoT silently
    drops messages for."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "username": "user@example.com",
            "password": "hunter2",
            CONF_TOKENS: {"username": "", "id_token": 42},
        },
        unique_id="us-west-2:00000000-1111-2222-3333-444455556666",
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert mock_client.mock_from_credentials.call_args.kwargs["tokens"] is None


async def test_refreshed_tokens_are_persisted(hass, entry, mock_client) -> None:
    """The token_updater must write each refresh into the config entry, or
    a restart is back to a full SRP login."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    updater = mock_client.mock_from_credentials.call_args.kwargs["token_updater"]
    updater(ZephyrTokens.from_dict(TOKENS_RECORD))
    await hass.async_block_till_done()

    assert entry.data[CONF_TOKENS] == TOKENS_RECORD


@pytest.mark.parametrize(
    "error",
    [ZephyrAuthError("expired"), ZephyrPolicyError("IoT refused the attach")],
)
async def test_terminal_failure_during_setup_triggers_reauth(
    hass, entry, mock_client, error
) -> None:
    """Both terminal errors from client.async_setup() must land in
    SETUP_ERROR (reauth), not the silent SETUP_RETRY loop."""
    mock_client.async_setup.side_effect = error
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_auth_failure_during_hood_init_triggers_reauth(
    hass, entry, mock_client
) -> None:
    """ZephyrAuthError subclasses ZephyrError. An auth failure raised from
    inside the per-hood loop (via hood.async_start(), called by
    coordinator.async_initialise()) must still surface as ConfigEntryAuthFailed
    and land in SETUP_ERROR, not be caught by the broader ZephyrError clause
    and downgraded to a perpetual SETUP_RETRY."""
    _the_hood(mock_client).async_start.side_effect = ZephyrAuthError("expired")
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    mock_client.async_stop.assert_awaited()


async def test_policy_failure_during_hood_init_triggers_reauth(
    hass, entry, mock_client
) -> None:
    """hood.async_start() attaches the IoT policy and opens the shadow
    subscription, so it can raise ZephyrPolicyError - the OTHER terminal
    error, and NOT a ZephyrAuthError subclass. Falling through to the
    generic ZephyrError clause would downgrade it to a silent, perpetual
    SETUP_RETRY; like the coordinator's poll mapping, it must surface as
    SETUP_ERROR so the reauth flow's reload can re-attach the policy."""
    _the_hood(mock_client).async_start.side_effect = ZephyrPolicyError(
        "IoT refused the attach"
    )
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    mock_client.async_stop.assert_awaited()


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


async def test_unload_stops_client_with_no_hoods(hass, entry, mock_client) -> None:
    """The shared client is stored directly on runtime_data, not reached via
    runtime_data[0].client, so it must still be stopped when the account has
    zero hoods and coordinators is empty."""
    mock_client.async_setup = AsyncMock(return_value=[])
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
    hood = _the_hood(mock_client)
    hood.async_poll.reset_mock()

    hood.connected = False
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    hood.async_poll.assert_awaited()


async def test_degraded_poll_uses_this_hoods_connection(
    hass, entry, mock_client
) -> None:
    """client.connected is an aggregate now - True while ANY hood is up. On
    a multi-hood account the coordinator must key the degraded fallback on
    its own hood's connection, or one healthy hood masks another's outage."""
    healthy, broken = _hood(), _hood("bbbbbbbbccccccccddddddddeeeeeeeeffffffff")
    mock_client.async_setup = AsyncMock(return_value=[healthy, broken])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    healthy.async_poll.reset_mock()
    broken.async_poll.reset_mock()

    broken.connected = False  # client.connected would still read True
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    broken.async_poll.assert_awaited()
    healthy.async_poll.assert_not_awaited()


async def test_terminal_supervisor_failure_reaches_reauth_via_poll(
    hass, entry, mock_client
) -> None:
    """A terminal credential failure inside the library's supervisor stops
    it, disconnects the hoods, and re-raises from the next hood.async_poll().
    The coordinator's periodic tick is what delivers that poll, so the
    failure must land as a reauth prompt (SETUP_ERROR after reload), not be
    swallowed as a routine UpdateFailed."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hood = _the_hood(mock_client)

    hood.connected = False  # the supervisor disconnected the hoods
    hood.async_poll.side_effect = ZephyrAuthError("refresh token revoked")
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_terminal_policy_failure_reaches_reauth_via_poll(
    hass, entry, mock_client
) -> None:
    """ZephyrPolicyError is the OTHER terminal supervisor error, and it is
    NOT a ZephyrAuthError subclass - an unqualified ZephyrError clause would
    map it to UpdateFailed, leaving the hood unavailable forever: the
    supervisor is stopped and every later poll re-raises the stored error,
    so nothing short of an entry reload recovers. The reauth flow's success
    path is that reload, and a fresh setup re-runs the identity exchange
    and re-attaches the IoT policy - the actual remediation."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hood = _the_hood(mock_client)

    hood.connected = False  # the supervisor disconnected the hoods
    hood.async_poll.side_effect = ZephyrPolicyError("IoT policy not attached")
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_transient_poll_failure_does_not_trigger_reauth(
    hass, entry, mock_client
) -> None:
    """The other half of the poll error mapping: only genuine credential
    rejections (ZephyrAuthError) are terminal - the library keeps DNS
    failures, timeouts and Cognito throttling as the retryable
    ZephyrTransportError precisely so consumers can map ZephyrAuthError to
    a reauth prompt without it firing for a Wi-Fi blip. A transient poll
    failure must mark the update failed and NOT start a reauth flow."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hood = _the_hood(mock_client)

    hood.connected = False
    hood.async_poll.side_effect = ZephyrTransportError("dns blip")
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    coordinator = entry.runtime_data.coordinators[0]
    assert coordinator.last_update_success is False
    assert not any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


async def test_failed_platform_forwarding_still_stops_the_client(
    hass, entry, mock_client
) -> None:
    """A setup that fails AFTER runtime_data is set (platform import error,
    entity-platform setup failure) never reaches async_unload_entry - HA
    only runs the entry's on_unload callbacks. The
    entry.async_on_unload(client.async_stop) registration made before any
    hood starts is the ONLY stop on that path; without it the client keeps
    its credential supervisor and per-hood paho threads alive, leaking one
    full client per setup retry."""
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=ImportError("broken platform"),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    mock_client.async_stop.assert_awaited()


async def test_safety_net_does_not_poll_before_threshold(
    hass, entry, mock_client
) -> None:
    """While connected, cached state is used for ticks short of the
    safety-net cadence - no real poll should happen yet."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hood = _the_hood(mock_client)
    hood.async_poll.reset_mock()

    now = dt_util.utcnow()
    for i in range(1, SAFETY_NET_TICKS):
        async_fire_time_changed(
            hass,
            now + timedelta(seconds=(DEGRADED_POLL_INTERVAL_SECONDS + 1) * i),
        )
        await hass.async_block_till_done()

    hood.async_poll.assert_not_awaited()


async def test_safety_net_polls_after_threshold(hass, entry, mock_client) -> None:
    """Every SAFETY_NET_TICKS-th connected tick must perform a real re-read,
    so a push missed while the transport was briefly unhealthy is eventually
    caught even though the connection never dropped."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hood = _the_hood(mock_client)
    hood.async_poll.reset_mock()

    now = dt_util.utcnow()
    for i in range(1, SAFETY_NET_TICKS + 1):
        async_fire_time_changed(
            hass,
            now + timedelta(seconds=(DEGRADED_POLL_INTERVAL_SECONDS + 1) * i),
        )
        await hass.async_block_till_done()

    hood.async_poll.assert_awaited_once_with()


async def test_partial_setup_failure_leaves_no_orphan_timers(
    hass, entry, mock_client
) -> None:
    """Verify that after a partially-failed setup, nothing continues polling.

    This property holds via two independent mechanisms. First, our explicit
    _release() helper shuts down already-initialised coordinators before
    stopping the shared client. Second, Home Assistant's DataUpdateCoordinator
    base class registers config_entry.async_on_unload(self.async_shutdown),
    which HA invokes on any failed setup. As a result, this test does not
    regress if _release() is removed — coordinators are cleaned up by HA
    regardless. The test is therefore a property guard (nothing polls after
    abandoned setup) rather than a regression guard for the _release() helper
    specifically."""
    first, second = _hood(), _hood("bbbbbbbbccccccccddddddddeeeeeeeeffffffff")
    mock_client.async_setup = AsyncMock(return_value=[first, second])

    # First hood starts fine; the second fails.
    second.async_start.side_effect = ZephyrError("hood 2 unreachable")

    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY

    # Nothing should still be polling on behalf of the abandoned setup.
    first.async_poll.reset_mock()
    second.async_poll.reset_mock()
    first.connected = False  # a disconnected tick would poll every time
    async_fire_time_changed(
        hass,
        dt_util.utcnow() + timedelta(seconds=DEGRADED_POLL_INTERVAL_SECONDS + 1),
    )
    await hass.async_block_till_done()

    first.async_poll.assert_not_awaited()
    second.async_poll.assert_not_awaited()


async def test_degraded_tick_polls_every_time(hass, entry, mock_client) -> None:
    """A disconnected tick must poll every time, not just at the safety-net
    cadence used while connected."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hood = _the_hood(mock_client)
    hood.async_poll.reset_mock()
    hood.connected = False

    now = dt_util.utcnow()
    for i in range(1, 4):
        async_fire_time_changed(
            hass,
            now + timedelta(seconds=(DEGRADED_POLL_INTERVAL_SECONDS + 1) * i),
        )
        await hass.async_block_till_done()

    assert hood.async_poll.await_count == 3
