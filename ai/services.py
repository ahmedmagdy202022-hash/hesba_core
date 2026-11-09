"""HG-078 (A0): the one door every AI call goes through.

A feature (A1's voice summary first) never talks to a provider directly. It calls a function here naming itself,
and this module, in order:

1. refuses everything while the kill switch ``SystemSetting ai_disabled`` is on;
2. refuses unless the feature's own capability (``settings_core.capabilities``) is on;
3. refuses a request that is empty or too large;
4. refuses when this server has no provider connected (``AI_MODE`` off, or live with a key missing);
5. refuses once today's calls (Cairo day) reach the cap in ``SystemSetting ai_daily_call_cap`` (default 50);
6. calls the provider, mock or live.

Each call, refused or not, writes one ``AuditLog`` row (module ``ai``, action ``call_ok`` / ``call_refused`` /
``call_error``) with the feature, provider, mode, entity, request size, result and duration, and **never** the text
sent, the audio, customer names, phones or amounts. Refusals are ``ValidationError`` in English, with the Arabic in
``settings_core/ui_messages.py``.

The cap is per installation (one client company, one key): every entity's calls count. Only calls that reached the
provider (ok or error) count; refusals do not. Two calls racing at the cap can both pass (the provider call is not held
under a lock), so the cap may be overshot by the number of calls in flight.
"""

import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from settings_core.models import SystemSetting

from . import providers
from .providers import ProviderError

CAIRO = ZoneInfo("Africa/Cairo")

KILL_SWITCH_KEY = "ai_disabled"
DAILY_CAP_KEY = "ai_daily_call_cap"
DEFAULT_DAILY_CAP = 50
#: The most characters one text-to-speech call may send (a day's summary is a few hundred).
TTS_MAX_CHARS = 5000

MODULE = "ai"
CALL_OK, CALL_REFUSED, CALL_ERROR = "call_ok", "call_refused", "call_error"
COUNTED = (CALL_OK, CALL_ERROR)

# Why a call was refused, as recorded in the audit row.
DISABLED, CAPABILITY_OFF, INVALID, NOT_CONNECTED, DAILY_CAP = "disabled", "capability_off", "invalid_request", "not_connected", "daily_cap"


@dataclass(frozen=True)
class Feature:
    code: str
    capability: str
    provider: str


#: The registry of AI features: each names the capability that must be on for every one of its calls, and its provider.
FEATURES = {
    "voice_summary": Feature(code="voice_summary", capability="ai_voice_summary", provider=providers.ELEVENLABS_TTS),
}


# ---- the settings (SystemSetting rows; never keys, which live in the server's environment) ----

def _setting(key):
    return SystemSetting.objects.filter(key=key, active=True).values_list("value", flat=True).first()


def is_disabled():
    return str(_setting(KILL_SWITCH_KEY) or "").strip().lower() in ("1", "true", "yes", "on")


def daily_cap():
    raw = str(_setting(DAILY_CAP_KEY) or "").strip()
    try:
        return max(0, int(raw)) if raw else DEFAULT_DAILY_CAP
    except ValueError:
        return DEFAULT_DAILY_CAP


def set_disabled(disabled, description="A0 (HG-078): every AI call is refused while this is on."):
    SystemSetting.objects.update_or_create(key=KILL_SWITCH_KEY, defaults={
        "value": "1" if disabled else "0", "data_type": SystemSetting.DataType.BOOLEAN, "active": True, "description": description})


def set_daily_cap(cap, description="A0 (HG-078): the most AI calls a day (Cairo day) for this company."):
    SystemSetting.objects.update_or_create(key=DAILY_CAP_KEY, defaults={
        "value": str(max(0, int(cap))), "data_type": SystemSetting.DataType.INTEGER, "active": True, "description": description})


# ---- the Cairo day ----

def cairo_day_bounds(now=None):
    """The start and end (exclusive) of the Cairo calendar day that contains ``now``."""

    day = timezone.localtime(now or timezone.now(), CAIRO).date()
    start = datetime.combine(day, dt_time.min, tzinfo=CAIRO)
    return start, datetime.combine(day + timedelta(days=1), dt_time.min, tzinfo=CAIRO)


def calls_today(now=None):
    start, end = cairo_day_bounds(now)
    return AuditLog.objects.filter(module=MODULE, action__in=COUNTED, created_at__gte=start, created_at__lt=end).count()


# ---- the audit row ----

def _audit(action, *, feature, provider_code, user, entity, request_chars, request_bytes, refusal="", status=None,
           response_bytes=0, duration_ms=0):
    result = {CALL_OK: "ok", CALL_REFUSED: "refused", CALL_ERROR: "error"}[action]
    AuditLog.objects.create(
        event_type=AuditEventType.EXPORT,
        actor=user if user is not None and getattr(user, "is_authenticated", False) else None,
        module=MODULE,
        action=action,
        object_type="entities.Entity" if entity is not None else "",
        object_id=str(entity.pk) if entity is not None else "",
        after_data={
            "feature": feature.code, "provider": provider_code, "mode": providers.mode(),
            "entity": entity.pk if entity is not None else None,
            "request_chars": request_chars, "request_bytes": request_bytes, "result": result, "refusal": refusal,
            "error_status": status, "response_bytes": response_bytes, "duration_ms": duration_ms,
        },
        reason=f"AI call for {feature.code}: {result}.",
    )


def _feature(code):
    try:
        return FEATURES[code]
    except KeyError:
        raise ValueError(f"Unknown AI feature: {code!r}") from None


def _capability_on(slug):
    from settings_core.capabilities import capability_enabled

    return capability_enabled(slug)


def _gate(feature, size, user, entity, *, valid, invalid_error):
    """Run the refusals in order; return the provider. Each refusal is audited, then raised."""

    chars, size_bytes = size

    def refuse(code, error):
        _audit(CALL_REFUSED, feature=feature, provider_code=feature.provider, user=user, entity=entity,
               request_chars=chars, request_bytes=size_bytes, refusal=code)
        raise error

    if is_disabled():
        refuse(DISABLED, ValidationError("AI features are switched off for this company."))
    if not _capability_on(feature.capability):
        refuse(CAPABILITY_OFF, ValidationError("This AI feature is not switched on."))
    if not valid:
        refuse(INVALID, invalid_error)
    provider = providers.get(feature.provider)
    if provider is None:
        refuse(NOT_CONNECTED, ValidationError("The AI service is not connected on this server."))
    cap = daily_cap()
    if calls_today() >= cap:
        refuse(DAILY_CAP, ValidationError(f"Today's AI call limit is reached ({cap} calls). Try again tomorrow."))
    return provider


# ---- the calls ----

def text_to_speech(text, *, feature, user=None, entity=None, voice_settings=None):
    """Voice ``text`` for ``feature`` (a key of ``FEATURES``); return ``providers.Speech``. Raises ValidationError."""

    feature = _feature(feature)
    text = str(text or "").strip()
    size = (len(text), len(text.encode("utf-8")))
    if not text:
        invalid = ValidationError("There is no text to read aloud.")
    else:
        invalid = ValidationError(f"The text is too long to read aloud (at most {TTS_MAX_CHARS} characters).")
    provider = _gate(feature, size, user, entity, valid=bool(text) and len(text) <= TTS_MAX_CHARS, invalid_error=invalid)
    started = time.monotonic()
    try:
        speech = provider.synthesize(text, voice_settings=voice_settings)
    except ProviderError as exc:
        _audit(CALL_ERROR, feature=feature, provider_code=provider.code, user=user, entity=entity, request_chars=size[0],
               request_bytes=size[1], status=exc.status, duration_ms=int((time.monotonic() - started) * 1000))
        raise ValidationError("The AI service did not answer. Try again later.") from exc
    _audit(CALL_OK, feature=feature, provider_code=provider.code, user=user, entity=entity, request_chars=size[0],
           request_bytes=size[1], response_bytes=speech.size, duration_ms=int((time.monotonic() - started) * 1000))
    return speech
