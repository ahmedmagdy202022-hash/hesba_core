"""DEMO-TRACK: who visited the demo, when, in which activity, and what they opened.

On a showcase install only (DEMO_MODE), every visitor gets a signed cookie of
their own on the first page. Each page they open is noted, with the activity
their demo copy is in, in the feedback database: outside every demo copy, so
a deploy or a fresh start never loses it. A client's real install records
nothing.

Only full pages are noted: a GET answered 200 with HTML. Static files, the
feedback endpoints themselves, the health check and background fetches are
not. The same page opened again within a few seconds is noted once, and one
visit keeps at most MAX_VIEWS pages. Tracking never breaks a page: if the
feedback database is down, the page is served and nothing is noted.
"""

import logging
import queue
import secrets
import threading

from django.conf import settings
from django.core.cache import cache
from django.db import DatabaseError
from django.utils import timezone

log = logging.getLogger("hesba.feedback")

COOKIE = "hesba_visit"
SALT = "hesba.demo-visit"
MAX_AGE = 365 * 86400
MAX_VIEWS = 300
REPEAT_SECONDS = 3
_SKIP = ("/static/", "/demo/feedback/", "/healthz", "/favicon", "/robots.txt")


def visitor_of(request):
    """The visitor's id from their cookie, or None for a first visit."""

    value = request.get_signed_cookie(COOKIE, default=None, salt=SALT)
    if value and 16 <= len(value) <= 64 and value.replace("-", "").replace("_", "").isalnum():
        return value
    return None


def _worth_noting(request, response):
    if request.method != "GET" or response.status_code != 200:
        return False
    if not response.get("Content-Type", "").startswith("text/html"):
        return False
    if request.headers.get("X-Requested-With") or request.headers.get("Sec-Purpose", "").startswith("prefetch"):
        return False
    return not request.path.startswith(_SKIP)


def note(request, visitor):
    """Note this page for this visitor; never raises."""

    from .views import _context_of, _sandbox_of

    path = request.get_full_path()[:500]
    if not cache.add(f"visit-page:{visitor}:{path}", 1, REPEAT_SECONDS):
        return
    activity, sub_activity, role = _context_of(request)
    lang = request.GET.get("lang", "")
    record = {
        "visitor": visitor, "at": timezone.now(), "path": path, "sandbox": _sandbox_of(request)[:64],
        "activity": activity, "sub_activity": sub_activity, "role": role,
        "lang": lang if lang in ("ar", "en") else "",
        "user_agent": request.META.get("HTTP_USER_AGENT", "")[:300],
    }
    if in_background():
        _enqueue(record)
    else:
        write(record)


def write(record):
    """Store one noted page; never raises."""

    from django.db import connections

    from .models import PageView, Visit

    try:
        visit, _ = Visit.objects.get_or_create(visitor=record["visitor"], defaults={
            "last_seen_at": record["at"], "first_path": record["path"], "lang": record["lang"],
            "user_agent": record["user_agent"],
        })
        changes = {"last_seen_at": record["at"], "last_path": record["path"]}
        if record["sandbox"]:
            changes["sandbox"] = record["sandbox"]
        # Keep the last activity chosen; a page before choosing one says nothing new.
        if record["activity"]:
            changes.update(activity=record["activity"], sub_activity=record["sub_activity"])
        if record["role"]:
            changes["role"] = record["role"]
        if record["lang"]:
            changes["lang"] = record["lang"]
        if visit.pages < MAX_VIEWS:
            PageView.objects.create(visit=visit, path=record["path"], activity=record["activity"],
                                    sub_activity=record["sub_activity"])
            changes["pages"] = visit.pages + 1
        Visit.objects.filter(pk=visit.pk).update(**changes)
    except DatabaseError:
        log.info("DEMO-TRACK: the feedback database is unreachable; page not noted.")
        connections["feedback"].close()  # the next page opens a fresh one
    except Exception as error:  # noting a page must never cost the visitor the page
        log.warning("DEMO-TRACK: page not noted (%s).", type(error).__name__)


# A remote feedback database (Supabase) is a network trip away, and its
# connection is not kept between requests. One writer thread per server
# process keeps a single connection open and stores pages after they are
# served, so the visitor never waits for them. Pages beyond MAX_QUEUE waiting
# are dropped rather than held in memory.
MAX_QUEUE = 1000
_queue = queue.Queue(maxsize=MAX_QUEUE)
_writer = None
_writer_lock = threading.Lock()


def in_background():
    choice = getattr(settings, "DEMO_TRACK_BACKGROUND", None)
    if choice is not None:
        return bool(choice)
    return settings.DATABASES["feedback"]["ENGINE"].endswith("postgresql")


def _run():
    while True:
        write(_queue.get())
        _queue.task_done()


def _enqueue(record):
    global _writer
    with _writer_lock:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_run, name="hesba-demo-track", daemon=True)
            _writer.start()
    try:
        _queue.put_nowait(record)
    except queue.Full:
        log.info("DEMO-TRACK: too many pages waiting; this one is not noted.")


class DemoVisitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not getattr(settings, "DEMO_MODE", False):
            return self.get_response(request)
        visitor = visitor_of(request)
        new = visitor is None
        if new:
            visitor = secrets.token_urlsafe(18)
        request.demo_visitor = visitor
        response = self.get_response(request)
        if _worth_noting(request, response):
            note(request, visitor)
            if new:
                response.set_signed_cookie(
                    COOKIE, visitor, salt=SALT, max_age=MAX_AGE, httponly=True, samesite="Lax",
                    secure=request.is_secure(),
                )
        return response
