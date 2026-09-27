"""OPS-001: a liveness check for the hosting platform or an uptime monitor.

Open to anonymous callers and deliberately bare: it says whether the app and
its database answer, never versions, hosts or counts.
"""

from django.contrib.auth.decorators import login_not_required
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache


@never_cache
@login_not_required
def healthz(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # the check must answer, not raise
        return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})
