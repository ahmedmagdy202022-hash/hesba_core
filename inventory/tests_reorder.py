"""REORDER-001: suggestions from stock, minimum and recent sales pace; the draft is prefilled, never posted."""

from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_item, make_location, make_seeded_role, make_stock_movement, make_supplier, make_user, make_user_profile, stock_in
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice, PurchaseLine
from reports.tests_dashboard import prepared_client

from .models import StockMovementType
from .reorder import suggestions


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class ReorderTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "reorder_owner")
        self.location = make_location(location_code="SHOP", is_default=True, is_receiving_location=True)
        self.fast = make_item(item_code="FAST", item_name="Fast seller", default_purchase_price=D("10.00"))
        self.slow = make_item(item_code="SLOW", item_name="Slow seller", default_purchase_price=D("5.00"))
        self.low = make_item(item_code="LOW", item_name="Below minimum", min_stock=D("20"), default_purchase_price=D("7.00"))
        stock_in(self.fast, self.location, 100, "10.00", movement_date=TODAY - timedelta(days=40))
        stock_in(self.slow, self.location, 100, "5.00", movement_date=TODAY - timedelta(days=40))
        stock_in(self.low, self.location, 15, "7.00", movement_date=TODAY - timedelta(days=40))
        # FAST: 90 sold, 6 returned in the last 30 days -> 84 net = 2.8 a day, 16 left.
        make_stock_movement(self.fast, self.location, StockMovementType.SALE_OUT, 90, "10.00", movement_date=TODAY - timedelta(days=5))
        make_stock_movement(self.fast, self.location, StockMovementType.SALE_RETURN_IN, 6, "10.00", movement_date=TODAY - timedelta(days=4))
        make_stock_movement(self.slow, self.location, StockMovementType.SALE_OUT, 3, "5.00", movement_date=TODAY - timedelta(days=5))

    def rows(self, **kwargs):
        return {row["item"].item_code: row for row in suggestions(**kwargs)}

    def test_pace_minimum_and_quantities(self):
        rows = self.rows(window=30, target=14)
        self.assertEqual(set(rows), {"FAST", "LOW"})  # SLOW has 97 left at 0.1 a day
        fast = rows["FAST"]
        self.assertEqual((fast["on_hand"], fast["daily"], fast["cover"]), (D("16"), D("2.80"), D("5.7")))
        self.assertEqual(fast["suggested"], D("24"))  # 2.8 x 14 = 39.2 - 16 = 23.2 -> 24
        low = rows["LOW"]
        self.assertTrue(low["below_minimum"])
        self.assertEqual(low["suggested"], D("5"))  # back up to the minimum of 20
        self.assertEqual(self.rows(window=30, target=7)["FAST"]["suggested"], D("4"))  # 19.6 - 16 -> 4

    def test_last_supplier_and_price_come_from_the_latest_posted_purchase(self):
        nile = make_supplier(supplier_code="NILE", name="Nile Foods")
        old = make_supplier(supplier_code="OLD", name="Old Supplier")
        for number, supplier, date, price, status in (("P1", old, TODAY - timedelta(days=60), "9.00", "posted"), ("P2", nile, TODAY - timedelta(days=20), "11.50", "posted"),
                                                      ("P3", old, TODAY - timedelta(days=2), "8.00", "draft")):
            invoice = PurchaseInvoice.objects.create(invoice_number=number, invoice_date=date, supplier=supplier, receiving_location=self.location, status=status)
            PurchaseLine.objects.create(invoice=invoice, line_number=1, item=self.fast, quantity=D("1"), unit_purchase_price=D(price), line_total_amount=D(price))
        fast = self.rows()["FAST"]
        self.assertEqual((fast["supplier"], fast["last_price"]), (nile, D("11.50")))

    def test_screen_groups_by_supplier_and_prefills_a_purchase_draft(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("inventory:stock")), reverse("inventory:reorder"))
        page = self.client.get(reverse("inventory:reorder"))
        self.assertContains(page, 'data-reorder-row="FAST"')
        draft_url = page.context["groups"][0]["draft_url"]
        self.assertIn("reorder_item=", draft_url)
        form = self.client.get(draft_url)
        initial = [f.initial for f in form.context["line_formset"].forms if f.initial]
        self.assertEqual({(row["item"], row["quantity"]) for row in initial}, {(self.fast.pk, D("24")), (self.low.pk, D("5"))})
        self.assertFalse(PurchaseInvoice.objects.exists())  # nothing is created until the owner saves
        tampered = self.client.get(reverse("purchases:create"), {"reorder_item": ["999999", "abc"], "reorder_qty": ["5", "x"], "reorder_price": ["1", "1"]})
        self.assertFalse([f.initial for f in tampered.context["line_formset"].forms if f.initial])

    def test_permissions_and_english(self):
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "reorder_keeper"))
        page = self.client.get(reverse("inventory:reorder"), {"lang": "en"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Buy soon")
        self.assertNotContains(page, "Last price")  # no cost for someone without inventory.view_cost
        self.assertContains(page, "<td>5.7</td>")     # days left, never a locale decimal comma
        from permissions.services import user_has_permission

        keeper = page.wsgi_request.user
        self.assertEqual("data-reorder-draft" in page.content.decode(), user_has_permission(keeper, "purchases.create_purchase_invoice"))
