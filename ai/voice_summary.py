"""A1 (HG-079): the voice summary's audio. The text comes from ``ai.summary``; only the voice is made outside.

``speak(summary, user)`` returns the cached audio row for this summary, voicing it first only when no row holds it:

1. old rows are pruned (seven Cairo days are kept: today and the six before);
2. a ready row for the same entity scope, language, Cairo day and text hash is returned as it is (no call);
3. otherwise the request claims the row: it inserts it, not ready, in its own committed transaction. The unique
   constraint lets one request in; a second one arriving meanwhile is told the audio is being prepared (``Busy``)
   instead of paying for the same text again. A claim left behind by a process that died is taken over after
   ``STALE_CLAIM``;
4. unless the listener has already started ``user_daily_cap()`` voicings today (listening to cached audio never counts),
   ``ai.services.text_to_speech`` is called **outside any transaction** (A0 refuses it inside one), and the audio is
   stored on the row, which becomes ready. If the call is refused or fails, the claim is deleted and the error raised,
   so the next press can try again.

The AI layer writes the audit rows (HG-078): the feature, entity and sizes, never the text.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import services as ai_services
from .models import VoiceSummaryAudio

logger = logging.getLogger(__name__)

FEATURE = "voice_summary"
CAPABILITY = "ai_voice_summary"
#: Today and the six days before.
KEEP_DAYS = 7
#: A claim older than this belongs to a request that never finished (the provider's own deadline is ~30 s).
STALE_CLAIM = timedelta(minutes=2)
#: The most voicings (paid calls) one person may start in a Cairo day, inside the company's cap. Listening to audio that
#: is already cached is free and never counted. ``AI_VOICE_SUMMARY_USER_DAILY_CAP`` in the server's settings overrides it.
DEFAULT_USER_DAILY_CAP = 5
#: ``VoiceSummaryAudio.content_type`` holds at most this many characters.
CONTENT_TYPE_MAX = 60


class Busy(ValidationError):
    """Another request is voicing the same summary right now."""

    def __init__(self):
        super().__init__("The audio for this summary is being prepared. Try again in a moment.")


def is_on():
    from settings_core.capabilities import capability_enabled

    return capability_enabled(CAPABILITY)


def _key(summary):
    return {"scope_key": summary.scope_key, "language": summary.lang, "day": summary.day, "text_sha256": summary.text_sha256}


def oldest_kept_day(today=None):
    return (today or timezone.localdate()) - timedelta(days=KEEP_DAYS - 1)


def prune(today=None):
    """Delete the audio older than the seven kept days. Returns how many rows went."""

    deleted, _ = VoiceSummaryAudio.objects.filter(day__lt=oldest_kept_day(today)).delete()
    return deleted


def _ready(summary):
    return VoiceSummaryAudio.objects.filter(ready=True, **_key(summary))


def is_cached(summary):
    """Whether this summary's audio is ready. One ``EXISTS`` query; the bytes are never read."""

    return bool(summary.text) and _ready(summary).exists()


def cached(summary):
    """The ready row for exactly this summary (scope, language, Cairo day and text), or None, without its bytes."""

    if not summary.text:
        return None
    return _ready(summary).defer("audio").first()


def cached_audio(summary):
    """The ready row with its bytes, for streaming (the only place that reads them), or None."""

    if not summary.text:
        return None
    return _ready(summary).only("audio", "content_type", "size").first()


def user_daily_cap():
    try:
        return max(0, int(getattr(settings, "AI_VOICE_SUMMARY_USER_DAILY_CAP", DEFAULT_USER_DAILY_CAP)))
    except (TypeError, ValueError):
        return DEFAULT_USER_DAILY_CAP


def voicings_today(user, now=None):
    """The voice summary calls ``user`` started this Cairo day: their ``call_started`` rows for this feature (HG-078)."""

    from audit.models import AuditLog

    start, end = ai_services.cairo_day_bounds(now)
    return AuditLog.objects.filter(module=ai_services.MODULE, action=ai_services.CALL_STARTED, actor=user,
                                   after_data__feature=FEATURE, created_at__gte=start, created_at__lt=end).count()


def _clean_content_type(value):
    """The provider's type, cut to what the column holds; anything that is not audio is stored as MP3."""

    value = str(value or "").split(";")[0].strip().lower()
    if not value.startswith("audio/") or len(value) > CONTENT_TYPE_MAX:
        return "audio/mpeg"
    return value


def _claim(summary, user):
    """Insert the not-ready row for this summary, or take over a stale one. Returns ``(row, claimed)``: a ready row
    already there comes back unclaimed; a fresh claim held by another request raises ``Busy``."""

    key = _key(summary)

    def insert():
        with transaction.atomic():
            return VoiceSummaryAudio.objects.create(entity=summary.entity, text=summary.text,
                                                    created_by=user if getattr(user, "is_authenticated", False) else None, **key)

    try:
        return insert(), True
    except IntegrityError:
        pass
    row = VoiceSummaryAudio.objects.filter(**key).defer("audio").first()
    if row is None:  # the other claim failed and was deleted in between: one more try
        return insert(), True
    if row.ready:
        return row, False
    now = timezone.now()
    if row.created_at <= now - STALE_CLAIM and VoiceSummaryAudio.objects.filter(pk=row.pk, ready=False, created_at=row.created_at).update(created_at=now):
        row.created_at = now
        return row, True
    raise Busy()


def speak(summary, user):
    """The ready audio row for ``summary``, voiced now if it is not cached. Raises ``ValidationError`` (a refusal from
    the AI layer, or ``Busy``); ``ai.services.AiCallInTransaction`` if called inside ``transaction.atomic()``."""

    if not summary.text:
        raise ValidationError("There is nothing to summarise for you yet.")
    if ai_services.in_outer_transaction():  # checked before claiming, so a misuse leaves no claim behind
        raise ai_services.AiCallInTransaction("The voice summary must be voiced outside transaction.atomic().")
    prune(summary.day)
    row, claimed = _claim(summary, user)
    if not claimed:
        return row  # cached: free, and not counted against anyone
    cap = user_daily_cap()
    if voicings_today(user) >= cap:
        VoiceSummaryAudio.objects.filter(pk=row.pk, ready=False).delete()
        raise ValidationError(f"You have reached today's voice summary limit ({cap} per person). Try again tomorrow.")
    try:
        speech = ai_services.text_to_speech(summary.text, feature=FEATURE, user=user, entity=summary.entity)
    except BaseException:
        VoiceSummaryAudio.objects.filter(pk=row.pk, ready=False).delete()
        raise
    content_type = _clean_content_type(speech.content_type)
    try:
        VoiceSummaryAudio.objects.filter(pk=row.pk).update(audio=speech.audio, content_type=content_type, size=speech.size, ready=True)
    except Exception:
        # The call was paid for (its audit rows are written), but the audio could not be kept. Free the claim at once, so
        # the next press is not told "being prepared" for two minutes, and say so in the log.
        logger.exception("A1 (HG-079): the voice summary audio could not be saved (row %s, %s bytes).", row.pk, speech.size)
        VoiceSummaryAudio.objects.filter(pk=row.pk, ready=False).delete()
        raise
    row.ready, row.content_type, row.size = True, content_type, speech.size
    return row


def card_context(user, lang, today=None, shared=None, sections=None, capabilities=None):
    """What the dashboard card needs, or None when the card is not shown: the capability is off, or the listener may
    see none of the figures. ``capabilities``, when given, is the set the dashboard already read (no extra query)."""

    on = CAPABILITY in capabilities if capabilities is not None else is_on()
    if not on:
        return None
    from django.urls import reverse

    from .summary import WORDS, build_summary

    summary = build_summary(user, lang, today=today, shared=shared, sections=sections)
    if not summary.has_figures:
        return None
    from .sample import notice

    return {"summary": summary, "words": WORDS[summary.lang], "audio_url": audio_url(summary) if is_cached(summary) else "",
            "endpoint": reverse("ai:voice_summary"), "sample": notice(FEATURE, summary.lang)}  # HG-078 dev. 7: the mock is labelled


def audio_url(summary):
    from django.urls import reverse

    return f"{reverse('ai:voice_summary_audio', args=[summary.text_sha256])}?lang={summary.lang}"
