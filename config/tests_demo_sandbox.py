"""DEMO-SANDBOX: every tester of the public demo works in a private copy."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings

from .demo_sandbox import COOKIE, Sandboxes, _NewCopyLimit, client_address, enabled


def make_template(folder):
    template = Path(folder) / "template.sqlite3"
    with sqlite3.connect(template) as db:
        db.execute("create table note (text)")
        db.execute("insert into note values ('from the template')")
    return template


def rows(path):
    with sqlite3.connect(path) as db:
        return [text for (text,) in db.execute("select text from note order by rowid")]


class SandboxesTests(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.template = make_template(folder.name)
        self.boxes = Sandboxes(self.template, Path(folder.name) / "boxes", ttl_days=10, keep=3)

    def test_a_copy_has_the_template_and_writing_to_it_stays_in_it(self):
        sandbox = self.boxes.create()
        self.assertTrue(self.boxes.exists(sandbox))
        with sqlite3.connect(self.boxes.path(sandbox)) as db:
            db.execute("insert into note values ('mine')")
        self.assertEqual(rows(self.boxes.path(sandbox)), ["from the template", "mine"])
        self.assertEqual(rows(self.template), ["from the template"])
        self.assertNotEqual(self.boxes.create(), sandbox)

    def test_the_template_is_opened_read_only(self):
        db = sqlite3.connect(self.boxes.template_read_only(), uri=True)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("select count(*) from note").fetchone(), (1,))
        with self.assertRaises(sqlite3.OperationalError):
            db.execute("insert into note values ('nope')")

    def test_only_well_formed_existing_ids_count(self):
        sandbox = self.boxes.create()
        for bad in (None, "", "short", "../template", "../" + sandbox, sandbox + "/x", "a" * 65, "x" * 30):
            self.assertFalse(self.boxes.exists(bad), bad)

    def test_idle_copies_are_removed(self):
        old, recent = self.boxes.create(), self.boxes.create()
        long_ago = time.time() - 11 * 86400
        os.utime(self.boxes.path(old), (long_ago, long_ago))
        Path(f"{self.boxes.path(old)}-journal").write_text("")
        self.assertEqual(self.boxes.prune(), 1)
        self.assertFalse(self.boxes.exists(old))
        self.assertFalse(Path(f"{self.boxes.path(old)}-journal").exists())
        self.assertTrue(self.boxes.exists(recent))

    def test_at_most_keep_copies_the_least_recently_used_go_first(self):
        made = []
        for age in (50, 40, 30, 20, 10):
            sandbox = self.boxes.create()
            stamp = time.time() - age
            os.utime(self.boxes.path(sandbox), (stamp, stamp))
            made.append(sandbox)
        kept = [sandbox for sandbox in made if self.boxes.exists(sandbox)]
        self.assertEqual(len(kept), 3)
        newest = self.boxes.create()
        self.assertEqual(sorted(path.stem for path in self.boxes.root.glob("*.sqlite3")), sorted([made[3], made[4], newest]))

    def test_an_abandoned_half_copy_is_cleaned_up_a_fresh_one_is_not(self):
        stale, fresh = self.boxes.root / "a.partial", self.boxes.root / "b.partial"
        stale.write_text("")
        fresh.write_text("")
        two_hours_ago = time.time() - 7200
        os.utime(stale, (two_hours_ago, two_hours_ago))
        self.boxes.prune()
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())


class NewCopyLimitTests(SimpleTestCase):
    def test_one_address_is_held_back_others_are_not(self):
        limit = _NewCopyLimit(limit=2, window=3600)
        self.assertTrue(limit.allow("1.1.1.1", now=1000))
        self.assertTrue(limit.allow("1.1.1.1", now=1001))
        self.assertFalse(limit.allow("1.1.1.1", now=1002))
        self.assertTrue(limit.allow("2.2.2.2", now=1002))
        self.assertTrue(limit.allow("1.1.1.1", now=1000 + 3601))

    def test_the_address_behind_the_proxy(self):
        from django.test import RequestFactory

        request = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="9.9.9.9, 41.1.2.3", REMOTE_ADDR="10.0.0.1")
        self.assertEqual(client_address(request), "41.1.2.3")
        self.assertEqual(client_address(RequestFactory().get("/", REMOTE_ADDR="10.0.0.1")), "10.0.0.1")


class SwitchTests(TestCase):
    def test_only_a_sqlite_showcase_with_sandboxes_on(self):
        self.assertFalse(enabled())
        with override_settings(DEMO_MODE=True, DEMO_SANDBOXES=False):
            self.assertFalse(enabled())
        with override_settings(DEMO_MODE=False, DEMO_SANDBOXES=True):
            self.assertFalse(enabled())
        if settings.DATABASES["default"]["ENGINE"].endswith("sqlite3"):
            with override_settings(DEMO_MODE=True, DEMO_SANDBOXES=True):
                self.assertTrue(enabled())
        postgres = {**settings.DATABASES, "default": {**settings.DATABASES["default"], "ENGINE": "django.db.backends.postgresql"}}
        with override_settings(DEMO_MODE=True, DEMO_SANDBOXES=True, DATABASES=postgres):
            self.assertFalse(enabled())

    def test_a_real_install_never_gets_a_copy(self):
        response = self.client.post("/login/", {"username": "x", "password": "y"})
        self.assertNotIn(COOKIE, response.cookies)


# The whole thing as it runs on the demo server: a real template built by the
# start script's commands, real requests through every middleware, two testers.
DRIVER = r"""
import hashlib, json, os, sqlite3, sys
import django
django.setup()
from django.core.management import call_command
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
call_command("migrate", verbosity=0, interactive=False)
call_command("prepare_demo", verbosity=0)
call_command("migrate", database="feedback", verbosity=0, interactive=False)
template, boxes = os.environ["SQLITE_PATH"], os.environ["DEMO_SANDBOX_DIR"]
digest = lambda: hashlib.sha256(open(template, "rb").read()).hexdigest()
before = digest()
copies = lambda: sorted(name for name in os.listdir(boxes) if name.endswith(".sqlite3"))
def invoices(sandbox):
    with sqlite3.connect(os.path.join(boxes, sandbox + ".sqlite3")) as db:
        return db.execute("select count(*) from sales_salesinvoice").fetchone()[0]
out = {}
a, b, c = Client(), Client(), Client()
out["anon_login_page"] = a.get("/login/").status_code
out["anon_health"] = a.get("/healthz/").status_code
out["copies_after_browsing"] = len(copies())
login = a.post("/login/", {"username": "owner", "password": "Demo-pass-1"})
out["a_login"] = [login.status_code, "hesba_sandbox" in login.cookies]
out["a_dashboard"] = a.get("/dashboard/").status_code
restart = b.post("/demo/restart/", {"lang": "ar"})
out["b_restart"] = [restart.status_code, restart.headers.get("Location", "")]
out["b_activity"] = b.get("/setup/activity/?lang=ar").status_code
out["copies_two_testers"] = len(copies())
out["a_dashboard_after_b"] = a.get("/dashboard/", follow=False).status_code
a_id = a.cookies["hesba_sandbox"].value.split(":")[0]
b_id = b.cookies["hesba_sandbox"].value.split(":")[0]
out["invoices"] = [invoices(a_id), invoices(b_id)]
a.post("/demo/restart/", {"lang": "ar"})
out["after_a_restart"] = [invoices(a_id), b.get("/setup/activity/?lang=ar").status_code, len(copies())]
c.cookies["hesba_sandbox"] = "forged-value-that-is-not-signed"
out["forged"] = [c.get("/login/").status_code, len(copies())]
note = c.post("/demo/feedback/send/", {"message": "الزرار مش واضح", "lang": "ar", "path": "/login/"})
out["anon_note"] = [note.status_code, len(copies())]
note = b.post("/demo/feedback/send/", {"message": "اختيار النشاط سهل", "lang": "ar", "mood": "great"})
out["b_note"] = note.status_code
from feedback.models import Feedback
out["notes"] = sorted((n.sandbox == b_id, n.message) for n in Feedback.objects.all())
out["template_unchanged"] = digest() == before
print("RESULT" + json.dumps(out, ensure_ascii=False))
"""


class EndToEndTests(SimpleTestCase):
    def test_two_testers_never_touch_each_other_or_the_template(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {key: value for key, value in os.environ.items() if not key.startswith(("DATABASE_", "POSTGRES_", "FEEDBACK_"))}
            env.update({
                "DJANGO_SETTINGS_MODULE": "config.settings",
                "DATABASE_BACKEND": "sqlite",
                "DEMO_MODE": "True",
                "DEMO_SANDBOXES": "True",
                "DEBUG": "True",
                "SQLITE_PATH": str(Path(folder) / "demo.sqlite3"),
                "DEMO_SANDBOX_DIR": str(Path(folder) / "boxes"),
                "FEEDBACK_SQLITE_PATH": str(Path(folder) / "feedback.sqlite3"),
            })
            done = subprocess.run([sys.executable, "-c", DRIVER], cwd=settings.BASE_DIR, env=env,
                                  capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        out = json.loads(done.stdout.rsplit("RESULT", 1)[1])
        # Just looking around makes no copy, and the read-only template serves it.
        self.assertEqual((out["anon_login_page"], out["anon_health"], out["copies_after_browsing"]), (200, 200, 0))
        self.assertEqual(out["a_login"], [302, True])
        self.assertEqual(out["a_dashboard"], 200)
        # B starts over in a copy of their own; A's shop and sign-in are untouched.
        self.assertEqual(out["b_restart"], [302, "/setup/activity/?lang=ar"])
        self.assertEqual(out["b_activity"], 200)
        self.assertEqual(out["copies_two_testers"], 2)
        self.assertEqual(out["a_dashboard_after_b"], 200)
        a_invoices, b_invoices = out["invoices"]
        self.assertGreater(a_invoices, 0)
        self.assertEqual(b_invoices, 0)
        # A starting over empties only A's copy; B carries on.
        self.assertEqual(out["after_a_restart"], [0, 200, 2])
        # A forged cookie is ignored, and a note needs no copy of its own.
        self.assertEqual(out["forged"], [200, 2])
        self.assertEqual(out["anon_note"], [200, 2])
        self.assertEqual(out["b_note"], 200)
        self.assertEqual(out["notes"], [[False, "الزرار مش واضح"], [True, "اختيار النشاط سهل"]])
        self.assertTrue(out["template_unchanged"])
