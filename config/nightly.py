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
