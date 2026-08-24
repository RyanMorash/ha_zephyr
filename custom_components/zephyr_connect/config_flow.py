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
