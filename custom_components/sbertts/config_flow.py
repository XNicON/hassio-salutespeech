from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components.tts import CONF_LANG
from homeassistant.const import CONF_AUTHENTICATION
from homeassistant.data_entry_flow import FlowResult
import homeassistant.helpers.config_validation as cv

from .const import *

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_AUTHENTICATION): cv.string,
    vol.Optional(CONF_LANG, default=DEFAULT_LANG): vol.In(SUPPORT_LANGUAGES),
    vol.Optional(CONF_VOICE, default=DEFAULT_VOICE): vol.In(SUPPORT_VOICES.keys()),
    vol.Optional(CONF_RATE, default=DEFAULT_RATE): vol.In(SUPPORT_RATE),
})


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            self._async_abort_entries_match({
                CONF_AUTHENTICATION: user_input[CONF_AUTHENTICATION],
                CONF_LANG: user_input[CONF_LANG],
                CONF_VOICE: user_input[CONF_VOICE],
                CONF_RATE: user_input[CONF_RATE],
            })
            return self.async_create_entry(
                title="SaluteSpeech text-to-speech", data=user_input
            )

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA)

    async def async_step_onboarding(self, data: dict[str, Any] | None = None) -> FlowResult:
        """Handle a flow initialized by onboarding."""
        return self.async_create_entry(
            title="SaluteSpeech text-to-speech",
            data={
                CONF_LANG: DEFAULT_LANG,
                CONF_VOICE: DEFAULT_VOICE,
                CONF_RATE: DEFAULT_RATE,
            },
        )
