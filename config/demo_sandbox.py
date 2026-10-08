"""DEMO-SANDBOX: a private copy of the demo for every visitor.

On a showcase install (DEMO_MODE and DEMO_SANDBOXES, SQLite only) the start
script builds one demo database, the template. Each visitor who signs in or
starts over gets their own copy of it, named by a signed cookie, and every
request of theirs runs against that copy: what one tester chooses, types or
wipes never reaches anyone else. Visitors without a copy only read the
template, opened read-only, so nothing can change it.

Copies idle for DEMO_SANDBOX_TTL_DAYS are deleted, and at most
DEMO_SANDBOX_MAX are kept (the least recently used go first). One address may
start at most NEW_PER_HOUR copies an hour, so a script hammering the login
form cannot push real testers' copies out. A client's real install never sets
DEMO_SANDBOXES, and then this middleware does nothing.

Starting over (DEMO-FAST) swaps the visitor onto a copy of a second template,
the demo already emptied back to choosing the activity (``prepare_demo_fresh``
builds it once). Copying a file takes a moment; emptying a database on the
showcase's small server took minutes and timed out.
"""

import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from django.conf import settings
from django.db import connections
from django.http import HttpResponse

COOKIE = "hesba_sandbox"
SALT = "hesba.demo-sandbox"
_ID = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
_SAFE = {"GET", "HEAD", "OPTIONS"}
# A note can be sent without a copy of one's own; it is stored elsewhere.
_NO_COPY_NEEDED = ("/demo/feedback/",)
NEW_PER_HOUR = 20
RESTART_PATH = "/demo/restart/"


def enabled():
    return bool(
        getattr(settings, "DEMO_MODE", False)
        and getattr(settings, "DEMO_SANDBOXES", False)
        and settings.DATABASES["default"]["ENGINE"].endswith("sqlite3")
    )


class Sandboxes:
    """Where the copies live and how they are made, kept and pruned."""

    def __init__(self, template, root=None, ttl_days=10, keep=200):
        self.template = Path(template)
        self.root = Path(root) if root else self.template.parent / "hesba_sandboxes"
        self.ttl = max(ttl_days, 1) * 86400
        self.keep = max(keep, 1)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, sandbox_id):
        return self.root / f"{sandbox_id}.sqlite3"

    def exists(self, sandbox_id):
        return bool(sandbox_id) and _ID.match(sandbox_id) is not None and self.path(sandbox_id).is_file()

    def template_read_only(self):
        return f"file:{self.template}?mode=ro"

    @property
    def fresh(self):
        return fresh_template(self.template)

    def has_fresh(self):
        return self.fresh.is_file()

    def create(self, fresh=False):
        """A new copy of the template (or of the emptied one, to start over)."""

        self.prune()
        sandbox_id = secrets.token_urlsafe(24)
        copy_database(self.fresh if fresh else self.template, self.path(sandbox_id))
        return sandbox_id

    def remove(self, sandbox_id):
        if self.exists(sandbox_id):
            self.path(sandbox_id).unlink(missing_ok=True)
            Path(f"{self.path(sandbox_id)}-journal").unlink(missing_ok=True)

    def prune(self, now=None):
        now = now or time.time()
        copies = []
        for entry in self.root.iterdir():
            if entry.suffix == ".partial" and now - entry.stat().st_mtime > 3600:
                entry.unlink(missing_ok=True)
            elif entry.suffix == ".sqlite3":
                copies.append((entry.stat().st_mtime, entry))
        copies.sort()
        stale = [entry for mtime, entry in copies if now - mtime > self.ttl]
        fresh = [entry for mtime, entry in copies if now - mtime <= self.ttl]
        # Room for the copy about to be made.
        stale += fresh[: max(len(fresh) - (self.keep - 1), 0)]
        for entry in stale:
            entry.unlink(missing_ok=True)
            Path(f"{entry}-journal").unlink(missing_ok=True)
        return len(stale)


def fresh_template(template):
    """Where the emptied demo lives, next to the template."""

    template = Path(template)
    return template.with_name(f"{template.stem}.fresh{template.suffix}")


def copy_database(source, target):
    """Copy a SQLite database consistently (backup API), appearing all at once."""

    target = Path(target)
    partial = target.with_suffix(".partial")
    reader = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        writer = sqlite3.connect(partial)
        try:
            reader.backup(writer)
        finally:
            writer.close()
    finally:
        reader.close()
    os.replace(partial, target)


class _NewCopyLimit:
    """At most ``limit`` new copies an hour per address (per process: a soft guard)."""

    def __init__(self, limit=NEW_PER_HOUR, window=3600):
        self.limit, self.window = limit, window
        self.seen = defaultdict(deque)
        self.lock = threading.Lock()

    def allow(self, address, now=None):
        now = now or time.time()
        with self.lock:
            stamps = self.seen[address]
            while stamps and now - stamps[0] > self.window:
                stamps.popleft()
            if len(stamps) >= self.limit:
                return False
            stamps.append(now)
            if len(self.seen) > 5000:  # forget idle addresses
                for key in [key for key, value in self.seen.items() if not value]:
                    del self.seen[key]
            return True


def client_address(request):
    """The visitor's address; behind Render's proxy the last X-Forwarded-For hop is the real one."""

    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR", "")


TOO_MANY = (
    "<!doctype html><meta charset=utf-8><title>Hesba</title>"
    "<p dir=rtl>جرّبت كتير الساعة دي. استنى شوية وجرّب تاني، أو كمّل في تجربتك الحالية.</p>"
    "<p dir=ltr>Too many fresh starts from here this hour. Please wait a little, or carry on in your current demo.</p>"
)


def _use(name):
    """Point this thread's default connection at ``name`` (its own copy of the settings)."""

    connection = connections["default"]
    if connection.settings_dict["NAME"] != name:
        connection.close()
        connection.settings_dict = {**connection.settings_dict, "NAME": name}


class DemoSandboxMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.sandboxes = None
        self.new_copies = _NewCopyLimit()
        if enabled():
            self.sandboxes = Sandboxes(
                settings.DATABASES["default"]["NAME"],
                getattr(settings, "DEMO_SANDBOX_DIR", "") or None,
                getattr(settings, "DEMO_SANDBOX_TTL_DAYS", 10),
                getattr(settings, "DEMO_SANDBOX_MAX", 200),
            )

    def __call__(self, request):
        if self.sandboxes is None:
            return self.get_response(request)
        sandbox_id = request.get_signed_cookie(COOKIE, default=None, salt=SALT)
        if not self.sandboxes.exists(sandbox_id):
            sandbox_id = None
        created = None
        if request.method == "POST" and request.path == RESTART_PATH and self.sandboxes.has_fresh():
            # Starting over: a copy of the emptied demo replaces theirs.
            if sandbox_id is None and not self.new_copies.allow(client_address(request)):
                _use(self.sandboxes.template_read_only())
                return HttpResponse(TOO_MANY, status=429)
            if sandbox_id is not None:
                _use(self.sandboxes.template_read_only())
                self.sandboxes.remove(sandbox_id)
            sandbox_id = created = self.sandboxes.create(fresh=True)
            request.demo_fresh_copy = True
        elif sandbox_id is None and request.method not in _SAFE and not request.path.startswith(_NO_COPY_NEEDED):
            # Signing in or starting over: this visitor needs their own copy.
            if not self.new_copies.allow(client_address(request)):
                _use(self.sandboxes.template_read_only())
                return HttpResponse(TOO_MANY, status=429)
            sandbox_id = created = self.sandboxes.create()
        request.demo_sandbox = sandbox_id
        if sandbox_id:
            os.utime(self.sandboxes.path(sandbox_id))
            _use(str(self.sandboxes.path(sandbox_id)))
        else:
            _use(self.sandboxes.template_read_only())
        response = self.get_response(request)
        if created:
            response.set_signed_cookie(
                COOKIE, created, salt=SALT, max_age=self.sandboxes.ttl,
                httponly=True, samesite="Lax", secure=not settings.DEBUG,
            )
        return response
