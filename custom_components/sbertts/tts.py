import asyncio
import logging
import time
import uuid
from asyncio import Semaphore
from http import HTTPStatus
from typing import Any

import aiohttp
import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components.tts import CONF_LANG, PLATFORM_SCHEMA, TtsAudioType, Voice, TextToSpeechEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_AUTHENTICATION
from homeassistant.core import callback, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity import generate_entity_id

from .const import *

_LOGGER = logging.getLogger(__name__)
config_schema_dict = {
    vol.Required(CONF_AUTHENTICATION): cv.string,
    vol.Optional(CONF_LANG, default=DEFAULT_LANG): vol.In(SUPPORT_LANGUAGES),
    vol.Optional(CONF_VOICE, default=DEFAULT_VOICE): vol.In(SUPPORT_VOICES.keys()),
    vol.Optional(CONF_RATE, default=DEFAULT_RATE): vol.In(SUPPORT_RATE),
    vol.Optional(CONF_FLOW_RESTRICTION, default=DEFAULT_FLOW_RESTRICTION): cv.positive_int,
}
PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(config_schema_dict)


async def async_setup_entry(
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        async_add_entities,
):
    """Setup entities from a config entry created in the integrations UI."""
    config = hass.data[DOMAIN][config_entry.entry_id]
    salute_speech = SaluteSpeechProvider(hass, config)
    async_add_entities([salute_speech], update_before_add=False)


async def async_get_engine(hass, config, discovery_info=None):
    """Set up SaluteSpeech TTS component."""
    return SaluteSpeechProvider(hass, config)


class SaluteSpeechProvider(TextToSpeechEntity):
    name = 'SaluteSpeech'

    def __init__(self, hass, conf):
        self.hass = hass
        self.has_entity = False
        self._client_auth_token = conf[CONF_AUTHENTICATION]
        self._lang = conf[CONF_LANG]
        self._rate = conf[CONF_RATE]
        self._voice = conf[CONF_VOICE]
        self._semaphore_flow_restriction = Semaphore(conf[CONF_FLOW_RESTRICTION])
        self._salute_speech = SaluteSpeechCloud(hass)
        self.entity_id = generate_entity_id("tts.{}", DOMAIN.lower(), hass=hass)

    @property
    def default_language(self):
        """Return the default language."""
        return self._lang

    @property
    def supported_languages(self):
        """Return list of supported languages."""
        return SUPPORT_LANGUAGES

    @property
    def supported_options(self):
        """Return a list of supported options."""
        return SUPPORT_OPTIONS

    @callback
    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        """Return a list of supported voices for a language."""
        if not (voices := MAP_VOICES.get(language)):
            return None
        return [Voice(voice, name) for voice, name in voices.items()]

    async def async_get_tts_audio(self, message: str, language: str, options: dict[str, Any]) -> TtsAudioType:
        async with self._semaphore_flow_restriction:  # ограничиваем одновременные потоки в зависимости от тарифа\
            return await self._salute_speech.send_text_to_cloud(
                message,
                self._client_auth_token,
                self.get_voice(options.get(CONF_VOICE), options.get(CONF_LANG)),
                self._rate
            )

    def get_voice(self, voice: str | None, lang: str | None) -> str:
        voice = voice or self._voice
        lang = lang or self._lang

        if lang == 'en-US':
            voice = 'Kin'
        elif lang == 'ru-RU' and voice == 'Kin':
            voice = 'Nec'

        return voice


class SaluteSpeechCloud:
    async_lock = asyncio.Lock()
    TOKEN_TIMEOUT = 1500  # 25min

    def __init__(self, hass: HomeAssistant):
        self._http_client = async_get_clientsession(hass, False)
        self.access_token: str | None = None
        self.access_token_time: int = 0

    async def send_text_to_cloud(
            self,
            message: str,
            base64_auth_token: str,
            announcer: str = DEFAULT_VOICE,
            rate: str = DEFAULT_RATE,
            codec: str = 'opus'
    ) -> TtsAudioType:
        try:
            async with asyncio.timeout(30):
                bearer_token = await self.get_auth_token(base64_auth_token)

                if bearer_token is None:
                    return None, None

                is_ssml = message.find('<speak>') != -1

                request = await self._http_client.post(
                    url=API_TTS_ENDPOINT,
                    headers={
                        'Authorization': 'Bearer {}'.format(bearer_token),
                        'Content-Type': 'application/ssml' if is_ssml else 'application/text'
                    },
                    params={
                        'format': codec,
                        'voice': announcer + '_' + rate,
                    },
                    data=message
                )

                data = await request.read()

                if request.status != HTTPStatus.OK:
                    _LOGGER.error('Error %d on load URL %s. Response %s' % (request.status, request.url, data))
                    return None, None

                return CODEC_FORMAT.get(codec), data
        except (asyncio.TimeoutError, aiohttp.ClientError):
            _LOGGER.error('Timeout for speech kit API')
            return None, None

    async def get_auth_token(self, base64_auth_token: str) -> str | None:
        async with self.async_lock:  # асинхронно проверять токен должен только 1 поток
            if time.time() - self.access_token_time < self.TOKEN_TIMEOUT and self.access_token:
                return self.access_token
            async with asyncio.timeout(10):
                request = await self._http_client.post(
                    url=API_AUTH_ENDPOINT,
                    headers={
                        'Authorization': 'Basic {}'.format(base64_auth_token),
                        'RqUID': str(uuid.uuid4()),
                        'Content-Type': 'application/x-www-form-urlencoded'
                    },
                    data={
                        'scope': 'SALUTE_SPEECH_PERS',
                    }
                )

                if request.status != HTTPStatus.OK:
                    error = await request.read()
                    _LOGGER.error('Error %d on load URL %s. Response %s' % (request.status, request.url, error))
                    return None

                data = await request.json()
                self.access_token = data.get('access_token')
                self.access_token_time = time.time()
                return self.access_token

    @classmethod
    async def request_new_token(cls, base64_auth_token, http_client: aiohttp.ClientSession):
        async with asyncio.timeout(10):
            request = await http_client.post(
                url=API_AUTH_ENDPOINT,
                ssl=False,
                headers={
                    'Authorization': 'Basic {}'.format(base64_auth_token),
                    'RqUID': str(uuid.uuid4()),
                    'Content-Type': 'application/x-www-form-urlencoded'
                },
                data={
                    'scope': 'SALUTE_SPEECH_PERS',
                }
            )

            if request.status != HTTPStatus.OK:
                error = await request.read()
                _LOGGER.error(f'Error {request.status} on load URL {request.url}. Response {error}')
                return None

            data = await request.json()
            return data.get('access_token')

    @classmethod
    async def validate_token(cls, token: str) -> None:
        async with aiohttp.ClientSession() as http_client:
            return await cls.request_new_token(token, http_client)

#
#
# async def async_setup_entry(
#         hass: HomeAssistant,
#         config_entry: ConfigEntry,
#         async_add_entities: AddEntitiesCallback,
# ) -> None:
#     async_add_entities([
#         SaluteSpeechTTSEntity(hass, config_entry)
#     ])
#
#
# class SaluteSpeechTTSEntity(TextToSpeechEntity):
#     def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
#         self._salute_speech = SaluteSpeechCloud(hass)
#         self._lang = config_entry.data[CONF_LANG]
#         self._voice = config_entry.data[CONF_VOICE]
#
#         if self._lang == 'en-US':
#             self._voice = 'Kin'
#         elif self._lang == 'ru-RU' and self._voice == 'Kin':
#             self._voice = 'Nec'
#
#         self._attr_name = f"SaluteSpeech {SUPPORT_VOICES[self._voice]}"
#         self._attr_unique_id = config_entry.entry_id
#
#     @property
#     def default_language(self):
#         """Return the default language."""
#         return self._lang
#
#     @property
#     def supported_languages(self):
#         """Return list of supported languages."""
#         return SUPPORT_LANGUAGES
#
#     @property
#     def supported_options(self):
#         """Return a list of supported options."""
#         return SUPPORT_OPTIONS
#
#     @property
#     def default_options(self):
#         """Return a dict include default options."""
#         return {
#             CONF_LANG: DEFAULT_LANG,
#             CONF_VOICE: DEFAULT_VOICE,
#             CONF_RATE: DEFAULT_RATE
#         }
#
#     @callback
#     def async_get_supported_voices(self, lang: str) -> list[str] | None:
#         """Return a list of supported voices for a language."""
#
#         if lang == 'ru-RU':
#             return SUPPORT_VOICES.values()
#
#         return [v for k, v in SUPPORT_VOICES.items() if k != 'Kin']
#
#     async def async_get_tts_audio(
#             self, message: str, language: str, options: dict[str, Any] | None = None
#     ) -> TtsAudioType:
#         try:
#             return await self._salute_speech.send_text_to_cloud(
#                 message,
#                 options[CONF_AUTHENTICATION],
#                 self._voice,
#                 options[CONF_RATE]
#             )
#         except Exception as exc:
#             _LOGGER.debug("Error during processing of TTS request %s", exc, exc_info=True)
#             raise HomeAssistantError(exc) from exc
