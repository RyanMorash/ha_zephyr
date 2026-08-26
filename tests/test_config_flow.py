"""Config flow tests. No network: ZephyrClient is mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zephyr_connect.const import CONF_TOKENS, DOMAIN
from pyzephyrconnect import ZephyrAuthError, ZephyrDataError, ZephyrError, ZephyrTokens

USER_INPUT = {"username": "user@example.com", "password": "hunter2"}

TOKENS_RECORD = {
    "username": "user@example.com",
    "id_token": "eyJr.id.token",
    "refresh_token": "eyJr.refresh.token",
    "identity_id": "us-west-2:00000000-1111-2222-3333-444455556666",
    "expires_at": 4_000_000_000.0,
}


@pytest.fixture
def mock_client():
    """A ZephyrClient that authenticates and returns one hood.

    Patched under both config_flow (credential validation) and the
    integration package itself (custom_components.zephyr_connect.ZephyrClient),
    since a config entry created by the flow is set up immediately after
    creation and __init__.async_setup_entry builds its own ZephyrClient. The
    Hood mock below mirrors what tests/test_init.py mocks, so that real entry
    setup (exercised by test_created_entry_actually_loads) never touches a
    socket.

    from_credentials invokes the token_updater with real ZephyrTokens, the
    way the real library does on the initial login - that is what carries
    tokens from the validation login into the created entry.
    """
    caps = MagicMock()
    caps.thing_name = "aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee"
    caps.model = "AK7400AS"
    hood = MagicMock()
    hood.capabilities = caps
    hood.thing_name = caps.thing_name
    hood.state = None
    hood.connected = True
    hood.async_start = AsyncMock()
    hood.async_stop = AsyncMock()
    hood.async_poll = AsyncMock(return_value=MagicMock())
    hood.add_listener = MagicMock(return_value=lambda: None)

    client = MagicMock()
    client.async_setup = AsyncMock(return_value=[hood])
    client.async_stop = AsyncMock()
    client.connected = True
    client.identity_id = "us-west-2:00000000-1111-2222-3333-444455556666"

    def fake_from_credentials(
        username, password, session, *, tokens=None, token_updater=None, **kwargs
    ):
        if token_updater is not None:
            token_updater(ZephyrTokens.from_dict(TOKENS_RECORD))
        return client

    with (
        patch(
            "custom_components.zephyr_connect.config_flow.ZephyrClient"
        ) as flow_cls,
        patch("custom_components.zephyr_connect.ZephyrClient") as init_cls,
    ):
        flow_cls.from_credentials.side_effect = fake_from_credentials
        init_cls.from_credentials.side_effect = fake_from_credentials
        # Exposed so a test can assert on HOW the client was built, not
        # just on what came back from it.
        client.mock_flow_from_credentials = flow_cls.from_credentials
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
    assert result["data"] == {**USER_INPUT, CONF_TOKENS: TOKENS_RECORD}


async def test_validation_builds_the_client_the_same_way_as_setup(
    hass: HomeAssistant, mock_client
) -> None:
    """Both client construction sites pass the same client-ID suffix.

    Validation only reaches the REST path today, so the suffix goes unused
    here - but the two sites are deliberately identical so they cannot
    drift, and the library validates the value at construction, which puts
    a bad one in front of the user while the form is still open.
    """
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    await hass.async_block_till_done()

    kwargs = mock_client.mock_flow_from_credentials.call_args.kwargs
    assert kwargs["client_id_suffix"] == "-ha"


async def test_validation_tokens_ride_into_the_entry(
    hass: HomeAssistant, mock_client
) -> None:
    """The flow's login already minted tokens; storing them means the entry
    setup that follows starts from them instead of burning a second SRP
    login against a pool that rate-limits."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.data[CONF_TOKENS] == TOKENS_RECORD


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


async def test_malformed_cloud_data_is_not_reported_as_cannot_connect(
    hass: HomeAssistant, mock_client, caplog
) -> None:
    """ZephyrDataError means the cloud WAS reached and responded - the
    payload was malformed (an unparseable capability, a non-object body).
    'Could not reach the Zephyr cloud service' would send the user off to
    check their network for a failure no retry can fix, and swallowing it
    unlogged would leave a filed bug report with no trace of the actual
    cause: this except clause is the only place the library's diagnostic
    text (naming the offending key) can reach the log."""
    mock_client.async_setup.side_effect = ZephyrDataError(
        "maxFanSpeed was present but unparseable: 6.5"
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unknown"}
    assert "malformed data" in caplog.text
    assert "maxFanSpeed" in caplog.text


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


async def test_created_entry_actually_loads(hass: HomeAssistant, mock_client) -> None:
    """Forwarding to platforms must succeed. Without stub platform modules
    every entry lands in SETUP_ERROR while the flow still reports success."""
    from homeassistant.config_entries import ConfigEntryState

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    await hass.async_block_till_done()

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.state is ConfigEntryState.LOADED


async def test_reauth_updates_the_password_and_tokens(
    hass: HomeAssistant, mock_client
) -> None:
    """Reauth fires because the stored record was rejected; leaving it in
    place would cost a doomed refresh attempt on every restart, so the
    fresh validation login's tokens replace it alongside the password."""
    stale = {**TOKENS_RECORD, "refresh_token": "revoked.refresh.token"}
    entry = MockConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="user@example.com",
        data={**USER_INPUT, CONF_TOKENS: stale},
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
    assert entry.data["password"] == "new-password"  # noqa: S105
    assert entry.data[CONF_TOKENS] == TOKENS_RECORD
