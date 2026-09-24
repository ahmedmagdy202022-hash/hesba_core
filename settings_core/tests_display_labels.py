import pathlib
import re

from django.apps import apps
from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from cashboxes.models import CashboxDirection
from hesba_testing.factories import (
    add_sales_line,
    make_cashbox,
    make_cashbox_movement,
    make_draft_sales_invoice,
    make_item,
    make_location,
    make_seeded_role,
    make_user,
    make_user_profile,
    stock_in,
)
from inventory.models import StockMovement
from permissions.models import RoleCode

from .display_labels import AR_LABELS, choice_label, localized_choices


class DisplayLabelCoverageTests(SimpleTestCase):
    def test_every_mapped_field_translates_every_choice(self):
        # A choice added to a model without an Arabic label here would leak its
        # English label onto Arabic screens; this fails first.
        for (model_label, field_name), labels in AR_LABELS.items():
            with self.subTest(model=model_label, field=field_name):
                field = apps.get_model(model_label)._meta.get_field(field_name)
                values = {value for value, _ in field.choices}
                self.assertEqual(values - set(labels), set())

    def test_no_template_renders_raw_choice_labels(self):
        root = pathlib.Path(settings.BASE_DIR) / "templates"
        offenders = [
            str(path.relative_to(root))
            for path in root.rglob("*.html")
            if re.search(r"\.get_[a-z_]+_display", path.read_text())
        ]
        self.assertEqual(offenders, [], "use {% choice_label obj \"field\" %} instead")

    def test_english_keeps_the_model_label_and_arabic_falls_back_to_it(self):
        movement = StockMovement(movement_type="sale_out")
        self.assertEqual(choice_label(movement, "movement_type", "en"), "Sale out")
        self.assertEqual(choice_label(movement, "movement_type", "ar"), "صادر مبيعات")
        self.assertEqual(
            localized_choices(StockMovement, "movement_type", "en"),
            list(StockMovement._meta.get_field("movement_type").choices),
        )


class ArabicScreensTests(TestCase):
    def login_as(self, role_code=RoleCode.OWNER, username="labels_owner"):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_seeded_role(role_code))
        self.client.force_login(user)

    def assertNoEnglish(self, response, *labels):
        self.assertEqual(response.status_code, 200)
        for label in labels:
            self.assertNotContains(response, label)

    def test_stock_movements_list_and_filter_are_arabic(self):
        self.login_as()
        stock_in(make_item(), make_location(), 3, "2.00")
        response = self.client.get(reverse("inventory:movements"))
        self.assertContains(response, "وارد مشتريات")
        self.assertContains(response, "صادر مبيعات")  # filter option
        self.assertNoEnglish(response, "Purchase in", "Sale out", "Opening stock")

    def test_stock_movements_stay_english_in_english(self):
        self.login_as()
        stock_in(make_item(), make_location(), 3, "2.00")
        response = self.client.get(reverse("inventory:movements"), {"lang": "en"})
        self.assertContains(response, "Purchase in")
        self.assertNotContains(response, "وارد مشتريات")

    def test_sales_list_status_and_filter_are_arabic(self):
        self.login_as()
        invoice = make_draft_sales_invoice()
        add_sales_line(invoice, make_item(), quantity=1, unit_sale_price="10.00")
        response = self.client.get(reverse("sales:list"))
        self.assertContains(response, "مسودة")
        self.assertContains(response, "كل الحالات")
        self.assertNoEnglish(response, ">Draft<", ">Posted<", ">Cancelled<", "All statuses")

        response = self.client.get(reverse("sales:list"), {"status": "posted"})
        self.assertContains(response, '<option value="posted" selected>')

    def test_sales_detail_status_and_payment_status_are_arabic(self):
        self.login_as()
        invoice = make_draft_sales_invoice()
        add_sales_line(invoice, make_item(), quantity=1, unit_sale_price="10.00")
        response = self.client.get(reverse("sales:detail", args=[invoice.pk]))
        self.assertContains(response, "مسودة")
        self.assertContains(response, "آجل")
        self.assertNoEnglish(response, ">Draft<", ">Credit<")

    def test_purchase_list_and_payment_lists_are_arabic(self):
        self.login_as()
        for url in (reverse("purchases:list"), reverse("purchases:payments"), reverse("sales:payments")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, "مُرحّل")
                self.assertNoEnglish(response, ">Posted<", ">Cancelled<", "All statuses")

    def test_cashbox_movements_are_arabic(self):
        self.login_as()
        make_cashbox_movement(make_cashbox(), CashboxDirection.IN, "25.00")
        response = self.client.get(reverse("cashboxes:movements"))
        self.assertContains(response, "إيداع مباشر")
        self.assertContains(response, "وارد")
        self.assertNoEnglish(response, ">Direct in<", ">In<", "Sales receipt")

    def test_operation_forms_offer_arabic_choices(self):
        self.login_as()
        response = self.client.get(reverse("cashboxes:operation_create"))
        self.assertContains(response, "تحويل بين الخزن")
        self.assertNoEnglish(response, "Cashbox transfer", "Direct cash in")

        response = self.client.get(reverse("inventory:adjustment"))
        self.assertContains(response, "زيادة")
        self.assertNoEnglish(response, ">Increase<", ">Decrease<")
