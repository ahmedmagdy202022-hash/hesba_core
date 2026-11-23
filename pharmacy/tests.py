"""T10 (MARKET-PARITY): drug data, alternatives in stock, and prescriptions dispensed on ordinary sales invoices (HG-062)."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import get_item_stock_quantity
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.services import cancel_posted_sales_invoice, post_sales_invoice
from settings_core import capabilities as caps

from . import services
from .models import DrugInfo, PrescriptionInvoice

TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class PharmacySetup(TestCase):
    def setUp(self):
        self.profile = prepared_client("commercial", "pharmacy")
        caps.set_capability_enabled(self.profile, "drugs", True)
        self.owner = person(RoleCode.OWNER, "ph_owner")
        self.location = make_location(location_code="PH-STORE", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="CASH-PH", is_default=True)
        self.generic = self.drug("AMX-G", "أموكسيسيلين 500 مجم", "85.00", "2", "Amoxicillin", "500 مجم", "capsule", rx=True)
        self.brand = self.drug("AMX-B", "أموكسيل 500 مجم", "92.00", "12", "amoxicillin", "500mg", "capsule", rx=True)
        self.syrup = self.drug("AMX-S", "أموكسيسيلين شراب", "60.00", "0", "Amoxicillin", "250 mg/5 ml", "suspension")
        self.panadol = self.drug("PAR-B", "بنادول 500 مجم", "42.00", "20", "Paracetamol", "500 mg", "tablet")
        self.customer = make_customer(customer_code="CUS-PH", name="محمود عبد الله", phone="01112345678")

    def drug(self, code, name, price, quantity, ingredient, strength, form, rx=False):
        item = make_item(item_code=code, item_name=name, default_sale_price=D(price), is_stock_tracked=True)
        if D(quantity):
            stock_in(item, self.location, quantity, "50.00")
        services.save_drug(item, {"active_ingredient": ingredient, "strength": strength, "form": form, "prescription_only": rx}, self.owner)
        return item

    def prescription(self, **extra):
        prescription = services.create_prescription({"patient_name": "سعاد علي", "doctor_name": "د. أحمد فؤاد", **extra}, self.owner)
        services.add_line(prescription, {"item": self.generic, "quantity": "1", "dosage": "كبسولة كل 8 ساعات"}, self.owner)
        services.add_line(prescription, {"item": self.panadol, "quantity": "2"}, self.owner)
        return prescription

    def dispense(self, prescription, post=True, day=None):
        """An ordinary draft for the prescription's drugs, as the invoice form saves it."""

        from taxes.services import create_sales_draft_with_tax

        header, lines = services.invoice_initial(prescription)
        invoice = create_sales_draft_with_tax({"invoice_number": f"SI-RX-{SalesInvoice.objects.count() + 1}", "invoice_date": day or TODAY,
                                               "customer": header["customer"], "selling_location": self.location, "paid_now": D("0")}, lines, self.owner)
        services.link_dispense(prescription, invoice, self.owner)
        if post:
            post_sales_invoice(invoice.pk, self.owner)
            invoice.refresh_from_db()
        return invoice


class DrugDataTests(PharmacySetup):
    def test_one_spelling_per_ingredient_and_strength(self):
        self.assertEqual(services.ingredient_key("Amoxicillin + Clavulanic acid"), services.ingredient_key("clavulanic acid/amoxicillin"))
        self.assertEqual(services.strength_key("500 مجم"), "500mg")
        self.assertEqual(services.strength_key("٥٠٠ ملجم"), "500mg")
        self.assertEqual(services.form_from_text("أقراص"), "tablet")

    def test_alternatives_put_the_same_strength_and_form_first_then_stock(self):
        rows = services.alternatives(self.generic)
        self.assertEqual([(row["item"], row["match"]) for row in rows], [(self.brand, "same"), (self.syrup, "other")])
        self.assertEqual(rows[0]["on_hand"], D("12"))
        self.assertEqual(services.alternatives(self.panadol), [])  # nothing else is paracetamol

    def test_search_finds_by_name_ingredient_or_barcode_and_counts_alternatives_in_stock(self):
        by_ingredient = {row["item"]: row for row in services.search("amoxicillin")}
        self.assertEqual(set(by_ingredient), {self.generic, self.brand, self.syrup})
        self.assertEqual(by_ingredient[self.generic]["alternatives"], 1)  # the brand; the syrup is sold out
        self.assertEqual(by_ingredient[self.syrup]["alternatives"], 2)
        self.assertEqual([row["item"] for row in services.search("بنادول")], [self.panadol])
        plain = make_item(item_code="GAUZE", item_name="شاش معقم", is_stock_tracked=True)
        self.assertEqual([row["item"] for row in services.search(missing=True)], [plain])

    def test_removing_the_drug_data_leaves_the_item(self):
        services.save_drug(self.panadol, {"active_ingredient": "Paracetamol", "remove": "1"}, self.owner)
        self.assertFalse(DrugInfo.objects.filter(item=self.panadol).exists())
        self.panadol.refresh_from_db()
        self.assertTrue(self.panadol.active)
        with self.assertRaises(ValidationError):
            services.save_drug(self.panadol, {"active_ingredient": ""}, self.owner)

    def test_a_cashier_looks_up_but_does_not_edit_drug_data(self):
        cashier = person(RoleCode.CASHIER, "ph_cashier")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("pharmacy:lookup") + "?q=amoxicillin").status_code, 200)
        page = self.client.get(reverse("pharmacy:drug", args=[self.generic.pk]))
        self.assertNotContains(page, "data-drug-form")
        with self.assertRaises(PermissionDenied):
            services.save_drug(self.generic, {"active_ingredient": "X"}, cashier)


class PrescriptionTests(PharmacySetup):
    def test_dispensing_posts_an_ordinary_sale_and_stock_leaves_once(self):
        prescription = self.prescription()
        self.assertEqual(services.state(prescription), "open")
        invoice = self.dispense(prescription)
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(get_item_stock_quantity(self.generic), D("1"))
        self.assertEqual(get_item_stock_quantity(self.panadol), D("18"))
        self.assertEqual(services.state(prescription), "dispensed")
        self.assertFalse(services.can_dispense(prescription))
        with self.assertRaises(ValidationError):  # the same paper is never dispensed twice
            self.dispense(prescription, post=False)
        with self.assertRaises(ValidationError):
            services.add_line(prescription, {"item": self.brand}, self.owner)

    def test_a_draft_counts_and_a_cancelled_invoice_frees_the_prescription(self):
        prescription = self.prescription()
        invoice = self.dispense(prescription)
        cancel_posted_sales_invoice(invoice.pk, self.owner, reason="رجّع الدوا")
        self.assertEqual(services.state(prescription), "open")
        self.assertEqual(get_item_stock_quantity(self.generic), D("2"))
        self.dispense(prescription, post=False)
        self.assertEqual(services.state(prescription), "dispensed")

    def test_another_customers_invoice_does_not_dispense_it(self):
        prescription = self.prescription(customer=self.customer)
        from taxes.services import create_sales_draft_with_tax

        other = make_customer(customer_code="CUS-OTHER", name="عميل تاني")
        invoice = create_sales_draft_with_tax({"invoice_number": "SI-OTHER", "invoice_date": TODAY, "customer": other,
                                               "selling_location": self.location, "paid_now": D("0")},
                                              [{"item": self.panadol, "quantity": D("1"), "unit_sale_price": D("42")}], self.owner)
        with self.assertRaises(ValidationError):
            services.link_dispense(prescription, invoice, self.owner)

    def test_a_chronic_prescription_comes_due_for_its_refill(self):
        prescription = self.prescription(refill_days="30", customer=self.customer)
        self.dispense(prescription, day=TODAY - timedelta(days=28))
        self.assertEqual(services.next_refill(prescription), TODAY + timedelta(days=2))
        self.assertEqual(services.state(prescription), "due")
        self.assertEqual(services.counts()["due"], 1)
        self.dispense(prescription)  # the refill
        self.assertEqual(services.state(prescription), "waiting")
        self.assertEqual(PrescriptionInvoice.objects.filter(prescription=prescription).count(), 2)

    def test_a_short_drug_swaps_for_an_alternative_but_not_for_another_drug(self):
        prescription = services.create_prescription({"patient_name": "سعاد"}, self.owner)
        line = services.add_line(prescription, {"item": self.generic, "quantity": "5"}, self.owner)
        services.choose_item(line, self.brand, self.owner)
        line.refresh_from_db()
        self.assertEqual((line.item, line.substituted), (self.brand, True))
        with self.assertRaises(ValidationError):
            services.choose_item(line, self.panadol, self.owner)

    def test_cancelling_is_refused_once_dispensed(self):
        prescription = self.prescription()
        self.dispense(prescription, post=False)
        with self.assertRaises(ValidationError):
            services.cancel(prescription, self.owner)

    def test_a_stock_keeper_cannot_record_prescriptions(self):
        keeper = person(RoleCode.STOCK_KEEPER, "ph_keeper")
        with self.assertRaises(PermissionDenied):
            services.create_prescription({"patient_name": "سعاد"}, keeper)


class CodexFixesTests(PharmacySetup):
    """Codex on #262: the board filters before it cuts the list, and a prescription never outgrows one invoice."""

    def test_a_due_refill_shows_behind_hundreds_of_newer_prescriptions(self):
        from .models import Prescription

        chronic = self.prescription(refill_days="30", customer=self.customer, issued_on=TODAY - timedelta(days=60))
        self.dispense(chronic, day=TODAY - timedelta(days=29))
        Prescription.objects.bulk_create([Prescription(number=f"RX-B{n:04d}", patient_name=f"مريض {n}", issued_on=TODAY, created_by=self.owner)
                                          for n in range(305)])
        self.client.force_login(self.owner)
        due = self.client.get(reverse("pharmacy:prescriptions") + "?lang=ar&show=due")
        self.assertContains(due, chronic.number)
        self.assertEqual(services.counts(), {"open": 305, "due": 1})
        waiting = self.client.get(reverse("pharmacy:prescriptions") + "?lang=ar&show=open")
        self.assertNotContains(waiting, chronic.number)

    def test_a_prescription_takes_no_more_drugs_than_one_invoice(self):
        prescription = services.create_prescription({"patient_name": "سعاد"}, self.owner)
        for n in range(services.INVOICE_LINES):
            services.add_line(prescription, {"prescribed_name": f"دوا {n}"}, self.owner)
        with self.assertRaises(ValidationError):
            services.add_line(prescription, {"prescribed_name": "دوا زيادة"}, self.owner)


class ScreensTests(PharmacySetup):
    def test_the_invoice_form_opens_with_the_drugs_and_links_on_save(self):
        self.client.force_login(self.owner)
        prescription = self.prescription(customer=self.customer)
        page = self.client.get(reverse("sales:create") + f"?lang=ar&prescription={prescription.pk}")
        self.assertContains(page, "data-from-prescription")
        lines = page.context["line_formset"].initial
        self.assertEqual([(row["item"], row["quantity"]) for row in lines], [(self.generic, D("1")), (self.panadol, D("2"))])
        post = {"lang": "ar", "prescription": str(prescription.pk), "invoice_number": "SI-RX-FORM", "invoice_date": TODAY.isoformat(),
                "customer": str(self.customer.pk), "selling_location": str(self.location.pk), "cashbox": "", "discount_amount": "0",
                "tax_amount": "0", "paid_now": "0", "notes": "", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0",
                "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "20", "lines-0-item": str(self.generic.pk), "lines-0-quantity": "1",
                "lines-0-unit_sale_price": "85", "lines-0-line_discount_amount": "0"}
        self.assertEqual(self.client.post(reverse("sales:create"), post).status_code, 302)
        self.assertEqual(services.state(prescription), "dispensed")
        post["invoice_number"] = "SI-RX-FORM-2"  # a second tab saving the same prescription is refused, not sold unlinked
        response = self.client.post(reverse("sales:create"), post)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SalesInvoice.objects.filter(invoice_number="SI-RX-FORM-2").exists())

    def test_the_screens_in_both_languages(self):
        self.client.force_login(self.owner)
        prescription = services.create_prescription({"patient_name": "سعاد علي", "patient_phone": "01001234567"}, self.owner)
        services.add_line(prescription, {"item": self.generic, "quantity": "5", "dosage": "كبسولة كل 8 ساعات"}, self.owner)
        for lang in ("ar", "en"):
            lookup = self.client.get(reverse("pharmacy:lookup") + f"?lang={lang}&q=amox")
            self.assertContains(lookup, self.brand.item_name)
            drug = self.client.get(reverse("pharmacy:drug", args=[self.generic.pk]) + f"?lang={lang}")
            self.assertContains(drug, 'data-match="same"')
            board = self.client.get(reverse("pharmacy:prescriptions") + f"?lang={lang}")
            self.assertContains(board, prescription.number)
            detail = self.client.get(reverse("pharmacy:prescription", args=[prescription.pk]) + f"?lang={lang}")
            self.assertContains(detail, "data-short")  # 2 on the shelf for 5 written
            self.assertContains(detail, f'data-pick="{self.brand.pk}"')  # the brand has 12
            self.assertContains(detail, "data-share-whatsapp")
            printed = self.client.get(reverse("pharmacy:print", args=[prescription.pk]) + f"?lang={lang}")
            self.assertContains(printed, "كبسولة كل 8 ساعات")
            self.assertNotContains(printed, "data-print-total")
        self.assertContains(self.client.get(reverse("pharmacy:new") + "?lang=ar"), "data-new-rx-form")

    def test_the_screens_post_their_actions(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("pharmacy:new"), {"lang": "ar", "patient_name": "سعاد", "refill_days": "30"})
        prescription = services.scoped().get()
        self.assertRedirects(response, reverse("pharmacy:prescription", args=[prescription.pk]) + "?lang=ar", fetch_redirect_response=False)
        url = reverse("pharmacy:prescription", args=[prescription.pk])
        self.client.post(url, {"lang": "ar", "action": "add_line", "prescribed_name": "Augmentin 1 g", "quantity": "1"})
        line = prescription.lines.get()
        self.assertIsNone(line.item)
        self.client.post(url, {"lang": "ar", "action": "choose", "line": line.pk, "item": self.brand.pk})
        line.refresh_from_db()
        self.assertEqual((line.item, line.substituted), (self.brand, False))  # chosen for a line with none is not a swap
        self.client.post(reverse("pharmacy:drug", args=[self.panadol.pk]), {"lang": "ar", "active_ingredient": "Paracetamol", "strength": "1 g",
                                                                            "form": "tablet"})
        self.assertEqual(DrugInfo.objects.get(item=self.panadol).strength_key, "1g")

    def test_the_menu_and_the_screens_follow_the_feature_switch(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard_snapshot") + "?lang=ar"), 'href="/pharmacy/')
        caps.set_capability_enabled(self.profile, "drugs", False)
        self.assertEqual(self.client.get(reverse("pharmacy:lookup")).status_code, 403)

    def test_the_feature_is_offered_where_it_fits(self):
        self.assertTrue(caps.fits("commercial", "drugs", "pharmacy"))
        self.assertEqual(caps.preset_state("commercial", "pharmacy", "drugs"), caps.SUGGESTED)
        self.assertTrue(caps.fits("medical", "drugs", "medical_center"))
        self.assertFalse(caps.fits("commercial", "drugs", "grocery"))
        self.assertFalse(caps.fits("restaurants", "drugs", "cafe"))
        self.assertEqual(caps.needs("drugs"), ("inventory",))


class ImportTests(PharmacySetup):
    def test_an_items_file_brings_the_drug_columns(self):
        from imports import columns
        from imports.apply_services import apply_item_row

        keys = [key for key, *_ in columns.fields("items")]
        self.assertIn("active_ingredient", keys)
        mapping = columns.suggest("items", ["اسم الصنف", "كود الصنف", "الاسم العلمي", "التركيز", "الشكل", "يصرف بروشتة"])
        self.assertEqual(mapping["الاسم العلمي"], "active_ingredient")
        self.assertEqual(mapping["يصرف بروشتة"], "prescription_only")
        [data] = columns.apply("items", [{"اسم الصنف": "أوجمنتين 1 جم", "كود الصنف": "AUG-1", "الاسم العلمي": "Amoxicillin + Clavulanic acid",
                                          "التركيز": "1 جم", "الشكل": "أقراص", "يصرف بروشتة": "نعم"}], mapping)

        class Batch:
            batch_code = "IMP-RX"

        item, _label = apply_item_row(data, Batch())
        self.assertEqual((item.drug.strength_key, item.drug.form, item.drug.prescription_only), ("1g", "tablet", True))
        caps.set_capability_enabled(self.profile, "drugs", False)
        self.assertNotIn("active_ingredient", [key for key, *_ in columns.fields("items")])


class MakeAgainKeepsThePrescriptionTests(PharmacySetup):
    """Codex #284: a cancelled sale that dispensed a prescription, made again, dispenses it again."""

    def test_the_copy_carries_the_prescription(self):
        prescription = self.prescription()
        invoice = self.dispense(prescription)
        cancel_posted_sales_invoice(invoice.pk, self.owner, "again")
        self.client.force_login(self.owner)
        page = self.client.get(f"/sales/new/?copy={invoice.pk}")
        self.assertEqual(page.context["from_prescription"], prescription)
        self.assertContains(page, f'name="prescription" value="{prescription.pk}"')
