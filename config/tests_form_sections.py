"""AN3 (Ahmed's notes, 17 Oct 2026): forms in short titled parts, nicer cards, and the small notes that came with them."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from cashboxes.forms import CashboxOperationForm
from cashboxes.models import CashboxOperation
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from master_data.forms import CustomerForm, ItemForm, SupplierForm
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class FormSectionsTests(TestCase):
    def setUp(self):
        prepared_client(sub_activity="grocery")  # barcode on
        self.owner = person(RoleCode.OWNER, "an3_owner")
        self.client.force_login(self.owner)

    def page(self, name, *args, **params):
        return self.client.get(reverse(name, args=args), {"lang": "ar", **params}).content.decode()

    def test_the_customer_form_reads_in_titled_parts(self):
        form = CustomerForm(lang="ar")
        names = [part["key"] for part in form.sections]
        self.assertGreaterEqual(len(names), 2)
        placed = [field.name for part in form.sections for field in part["fields"]] + [field.name for field in form.checks]
        self.assertCountEqual(placed, [field.name for field in form.visible_fields()])  # no field is lost
        html = self.page("master_data:create", "customers")
        self.assertIn('class="hs-section', html)
        self.assertIn('name="name"', html)

    def test_balances_and_minimum_stock_are_optional_and_zero_when_blank(self):
        customer = CustomerForm(data={"name": "عميل جديد", "opening_balance": "", "credit_limit": ""}, lang="ar")
        self.assertFalse(customer.fields["opening_balance"].required)
        self.assertTrue(customer.is_valid(), customer.errors)
        self.assertEqual(customer.cleaned_data["opening_balance"], Decimal("0"))
        self.assertEqual(customer.cleaned_data["credit_limit"], Decimal("0"))
        supplier = SupplierForm(data={"name": "مورد جديد", "opening_balance": ""}, lang="ar")
        self.assertTrue(supplier.is_valid(), supplier.errors)
        self.assertEqual(supplier.cleaned_data["opening_balance"], Decimal("0"))
        self.assertFalse(ItemForm(lang="ar").fields["min_stock"].required)  # Ahmed: no star on the minimum

    def test_a_cash_movement_numbers_itself(self):
        cashbox = make_cashbox()
        form = CashboxOperationForm(data={"operation_type": "direct_in", "amount": "50", "operation_date": "2026-10-17",
                                          "destination_cashbox": cashbox.pk, "reason": "إيداع"}, lang="ar")
        self.assertEqual(form.fields["reference_number"].label, "رقم الحركة")
        self.assertFalse(form.fields["reference_number"].required)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data["reference_number"].startswith("CO-"))
        html = self.page("cashboxes:operation_create")
        self.assertIn("رقم الحركة", html)
        self.assertNotIn("رقم المرجع", html)
        self.assertIn("بيتعمل لوحده", html)
        self.assertEqual(CashboxOperation.objects.count(), 0)

    def test_the_reports_and_master_data_hubs_show_cards_with_icons(self):
        reports = self.page("report_hub")
        self.assertIn('class="hs-tile hs-tile--', reports)
        self.assertIn("#hs-i-customers", reports)
        self.assertNotIn("يظل تقرير الأرباح محميًا", reports)
        hub = self.page("master_data:hub")
        self.assertIn('class="hs-tile hs-tile--', hub)
        self.assertIn("hs-tile__count", hub)

    def test_the_hub_tiles_never_restyle_data_tables(self):
        # table_cards.js marks every data table "hs-cards" for its phone layout; a hub grid of that name broke them all.
        from django.contrib.staticfiles import finders

        css = open(finders.find("hesba/css/form_controls.css"), encoding="utf-8").read()
        self.assertNotIn(".hs-cards", css)
        self.assertNotIn(".hs-card{", css)

    def test_the_customer_card_calls_and_whatsapps_with_icons(self):
        customer = make_customer(phone="01001234567")
        card = self.page("parties:card", "customer", customer.pk)
        self.assertIn('href="tel:01001234567"', card)
        self.assertIn("data-party-call", card)
        self.assertIn("hs-contact--call", card)


class InvoiceBalanceTests(TestCase):
    def setUp(self):
        prepared_client()
        self.customer = make_customer(opening_balance=Decimal("80.00"))

    def balance(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("parties:balance", args=("customer", self.customer.pk)))

    def test_the_invoice_shows_the_balance_before_and_after(self):
        owner = person(RoleCode.OWNER, "an3_bal_owner")
        response = self.balance(owner)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Decimal(response.json()["balance"]), Decimal("80.00"))
        form = self.client.get(reverse("sales:create"), {"lang": "ar"}).content.decode()
        self.assertIn("data-party-balance", form)

    def test_only_who_writes_the_invoice_or_reads_the_ledger_sees_it(self):
        self.assertEqual(self.balance(person(RoleCode.STOCK_KEEPER, "an3_keeper")).status_code, 403)
        self.assertEqual(self.client.get(reverse("parties:balance", args=("nobody", self.customer.pk))).status_code, 404)


class StockLabelsTests(TestCase):
    def setUp(self):
        prepared_client(sub_activity="grocery")
        self.owner = person(RoleCode.OWNER, "an3_lbl_owner")
        self.client.force_login(self.owner)
        self.shelf = make_location(location_code="WH-1", name_ar="المخزن", is_default=True)
        self.oil = make_item(item_code="IT-OIL", item_name="زيت")
        self.rice = make_item(item_code="IT-RICE", item_name="رز")
        make_item(item_code="IT-NONE", item_name="صنف خلصان")
        stock_in(self.oil, self.shelf, 3, "10.00")
        stock_in(self.rice, self.shelf, Decimal("2.5"), "10.00")

    def test_the_inventory_report_prints_labels_for_what_is_on_the_shelf(self):
        report = self.client.get(reverse("reports:inventory"), {"lang": "ar"}).content.decode()
        self.assertIn("data-stock-labels", report)
        labels = self.client.get(reverse("barcode:labels"), {"lang": "ar", "stock": "1"}).content.decode()
        self.assertIn(f'name="copies_{self.oil.pk}" value="3"', labels)
        self.assertIn(f'name="copies_{self.rice.pk}" value="3"', labels)  # part of a unit still needs its label
        self.assertNotIn("صنف خلصان", labels)

    def test_without_the_inventory_report_permission_it_lists_nothing_extra(self):
        self.client.force_login(person(RoleCode.CASHIER, "an3_lbl_cashier"))
        response = self.client.get(reverse("barcode:labels"), {"lang": "ar", "stock": "1"})
        if response.status_code == 200:
            self.assertNotIn(f'name="copies_{self.oil.pk}" value="3"', response.content.decode())
