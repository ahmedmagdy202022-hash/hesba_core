"""The real Hesba mark is what browsers, phones and the dashboard show."""

import json
import pathlib

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode


BRAND_FILES = (
    "favicon.ico", "favicon-16.png", "favicon-32.png", "apple-touch-icon.png", "icon-192.png", "icon-512.png",
    "hesba-logo.png", "hesba-logo-reversed.png", "hesba-mark.png",
)


def png_size(path):
    # Width and height sit in the IHDR chunk right after the 8-byte signature.
    with open(path, "rb") as handle:
        header = handle.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n", path
    return f"{int.from_bytes(header[16:20], 'big')}x{int.from_bytes(header[20:24], 'big')}"


class BrandAssetTests(TestCase):
    def test_brand_files_ship(self):
        for name in BRAND_FILES:
            with self.subTest(file=name):
                self.assertIsNotNone(finders.find(f"hesba/brand/{name}"))

    def test_placeholder_icon_is_gone_everywhere(self):
        # The old navy square with two dots was never the logo.
        root = pathlib.Path(settings.BASE_DIR)
        offenders = [
            str(path.relative_to(root))
            for folder in ("templates", "static")
            for path in (root / folder).rglob("*")
            if path.suffix in {".html", ".css", ".json"} and "hesba-icon.svg" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])

    def test_manifest_icons_exist(self):
        manifest = json.loads((pathlib.Path(settings.BASE_DIR) / "static/hesba/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["start_url"], "/login/")
        for icon in manifest["icons"]:
            with self.subTest(icon=icon["src"]):
                path = finders.find(icon["src"].removeprefix("/static/"))
                self.assertIsNotNone(path)
                self.assertEqual(png_size(path), icon["sizes"])

    def test_manifest_meets_the_install_icon_sizes(self):
        # Chromium only offers "Install app" with both a 192px and a 512px icon.
        manifest = json.loads((pathlib.Path(settings.BASE_DIR) / "static/hesba/manifest.json").read_text(encoding="utf-8"))
        sizes = {icon["sizes"] for icon in manifest["icons"]}
        self.assertTrue({"192x192", "512x512"} <= sizes)

    def test_login_and_signed_in_pages_carry_the_favicon(self):
        self.assertContains(self.client.get(reverse("login")), "hesba/brand/favicon.ico")
        user = make_user(username="brand_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)
        for name in ("dashboard_snapshot", "sales:list", "setup_gate"):
            with self.subTest(route=name):
                self.assertContains(self.client.get(reverse(name)), "hesba/brand/favicon.ico")

    def test_dashboard_shows_the_real_mark(self):
        user = make_user(username="brand_owner2")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)
        self.assertContains(self.client.get(reverse("dashboard_snapshot")), "hesba/brand/hesba-mark.png")
