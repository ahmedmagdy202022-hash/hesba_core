"""OPS-001: health check, nightly backup with rotation, backup verification."""

import gzip
import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_customer
from master_data.models import Customer


class HealthzTests(TestCase):
    def test_anonymous_gets_a_bare_ok(self):
        response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-cache", response["Cache-Control"])


class BackupTests(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_customer(customer_code="BK-1", name="Backup Customer")

    def backup(self, **options):
        call_command("backup_data", dir=str(self.dir), stdout=StringIO(), **options)
        return sorted(self.dir.glob("hesba-*.json.gz"))

    def test_backup_holds_the_business_rows(self):
        fixture = self.backup()[-1]
        with gzip.open(fixture, "rt", encoding="utf-8") as handle:
            rows = json.load(handle)
        models = {row["model"] for row in rows}
        self.assertIn("master_data.customer", models)
        self.assertNotIn("sessions.session", models)
        self.assertNotIn("contenttypes.contenttype", models)
        self.assertTrue(any(row["model"] == "master_data.customer" and row["fields"]["name"] == "Backup Customer" for row in rows))

    def test_rotation_keeps_the_newest(self):
        for stamp in ("20200101-000000", "20200102-000000", "20200103-000000"):
            (self.dir / f"hesba-{stamp}.json.gz").write_bytes(b"old")
        files = self.backup(keep=2)
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0].name, "hesba-20200103-000000.json.gz")

    def test_verify_passes_on_a_fresh_backup(self):
        out = StringIO()
        call_command("verify_backup", str(self.backup()[-1]), stdout=out)
        self.assertIn("Backup OK.", out.getvalue())

    def test_verify_fails_when_rows_are_missing(self):
        fixture = self.backup()[-1]
        make_customer(customer_code="BK-2", name="Added after the backup")
        with self.assertRaisesMessage(CommandError, "master_data.customer"):
            call_command("verify_backup", str(fixture), stdout=StringIO())
        self.assertEqual(Customer.objects.count(), 2)

    def test_verify_fails_on_an_unreadable_file(self):
        broken = self.dir / "broken.json.gz"
        broken.write_bytes(b"not gzip")
        with self.assertRaisesMessage(CommandError, "not readable"):
            call_command("verify_backup", str(broken), stdout=StringIO())
