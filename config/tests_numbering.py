"""AUTONUM: codes and document numbers fill themselves in when left blank."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from master_data.forms import CustomerForm, ItemForm, SupplierForm
from master_data.models import Customer, Item, Supplier
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.forms import CustomerPaymentForm, SalesDraftForm, SalesReturnForm
from sales.models import SalesInvoice

from .numbering import next_in_series


class SeriesTests(TestCase):
    def test_the_next_after_the_highest_of_the_series_only(self):
        self.assertEqual(next_in_series(Customer, "customer_code", "C-"), "C-00001")
        for code in ("C-00007", "C-00002", "KARIM", "C-12A", "CUS-00099", "C-"):
            make_customer(customer_code=code)
        self.assertEqual(next_in_series(Customer, "customer_code", "C-"), "C-00008")

    def test_a_taken_number_is_skipped(self):
        make_customer(customer_code="C-1")       # a short hand-typed one is still the series' 1
        make_customer(customer_code="C-00002")
        self.assertEqual(next_in_series(Customer, "customer_code", "C-"), "C-00003")


class MasterDataFormTests(TestCase):
    def test_a_new_customer_gets_the_next_code_and_shows_it(self):
        make_customer(customer_code="C-00004")
        form = CustomerForm(lang="ar")
        field = form.fields["customer_code"]
        self.assertFalse(field.required)
        self.assertEqual(field.widget.attrs["placeholder"], "تلقائي: C-00005")
        self.assertIn("Automatic: C-00005", str(CustomerForm(lang="en")["customer_code"]))
        form = CustomerForm({"customer_code": "", "name": "عميل جديد", "opening_balance": "0", "credit_limit": "0", "active": "on"}, lang="ar")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().customer_code, "C-00005")

    def test_a_typed_code_is_kept_and_an_existing_one_stays_required(self):
        form = CustomerForm({"customer_code": " VIP-1 ", "name": "عميل", "opening_balance": "0", "credit_limit": "0"}, lang="ar")
        self.assertTrue(form.is_valid(), form.errors)
        customer = form.save()
        self.assertEqual(customer.customer_code, "VIP-1")
        edit = CustomerForm(instance=customer, lang="ar")
        self.assertTrue(edit.fields["customer_code"].required)
        self.assertNotIn("placeholder", edit.fields["customer_code"].widget.attrs)
        self.assertFalse(CustomerForm({"customer_code": "", "name": "عميل"}, instance=customer, lang="ar").is_valid())

    def test_suppliers_and_items_too(self):
        supplier = SupplierForm({"supplier_code": "", "name": "مورد", "opening_balance": "0", "active": "on"}, lang="ar")
        self.assertTrue(supplier.is_valid(), supplier.errors)
        self.assertEqual(supplier.save().supplier_code, "S-00001")
        item = ItemForm({"item_code": "", "item_name": "صنف", "unit": "قطعة", "default_sale_price": "10", "min_stock": "0", "is_stock_tracked": "on", "active": "on"}, lang="ar")
        self.assertTrue(item.is_valid(), item.errors)
        self.assertEqual(item.save().item_code, "IT-00001")
        self.assertTrue(Supplier.objects.exists() and Item.objects.exists())

    def test_the_customer_screen_saves_with_the_code_left_blank(self):
        prepared_client()
        owner = make_user(username="autonum_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        page = self.client.get(reverse("master_data:customer_create"))
        self.assertContains(page, 'placeholder="تلقائي: C-00001"')
        response = self.client.post(reverse("master_data:customer_create") + "?lang=ar", {"customer_code": "", "name": "عميل من الشاشة", "opening_balance": "0", "credit_limit": "0", "active": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Customer.objects.get(name="عميل من الشاشة").customer_code, "C-00001")


class SalesFormTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        prepared_client()
        cls.owner = make_user(username="autonum_seller")
        make_user_profile(user=cls.owner, role=make_seeded_role(RoleCode.OWNER))
        cls.customer = make_customer(customer_code="C-00001")
        cls.location = make_location(location_code="L-A", is_selling_location=True)
        cls.cashbox = make_cashbox(cashbox_code="K-A")
        cls.item = make_item(item_code="IT-A")

    def header(self, number):
        return {"invoice_number": number, "invoice_date": timezone.localdate().isoformat(), "customer": self.customer.pk,
                "selling_location": self.location.pk, "cashbox": self.cashbox.pk, "discount_amount": "0", "tax_amount": "0", "paid_now": "0"}

    def test_a_blank_invoice_number_becomes_the_next(self):
        SalesInvoice.objects.create(invoice_number="SI-00041", invoice_date=timezone.localdate(), customer=self.customer, selling_location=self.location, created_by=self.owner)
        form = SalesDraftForm(lang="ar")
        self.assertEqual(form.fields["invoice_number"].widget.attrs["placeholder"], "تلقائي: SI-00042")
        form = SalesDraftForm(self.header(""), lang="ar")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["invoice_number"], "SI-00042")

    def test_a_taken_number_is_a_message_not_a_server_error(self):
        SalesInvoice.objects.create(invoice_number="MINE-1", invoice_date=timezone.localdate(), customer=self.customer, selling_location=self.location, created_by=self.owner)
        form = SalesDraftForm(self.header("MINE-1"), lang="ar")
        self.assertFalse(form.is_valid())
        self.assertIn("invoice_number", form.errors)
        self.client.force_login(self.owner)
        response = self.client.post(reverse("sales:create") + "?lang=ar", {
            **self.header("MINE-1"), "lang": "ar", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0",
            "lines-0-item": self.item.pk, "lines-0-quantity": "1", "lines-0-unit_sale_price": "10",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "فيه فاتورة بيع بنفس الرقم ده")

    def test_collections_and_returns_too(self):
        payment = CustomerPaymentForm({"payment_number": "", "payment_date": timezone.localdate().isoformat(), "customer": self.customer.pk,
                                       "cashbox": self.cashbox.pk, "amount": "5"}, lang="ar")
        self.assertTrue(payment.is_valid(), payment.errors)
        self.assertEqual(payment.cleaned_data["payment_number"], "CP-00001")
        ret = SalesReturnForm({"return_number": "", "return_date": timezone.localdate().isoformat(), "reason": "تالف"}, lang="ar")
        self.assertTrue(ret.is_valid(), ret.errors)
        self.assertEqual(ret.cleaned_data["return_number"], "SR-00001")
        self.assertEqual(D("5"), payment.cleaned_data["amount"])


class TwoSavesAtOnceTests(TestCase):
    """The number shown is taken by someone else before this save lands: the next one is used."""

    def test_a_customer_saved_after_its_code_was_taken(self):
        form = CustomerForm({"customer_code": "", "name": "الأول", "opening_balance": "0", "credit_limit": "0"}, lang="ar")
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["customer_code"], "C-00001")
        make_customer(customer_code="C-00001", name="سبقه")
        self.assertEqual(form.save().customer_code, "C-00002")

    def test_an_invoice_number_taken_meanwhile(self):
        prepared_client()
        owner = make_user(username="race_owner")
        customer = make_customer(customer_code="C-R")
        location = make_location(location_code="L-R", is_selling_location=True)
        header = {"invoice_number": "", "invoice_date": timezone.localdate().isoformat(), "customer": customer.pk,
                  "selling_location": location.pk, "discount_amount": "0", "tax_amount": "0", "paid_now": "0"}
        form = SalesDraftForm(header, lang="ar")
        self.assertTrue(form.is_valid(), form.errors)
        SalesInvoice.objects.create(invoice_number=form.cleaned_data["invoice_number"], invoice_date=timezone.localdate(),
                                    customer=customer, selling_location=location, created_by=owner)
        invoice = form.create_with_fresh_number(lambda: SalesInvoice.objects.create(
            invoice_number=form.cleaned_data["invoice_number"], invoice_date=timezone.localdate(),
            customer=customer, selling_location=location, created_by=owner))
        self.assertEqual(invoice.invoice_number, "SI-00002")

    def test_a_typed_number_that_clashes_is_not_renumbered(self):
        from django.db import IntegrityError

        form = CustomerForm({"customer_code": "MINE", "name": "عميل", "opening_balance": "0", "credit_limit": "0"}, lang="ar")
        self.assertTrue(form.is_valid(), form.errors)
        make_customer(customer_code="MINE")
        with self.assertRaises(IntegrityError):
            form.save()
