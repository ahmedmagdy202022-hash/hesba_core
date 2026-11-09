"""HG-078 (A0): which AI providers this server talks to, mirroring ``payments.providers`` (S4).

``settings.AI_MODE`` (from the server's environment):

* ``live``: the real provider, only when its key is in the server's environment (and, for ElevenLabs, a real voice).
  A key missing gives **none**, never the mock: a half-configured live server must not pretend.
* ``mock``: the provider's own code with in-process answers and no socket (development and tests).
* ``off``: none. Every call is refused as not connected.
* empty: ``mock`` while ``DEBUG`` is on, ``off`` once it is off, so a production server that never set AI connects
  nothing. An explicit ``mock`` with ``DEBUG`` off refuses to start (``ai.checks``).

``PROVIDERS`` is the registry: one factory per provider code.

A2 (HG-080) adds ``claude_router``, the language model that routes «اسأل حسبة» questions: live only with
``ANTHROPIC_API_KEY`` and a model (``ANTHROPIC_MODEL``, default ``claude-opus-5-5``); the mock never builds the SDK client.
"""

from django.conf import settings

from .base import ProviderError, QuestionRouter, Routing, Speech, TextToSpeechProvider  # noqa: F401 - the interface, re-exported

MOCK, LIVE, OFF = "mock", "live", "off"
KNOWN_MODES = (MOCK, LIVE, OFF)
PLACEHOLDER_VOICE = "hesba-placeholder-voice"

ELEVENLABS_TTS = "elevenlabs_tts"
CLAUDE_ROUTER = "claude_router"  # A2 (HG-080)


def mode():
    raw = str(getattr(settings, "AI_MODE", "") or "").strip().lower()
    if raw in KNOWN_MODES:
        return raw
    if raw:
        return OFF  # an unknown value connects nothing (ai.E003 refuses to start anyway)
    return MOCK if settings.DEBUG else OFF


def _elevenlabs_settings():
    return (str(getattr(settings, "ELEVENLABS_API_KEY", "") or "").strip(),
            str(getattr(settings, "ELEVENLABS_VOICE_ID", "") or "").strip(),
            str(getattr(settings, "ELEVENLABS_MODEL_ID", "") or "").strip())


def _elevenlabs_tts():
    key, voice, model = _elevenlabs_settings()
    current = mode()
    if current == LIVE:
        if key and voice and voice != PLACEHOLDER_VOICE and model:
            from .elevenlabs import ElevenLabsTTS

            return ElevenLabsTTS(api_key=key, voice_id=voice, model_id=model)
        return None
    if current == MOCK:
        from .mock import MockElevenLabsTTS

        return MockElevenLabsTTS(voice_id=voice or PLACEHOLDER_VOICE, model_id=model or "mock-model")
    return None


def _anthropic_settings():
    return (str(getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip(),
            str(getattr(settings, "ANTHROPIC_MODEL", "") or "").strip())


def _claude_router():
    """A2 (HG-080). Live needs the key and a model; a key missing connects nothing (never the mock). The mock is built
    without touching the SDK."""

    key, model = _anthropic_settings()
    current = mode()
    if current == LIVE:
        if key and model:
            from .claude import ClaudeRouter

            return ClaudeRouter(api_key=key, model=model)
        return None
    if current == MOCK:
        from .mock import MockClaudeRouter

        return MockClaudeRouter(model=model or "mock-model")
    return None


PROVIDERS = {ELEVENLABS_TTS: _elevenlabs_tts, CLAUDE_ROUTER: _claude_router}


def get(code):
    """The provider for ``code`` in this server's mode, or None when nothing is connected."""

    return PROVIDERS[code]()
