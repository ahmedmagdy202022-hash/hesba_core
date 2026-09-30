"""DIGEST-002: send each chosen user their daily follow-up email.

The owner picks, from Settings -> Daily email:

* morning, evening or both, and the hour of each (Cairo time);
* which users receive it (each gets only what their own permissions show);
* the language.

An external scheduler (the same cron-job.org job family as the nightly
backup) calls ``/ops/digest/`` every hour. A slot is sent once per day, the
first time the scheduler calls at or after its hour and within a few hours
of it, so a missed call is caught up but a stale morning email is never sent
at night. The last send of each slot is recorded; the settings screen shows it.
"""

import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import EmailMultiAlternatives, get_connection
from django.template.loader import render_to_string
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from .models import SystemSetting


PREFIX = "digest."
SLOTS = ("morning", "evening")
DEFAULT_HOURS = {"morning": 8, "evening": 21}
WINDOW_HOURS = 3


def _get(key, default=""):
    value = SystemSetting.objects.filter(key=PREFIX + key, active=True).values_list("value", flat=True).first()
    return default if value in (None, "") else value


def _set(key, value, description=""):
    SystemSetting.objects.update_or_create(key=PREFIX + key, defaults={"value": value, "active": True, "description": description,
                                                                       "data_type": SystemSetting.DataType.JSON if isinstance(value, str) and value[:1] in "[{" else SystemSetting.DataType.STRING})


def email_ready():
    """True when an SMTP server is configured (a test backend counts)."""

    backend = getattr(settings, "EMAIL_BACKEND", "")
    return bool(getattr(settings, "EMAIL_HOST", "")) or not backend.endswith("smtp.EmailBackend")


def config():
    try:
        slots = [slot for slot in json.loads(_get("slots", "[]")) if slot in SLOTS]
    except ValueError:
        slots = []
    try:
        recipients = [int(pk) for pk in json.loads(_get("recipients", "[]"))]
    except (ValueError, TypeError):
        recipients = []
    hours = {}
    for slot in SLOTS:
        try:
            hours[slot] = min(max(int(_get(f"{slot}_hour", DEFAULT_HOURS[slot])), 0), 23)
        except (TypeError, ValueError):
            hours[slot] = DEFAULT_HOURS[slot]
    return {"slots": slots, "hours": hours, "recipients": recipients, "lang": "en" if _get("lang") == "en" else "ar",
            "last": {slot: _get(f"sent.{slot}") for slot in SLOTS}}


def save_config(*, slots, hours, recipients, lang, user):
    slots = [slot for slot in SLOTS if slot in slots]
    users = list(get_user_model().objects.filter(pk__in=recipients, is_active=True).exclude(email="").values_list("pk", flat=True))
    _set("slots", json.dumps(slots), "Daily email slots")
    for slot in SLOTS:
        _set(f"{slot}_hour", str(min(max(int(hours.get(slot, DEFAULT_HOURS[slot])), 0), 23)))
    _set("recipients", json.dumps(sorted(users)), "Daily email recipients (user ids)")
    _set("lang", "en" if lang == "en" else "ar")
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user, module="settings", action="daily_email_settings",
                            object_type="settings_core.SystemSetting", object_id="digest", before_data={},
                            after_data={"slots": slots, "hours": {s: int(hours.get(s, DEFAULT_HOURS[s])) for s in SLOTS}, "recipients": users, "lang": lang})
    return config()


def _link(path):
    base = getattr(settings, "PUBLIC_BASE_URL", "")
    return f"{base}{path}" if base and path else ""


def build_message(user, slot, today, lang, connection=None):
    """One recipient's email, built from what they may see."""

    from printing.company import company_details
    from reports.followup import followup

    data = followup(user, today, slot, lang)
    company = company_details()
    for item in data["todo"]:
        item["link"] = _link(item["path"] + ("&" if "?" in item["path"] else "?") + f"lang={lang}" if item["path"] else "")
    context = {"data": data, "company": company, "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "user": user,
               "summary_link": _link(f"/reports/daily/?lang={lang}&date={data['covered'].isoformat()}"),
               "settings_link": _link(f"/settings/daily-email/?lang={lang}")}
    subject = f"{company['name']} · {data['title']} · {today:%Y-%m-%d}"
    text = render_to_string("emails/daily_followup.txt", context)
    html = render_to_string("emails/daily_followup.html", context)
    message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [user.email], connection=connection)
    message.attach_alternative(html, "text/html")
    return message


def send_slot(slot, today=None, users=None, record=True):
    """Send one slot to its recipients now. Returns {"sent": n, "failed": [...]}."""

    today = today or timezone.localdate()
    conf = config()
    if users is None:
        users = get_user_model().objects.filter(pk__in=conf["recipients"], is_active=True).exclude(email="")
    sent, failed = 0, []
    connection = get_connection(fail_silently=False)
    for user in users:
        try:
            build_message(user, slot, today, conf["lang"], connection).send()
            sent += 1
        except Exception as exc:  # one bad address must not stop the others
            failed.append({"user": user.get_username(), "error": exc.__class__.__name__})
    if record and sent:
        _set(f"sent.{slot}", today.isoformat())
    return {"sent": sent, "failed": failed}


def run_due(now=None):
    """Called every hour: send each chosen slot that is due and not sent today."""

    now = timezone.localtime(now or timezone.now())
    conf = config()
    report = {"status": "idle", "slots": {}}
    if not email_ready():
        report["status"] = "not_configured"
        return report
    for slot in conf["slots"]:
        hour = conf["hours"][slot]
        due = hour <= now.hour < hour + WINDOW_HOURS
        if not due or conf["last"].get(slot) == now.date().isoformat():
            continue
        result = send_slot(slot, now.date())
        report["slots"][slot] = result
        report["status"] = "sent" if result["sent"] else "failed"
    return report


def recipients_choices():
    """Active users, with whether they have an email address to send to."""

    users = get_user_model().objects.filter(is_active=True).order_by("username")
    return [{"user": user, "has_email": bool(user.email)} for user in users]


def next_run(conf, now=None):
    now = timezone.localtime(now or timezone.now())
    upcoming = []
    for slot in conf["slots"]:
        at = now.replace(hour=conf["hours"][slot], minute=0, second=0, microsecond=0)
        if at <= now:
            at += timedelta(days=1)
        upcoming.append((at, slot))
    return min(upcoming) if upcoming else None
