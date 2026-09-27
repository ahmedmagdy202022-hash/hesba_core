"""EINV-001: the ETA v1.0 invoice document built from a posted sales invoice."""

import json
from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from closing.models import Period
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from printing.company import save_company_details
from reports.tests_dashboard import prepared_client
from sales.services import post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile
from taxes.models import ItemTaxRate, TaxRate
from taxes.services import create_sales_draft_with_tax

from .models import ItemCode, ReceiverProfile
from .services import build_document, document_json, save_issuer_settings


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class DocumentSetup(TestCase):
    def setUp(self):
        profile = prepared_client(sub_activity="wholesale")  # vat and e_invoice suggested
        set_capability_enabled(profile, "e_invoice", True)
        Period.objects.create(period_code="EI", name="ei", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "ei_owner")
        self.location = make_location()
        self.cashbox = make_cashbox(cashbox_code="EI-CASH")
        self.a = make_item(item_code="A", item_name="Taxed item", default_sale_price=D("100.00"))
        self.b = make_item(item_code="B", item_name="Exempt item", default_sale_price=D("50.00"))
        ItemTaxRate.objects.create(item=self.b, tax_rate=TaxRate.objects.get(code="EXEMPT"))
        for item in (self.a, self.b):
            stock_in(item, self.location, 20, "10.00", movement_date=TODAY - timedelta(days=5))
            recalculate_item_average_cost(item)
        self.customer = make_customer(customer_code="BUY", name="Buyer Co")

    def complete_data(self):
        save_company_details({"company.tax_number": "100-200-300"}, self.owner)
        save_issuer_settings({"einvoice.activity_code": "4711", "einvoice.branch_id": "0", "einvoice.governate": "Cairo", "einvoice.region_city": "Nasr City",
                              "einvoice.street": "Abbas El Akkad", "einvoice.building_number": "12", "einvoice.person_id_threshold": "50000"}, self.owner)
        ItemCode.objects.create(item=self.a, code_type="EGS", code="EG-100200300-A1", unit_type="EA")
        ItemCode.objects.create(item=self.b, code_type="GS1", code="6221234567890", unit_type="BOX")
        ReceiverProfile.objects.create(customer=self.customer, receiver_type="B", tax_id="987654321", governate="Giza", region_city="Dokki", street="Tahrir", building_number="5")

    def invoice(self, discount="10.00"):
        invoice = create_sales_draft_with_tax(
            {"invoice_number": "EI-1", "invoice_date": TODAY, "customer": self.customer, "selling_location": self.location, "cashbox": self.cashbox, "paid_now": D("0"), "discount_amount": D(discount)},
            [{"item": self.a, "quantity": D("2"), "unit_sale_price": D("100.00"), "line_discount_amount": D("5.00")}, {"item": self.b, "quantity": D("1"), "unit_sale_price": D("50.00")}],
            self.owner,
        )
        post_sales_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        return invoice


class DocumentTests(DocumentSetup):
    def test_a_complete_invoice_maps_to_the_eta_structure(self):
        self.complete_data()
        invoice = self.invoice()
        document, problems = build_document(invoice)
        self.assertEqual(problems, [])
        self.assertEqual((document["documentType"], document["documentTypeVersion"], document["internalID"], document["taxpayerActivityCode"]), ("i", "1.0", "EI-1", "4711"))
        self.assertEqual(document["issuer"]["id"], "100200300")
        self.assertEqual(document["issuer"]["address"]["country"], "EG")
        self.assertEqual((document["receiver"]["type"], document["receiver"]["id"]), ("B", "987654321"))
        self.assertRegex(document["dateTimeIssued"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        line_a, line_b = document["invoiceLines"]
        # 2 x 100 = 200, line discount 5 -> net 195, VAT 14% = 27.30, total 222.30
        self.assertEqual((line_a["salesTotal"], line_a["discount"]["amount"], line_a["netTotal"], line_a["total"]), (200.0, 5.0, 195.0, 222.3))
        self.assertEqual(line_a["taxableItems"], [{"taxType": "T1", "amount": 27.3, "subType": "V009", "rate": 14.0}])
        self.assertEqual((line_a["itemType"], line_a["itemCode"], line_a["unitType"], line_a["unitValue"]), ("EGS", "EG-100200300-A1", "EA", {"currencySold": "EGP", "amountEGP": 100.0}))
        self.assertEqual(line_b["taxableItems"], [{"taxType": "T1", "amount": 0.0, "subType": "V003", "rate": 0.0}])
        self.assertEqual((line_b["itemType"], line_b["unitType"]), ("GS1", "BOX"))
        self.assertEqual((document["totalSalesAmount"], document["totalDiscountAmount"], document["netAmount"]), (250.0, 5.0, 245.0))
        self.assertEqual(document["taxTotals"], [{"taxType": "T1", "amount": 27.3}])
        self.assertEqual(document["extraDiscountAmount"], 10.0)
        self.assertEqual(document["totalAmount"], float(invoice.total_amount))  # 245 + 27.30 - 10 = 262.30
        self.assertEqual(invoice.total_amount, D("262.30"))
        payload = json.loads(document_json(document))
        self.assertEqual(payload["documents"][0]["internalID"], "EI-1")

    def test_missing_data_is_listed_for_the_owner(self):
        ReceiverProfile.objects.create(customer=self.customer, receiver_type="B")  # a business with no details yet
        invoice = self.invoice()
        _, problems = build_document(invoice)
        text = " | ".join(problems)
        for expected in ("الرقم الضريبي للشركة", "كود النشاط", "عنوان الفرع", "رقم العميل", "عنوان العميل", "«A» مالوش كود", "«B» مالوش كود"):
            with self.subTest(expected=expected):
                self.assertIn(expected, text)
        _, english = build_document(invoice, "en")
        self.assertTrue(any("activity code" in problem for problem in english))

    def test_a_small_sale_to_a_person_needs_no_receiver_details(self):
        self.complete_data()
        ReceiverProfile.objects.filter(customer=self.customer).delete()
        document, problems = build_document(self.invoice())
        self.assertEqual(problems, [])
        self.assertEqual(document["receiver"], {"type": "P", "name": "Buyer Co"})

    def test_a_person_above_the_threshold_needs_an_id(self):
        self.complete_data()
        ReceiverProfile.objects.filter(customer=self.customer).update(receiver_type="P", tax_id="")
        save_issuer_settings({"einvoice.activity_code": "4711", "einvoice.branch_id": "0", "einvoice.governate": "Cairo", "einvoice.region_city": "Nasr City",
                              "einvoice.street": "Abbas El Akkad", "einvoice.building_number": "12", "einvoice.person_id_threshold": "100"}, self.owner)
        _, problems = build_document(self.invoice())
        self.assertTrue(any("رقم العميل" in problem for problem in problems))

    def test_a_draft_or_a_non_egp_company_is_flagged(self):
        self.complete_data()
        draft = create_sales_draft_with_tax(
            {"invoice_number": "EI-D", "invoice_date": TODAY, "customer": self.customer, "selling_location": self.location, "cashbox": self.cashbox, "paid_now": D("0")},
            [{"item": self.a, "quantity": D("1"), "unit_sale_price": D("100.00")}], self.owner,
        )
        _, problems = build_document(draft)
        self.assertIn("الفاتورة لازم تكون مرحّلة.", problems)
        ClientProfile.objects.update(default_currency="USD")
        _, problems = build_document(self.invoice())
        self.assertTrue(any("جنيه مصري" in problem for problem in problems))


class ScreenTests(DocumentSetup):
    def test_document_page_download_and_links(self):
        self.complete_data()
        invoice = self.invoice()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("sales:detail", args=[invoice.pk])), reverse("einvoice:sales_document", args=[invoice.pk]))
        page = self.client.get(reverse("einvoice:sales_document", args=[invoice.pk]))
        self.assertContains(page, "data-einvoice-ready")
        download = self.client.get(reverse("einvoice:sales_document", args=[invoice.pk]), {"format": "json"})
        self.assertEqual(download["Content-Type"], "application/json; charset=utf-8")
        self.assertIn("attachment", download["Content-Disposition"])
        self.assertEqual(json.loads(download.content)["documents"][0]["totalAmount"], 262.3)
        self.assertContains(self.client.get(reverse("settings_core:overview")), reverse("einvoice:issuer"))

    def test_data_screens_save_and_audit(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("einvoice:issuer"), {"einvoice.activity_code": "4711", "einvoice.governate": "Cairo"})
        from .services import issuer_settings

        self.assertEqual(issuer_settings()["einvoice.activity_code"], "4711")
        self.client.post(reverse("einvoice:items"), {f"shown_{self.a.pk}": "1", f"type_{self.a.pk}": "GS1", f"code_{self.a.pk}": "6220000000001", f"unit_{self.a.pk}": "ea"})
        self.assertEqual((ItemCode.objects.get(item=self.a).code_type, ItemCode.objects.get(item=self.a).unit_type), ("GS1", "EA"))
        self.client.post(reverse("einvoice:items"), {f"shown_{self.a.pk}": "1", f"code_{self.a.pk}": ""})
        self.assertFalse(ItemCode.objects.filter(item=self.a).exists())
        self.client.post(reverse("einvoice:customers"), {f"shown_{self.customer.pk}": "1", f"type_{self.customer.pk}": "B", f"tax_id_{self.customer.pk}": "111222333", f"governate_{self.customer.pk}": "Giza"})
        self.assertEqual(ReceiverProfile.objects.get(customer=self.customer).tax_id, "111222333")
        from audit.models import AuditLog

        self.assertEqual(set(AuditLog.objects.filter(module="einvoice").values_list("action", flat=True)), {"update_issuer", "set_item_codes", "set_receiver_profiles"})

    def test_permissions_and_capability(self):
        self.client.force_login(person(RoleCode.CASHIER, "ei_cashier"))
        self.assertEqual(self.client.post(reverse("einvoice:items"), {f"shown_{self.a.pk}": "1", f"code_{self.a.pk}": "X"}).status_code, 403)
        self.assertFalse(ItemCode.objects.exists())
        set_capability_enabled(ClientProfile.get_active(), "e_invoice", False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("einvoice:issuer")).status_code, 403)
