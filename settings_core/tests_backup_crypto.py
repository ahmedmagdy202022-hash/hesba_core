"""BACKUP-002: backups encrypted to the owner's key; only the owner's private key opens them."""

import gzip
import json
import shutil
import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from . import backup_crypto as bc
from .models import SystemSetting


PASSWORD = "service-tests-only"


class CryptoTests(TestCase):
    def test_round_trip_and_every_way_it_must_fail(self):
        public_text, private_text = bc.new_key_pair()
        public, private = bc.load_public(public_text), bc.load_private(private_text)
        blob = bc.encrypt(b"secret ledger", public)
        self.assertTrue(blob.startswith(bc.MAGIC))
        self.assertNotIn(b"secret ledger", blob)
        self.assertEqual(bc.decrypt(blob, private), b"secret ledger")
        self.assertNotEqual(blob, bc.encrypt(b"secret ledger", public))  # fresh ephemeral key and nonce each time
        _, other_private = bc.new_key_pair()
        with self.assertRaisesMessage(bc.BackupKeyError, "different key"):
            bc.decrypt(blob, bc.load_private(other_private))
        tampered = bytearray(blob)
        tampered[-1] ^= 1
        with self.assertRaisesMessage(bc.BackupKeyError, "changed or damaged"):
            bc.decrypt(bytes(tampered), private)
        header_tampered = bytearray(blob)
        header_tampered[len(bc.MAGIC) + 8] ^= 1  # the ephemeral key is authenticated too
        with self.assertRaises(bc.BackupKeyError):
            bc.decrypt(bytes(header_tampered), private)
        for bad in ("", "hesba-key-1:", public_text):
            with self.subTest(bad=bad), self.assertRaises(bc.BackupKeyError):
                bc.load_private(bad)
        with self.assertRaises(bc.BackupKeyError):
            bc.decrypt(b"not a backup", private)


class EncryptedBackupCommandTests(TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        make_customer(customer_code="ENC-1", name="Encrypted Customer")
        public_text, self.private_text = bc.new_key_pair()
        SystemSetting.objects.create(key=bc.SETTING_KEY, value=public_text)
        self.key_file = self.dir / "key.txt"
        self.key_file.write_text(self.private_text + "\n", encoding="utf-8")

    def test_backup_is_only_the_encrypted_file_and_the_owner_can_restore_it(self):
        call_command("backup_data", dir=str(self.dir), stdout=StringIO())
        encrypted = sorted(self.dir.glob("hesba-*.hesba-backup"))
        self.assertEqual(len(encrypted), 1)
        self.assertEqual(list(self.dir.glob("hesba-*.json.gz")) + list(self.dir.glob("hesba-*.sqlite3")), [])  # nothing readable left behind
        self.assertNotIn(b"Encrypted Customer", encrypted[0].read_bytes())
        out = StringIO()
        call_command("verify_backup", str(encrypted[0]), key_file=str(self.key_file), stdout=out)
        self.assertIn("Backup OK.", out.getvalue())
        call_command("decrypt_backup", str(encrypted[0]), key_file=str(self.key_file), stdout=StringIO())
        fixture = encrypted[0].with_name(encrypted[0].name.replace(".hesba-backup", ".json.gz"))
        with gzip.open(fixture, "rt", encoding="utf-8") as handle:
            rows = json.load(handle)
        self.assertTrue(any(row["model"] == "master_data.customer" and row["fields"]["name"] == "Encrypted Customer" for row in rows))

    def test_the_wrong_key_or_no_key_cannot_open_it(self):
        call_command("backup_data", dir=str(self.dir), stdout=StringIO())
        encrypted = sorted(self.dir.glob("hesba-*.hesba-backup"))[0]
        wrong = self.dir / "wrong.txt"
        wrong.write_text(bc.new_key_pair()[1], encoding="utf-8")
        with self.assertRaisesMessage(CommandError, "different key"):
            call_command("decrypt_backup", str(encrypted), key_file=str(wrong), stdout=StringIO())
        with self.assertRaises(CommandError):
            call_command("verify_backup", str(encrypted), stdout=StringIO())  # not a plain gzip without the key

    def test_rotation_covers_encrypted_files(self):
        for stamp in ("20200101-000000", "20200102-000000"):
            (self.dir / f"hesba-{stamp}.hesba-backup").write_bytes(b"old")
        call_command("backup_data", dir=str(self.dir), keep=2, stdout=StringIO())
        names = sorted(path.name for path in self.dir.glob("hesba-*.hesba-backup"))
        self.assertEqual(len(names), 2)
        self.assertNotIn("hesba-20200101-000000.hesba-backup", names)


class BackupKeyScreenTests(TestCase):
    def setUp(self):
        self.owner = make_user(username="bk_owner")
        make_user_profile(user=self.owner, role=make_seeded_role(RoleCode.OWNER))
        self.url = reverse("settings_core:backup_key")

    def test_owner_creates_the_key_sees_it_once_and_only_the_public_half_is_stored(self):
        self.client.force_login(self.owner)
        page = self.client.get(self.url)
        self.assertContains(page, 'data-backup-encrypted="no"')
        self.assertContains(self.client.post(self.url, {"password": "wrong"}), "data-backup-error")
        self.assertFalse(SystemSetting.objects.filter(key=bc.SETTING_KEY).exists())
        created = self.client.post(self.url, {"password": PASSWORD})
        private_text = created.context["private_text"]
        self.assertTrue(private_text.startswith(bc.PRIVATE_PREFIX))
        self.assertContains(created, "data-private-key-once")
        self.assertIn("no-cache", created["Cache-Control"])
        stored = SystemSetting.objects.get(key=bc.SETTING_KEY).value
        self.assertTrue(stored.startswith(bc.PUBLIC_PREFIX))
        self.assertEqual(bc.fingerprint(bc.load_private(private_text).public_key()), bc.fingerprint(bc.load_public(stored)))
        # The private key is nowhere in the database: not in settings, not in the audit trail.
        self.assertFalse(SystemSetting.objects.filter(value__contains=private_text[len(bc.PRIVATE_PREFIX):]).exists())
        log = AuditLog.objects.get(action="create_backup_key")
        self.assertNotIn(private_text[len(bc.PRIVATE_PREFIX):], json.dumps(log.after_data) + json.dumps(log.before_data))
        again = self.client.get(self.url)
        self.assertNotContains(again, "data-private-key-once")
        self.assertContains(again, 'data-backup-encrypted="yes"')
        self.assertContains(again, "data-key-fingerprint")

    def test_only_settings_managers_can_create_a_key(self):
        cashier = make_user(username="bk_cashier")
        make_user_profile(user=cashier, role=make_seeded_role(RoleCode.CASHIER))
        self.client.force_login(cashier)
        self.assertIn(self.client.post(self.url, {"password": PASSWORD}).status_code, (302, 403))
        self.assertFalse(SystemSetting.objects.filter(key=bc.SETTING_KEY).exists())
