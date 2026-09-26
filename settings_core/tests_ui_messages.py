"""I18N-001: business-service errors reach Arabic screens in Arabic."""

import pathlib
import re
from datetime import date

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from closing.models import Period, PeriodStatus
from hesba_testing.factories import DEFAULT_DATE, make_seeded_role, make_user, make_user_profile, posted_invoice_ready
from permissions.models import RoleCode

from .ui_messages import EXACT, translate


SERVICE_APPS = ("sales", "purchases", "cashboxes", "inventory", "closing", "expenses", "master_data")
LITERAL = re.compile(r'ValidationError\(\s*"([^"]{8,})"')


class TranslationTests(SimpleTestCase):
    def test_every_literal_service_message_has_an_arabic_version(self):
        # A new message added to a service without a translation fails here.
        root = pathlib.Path(settings.BASE_DIR)
        missing = set()
        for app in SERVICE_APPS:
            for name in ("services.py", "models.py", "forms.py"):
                path = root / app / name
                if path.exists():
                    for message in LITERAL.findall(path.read_text(encoding="utf-8")):
                        if message not in EXACT and not re.search(r"[\u0600-\u06FF]", message):
                            missing.add(f"{app}/{name}: {message}")
        self.assertEqual(sorted(missing), [])

    def test_exact_patterns_and_passthrough(self):
        self.assertEqual(translate("Period must be open for posting."), EXACT["Period must be open for posting."])
        self.assertEqual(
            translate("Not enough stock for item ITEM-1 - Shirt. Available: 2.000, required: 5.000."),
            "الكمية مش كفاية للصنف ITEM-1 - Shirt: المتاح 2.000 والمطلوب 5.000.",
        )
        self.assertEqual(translate("A; B unknown"), "A؛ B unknown")
        self.assertEqual(translate("Period must be open for posting.", "en"), "Period must be open for posting.")
        self.assertEqual(translate("تم الحفظ."), "تم الحفظ.")


class RenderedMessageTests(TestCase):
    def test_posting_into_a_closed_month_explains_itself_in_arabic(self):
        Period.objects.create(period_code="C", name="c", start_date=DEFAULT_DATE.replace(day=1), end_date=DEFAULT_DATE.replace(day=28), status=PeriodStatus.CLOSED, closed_at=timezone.now())
        invoice, *_ = posted_invoice_ready()
        user = make_user(username="msg_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)
        page = self.client.post(reverse("sales:post", args=[invoice.pk]), {"lang": "ar"}, follow=True)
        self.assertContains(page, "تاريخ الحركة جوّه فترة مقفولة")
        self.assertNotContains(page, "Period must be open for posting.")
        english = self.client.post(reverse("sales:post", args=[invoice.pk]), {"lang": "en"}, follow=True)
        self.assertContains(english, "Period must be open for posting.")
