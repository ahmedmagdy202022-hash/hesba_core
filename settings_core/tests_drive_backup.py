"""BACKUP-003: the nightly encrypted backup goes to the client's own Google Drive (Google faked)."""

import json
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from . import backup_crypto as bc
from . import drive_backup as db
from .models import SystemSetting


GOOGLE = {"GOOGLE_OAUTH_CLIENT_ID": "client-id", "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret", "NIGHTLY_TOKEN": "night-token"}


class FakeGoogle:
    """Just enough of OAuth + Drive v3 to exercise the flow; records every upload."""

    def __init__(self, existing=0):
        self.uploads, self.deleted, self.revoked = [], [], []
        self.files = [{"id": f"old{n}", "name": f"hesba-2020010{n}-000000.hesba-backup"} for n in range(existing)]
        self.folder_created = 0

    def __call__(self, method, url, data=None, headers=None):
        if url == db.TOKEN_URL:
            form = parse_qs(data.decode())
            if form["grant_type"] == ["authorization_code"]:
                return 200, json.dumps({"refresh_token": "refresh-1", "access_token": "a"}).encode()
            return 200, json.dumps({"access_token": "access-1"}).encode()
        if url == db.REVOKE_URL:
            self.revoked.append(parse_qs(data.decode())["token"][0])
            return 200, b""
        assert headers.get("Authorization") == "Bearer access-1", headers
        if url.startswith(db.UPLOAD_URL.split("?")[0]):
            self.uploads.append(data)
            name = json.loads(data.split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0])["name"]
            self.files.insert(0, {"id": f"new{len(self.uploads)}", "name": name})
            return 200, json.dumps({"id": "new", "name": name, "size": str(len(data))}).encode()
        if method == "POST" and url.startswith(db.FILES_URL):
            self.folder_created += 1
            return 200, json.dumps({"id": "folder-1"}).encode()
        if method == "GET" and "?q=" not in url and "folder-1" in url:
            return 200, json.dumps({"id": "folder-1", "trashed": False}).encode()
        if method == "GET":
            return 200, json.dumps({"files": self.files}).encode()
        if method == "DELETE":
            self.deleted.append(url.rsplit("/", 1)[1])
            return 204, b""
        raise AssertionError((method, url))


@override_settings(**GOOGLE)
class DriveBackupTests(TestCase):
    def setUp(self):
        self.owner = make_user(username="drive_owner")
        make_user_profile(user=self.owner, role=make_seeded_role(RoleCode.OWNER))
        make_customer(customer_code="DRV-1", name="Drive Customer")
        self.url = reverse("settings_core:backup_key")

    def with_key(self):
        public_text, private_text = bc.new_key_pair()
        SystemSetting.objects.create(key=bc.SETTING_KEY, value=public_text)
        return private_text

    def connect(self, google):
        self.client.force_login(self.owner)
        with mock.patch.object(db, "_http", google):
            start = self.client.post(self.url, {"action": "drive_connect"})
            query = parse_qs(urlsplit(start["Location"]).query)
            self.assertEqual(query["scope"], [db.SCOPE])  # drive.file only, never the whole Drive
            self.assertEqual(query["access_type"], ["offline"])
            return self.client.get(reverse("settings_core:backup_drive_callback"), {"code": "c1", "state": query["state"][0]})

    def test_nothing_is_uploaded_without_the_backup_key(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url, {"action": "drive_connect"}, follow=True)
        self.assertContains(response, "اعمل مفتاح النسخ الاحتياطي الأول")
        SystemSetting.objects.create(key=db.REFRESH_KEY, value="refresh-1")
        with self.assertRaisesMessage(db.DriveError, "nothing is uploaded unencrypted"):
            db.run_nightly(force=True)

    def test_connect_upload_encrypted_prune_and_skip_the_same_day(self):
        private_text = self.with_key()
        google = FakeGoogle(existing=3)
        self.connect(google)
        self.assertEqual(SystemSetting.objects.get(key=db.REFRESH_KEY).is_sensitive, True)
        with mock.patch.object(db, "_http", google), override_settings(BACKUP_DRIVE_KEEP=2):
            report = db.run_nightly()
            self.assertEqual(report["status"], "uploaded")
            self.assertEqual(db.run_nightly()["status"], "skipped")  # once a day
        body = google.uploads[0]
        self.assertTrue(body.split(b"\r\n\r\n", 2)[2].startswith(bc.MAGIC))  # the file itself is the encrypted one
        self.assertNotIn(b"Drive Customer", body)
        encrypted = body.split(b"\r\n\r\n", 2)[2].rsplit(b"\r\n--", 1)[0]
        self.assertIn(b"Drive Customer", __import__("gzip").decompress(bc.decrypt(encrypted, bc.load_private(private_text))))
        self.assertEqual(sorted(google.deleted), ["old1", "old2"])  # newest two kept: today's and old0
        self.assertEqual(google.folder_created, 1)
        self.assertTrue(db.uploaded_today())
        self.assertTrue(AuditLog.objects.filter(action="backup_drive_uploaded").exists())

    def test_the_callback_refuses_a_foreign_state(self):
        self.with_key()
        self.client.force_login(self.owner)
        self.client.post(self.url, {"action": "drive_connect"})
        page = self.client.get(reverse("settings_core:backup_drive_callback"), {"code": "c1", "state": "forged"}, follow=True)
        self.assertContains(page, "الربط اتلغى")
        self.assertFalse(db.is_connected())

    def test_disconnect_revokes_and_forgets(self):
        self.with_key()
        google = FakeGoogle()
        self.connect(google)
        with mock.patch.object(db, "_http", google):
            self.client.post(self.url, {"action": "drive_disconnect"})
        self.assertEqual(google.revoked, ["refresh-1"])
        self.assertFalse(db.is_connected())

    def test_a_google_failure_is_recorded_and_reported(self):
        self.with_key()
        self.connect(FakeGoogle())

        def broken(method, url, data=None, headers=None):
            if url == db.TOKEN_URL:
                return 400, b'{"error":"invalid_grant"}'
            raise AssertionError(url)

        with mock.patch.object(db, "_http", broken):
            with self.assertRaises(CommandError):
                call_command("nightly_backup")
            page = self.client.post(self.url, {"action": "backup_now"}, follow=True)
        self.assertContains(page, "الرفع ماتمّش")
        self.assertIn("invalid_grant", SystemSetting.objects.get(key=db.LAST_ERROR_KEY).value)

    def test_the_nightly_endpoint_needs_the_token(self):
        self.with_key()
        google = FakeGoogle()
        self.connect(google)
        self.client.logout()
        endpoint = reverse("ops_nightly")
        self.assertEqual(self.client.post(endpoint).status_code, 404)
        self.assertEqual(self.client.post(endpoint, HTTP_X_HESBA_TOKEN="wrong").status_code, 404)
        with mock.patch.object(db, "_http", google):
            ok = self.client.post(endpoint, HTTP_X_HESBA_TOKEN="night-token")
        self.assertEqual(ok.json(), {"status": "uploaded"})
        with override_settings(NIGHTLY_TOKEN=""):
            self.assertEqual(self.client.post(endpoint, HTTP_X_HESBA_TOKEN="").status_code, 404)

    def test_only_settings_managers_can_connect_or_upload(self):
        self.with_key()
        cashier = make_user(username="drive_cashier")
        make_user_profile(user=cashier, role=make_seeded_role(RoleCode.CASHIER))
        self.client.force_login(cashier)
        for action in ("drive_connect", "backup_now", "drive_disconnect"):
            self.assertIn(self.client.post(self.url, {"action": action}).status_code, (302, 403))
        self.assertIn(self.client.get(reverse("settings_core:backup_drive_callback"), {"code": "x", "state": "y"}).status_code, (302, 403))
        self.assertFalse(db.is_connected())
