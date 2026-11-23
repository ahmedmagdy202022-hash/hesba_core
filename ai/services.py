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
6. calls the provider, mock or live, outside any transaction, and appends a ``call_ok`` or ``call_error`` row whose
   ``before_data`` is ``{"started": <started row id>}``. Any exception from the provider is recorded as an error before
   it propagates.

Audit rows are append-only (``audit.models.AuditLog``): nothing here updates one. A refused call leaves one
``call_refused`` row; a call that reached the provider leaves its ``call_started`` row plus one outcome row. Each row
holds the feature, provider, mode, entity, request size and result (outcome rows add the duration and response size),
and **never** the text sent, the audio, customer names, phones or amounts. Refusals are ``ValidationError`` in
English, with the Arabic in ``settings_core/ui_messages.py``.

The cap is per installation (one client company, one key): every entity's calls count. Only ``call_started`` rows
count, one per call; refusals and outcome rows do not. A call cut off before it finishes (the process killed mid-call)
is a started row with no outcome row, and keeps counting for that day.

Two doors share every step above:

* ``text_to_speech`` (A1, HG-079): text in, audio out;
* ``route_question`` (A2, HG-080): a question in, **one tool name and its enum arguments** out. Before it reaches the
  gate the question is cleaned (control and invisible characters dropped, spaces collapsed) and must be 1 to
  ``QUESTION_MAX_CHARS`` characters. A per-person daily limit is checked under the same lock as the company cap. The
  provider's choice is checked again here against the very tools that were sent (``check_tool_call``): an unknown name,
  a missing or extra argument, or a value outside its enum (``True`` is not ``1``) is ``invalid_*``, never run. The
  outcome row adds the routing status and, only when valid, the tool name, period and limit: server constants, never
  anything the model wrote, and never the question.
* ``read_invoice_image`` (A3, HG-081): a photo in (JPEG, PNG or WebP, its type taken from its first bytes and matching
  the type declared, 1 byte to ``IMAGE_MAX_BYTES``), the model's decoded JSON out, **unchecked**: the feature
  (``ai.invoice_photo``) validates every field. The request size is the photo's bytes (no characters). A per-person daily
  limit is checked under the lock, as for questions. The outcome row adds the reading status and how many lines the
  answer had: never the photo, a supplier's name, a line's text or an amount.
"""

import time
import unicodedata
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
from .providers import ProviderError, Routing
from .providers.base import INVALID_ARGUMENTS, INVALID_TOOL, READ

CAIRO = ZoneInfo("Africa/Cairo")

KILL_SWITCH_KEY = "ai_disabled"
DAILY_CAP_KEY = "ai_daily_call_cap"
#: An inactive row that holds no value: every reservation locks it, so check-and-reserve is one step at a time.
LOCK_KEY = "ai_call_lock"
DEFAULT_DAILY_CAP = 50
#: The most characters one text-to-speech call may send (a day's summary is a few hundred).
TTS_MAX_CHARS = 5000
#: A2 (HG-080): the most characters one question may have, after cleaning (a question is a sentence).
QUESTION_MAX_CHARS = 300
#: A3 (HG-081): the largest photo one call may send. The API takes up to 10 MB of base64 per image (about 7.5 MB of
#: bytes); 5 MB of bytes stays well inside it, and is S1's own per-file default, so a photo read can always be attached.
IMAGE_MAX_BYTES = 5 * 1024 * 1024
#: The image types the door sends: what the API reads and what S1 can verify by magic bytes (no GIF: S1 cannot keep it).
IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")

MODULE = "ai"
CALL_STARTED, CALL_OK, CALL_REFUSED, CALL_ERROR = "call_started", "call_ok", "call_refused", "call_error"
#: One started row per call reached the provider; the outcome rows (ok / error) point back to it and are not counted.
COUNTED = (CALL_STARTED,)
RESULTS = {CALL_STARTED: "started", CALL_OK: "ok", CALL_REFUSED: "refused", CALL_ERROR: "error"}

# Why a call was refused, as recorded in the audit row.
DISABLED, CAPABILITY_OFF, INVALID, NOT_CONNECTED, DAILY_CAP = "disabled", "capability_off", "invalid_request", "not_connected", "daily_cap"
USER_DAILY_CAP = "user_daily_cap"  # A2 (HG-080): one person's own limit for a feature


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
    "ask": Feature(code="ask", capability="ai_ask", provider=providers.CLAUDE_ROUTER),  # A2 (HG-080)
    # A3 (HG-081): a supplier's invoice photo read into a draft purchase invoice.
    "invoice_photo": Feature(code="invoice_photo", capability="ai_invoice_photo", provider=providers.CLAUDE_INVOICE_READER),
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


def user_calls_today(user, feature_code, now=None):
    """The calls of ``feature_code`` that ``user`` started this Cairo day (their ``call_started`` rows)."""

    start, end = cairo_day_bounds(now)
    rows = AuditLog.objects.filter(module=MODULE, action__in=COUNTED, after_data__feature=feature_code, created_at__gte=start, created_at__lt=end)
    if user is not None and getattr(user, "is_authenticated", False):
        return rows.filter(actor=user).count()
    return rows.filter(actor__isnull=True).count()


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


def _write(action, user, entity, data, before_data=None):
    return AuditLog.objects.create(
        event_type=AuditEventType.EXPORT,
        actor=user if user is not None and getattr(user, "is_authenticated", False) else None,
        module=MODULE,
        action=action,
        object_type="entities.Entity" if entity is not None else "",
        object_id=str(entity.pk) if entity is not None else "",
        before_data=before_data,
        after_data=data,
        reason=f"AI call for {data['feature']}: {data['result']}.",
    )


def _finish(started_row, action, started, user, entity, **fields):
    """Append the call's outcome as its own row, pointing back to the ``call_started`` row (audit rows are append-only:
    the started row is never changed)."""

    data = dict(started_row.after_data, result=RESULTS[action], duration_ms=int((time.monotonic() - started) * 1000), **fields)
    return _write(action, user, entity, data, before_data={"started": started_row.pk})


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


#: The per-person refusal of each feature that has one (``{cap}`` is the limit). A2's question limit is the default.
USER_CAP_MESSAGES = {
    "invoice_photo": "You have reached today's invoice photo limit ({cap} per person). Try again tomorrow.",  # A3 (HG-081)
}
QUESTION_CAP_MESSAGE = "You have reached today's question limit ({cap} per person). Try again tomorrow."


def _reserve(feature, provider, size, user, entity, user_cap=None):
    """Refusal 5 (and, with ``user_cap``, the person's own daily limit) and the reservation, under the lock.

    Returns ``(started row, None)``, or ``(None, (refusal code, error))``.
    """

    with transaction.atomic(durable=True):
        _lock()
        cap = daily_cap()
        if calls_today() >= cap:
            return None, (DAILY_CAP, ValidationError(f"Today's AI call limit is reached ({cap} calls). Try again tomorrow."))
        if user_cap is not None and user_calls_today(user, feature.code) >= user_cap:
            message = USER_CAP_MESSAGES.get(feature.code, QUESTION_CAP_MESSAGE).format(cap=user_cap)
            return None, (USER_DAILY_CAP, ValidationError(message))
        mode = providers.LIVE if provider.live else providers.MOCK
        return _write(CALL_STARTED, user, entity, _data(feature, provider.code, mode, entity, size, "started")), None


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
    row, refusal = _reserve(feature, provider, size, user, entity)
    if row is None:
        _refuse(feature, user, entity, size, *refusal)
    started = time.monotonic()
    try:
        speech = provider.synthesize(text, voice_settings=voice_settings)
    except ProviderError as exc:
        _finish(row, CALL_ERROR, started, user, entity, error_status=exc.status)
        raise ValidationError("The AI service did not answer. Try again later.") from exc
    except Exception:
        _finish(row, CALL_ERROR, started, user, entity)  # recorded, then raised as it came (a test's network guard stays visible)
        raise
    _finish(row, CALL_OK, started, user, entity, response_bytes=speech.size)
    return speech


# ---- A2 (HG-080): a question routed to one tool ----

#: Unicode categories dropped from a question: control (newlines, escapes), format (bidi overrides, zero-width marks),
#: surrogate, private use and unassigned characters.
_DROPPED_CATEGORIES = ("Cc", "Cf", "Cs", "Co", "Cn")


def clean_question(question):
    """The question with control and invisible characters dropped (each becomes a space) and spaces collapsed."""

    text = unicodedata.normalize("NFC", str(question or ""))
    text = "".join(" " if unicodedata.category(char) in _DROPPED_CATEGORIES else char for char in text)
    return " ".join(text.split())


def check_tool_call(routing, tools):
    """``routing`` checked against the ``tools`` that were sent; never trusts the provider.

    A tool chosen must be one of ``tools`` by name, its arguments a dict with exactly the schema's required keys and no
    other, and each value one of its ``enum`` options **of the same type** (``True`` is not ``1``, ``1.0`` is not ``1``).
    The result carries the schema's own name and enum values, never the provider's objects.
    """

    if not routing.routed:
        return routing
    by_name = {tool["name"]: tool for tool in tools}
    tool = by_name.get(routing.tool) if isinstance(routing.tool, str) else None
    if tool is None:
        return Routing(status=INVALID_TOOL, stop_reason=routing.stop_reason)
    schema = tool["input_schema"]
    properties = schema.get("properties", {})
    arguments = routing.arguments
    invalid = Routing(status=INVALID_ARGUMENTS, stop_reason=routing.stop_reason)
    if type(arguments) is not dict or set(arguments) != set(schema.get("required", ())) or not set(arguments) <= set(properties):
        return invalid
    clean = {}
    for key, value in arguments.items():
        options = properties[key].get("enum")
        if not options:
            return invalid  # every argument is an enum; anything else is refused
        match = [option for option in options if type(option) is type(value) and option == value]
        if not match:
            return invalid
        clean[key] = match[0]
    return Routing(status=routing.status, tool=tool["name"], arguments=clean, stop_reason=routing.stop_reason)


def route_question(question, *, feature, system, tools, user=None, entity=None, user_daily_cap=None):
    """Ask the provider which one of ``tools`` answers ``question``; return the checked ``providers.Routing``.

    The same door as ``text_to_speech``: transaction guard, kill switch, capability, size, connection, the locked cap
    reservation (plus ``user_daily_cap`` when given), and the audit. ``system`` and ``tools`` are the feature's
    constants; the question is the only thing that varies. Raises ValidationError.
    """

    feature = _feature(feature)
    if in_outer_transaction():
        raise AiCallInTransaction(
            "AI calls must not run inside transaction.atomic(): a rollback would erase the audit row of a call that may "
            "have been paid for. Call ai.services outside the transaction.")
    question = clean_question(question)
    size = (len(question), len(question.encode("utf-8")))
    if not question:
        invalid = ValidationError("There is no question to answer.")
    else:
        invalid = ValidationError(f"The question is too long (at most {QUESTION_MAX_CHARS} characters).")
    provider = _gate(feature, size, user, entity, valid=bool(question) and len(question) <= QUESTION_MAX_CHARS, invalid_error=invalid)
    row, refusal = _reserve(feature, provider, size, user, entity, user_cap=user_daily_cap)
    if row is None:
        _refuse(feature, user, entity, size, *refusal)
    started = time.monotonic()
    try:
        routing = check_tool_call(provider.route(question, system=system, tools=tools), tools)
    except ProviderError as exc:
        _finish(row, CALL_ERROR, started, user, entity, error_status=exc.status)
        raise ValidationError("The AI service did not answer. Try again later.") from exc
    except Exception:
        _finish(row, CALL_ERROR, started, user, entity)
        raise
    arguments = routing.arguments if routing.routed else {}
    _finish(row, CALL_OK, started, user, entity, routing=routing.status, tool=routing.tool if routing.routed else "",
            period=arguments.get("period", ""), limit=arguments.get("limit"))
    return routing


# ---- A3 (HG-081): a photo of a supplier's invoice read into JSON ----

def image_type(image):
    """The photo's real type from its first bytes (S1's own sniffing), when it is one the door sends; else None."""

    from attachments.services import sniff

    real = sniff(bytes(image[:16]))
    return real if real in IMAGE_TYPES else None


def read_invoice_image(image, media_type, *, feature, system, schema, user=None, entity=None, user_daily_cap=None):
    """Read ``image`` (bytes) with the provider; return its ``providers.Reading``, whose data is still unchecked.

    The same door as the others: transaction guard, kill switch, capability, size and type, connection, the locked cap
    reservation (plus ``user_daily_cap`` when given), and the audit. ``system`` and ``schema`` are the feature's
    constants; the photo is the only thing that varies. Raises ValidationError.
    """

    feature = _feature(feature)
    if in_outer_transaction():
        raise AiCallInTransaction(
            "AI calls must not run inside transaction.atomic(): a rollback would erase the audit row of a call that may "
            "have been paid for. Call ai.services outside the transaction.")
    image = bytes(image or b"")
    size = (0, len(image))
    if not image:
        valid, invalid = False, ValidationError("There is no photo to read.")
    elif len(image) > IMAGE_MAX_BYTES:
        valid, invalid = False, ValidationError(f"The photo is too large (at most {IMAGE_MAX_BYTES // (1024 * 1024)} MB).")
    elif media_type not in IMAGE_TYPES or image_type(image) != media_type:
        valid, invalid = False, ValidationError("The photo must be a JPEG, PNG or WebP image.")
    else:
        valid, invalid = True, None
    provider = _gate(feature, size, user, entity, valid=valid, invalid_error=invalid)
    row, refusal = _reserve(feature, provider, size, user, entity, user_cap=user_daily_cap)
    if row is None:
        _refuse(feature, user, entity, size, *refusal)
    started = time.monotonic()
    try:
        reading = provider.read(image, media_type, system=system, schema=schema)
    except ProviderError as exc:
        _finish(row, CALL_ERROR, started, user, entity, error_status=exc.status)
        raise ValidationError("The AI service did not answer. Try again later.") from exc
    except Exception:
        _finish(row, CALL_ERROR, started, user, entity)
        raise
    lines = reading.data.get("lines") if reading.status == READ else None
    _finish(row, CALL_OK, started, user, entity, reading=reading.status, line_count=len(lines) if isinstance(lines, list) else 0)
    return reading
