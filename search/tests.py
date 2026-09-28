"""SEARCH-001: one box finds records the user may open, grouped, with permissions and modules respected."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_customer, make_item, make_seeded_role, make_supplier, make_user, make_user_profile, posted_invoice_ready
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.models import ClientProfile
from settings_core.setup_services import set_module_enabled

from .services import search


SEARCH = reverse("search:results")


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class SearchTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "search_owner")
        self.karim = make_customer(customer_code="C-KARIM", name="Karim Hassan", phone="0100 123 4567")
        make_supplier(supplier_code="S-NILE", name="Nile Foods", phone="0225551111")
        self.rice = make_item(item_code="RICE-5", item_name="Rice 5kg", barcode="6221111111111")

    def keys(self, user, query):
        return {group["key"]: [row["label"] for row in group["rows"]] for group in search(user, query)}

    def test_finds_by_name_code_phone_barcode_and_invoice_number(self):
        self.assertEqual(self.keys(self.owner, "karim")["customers"], ["Karim Hassan"])
        self.assertEqual(self.keys(self.owner, "1234567")["customers"], ["Karim Hassan"])  # phone digits, spaces ignored
        self.assertEqual(self.keys(self.owner, "6221111111111")["items"], ["Rice 5kg"])
        self.assertEqual(self.keys(self.owner, "nile")["suppliers"], ["Nile Foods"])
        invoice, *_ = posted_invoice_ready()
        self.assertIn(invoice.invoice_number, self.keys(self.owner, invoice.invoice_number)["sales"])
        self.assertEqual(search(self.owner, "k"), [])  # too short

    def test_links_go_to_the_card_and_a_single_hit_opens_directly(self):
        groups = search(self.owner, "karim")
        self.assertEqual(groups[0]["rows"][0]["url"], reverse("parties:card", args=["customer", self.karim.pk]))
        self.client.force_login(self.owner)
        response = self.client.get(SEARCH, {"q": "karim", "go": "1"})
        self.assertRedirects(response, f"{reverse('parties:card', args=['customer', self.karim.pk])}?lang=ar", fetch_redirect_response=False)
        page = self.client.get(SEARCH, {"q": "karim"})
        self.assertContains(page, 'data-search-group="customers"')
        self.assertContains(self.client.get(SEARCH, {"q": "zzzz"}), "data-search-empty")
        self.assertContains(self.client.get(reverse("sales:list")), "data-shell-search")

    def test_permissions_and_switched_off_modules_hide_groups(self):
        from purchases.models import PurchaseInvoice
        from hesba_testing.factories import make_location

        PurchaseInvoice.objects.create(invoice_number="PI-NILE-1", invoice_date="2026-01-15", supplier=make_supplier(supplier_code="S-NILE"), receiving_location=make_location())
        self.assertIn("purchases", self.keys(self.owner, "PI-NILE"))
        cashier = person(RoleCode.CASHIER, "search_cashier")
        self.assertNotIn("purchases", self.keys(cashier, "PI-NILE"))
        keeper = person(RoleCode.STOCK_KEEPER, "search_keeper")
        invoice, *_ = posted_invoice_ready()
        self.assertIn("sales", self.keys(self.owner, invoice.invoice_number))
        self.assertNotIn("sales", self.keys(keeper, invoice.invoice_number))
        set_module_enabled(ClientProfile.get_active(), "suppliers", False, self.owner)
        self.assertNotIn("suppliers", self.keys(self.owner, "nile"))
