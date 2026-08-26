"""Config flow for Zephyr Connect."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
from pyzephyrconnect import (
    ZephyrAuthError,
    ZephyrClient,
    ZephyrDataError,
    ZephyrError,
    ZephyrTokens,
)

from .const import CONF_TOKENS, DOMAIN, MQTT_CLIENT_ID_SUFFIX

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class ZephyrConfigFlow(ConfigFlow, domain=DOMAIN):
    """Authenticate against the vendor cloud and register the account."""

    VERSION = 1

    async def _validate(
        self, username: str, password: str
    ) -> tuple[str, dict[str, Any] | None]:
        """Return the Cognito identity ID and the tokens the login minted.

        The tokens ride along into the config entry so async_setup_entry can
        start from them instead of burning a second SRP login against a pool
        that rate-limits (the library invokes token_updater on the initial
        login, not just on refreshes).

        Always releases the client: validation opens an authenticated
        session, and leaking it would leave an MQTT connection open for a
        flow the user may abandon.
        """
        captured: ZephyrTokens | None = None

        def _capture(tokens: ZephyrTokens) -> None:
            nonlocal captured
            captured = tokens

        client = ZephyrClient.from_credentials(
            username,
            password,
            async_get_clientsession(self.hass),
            token_updater=_capture,
            # The same consumer identity async_setup_entry connects under.
            # Validation only reaches the REST path today, so the suffix is
            # unused here - but the client is built the same way in both
            # places deliberately, so the two cannot drift, and the
            # library's construction-time check on the value runs while the
            # user is still looking at the form.
            client_id_suffix=MQTT_CLIENT_ID_SUFFIX,
        )
        try:
            await client.async_setup()
            return client.identity_id, (
                captured.as_dict() if captured is not None else None
            )
        finally:
            await client.async_stop()

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """Offer setup when a hood takes a DHCP lease on this network.

        Discovery here is a prompt, not a transport. These hoods speak no
        local protocol - every read and write in this integration is a
        cloud round trip - so a lease proves only that the hardware is on
        the network. It cannot say which Zephyr account owns the hood, and
        the account is what a config entry is keyed on, so the user still
        has to sign in. What discovery buys is the introduction: an owner
        who never went looking for this integration gets told it exists.

        Nothing from the lease reaches the entry this flow creates. The IP
        is never touched - connecting to it would be meaningless - and the
        MAC serves only as the flow's unique ID. That one use does outlive
        the flow in a single case: dismissing the card writes an ignored
        entry keyed on the MAC, which is the only way Home Assistant can
        remember a dismissal. Neither value is ever logged; the MAC is
        personal data.
        """
        # Identifies the FLOW, not the eventual entry - async_step_user
        # replaces it with the Cognito identity before creating anything.
        # Two things need a flow-level ID: Home Assistant only offers
        # "Ignore" on a flow that carries one, and it is what makes the
        # next lease renewal land on the card already on screen instead of
        # stacking another beside it.
        await self.async_set_unique_id(format_mac(discovery_info.macaddress))
        # Catches the hood whose card the user already dismissed: ignoring
        # writes an entry under this same MAC.
        self._abort_if_unique_id_configured()

        # An account is already set up, so there is nothing to introduce.
        # One entry covers every hood on its account, and a lease carries
        # nothing that would distinguish a second account from the first -
        # so a card here could only offer a duplicate. Someone who really
        # does have two accounts adds the second one by hand.
        #
        # Ignored entries are excluded deliberately, and explicitly rather
        # than by leaning on what this helper infers from the flow source:
        # an ignore records one dismissed hood, not a configured account,
        # so counting it here would let the card the owner dismissed in the
        # kitchen bury the one for a second hood they do want.
        if self._async_current_entries(include_ignore=False):
            return self.async_abort(reason="already_configured")

        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                identity_id, tokens = await self._validate(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except ZephyrAuthError:
                errors["base"] = "invalid_auth"
            except ZephyrDataError:
                # The cloud WAS reached and responded - the payload was
                # malformed (an unparseable capability, a non-object body).
                # "cannot_connect" would send the user off to check their
                # network for a failure no retry can fix, and this except
                # is the only place the library's diagnostic text (naming
                # the offending key) can reach the log.
                _LOGGER.exception(
                    "Zephyr cloud returned malformed data during validation"
                )
                errors["base"] = "unknown"
            except ZephyrError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error validating Zephyr credentials")
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(identity_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data={**user_input, CONF_TOKENS: tokens},
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
                _, tokens = await self._validate(
                    entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except ZephyrAuthError:
                errors["base"] = "invalid_auth"
            except ZephyrDataError:
                # See async_step_user: malformed cloud data, not a
                # connectivity problem - log the only trail and be honest.
                _LOGGER.exception(
                    "Zephyr cloud returned malformed data during reauth"
                )
                errors["base"] = "unknown"
            except ZephyrError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during Zephyr reauth")
                errors["base"] = "unknown"
            else:
                # The fresh tokens replace whatever record triggered the
                # reauth; leaving a rejected record in place would cost a
                # doomed refresh attempt on every restart.
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_TOKENS: tokens,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
        )
