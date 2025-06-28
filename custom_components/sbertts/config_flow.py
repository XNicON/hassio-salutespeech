from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components.tts import CONF_LANG
from homeassistant.const import CONF_AUTHENTICATION
from homeassistant.data_entry_flow import FlowResult

from .const import *
from .tts import SaluteSpeechCloud, config_schema_dict

STEP_USER_DATA_SCHEMA = vol.Schema(config_schema_dict)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
            self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._async_abort_entries_match({
                CONF_AUTHENTICATION: user_input[CONF_AUTHENTICATION],
                CONF_LANG: user_input[CONF_LANG],
                CONF_VOICE: user_input[CONF_VOICE],
                CONF_RATE: user_input[CONF_RATE],
                CONF_FLOW_RESTRICTION: user_input[CONF_FLOW_RESTRICTION],
            })
            auth_token = await SaluteSpeechCloud.validate_token(user_input[CONF_AUTHENTICATION])
            if auth_token is None:
                errors["base"] = "authentication error"
            if not errors:
                return self.async_create_entry(
                    title="SaluteSpeech text-to-speech", data=user_input
                )

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    async def async_step_onboarding(self, data: dict[str, Any] | None = None) -> FlowResult:
        """Handle a flow initialized by onboarding."""
        return self.async_create_entry(
            title="SaluteSpeech text-to-speech",
            data={
                CONF_LANG: DEFAULT_LANG,
                CONF_VOICE: DEFAULT_VOICE,
                CONF_RATE: DEFAULT_RATE,
                CONF_FLOW_RESTRICTION: DEFAULT_FLOW_RESTRICTION,
            },
        )
