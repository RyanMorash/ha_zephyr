"""Config flow tests. No network: ZephyrClient is mocked."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

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
    entry = MockConfigEntry(
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
