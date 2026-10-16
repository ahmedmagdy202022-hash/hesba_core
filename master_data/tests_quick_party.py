"""FB2-QUICKPARTY: a new supplier, customer or warehouse from inside the invoice, without leaving it."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from master_data.models import Customer, Location, Supplier
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class QuickPartyTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "qp_owner")
        self.client.force_login(self.owner)

    def url(self, kind):
        from master_data.quick import URL_NAMES

        return reverse(URL_NAMES[kind])

    def test_the_invoice_lists_offer_a_new_one(self):
        purchase = self.client.get(reverse("purchases:create")).content.decode()
        self.assertIn('data-quick-add="+ مورد جديد…"', purchase)
        self.assertIn('data-quick-add="+ مخزن جديد…"', purchase)
        self.assertIn(self.url("supplier"), purchase)
        sale = self.client.get(reverse("sales:create")).content.decode()
        self.assertIn('data-quick-add="+ عميل جديد…"', sale)
        self.assertIn(self.url("location") + "?use=selling", sale)
        self.assertIn(self.url("location") + "?use=receiving", purchase)

    def test_each_kind_is_made_with_its_automatic_code_and_handed_back(self):
        supplier = self.client.post(self.url("supplier"), {"name": "مصنع النور"}).json()
        made = Supplier.objects.get(pk=supplier["id"])
        self.assertTrue(made.supplier_code.startswith("S-") and made.active)
        self.assertEqual(supplier["label"], str(made))
        customer = Customer.objects.get(pk=self.client.post(self.url("customer"), {"name": "أم أحمد"}).json()["id"])
        self.assertTrue(customer.customer_code.startswith("C-"))
        place = Location.objects.get(pk=self.client.post(self.url("location"), {"name": "مخزن العبور"}).json()["id"])
        self.assertTrue(place.location_code.startswith("WH-"))
        self.assertTrue(place.is_receiving_location and place.is_selling_location and place.active)
        self.assertIsNotNone(place.entity_id)

    def test_the_same_name_twice_gives_the_same_record(self):
        first = self.client.post(self.url("supplier"), {"name": "مصنع النور"}).json()["id"]
        Supplier.objects.filter(pk=first).update(active=False)
        again = self.client.post(self.url("supplier"), {"name": "مصنع  النور".replace("  ", " ")}).json()
        self.assertEqual(again["id"], first)
        self.assertTrue(Supplier.objects.get(pk=first).active)  # brought back, not doubled
        self.assertEqual(Supplier.objects.filter(name="مصنع النور").count(), 1)

    def test_a_same_name_warehouse_is_made_usable_where_it_was_picked(self):
        # Codex #230: a receiving-only warehouse reused from a sales invoice would fail on save.
        place = Location.objects.create(location_code="WH-RCV", name_ar="مخزن الاستلام", is_receiving_location=True, is_selling_location=False)
        answer = self.client.post(self.url("location") + "?use=selling", {"name": "مخزن الاستلام"}).json()
        self.assertEqual(answer["id"], place.pk)
        place.refresh_from_db()
        self.assertTrue(place.is_selling_location and place.is_receiving_location)
        from sales.forms import SalesDraftForm

        self.assertIn(place, SalesDraftForm().fields["selling_location"].queryset)

    def test_a_switched_off_module_closes_its_quick_add_too(self):
        # Codex #230: the module gate must cover the endpoint, not only hide the choice.
        from settings_core.models import ClientProfile
        from settings_core.setup_services import set_module_enabled

        set_module_enabled(ClientProfile.get_active(), "suppliers", False, user=self.owner)
        response = self.client.post(self.url("supplier"), {"name": "مورد ممنوع"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Supplier.objects.filter(name="مورد ممنوع").exists())
        self.assertNotIn(self.url("supplier"), self.client.get(reverse("purchases:create")).content.decode())

    def test_no_name_unknown_kind_and_get_are_refused(self):
        self.assertEqual(self.client.post(self.url("supplier"), {"name": "  "}).status_code, 400)
        self.assertEqual(self.client.get(self.url("supplier")).status_code, 405)

    def test_only_who_may_add_one_is_offered_it_or_can_make_it(self):
        cashier = person(RoleCode.CASHIER, "qp_cashier")
        self.client.force_login(cashier)
        answer = self.client.post(self.url("supplier"), {"name": "مورد"})
        self.assertEqual(answer.status_code, 403)
        self.assertFalse(Supplier.objects.filter(name="مورد").exists())
        page = self.client.get(reverse("sales:create"))
        self.assertEqual(page.status_code, 200)  # the cashier sells…
        self.assertNotIn(self.url("customer"), page.content.decode())  # …but is not offered a new customer
