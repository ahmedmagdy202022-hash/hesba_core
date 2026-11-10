"""HG-078 (A0): which AI providers this server talks to, mirroring ``payments.providers`` (S4).

``settings.AI_MODE`` (from the server's environment):

* ``live``: the real provider, only when its key is in the server's environment (and, for ElevenLabs, a real voice).
  A key missing gives **none**, never the mock: a half-configured live server must not pretend.
* ``mock``: the provider's own code with in-process answers and no socket (development and tests).
* ``off``: none. Every call is refused as not connected.
* empty: ``mock`` while ``DEBUG`` is on, ``off`` once it is off, so a production server that never set AI connects
  nothing. An explicit ``mock`` with ``DEBUG`` off refuses to start (``ai.checks``).
* AI demo (HG-078 deviation 7): on a showcase install (``DEMO_MODE``) an empty ``AI_MODE`` is ``mock`` even with ``DEBUG``
  off, and ``ai.E001`` lets an explicit ``mock`` start there, so visitors can try the AI features with no key and no call
  out. Every mock answer a person sees is labelled as a sample (``ai.sample``). ``live`` on a sandboxed showcase is
  still refused (``ai.E006``).

``PROVIDERS`` is the registry: one factory per provider code.

A2 (HG-080) adds ``claude_router``, the language model that routes «اسأل حسبة» questions: live only with
``ANTHROPIC_API_KEY`` and a model (``ANTHROPIC_MODEL``, default ``claude-opus-5-5``); the mock never builds the SDK client.

A3 (HG-081) adds ``claude_invoice_reader``, the same model reading a photo of a supplier's invoice, under the same two
settings; its mock answers with a fixed sample and never builds the SDK client either.
"""

from django.conf import settings

from .base import (  # noqa: F401 - the interface, re-exported
    InvoiceReader, ProviderError, QuestionRouter, Reading, Routing, Speech, TextToSpeechProvider,
)

MOCK, LIVE, OFF = "mock", "live", "off"
KNOWN_MODES = (MOCK, LIVE, OFF)
PLACEHOLDER_VOICE = "hesba-placeholder-voice"

ELEVENLABS_TTS = "elevenlabs_tts"
CLAUDE_ROUTER = "claude_router"  # A2 (HG-080)
CLAUDE_INVOICE_READER = "claude_invoice_reader"  # A3 (HG-081)


def mock_allowed():
    """Where the mock may run: development (``DEBUG`` on) or a showcase install (``DEMO_MODE``), never a real server."""

    return bool(settings.DEBUG or getattr(settings, "DEMO_MODE", False))


def mode():
    raw = str(getattr(settings, "AI_MODE", "") or "").strip().lower()
    if raw in KNOWN_MODES:
        return raw
    if raw:
        return OFF  # an unknown value connects nothing (ai.E003 refuses to start anyway)
    return MOCK if mock_allowed() else OFF


def is_mock():
    """True when this server answers with the in-process mock: what a person sees is a sample, never a real AI answer."""

    return mode() == MOCK


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


def _sample_reading():
    """The mock invoice reader's answer: a pharmacy reads a medicine distributor's invoice, whatever the photo; every other
    activity the car-parts one. Looked up when a photo is read, so building the reader touches no database."""

    from settings_core.demo_ai import pharmacy

    from .mock import PHARMACY_SAMPLE_READING, SAMPLE_READING

    return PHARMACY_SAMPLE_READING if pharmacy() else SAMPLE_READING


def _claude_invoice_reader():
    """A3 (HG-081). The same rules as the router: live needs the key and a model (a key missing connects nothing, never
    the mock); the mock is built without touching the SDK."""

    key, model = _anthropic_settings()
    current = mode()
    if current == LIVE:
        if key and model:
            from .claude import ClaudeInvoiceReader

            return ClaudeInvoiceReader(api_key=key, model=model)
        return None
    if current == MOCK:
        from .mock import MockClaudeInvoiceReader

        return MockClaudeInvoiceReader(model=model or "mock-model", reading=_sample_reading)
    return None


PROVIDERS = {ELEVENLABS_TTS: _elevenlabs_tts, CLAUDE_ROUTER: _claude_router, CLAUDE_INVOICE_READER: _claude_invoice_reader}


def get(code):
    """The provider for ``code`` in this server's mode, or None when nothing is connected."""

    return PROVIDERS[code]()
