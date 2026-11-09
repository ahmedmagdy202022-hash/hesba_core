"""HG-078 (A0): the one door every AI call goes through.

A feature (A1's voice summary first) never talks to a provider directly. It calls a function here naming itself,
and this module, in order:

0. refuses, as a programming error (``AiCallInTransaction``), to run inside a caller's ``transaction.atomic()``: a
   rollback there would erase the audit row of a call that may have been paid for;
1. refuses everything while the kill switch ``SystemSetting ai_disabled`` is on;
2. refuses unless the feature's own capability (``settings_core.capabilities``) is on;
3. refuses a request that is empty or too large;
4. refuses when this server has no provider connected (``AI_MODE`` off, or live with a key missing);
5. under a row lock that serialises every reservation, refuses once today's calls (Cairo day) reach the cap in
   ``SystemSetting ai_daily_call_cap`` (default 50, never above ``settings.AI_DAILY_CALL_CAP_MAX``), or else writes a
   ``call_started`` audit row, which counts at once;
6. calls the provider, mock or live, outside any transaction, and turns that same row into ``call_ok`` or
   ``call_error``. Any exception from the provider is recorded as an error before it propagates.

Each call, refused or not, leaves one ``AuditLog`` row (module ``ai``) with the feature, provider, mode, entity, request
size, result and duration, and **never** the text sent, the audio, customer names, phones or amounts. Refusals are
``ValidationError`` in English, with the Arabic in ``settings_core/ui_messages.py``.

The cap is per installation (one client company, one key): every entity's calls count. Started, ok and error rows
count; refusals do not. A call cut off before it finishes (the process killed mid-call) stays ``call_started`` and
keeps counting for that day.
"""

import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from settings_core.models import SystemSetting

from . import providers
from .providers import ProviderError

CAIRO = ZoneInfo("Africa/Cairo")

KILL_SWITCH_KEY = "ai_disabled"
DAILY_CAP_KEY = "ai_daily_call_cap"
#: An inactive row that holds no value: every reservation locks it, so check-and-reserve is one step at a time.
LOCK_KEY = "ai_call_lock"
DEFAULT_DAILY_CAP = 50
#: The most characters one text-to-speech call may send (a day's summary is a few hundred).
TTS_MAX_CHARS = 5000

MODULE = "ai"
CALL_STARTED, CALL_OK, CALL_REFUSED, CALL_ERROR = "call_started", "call_ok", "call_refused", "call_error"
COUNTED = (CALL_STARTED, CALL_OK, CALL_ERROR)
RESULTS = {CALL_STARTED: "started", CALL_OK: "ok", CALL_REFUSED: "refused", CALL_ERROR: "error"}

# Why a call was refused, as recorded in the audit row.
DISABLED, CAPABILITY_OFF, INVALID, NOT_CONNECTED, DAILY_CAP = "disabled", "capability_off", "invalid_request", "not_connected", "daily_cap"


class AiCallInTransaction(RuntimeError):
    """A developer called the AI layer inside ``transaction.atomic()``. Call it outside the transaction instead."""


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


def cap_ceiling():
    """The hard upper bound from the server's environment (``AI_DAILY_CALL_CAP_MAX``), whatever the setting says."""

    try:
        return max(0, int(getattr(settings, "AI_DAILY_CALL_CAP_MAX", 500)))
    except (TypeError, ValueError):
        return 0


def daily_cap():
    raw = str(_setting(DAILY_CAP_KEY) or "").strip()
    try:
        cap = max(0, int(raw)) if raw else DEFAULT_DAILY_CAP
    except ValueError:
        cap = DEFAULT_DAILY_CAP
    return min(cap, cap_ceiling())


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


# ---- transactions ----

def in_outer_transaction(using=None):
    """True inside a caller's ``transaction.atomic()``. A Django ``TestCase``'s own wrapping blocks do not count (the
    same exemption Django gives durable blocks)."""

    return any(not getattr(block, "_from_testcase", False) for block in transaction.get_connection(using).atomic_blocks)


def _lock():
    """Take the reservation lock: one row, created on demand, locked FOR UPDATE until the reservation commits."""

    SystemSetting.objects.get_or_create(key=LOCK_KEY, defaults={
        "value": "", "active": False, "description": "A0 (HG-078): locked while an AI call is reserved; holds no value."})
    return SystemSetting.objects.select_for_update().get(key=LOCK_KEY)


# ---- the audit row ----

def _data(feature, provider_code, mode, entity, size, result, refusal="", status=None, response_bytes=0, duration_ms=0):
    return {
        "feature": feature.code, "provider": provider_code, "mode": mode, "entity": entity.pk if entity is not None else None,
        "request_chars": size[0], "request_bytes": size[1], "result": result, "refusal": refusal,
        "error_status": status, "response_bytes": response_bytes, "duration_ms": duration_ms,
    }


def _write(action, user, entity, data):
    return AuditLog.objects.create(
        event_type=AuditEventType.EXPORT,
        actor=user if user is not None and getattr(user, "is_authenticated", False) else None,
        module=MODULE,
        action=action,
        object_type="entities.Entity" if entity is not None else "",
        object_id=str(entity.pk) if entity is not None else "",
        after_data=data,
        reason=f"AI call for {data['feature']}: {data['result']}.",
    )


def _finish(row, action, started, **fields):
    """Turn the reserved ``call_started`` row into the call's outcome (the one update an AI audit row ever gets)."""

    row.action = action
    row.after_data = dict(row.after_data, result=RESULTS[action], duration_ms=int((time.monotonic() - started) * 1000), **fields)
    row.reason = f"AI call for {row.after_data['feature']}: {row.after_data['result']}."
    row.save(update_fields=["action", "after_data", "reason"])


def _feature(code):
    try:
        return FEATURES[code]
    except KeyError:
        raise ValueError(f"Unknown AI feature: {code!r}") from None


def _capability_on(slug):
    from settings_core.capabilities import capability_enabled

    return capability_enabled(slug)


def _refuse(feature, user, entity, size, code, error):
    _write(CALL_REFUSED, user, entity, _data(feature, feature.provider, providers.mode(), entity, size, "refused", refusal=code))
    raise error


def _gate(feature, size, user, entity, *, valid, invalid_error):
    """Refusals 1 to 4, in order; return the provider. Each refusal is audited, then raised."""

    if is_disabled():
        _refuse(feature, user, entity, size, DISABLED, ValidationError("AI features are switched off for this company."))
    if not _capability_on(feature.capability):
        _refuse(feature, user, entity, size, CAPABILITY_OFF, ValidationError("This AI feature is not switched on."))
    if not valid:
        _refuse(feature, user, entity, size, INVALID, invalid_error)
    provider = providers.get(feature.provider)
    if provider is None:
        _refuse(feature, user, entity, size, NOT_CONNECTED, ValidationError("The AI service is not connected on this server."))
    return provider


def _reserve(feature, provider, size, user, entity):
    """Refusal 5 and the reservation, under the lock. Returns ``(started row or None, cap)``."""

    with transaction.atomic(durable=True):
        _lock()
        cap = daily_cap()
        if calls_today() >= cap:
            return None, cap
        mode = providers.LIVE if provider.live else providers.MOCK
        return _write(CALL_STARTED, user, entity, _data(feature, provider.code, mode, entity, size, "started")), cap


# ---- the calls ----

def text_to_speech(text, *, feature, user=None, entity=None, voice_settings=None):
    """Voice ``text`` for ``feature`` (a key of ``FEATURES``); return ``providers.Speech``. Raises ValidationError."""

    feature = _feature(feature)
    if in_outer_transaction():
        raise AiCallInTransaction(
            "AI calls must not run inside transaction.atomic(): a rollback would erase the audit row of a call that may "
            "have been paid for. Call ai.services outside the transaction.")
    text = str(text or "").strip()
    size = (len(text), len(text.encode("utf-8")))
    if not text:
        invalid = ValidationError("There is no text to read aloud.")
    else:
        invalid = ValidationError(f"The text is too long to read aloud (at most {TTS_MAX_CHARS} characters).")
    provider = _gate(feature, size, user, entity, valid=bool(text) and len(text) <= TTS_MAX_CHARS, invalid_error=invalid)
    row, cap = _reserve(feature, provider, size, user, entity)
    if row is None:
        _refuse(feature, user, entity, size, DAILY_CAP, ValidationError(f"Today's AI call limit is reached ({cap} calls). Try again tomorrow."))
    started = time.monotonic()
    try:
        speech = provider.synthesize(text, voice_settings=voice_settings)
    except ProviderError as exc:
        _finish(row, CALL_ERROR, started, error_status=exc.status)
        raise ValidationError("The AI service did not answer. Try again later.") from exc
    except Exception:
        _finish(row, CALL_ERROR, started)  # recorded, then raised as it came (a test's network guard stays visible)
        raise
    _finish(row, CALL_OK, started, response_bytes=speech.size)
    return speech
