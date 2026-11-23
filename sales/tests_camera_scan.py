"""CAM-001: the camera button ships on every scan screen and works offline."""

from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from reports.tests_dashboard import prepared_client
from hesba_testing.factories import make_location, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

STATIC = Path(settings.BASE_DIR) / "static" / "hesba"


class CameraScanTests(TestCase):
    def setUp(self):
        prepared_client()
        user = make_user(username="cam_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        make_location(location_code="MAIN", is_default=True)
        self.client.force_login(user)

    def test_every_scan_screen_loads_the_camera_scanner_with_the_local_reader(self):
        for name in ("sales:pos", "sales:create", "purchases:create"):
            with self.subTest(page=name):
                page = self.client.get(reverse(name)).content.decode()
                self.assertIn("hesba/js/camera_scan.js", page)
                self.assertIn('data-zxing="/static/hesba/vendor/zxing/zxing-0.23.0.min.js"', page)  # a local file, not a CDN

    def test_the_reader_is_vendored_with_its_license_and_no_missing_source_map(self):
        library = STATIC / "vendor" / "zxing" / "zxing-0.23.0.min.js"
        self.assertTrue(library.exists())
        self.assertNotIn("sourceMappingURL", library.read_text(encoding="utf-8"))  # would break collectstatic's manifest
        self.assertIn("Apache License", (library.parent / "LICENSE").read_text(encoding="utf-8"))

    def test_the_camera_only_feeds_the_screen_s_own_scan_box(self):
        script = (STATIC / "js" / "camera_scan.js").read_text(encoding="utf-8")
        self.assertIn("window.isSecureContext", script)  # no button where the browser would refuse the camera
        self.assertIn("new KeyboardEvent('keydown', { key: 'Enter'", script)  # same path as a hardware scanner
        self.assertIn("t.stop()", script)  # the camera is released on close
        self.assertNotIn("fetch(", script)  # it never talks to the server itself
