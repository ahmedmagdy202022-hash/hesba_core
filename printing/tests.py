"""PRINT-001: printed invoices, returns and vouchers."""

from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from audit.models import AuditLog
from closing.models import Period
from hesba_testing.factories import (
    DEFAULT_DATE,
    make_cashbox,
    make_customer,
    make_seeded_role,
    make_user,
    make_user_profile,
    posted_invoice_ready,
    purchase_ready,
)
from permissions.models import RoleCode
from purchases.services import post_purchase_invoice, record_supplier_payment
from reports.tests_dashboard import prepared_client
from sales.services import create_sales_return, post_sales_invoice, record_customer_payment
from settings_core.models import ClientProfile
from settings_core.setup_services import set_module_enabled

from .amount_words import amount_in_words, arabic_number, english_number
from .company import company_details, save_company_details


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class AmountWordsTests(SimpleTestCase):
    def test_arabic_numbers(self):
        cases = {
            1: "واحد",
            11: "أحد عشر",
            21: "واحد وعشرون",
            200: "مائتان",
            1000: "ألف",
            2000: "ألفان",
            3000: "ثلاثة آلاف",
            11000: "أحد عشر ألف",
            12345: "اثنا عشر ألف وثلاثمائة وخمسة وأربعون",
            2000000: "مليونان",
            3500000: "ثلاثة ملايين وخمسمائة ألف",
        }
        for number, words in cases.items():
            with self.subTest(number=number):
                self.assertEqual(arabic_number(number), words)

    def test_english_numbers(self):
        self.assertEqual(english_number(1250), "one thousand two hundred fifty")
        self.assertEqual(english_number(2000021), "two million twenty-one")

    def test_receipt_phrases_with_minor_units(self):
        self.assertEqual(amount_in_words(D("1250.50"), "EGP"), "فقط ألف ومائتان وخمسون جنيه مصري وخمسون قرش لا غير")
        self.assertEqual(amount_in_words(D("100"), "SAR"), "فقط مائة ريال سعودي لا غير")
        self.assertEqual(amount_in_words(D("1250.50"), "EGP", "en"), "One thousand two hundred fifty Egyptian pounds and fifty piasters only")
        # Three-decimal currencies count their minor unit in thousandths.
        self.assertEqual(amount_in_words(D("0.75"), "KWD"), "فقط صفر دينار كويتي وسبعمائة وخمسون فلس لا غير")


class CompanyDetailsTests(TestCase):
    def test_saved_details_are_audited_and_read_back(self):
        prepared_client()
        user = person(RoleCode.OWNER, "company_owner")
        changed = save_company_details({"company.phone": "01000000000", "company.tax_number": "123-456-789"}, user)
        self.assertTrue(changed)
        details = company_details()
        self.assertEqual((details["phone"], details["tax_number"], details["name"]), ("01000000000", "123-456-789", "Demo Store"))
        log = AuditLog.objects.get(action="update_company_details")
        self.assertEqual(log.after_data["company.phone"], "01000000000")
        self.assertFalse(save_company_details({"company.phone": "01000000000", "company.tax_number": "123-456-789"}, user))

    def test_settings_screen_saves_for_the_owner_and_is_read_only_for_the_manager(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "company_screen_owner"))
        self.client.post(reverse("settings_core:company"), {"company.address": "شارع التحرير، القاهرة"})
        self.assertEqual(company_details()["address"], "شارع التحرير، القاهرة")
        self.client.force_login(person(RoleCode.MANAGER, "company_screen_manager"))
        page = self.client.get(reverse("settings_core:company"))
        self.assertContains(page, "شارع التحرير")
        self.assertContains(page, "disabled")
        self.assertEqual(self.client.post(reverse("settings_core:company"), {"company.address": "x"}).status_code, 403)


class PrintedDocumentTests(TestCase):
    def setUp(self):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,pdf_printing,reports")
        Period.objects.create(period_code="PR", name="print", start_date=DEFAULT_DATE.replace(day=1), end_date=DEFAULT_DATE.replace(day=28))
        self.owner = person(RoleCode.OWNER, "print_owner")
        self.client.force_login(self.owner)
        save_company_details({"company.phone": "0225550000", "print.footer_note": "البضاعة المباعة ترد خلال 14 يوم"}, self.owner)

    def test_sales_invoice_a4(self):
        invoice, *_ = posted_invoice_ready(paid_now="20.00")
        post_sales_invoice(invoice.pk, self.owner)
        response = self.client.get(reverse("printing:sales_invoice", args=[invoice.pk]))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("فاتورة بيع", body)
        self.assertIn(invoice.invoice_number, body)
        self.assertIn("Demo Store", body)
        self.assertIn("0225550000", body)
        self.assertIn("60.00", body)  # 2 x 30.00
        self.assertIn("فقط ستون جنيه مصري لا غير", body)
        self.assertIn("40.00", body)  # remaining credit
        self.assertIn("البضاعة المباعة ترد خلال 14 يوم", body)
        self.assertIn('class="pr-body pr-a4"', body)
        self.assertNotIn("pr-watermark--status", body)
        # Cost and profit never reach paper.
        self.assertNotIn("تكلفة", body)
        self.assertNotIn("ربح", body)

    def test_receipt_format_and_english(self):
        invoice, *_ = posted_invoice_ready(paid_now="60.00")
        post_sales_invoice(invoice.pk, self.owner)
        response = self.client.get(reverse("printing:sales_invoice", args=[invoice.pk]), {"format": "receipt", "lang": "en"})
        self.assertContains(response, 'class="pr-body pr-receipt"')
        self.assertContains(response, "Sales invoice")
        self.assertContains(response, "Sixty Egyptian pounds only")
        self.assertContains(response, 'dir="ltr"')

    def test_draft_and_cancelled_documents_carry_a_status_watermark(self):
        invoice, *_ = posted_invoice_ready()
        draft = self.client.get(reverse("printing:sales_invoice", args=[invoice.pk]))
        self.assertContains(draft, "pr-watermark--status")
        self.assertContains(draft, "مسودة")

    def test_purchase_invoice_and_supplier_voucher(self):
        invoice, *_ = purchase_ready(paid_now="0.00")
        post_purchase_invoice(invoice.pk, self.owner)
        self.assertContains(self.client.get(reverse("printing:purchase_invoice", args=[invoice.pk])), "فاتورة شراء")
        cashbox = make_cashbox(cashbox_code="CASH-PR")
        payment = record_supplier_payment("SP-PR-1", DEFAULT_DATE, invoice.supplier, cashbox, D("35.00"), user=self.owner)
        voucher = self.client.get(reverse("printing:supplier_payment", args=[payment.pk]))
        self.assertContains(voucher, "سند صرف")
        self.assertContains(voucher, "صرفنا إلى")
        self.assertContains(voucher, "فقط خمسة وثلاثون جنيه مصري لا غير")

    def test_customer_receipt_voucher(self):
        payment = record_customer_payment("CP-PR-1", DEFAULT_DATE, make_customer(), make_cashbox(cashbox_code="CASH-CP"), D("1250.50"), user=self.owner)
        voucher = self.client.get(reverse("printing:customer_payment", args=[payment.pk]))
        self.assertContains(voucher, "سند قبض")
        self.assertContains(voucher, "استلمنا من")
        self.assertContains(voucher, "1,250.50")
        self.assertContains(voucher, "فقط ألف ومائتان وخمسون جنيه مصري وخمسون قرش لا غير")

    def test_sales_return_document(self):
        invoice, item, location, cashbox = posted_invoice_ready(paid_now="60.00")
        post_sales_invoice(invoice.pk, self.owner)
        line = invoice.lines.get()
        sales_return = create_sales_return(
            return_number="SR-PR-1",
            return_date=DEFAULT_DATE,
            source_invoice_id=invoice.pk,
            lines=[{"source_line": line.pk, "quantity": "1"}],
            reason="مقاس غلط",
            user=self.owner,
        )
        page = self.client.get(reverse("printing:sales_return", args=[sales_return.pk]))
        self.assertContains(page, "مرتجع بيع")
        self.assertContains(page, invoice.invoice_number)
        self.assertContains(page, "مقاس غلط")

    def test_detail_screen_links_to_print(self):
        invoice, *_ = posted_invoice_ready()
        detail = self.client.get(reverse("sales:detail", args=[invoice.pk]))
        self.assertContains(detail, reverse("printing:sales_invoice", args=[invoice.pk]))
        self.assertContains(detail, "format=receipt")

    def test_switching_printing_off_hides_links_and_closes_pages(self):
        invoice, *_ = posted_invoice_ready()
        set_module_enabled(ClientProfile.get_active(), "pdf_printing", False, user=self.owner)
        self.assertNotContains(self.client.get(reverse("sales:detail", args=[invoice.pk])), "/print/")
        self.assertEqual(self.client.get(reverse("printing:sales_invoice", args=[invoice.pk])).status_code, 403)

    def test_permissions_follow_the_document(self):
        invoice, *_ = posted_invoice_ready()
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "print_keeper"))
        # The stock keeper cannot read sales invoices, so cannot print them either.
        self.assertEqual(self.client.get(reverse("printing:sales_invoice", args=[invoice.pk])).status_code, 403)
        self.client.force_login(person(RoleCode.CASHIER, "print_cashier"))
        self.assertEqual(self.client.get(reverse("printing:sales_invoice", args=[invoice.pk])).status_code, 200)
