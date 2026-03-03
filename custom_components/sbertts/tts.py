import asyncio
import logging
import time
import uuid
from http import HTTPStatus
from typing import Any

import aiohttp
from homeassistant.components.tts import (
    CONF_LANG,
    TextToSpeechEntity,
    TtsAudioType,
    Voice,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_AUTHENTICATION
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    API_AUTH_ENDPOINT,
    API_TTS_ENDPOINT,
    CODEC_FORMAT,
    CONF_RATE,
    CONF_VOICE,
    DEFAULT_LANG,
    DEFAULT_RATE,
    DEFAULT_VOICE,
    MAP_VOICES,
    SUPPORT_LANGUAGES,
)

_LOGGER = logging.getLogger(__name__)
TTS_OPTIONS = [CONF_LANG, CONF_VOICE, CONF_RATE]

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up SaluteSpeech TTS from a config entry."""
    async_add_entities([SaluteSpeechTTSEntity(hass, config_entry)])


class SaluteSpeechTTSEntity(TextToSpeechEntity):
    """SaluteSpeech TTS entity for config entry setup."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        data = config_entry.data
        options = config_entry.options
        self._salute_speech = SaluteSpeechCloud(hass)
        self._client_auth_token = data.get(CONF_AUTHENTICATION, "")
        self._lang = options.get(CONF_LANG, data.get(CONF_LANG, DEFAULT_LANG))
        self._rate = options.get(CONF_RATE, data.get(CONF_RATE, DEFAULT_RATE))
        self._voice = self._get_voice(options.get(CONF_VOICE, data.get(CONF_VOICE)), self._lang)
        voice_name = MAP_VOICES.get(self._lang, {}).get(self._voice, self._voice)

        self._attr_name = (
            f"SaluteSpeech {voice_name} ({self._voice}, {self._lang}, {self._rate})"
        )
        self._attr_unique_id = config_entry.entry_id

    @property
    def default_language(self) -> str:
        """Return the default language."""
        return self._lang

    @property
    def supported_languages(self) -> list[str]:
        """Return list of supported languages."""
        return SUPPORT_LANGUAGES

    @property
    def supported_options(self) -> list[str]:
        """Return a list of supported options."""
        return TTS_OPTIONS

    @property
    def default_options(self) -> dict[str, str]:
        """Return a dict with default options."""
        return {
            CONF_LANG: self._lang,
            CONF_VOICE: self._voice,
            CONF_RATE: self._rate,
        }

    @callback
    def async_get_supported_voices(self, language: str) -> list[Voice] | None:
        """Return a list of supported voices for a language."""
        if not (voices := MAP_VOICES.get(language)):
            return None
        return [Voice(voice, name) for voice, name in voices.items()]

    async def async_get_tts_audio(
        self, message: str, language: str, options: dict[str, Any]
    ) -> TtsAudioType:
        if not self._client_auth_token:
            raise HomeAssistantError("Missing SaluteSpeech authentication token")
        options = options or {}

        target_lang = options.get(CONF_LANG, language) or self._lang
        target_voice = self._get_voice(options.get(CONF_VOICE), target_lang)
        target_rate = options.get(CONF_RATE, self._rate)

        try:
            return await self._salute_speech.send_text_to_cloud(
                message,
                self._client_auth_token,
                target_voice,
                target_rate,
            )
        except Exception as exc:
            _LOGGER.debug("Error during processing of TTS request %s", exc, exc_info=True)
            raise HomeAssistantError(exc) from exc

    def _get_voice(self, voice: str | None, lang: str | None) -> str:
        if voice is None:
            voice = getattr(self, "_voice", DEFAULT_VOICE)
        if lang is None:
            lang = getattr(self, "_lang", DEFAULT_LANG)

        if lang == "en-US":
            voice = "Kin"
        elif lang == "ru-RU" and voice == "Kin":
            voice = "Nec"

        return voice


class SaluteSpeechCloud:
    def __init__(self, hass: HomeAssistant) -> None:
        self._http_client = async_get_clientsession(hass, False)
        self._token_cache: dict[str, tuple[str, float]] = {}
        self._auth_lock = asyncio.Lock()

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

                async with self._http_client.post(
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
                ) as request:
                    data = await request.read()

                    if request.status != HTTPStatus.OK:
                        _LOGGER.error('Error %d on load URL %s. Response %s' % (request.status, request.url, data))
                        return None, None

                    return CODEC_FORMAT.get(codec), data
        except (asyncio.TimeoutError, aiohttp.ClientError):
            _LOGGER.error('Timeout for speech kit API')
            return None, None

    async def get_auth_token(self, base64_auth_token: str) -> str | None:
        cached_token = self._get_cached_token(base64_auth_token)
        if cached_token is not None:
            return cached_token

        try:
            async with self._auth_lock:
                cached_token = self._get_cached_token(base64_auth_token)
                if cached_token is not None:
                    return cached_token

                async with asyncio.timeout(10):
                    async with self._http_client.post(
                        url=API_AUTH_ENDPOINT,
                        headers={
                            'Authorization': 'Basic {}'.format(base64_auth_token),
                            'RqUID': str(uuid.uuid4()),
                            'Content-Type': 'application/x-www-form-urlencoded'
                        },
                        data={
                            'scope': 'SALUTE_SPEECH_PERS',
                        }
                    ) as request:
                        if request.status != HTTPStatus.OK:
                            error = await request.read()
                            _LOGGER.error('Error %d on load URL %s. Response %s' % (request.status, request.url, error))
                            return None

                        data = await request.json()
                        access_token = data.get('access_token')
                        if not access_token:
                            _LOGGER.error("Auth response has no access_token: %s", data)
                            return None

                        self._store_cached_token(base64_auth_token, data)
                        return access_token
        except (asyncio.TimeoutError, aiohttp.ClientError):
            _LOGGER.error('Timeout while requesting SaluteSpeech auth token')
            return None

    def _get_cached_token(self, base64_auth_token: str) -> str | None:
        cached = self._token_cache.get(base64_auth_token)
        if cached is None:
            return None

        access_token, expires_at = cached
        if time.time() >= expires_at - 30:
            self._token_cache.pop(base64_auth_token, None)
            return None

        return access_token

    def _store_cached_token(self, base64_auth_token: str, payload: dict[str, Any]) -> None:
        access_token = payload.get("access_token")
        expires_at = payload.get("expires_at")

        try:
            expires_at_val = float(expires_at)
        except (TypeError, ValueError):
            _LOGGER.error("Auth response has invalid expires_at: %s", expires_at)
            return

        self._token_cache[base64_auth_token] = (access_token, expires_at_val)
