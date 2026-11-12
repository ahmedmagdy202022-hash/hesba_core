"""Watch round 2 fixes for contracting: CN-04 (a project's loss reads «خسارة 600.00», never «600.00-»)."""

from decimal import Decimal as D

from django.urls import reverse

from sales.services import post_sales_invoice

from . import services
from .tests import ProjectSetup


class LossInWordsTests(ProjectSetup):
    """Billed 1,000.00 and 25 bags of cement at 80.00 (2,000.00) leave a loss of 1,000.00."""

    def setUp(self):
        super().setUp()
        post_sales_invoice(services.bill_progress(self.project, self.owner, amount="1000", description="مستخلص 1").pk, self.owner)
        services.issue_materials(self.project, self.owner, item=self.cement, location=self.store, quantity="25")
        self.assertEqual(services.summary(self.project)["profit"], D("-1000.00"))
        self.client.force_login(self.owner)

    def page(self, name, lang, *args):
        return self.client.get(reverse(name, args=args), {"lang": lang}).content.decode()

    def test_the_project_list_says_loss_in_both_languages(self):
        ar, en = self.page("projects:list", "ar"), self.page("projects:list", "en")
        self.assertIn('<td data-figure="profit">خسارة <bdi>1,000.00</bdi></td>', ar)
        self.assertIn('<td data-figure="profit">Loss <bdi>1,000.00</bdi></td>', en)
        self.assertNotIn("-1,000.00", ar + en)

    def test_the_project_page_and_the_report_say_it_too(self):
        detail = self.page("projects:detail", "ar", self.project.pk)
        self.assertIn('<strong data-figure="profit">خسارة <bdi>1,000.00</bdi></strong>', detail)
        report_ar, report_en = self.page("projects:report", "ar"), self.page("projects:report", "en")
        self.assertIn("<strong data-p-profit>خسارة <bdi>1,000.00</bdi></strong>", report_ar)
        self.assertIn("<strong data-p-profit>Loss <bdi>1,000.00</bdi></strong>", report_en)
        self.assertIn("خسارة <bdi>1,000.00</bdi></span>", report_ar)
        self.assertNotIn("-1,000.00", detail + report_ar + report_en)

    def test_a_profit_still_reads_as_a_plain_amount(self):
        other = services.save_project({"name": "شقة المعادي", "customer": self.customer, "contract_value": "50000"}, self.owner)
        post_sales_invoice(services.bill_progress(other, self.owner, amount="4000", description="مستخلص").pk, self.owner)
        self.assertIn('<strong data-figure="profit">4,000.00</strong>', self.page("projects:detail", "ar", other.pk))


class VagueMessageTests(ProjectSetup):
    """CN-07 / S2-06: the contract value, the item's rate and a subcontract's value say what is valid."""

    def check(self, call, ar, en):
        from django.core.exceptions import ValidationError

        for lang, text in (("ar", ar), ("en", en)):
            for bad in ("-1", "abc"):
                with self.assertRaises(ValidationError) as caught:
                    call(bad, lang)
                self.assertEqual(caught.exception.messages, [text], (lang, bad))

    def test_the_contract_value(self):
        self.check(lambda bad, lang: services.save_project({"name": "x", "customer": self.customer, "contract_value": bad}, self.owner, lang=lang),
                   "اكتب قيمة العقد رقم صفر أو أكبر.", "Type the contract value as a number, 0 or more.")

    def test_a_bill_of_quantities_rate(self):
        from . import contract

        self.check(lambda bad, lang: contract.save_boq_line(self.project, self.owner, {"description": "خرسانة", "quantity": "1", "rate": bad}, lang=lang),
                   "اكتب فئة البند (سعر الوحدة) رقم صفر أو أكبر.", "Type the item's rate (price per unit) as a number, 0 or more.")

    def test_a_subcontract_value(self):
        from hesba_testing.factories import make_supplier

        from . import costs

        supplier = make_supplier(supplier_code="SUB-1", name="مقاول باطن")
        self.check(lambda bad, lang: costs.save_subcontract(self.project, self.owner, {"supplier": supplier, "scope": "أعمال كهرباء", "value": bad}, lang=lang),
                   "اكتب القيمة رقم صفر أو أكبر.", "Type the value as a number, 0 or more.")
