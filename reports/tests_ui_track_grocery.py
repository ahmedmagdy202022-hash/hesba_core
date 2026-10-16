"""UI-TRACK commercial (supermarket): every button of every screen reviewed; these are the fixes.

Each test names the finding it covers (G01…G08 on the track page).
"""

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from batches.services import register_batch
from hesba_testing.factories import make_item, make_location, make_seeded_role, make_stock_movement, make_user, make_user_profile, stock_in
from inventory.models import StockMovementType
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled


TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class GroceryScreensTests(TestCase):
    def setUp(self):
        self.profile = prepared_client(sub_activity="grocery")  # units, barcode, batches & expiry suggested -> on
        self.owner = person(RoleCode.OWNER, "gr_owner")
        self.client.force_login(self.owner)
        self.shelf = make_location(location_code="WH-1", name_ar="المخزن الرئيسي", name_en="Main store", is_default=True, is_receiving_location=True)
        self.oil = make_item(item_code="IT-0007", item_name="زيت عباد الشمس", default_sale_price=Decimal("75.00"))

    def page(self, name, *args, **params):
        return self.client.get(reverse(name, args=args), {"lang": "ar", **params}).content.decode()

    # G01
    def test_the_item_form_asks_for_size_and_colour_only_where_they_are_sold(self):
        new_item = self.page("master_data:create", "items")
        self.assertNotIn('name="size"', new_item)
        self.assertNotIn('name="color"', new_item)
        sized = make_item(item_code="IT-SZ", item_name="قديم بمقاس", size="كبير")
        kept = self.page("master_data:edit", "items", sized.pk)
        self.assertIn('name="size"', kept)  # a value already typed is never hidden away
        self.assertNotIn('name="color"', kept)
        set_capability_enabled(self.profile, "variants", True)
        both = self.page("master_data:create", "items")
        self.assertIn('name="size"', both)
        self.assertIn('name="color"', both)

    # G02
    def test_the_item_card_lists_the_batches_still_on_the_shelf(self):
        stock_in(self.oil, self.shelf, 30, "50.00", movement_date=TODAY - timedelta(days=10))
        for batch_no, days, quantity in (("LOT-SOON", 20, "10"), ("LOT-LATE", 200, "20")):
            register_batch(item=self.oil, location=self.shelf, batch_no=batch_no, expiry_date=TODAY + timedelta(days=days),
                           quantity=Decimal(quantity), received_on=TODAY - timedelta(days=10), user=self.owner)
        make_stock_movement(self.oil, self.shelf, StockMovementType.SALE_OUT, 10, "50.00", movement_date=TODAY)  # the soonest batch is sold out
        card = self.page("inventory:item_detail", self.oil.pk)
        self.assertIn("data-item-batches", card)
        self.assertIn("LOT-LATE", card)
        self.assertNotIn("LOT-SOON", card)
        self.assertIn("المخزن الرئيسي", card)
        set_capability_enabled(self.profile, "batches_expiry", False)
        self.assertNotIn("data-item-batches", self.page("inventory:item_detail", self.oil.pk))

    # G03
    def test_the_batch_screen_names_the_warehouse(self):
        stock_in(self.oil, self.shelf, 5, "50.00", movement_date=TODAY - timedelta(days=3))
        register_batch(item=self.oil, location=self.shelf, batch_no="LOT-1", expiry_date=TODAY + timedelta(days=90),
                       quantity=Decimal("5"), received_on=TODAY - timedelta(days=3), user=self.owner)
        batches = self.page("batches:index")
        self.assertIn("<td>المخزن الرئيسي</td>", batches)
        self.assertNotIn("<td>WH-1</td>", batches)
        self.assertIn("<td>Main store</td>", self.page("batches:index", lang="en"))

    # G04
    def test_the_units_table_is_marked_to_fit_its_inputs(self):
        self.assertIn("data-units-table", self.page("units:item", self.oil.pk))

    # G05
    def test_an_invoice_line_starts_with_the_item_name(self):
        for screen in ("sales:create", "purchases:create"):
            self.assertIn(">زيت عباد الشمس · IT-0007<", self.page(screen), screen)

    # G08
    def test_batch_and_expiry_on_a_purchase_line_are_shown_open(self):
        form = self.page("purchases:create")
        self.assertIn('name="lines-0-expiry_date"', form)
        self.assertIn('<details class="inv-line-more" open>', form)
        set_capability_enabled(self.profile, "batches_expiry", False)
        self.assertNotIn('<details class="inv-line-more" open>', self.page("purchases:create"))
