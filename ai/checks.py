"""HG-078 (A0): refuse to start with an AI setting that could send data out by mistake or fake answers in production.

Mirrors ``payments/checks.py``. The production signal is the same one payments uses: ``DEBUG`` off.
``ai.E006`` refuses live calls on a sandboxed showcase (``DEMO_SANDBOXES``), where every visitor's copy has its own cap.
Only an **explicit** ``AI_MODE=mock`` is refused there; an empty ``AI_MODE`` resolves to ``off`` once ``DEBUG`` is off
(``ai.providers.mode``), so a server that never configured AI starts with nothing connected.
"""

from django.conf import settings
from django.core.checks import Error, register

from .providers import KNOWN_MODES, LIVE, MOCK, PLACEHOLDER_VOICE


@register()
def ai_settings(app_configs, **kwargs):
    errors = []
    raw = str(getattr(settings, "AI_MODE", "") or "").strip().lower()
    if raw and raw not in KNOWN_MODES:
        errors.append(Error(f"AI_MODE is {raw!r}; it must be mock, live or off.",
                            hint="Set AI_MODE to mock, live or off, or leave it empty.", id="ai.E003"))
    if raw == MOCK and not settings.DEBUG:
        errors.append(Error(
            "AI_MODE is mock while DEBUG is off: the mock answers with fake audio, which must not reach real users.",
            hint="Set AI_MODE=live with its keys, or AI_MODE=off (or leave it empty) on a real server.", id="ai.E001"))
    if raw == LIVE:
        if not str(getattr(settings, "ELEVENLABS_API_KEY", "") or "").strip():
            errors.append(Error("AI_MODE is live but ELEVENLABS_API_KEY is missing from the server's environment.",
                                hint="Set ELEVENLABS_API_KEY, or switch AI_MODE off.", id="ai.E002"))
        voice = str(getattr(settings, "ELEVENLABS_VOICE_ID", "") or "").strip()
        if not voice or voice == PLACEHOLDER_VOICE:
            errors.append(Error("AI_MODE is live but ELEVENLABS_VOICE_ID is empty or still the placeholder.",
                                hint="Set ELEVENLABS_VOICE_ID to the chosen ElevenLabs voice, or switch AI_MODE off.", id="ai.E004"))
        if not str(getattr(settings, "ELEVENLABS_MODEL_ID", "") or "").strip():
            errors.append(Error("AI_MODE is live but ELEVENLABS_MODEL_ID is empty.",
                                hint="Set ELEVENLABS_MODEL_ID (for example eleven_multilingual_v2), or switch AI_MODE off.", id="ai.E005"))
        if getattr(settings, "DEMO_SANDBOXES", False):
            # A1 review (HG-078 OQ5, HG-079): each visitor's sandbox is its own database, with its own audit log and so its
            # own daily cap. Live calls there would be capped per visitor, not per company: effectively uncapped spend.
            errors.append(Error("AI_MODE is live while DEMO_SANDBOXES is on: every sandbox counts its own daily cap, so spend would be uncapped.",
                                hint="Use AI_MODE=off (or leave it empty) on a sandboxed showcase.", id="ai.E006"))
    return errors
