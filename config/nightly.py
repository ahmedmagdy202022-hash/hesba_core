"""BACKUP-003: a daily trigger for hosts without cron (e.g. a free web service).

An external scheduler calls ``/ops/nightly/`` once a day with the
``NIGHTLY_TOKEN`` in the ``X-Hesba-Token`` header (or ``?token=``). The answer
says only whether the backup was uploaded, skipped or failed; nothing else.
"""

import hmac

from django.conf import settings
from django.contrib.auth.decorators import login_not_required
from django.http import Http404, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
@never_cache
@login_not_required
def nightly(request):
    expected = getattr(settings, "NIGHTLY_TOKEN", "")
    given = request.headers.get("X-Hesba-Token") or request.GET.get("token") or ""
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise Http404()
    from settings_core.drive_backup import DriveError, run_nightly

    try:
        report = run_nightly()
    except DriveError:
        return JsonResponse({"status": "failed"}, status=503)
    return JsonResponse({"status": report["status"]})


@csrf_exempt
@never_cache
@login_not_required
def digest(request):
    """DIGEST-002: called every hour; sends each daily email that is due, once."""

    expected = getattr(settings, "NIGHTLY_TOKEN", "")
    given = request.headers.get("X-Hesba-Token") or request.GET.get("token") or ""
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise Http404()
    from settings_core.daily_email import run_due

    report = run_due()
    return JsonResponse({"status": report["status"], "sent": {slot: result["sent"] for slot, result in report["slots"].items()}})


@csrf_exempt
@never_cache
@login_not_required
def messages_run(request):
    """M3: called every hour by the same scheduler: gathers the day's customer messages (appointment and follow-up
    reminders among them) and sends those with an approved WhatsApp template, inside the sending hours. Without
    WhatsApp keys it only gathers: the messages wait in the outbox for a click. Same guard as ``digest``; the answer
    holds counts only, never a name, a phone or a text."""

    expected = getattr(settings, "NIGHTLY_TOKEN", "")
    given = request.headers.get("X-Hesba-Token") or request.GET.get("token") or ""
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise Http404()
    from messaging.services import run_scheduled

    return JsonResponse(run_scheduled())
