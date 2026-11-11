"""Watch round 1 fixes on the reports screens (SH-05 title, SH-04, MT-06, CL-05, SH-03 note)."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .tests_dashboard import prepared_client


def owner_client(test, username="watch1_owner"):
    prepared_client()
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
    test.client.force_login(user)
    return user


class DailySummaryTitleTests(TestCase):
    def setUp(self):
        owner_client(self)

    def test_the_title_writes_the_day_the_way_the_app_does_and_never_breaks_it(self):
        ar = self.client.get(reverse("reports:daily"), {"date": "2026-11-10"}).content.decode()
        self.assertIn("<bdi>10 نوفمبر 2026</bdi>", ar)
        self.assertNotIn("2026-11-10", ar.split("</h1>")[0])
        en = self.client.get(reverse("reports:daily"), {"date": "2026-11-10", "lang": "en"}).content.decode()
        self.assertIn("<bdi>10 November 2026</bdi>", en)

    def test_a_title_date_stays_in_one_piece_in_the_stylesheet(self):
        from reports.tests_ux_wave3 import read_static

        self.assertIn(".hs-report-title bdi{white-space:nowrap}", read_static("hesba/css/report_tools.css"))
