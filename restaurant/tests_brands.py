"""Restaurants R11 (HG-117): several brands from one kitchen.

The set-up of ``tests_split_pay`` (the main hall «HALL» with «TILL» and the Maadi branch «BR-HALL» with «BR-TILL», a «T1» in each,
the koshary made in the kitchen from rice and lentils at 60.00 a plate, a cola at 20.00, a tea at 15.00, the clock on Wednesday 11 Nov
2026 at noon in Cairo), VAT **off** so the figures are exact, plus a pizza at 90.00. One kitchen sells as «برجر هاوس» (the default;
its menu is the burgers and the drinks) and «بيتزا نايل» (the pizzas and the drinks).

* **no brand set up:** every page, ticket, check, kitchen card, receipt and report reads as before, with no brand word, and the
  order's brand stays empty (the one addition is the set-up link on the board's toolbar, not a word about brands on any piece);
* **brand A 2 orders, 300.00 net; brand B 1 order, 120.00:** the report's «حسب البراند» says so, the overall totals are the sums
  (3 orders, 420.00), and every plate came out of the same hall's stock (nothing at the branch's);
* a takeaway or a delivery takes the picked brand (the default when none is picked); dine-in always takes the default; a part of
  a bill keeps its bill's brand; a crafted brand (another branch's, switched off, not a number) refuses the order, nothing kept;
* the waiter's menu shows the brand's categories (the whole menu for a brand with none); the ticket, the kitchen card, the check
  and the receipt name the brand, the check and the receipt add its header line; the company stays the issuer;
* nothing is posted differently: the same bills with and without a brand give the same invoices, cash, journal and stock moves.
"""

import json
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import Client
from django.urls import reverse

from audit.models import AuditLog
from entities.current import working_in
from hesba_testing.factories import make_customer, make_item, stock_in
from inventory.models import StockMovement
from ledger.models import JournalLine
from ledger.projector import Projector
from master_data.models import Category
from permissions.models import RoleCode
from sales.models import CustomerLedgerEntry, SalesInvoice

from . import brands, callcentre, services
from .insights import period_report
from .models import Brand, Order, OrderKind, OrderStatus
from .tests import person
from .tests_split_pay import SplitBillSetup

BRAND_MARKERS = ("data-brand-pick", "data-order-brand", "data-ticket-brand", "data-check-brand", "data-print-brand", "data-r-brands", "data-card-brand",
                 "data-call-brand", "برجر هاوس", "بيتزا نايل", "Burger House", "حسب البراند", "By brand")


class BrandSetup(SplitBillSetup):
    def setUp(self):
        super().setUp()
        self.vat(False)
        self.cat_burger = Category.objects.create(category_code="C-BG", name_ar="برجر", name_en="Burgers")
        self.cat_pizza = Category.objects.create(category_code="C-PZ", name_ar="بيتزا", name_en="Pizzas")
        self.cat_drinks = Category.objects.create(category_code="C-DR", name_ar="مشروبات", name_en="Drinks")
        self.pizza = make_item(item_code="D-PIZ", item_name="بيتزا مارجريتا", default_sale_price="90.00", is_stock_tracked=False)
        for item, category in ((self.burger, self.cat_burger), (self.pizza, self.cat_pizza), (self.tea, self.cat_drinks)):
            item.category = category
            item.save(update_fields=["category"])
        self.manager = person(RoleCode.MANAGER, "brand_manager")
        self.cashier = person(RoleCode.CASHIER, "brand_cashier")
        self.waiter_user = person(RoleCode.WAITER, "brand_waiter")
        self.keeper = person(RoleCode.STOCK_KEEPER, "brand_keeper")

    # ---- helpers ------------------------------------------------------------------------------------------------

    def make_brands(self):
        """«برجر هاوس» (the default; burgers and drinks) and «بيتزا نايل» (pizzas and drinks), in the main entity."""

        self.bh = brands.save_brand({"name_ar": "برجر هاوس", "name_en": "Burger House", "header": "أحلى برجر في المعادي", "sort": "1",
                                     "categories": [self.cat_burger.pk, self.cat_drinks.pk]}, self.owner, entity=self.main)
        self.pn = brands.save_brand({"name_ar": "بيتزا نايل", "name_en": "Pizza Nile", "header": "بيتزا على نار هادية", "sort": "2",
                                     "categories": [self.cat_pizza.pk, self.cat_drinks.pk]}, self.owner, entity=self.main)

    def take(self, brand=None, plates=2, kind=OrderKind.TAKEAWAY, **extra):
        """A takeaway of ``plates`` koshary (60.00 each, made in the kitchen at payment), under ``brand`` (the default when none)."""

        order = services.open_order(self.owner, kind=kind, brand=brand, entity=self.main, **extra)
        services.add_item(order, self.koshary, self.owner, plates)
        return order

    def pay(self, order, **extra):
        extra.setdefault("tendered", D("10000"))
        return services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, **extra)[0]

    def enter(self, user=None, entity=None):
        self.work_in(self.main if entity is None else entity, user or self.owner)

    def page(self, name, *args, user=None, lang="ar", **query):
        self.enter(user)
        return self.client.get(reverse(name, args=args), {"lang": lang, **query})

    def fingerprint(self):
        Projector().rebuild()
        by_location = {}
        for location_id in StockMovement.objects.values_list("location_id", flat=True):
            by_location[location_id] = by_location.get(location_id, 0) + 1
        return {"invoices": SalesInvoice.objects.count(), "journal": JournalLine.objects.count(), "customer_ledger": CustomerLedgerEntry.objects.count(),
                "by_location": by_location, **{key: value for key, value in self.moved().items() if key in ("cash", "stock", "runs")}}


class NoBrandTests(BrandSetup):
    """With no brand set up, the floor reads exactly as before."""

    def test_no_brand_every_piece_is_as_before_and_the_order_has_no_brand(self):
        self.assertEqual(Brand.objects.count(), 0)
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main)
        dine = services.open_order(self.owner, table=self.a_t1)
        for one in (order, dine):
            services.add_item(one, self.koshary, self.owner, 2)
            ticket = services.send_to_kitchen(one, self.owner)
        self.assertEqual((order.brand, dine.brand), (None, None))
        audit = AuditLog.objects.filter(action="open_order").order_by("pk").first()
        self.assertNotIn("brand", audit.after_data)
        for lang in ("ar", "en"):
            pages = [self.page("restaurant:order", order.pk, lang=lang), self.page("restaurant:board", lang=lang), self.page("restaurant:ticket", ticket.pk, lang=lang),
                     self.page("restaurant:check", order.pk, lang=lang), self.page("restaurant:call", lang=lang, phone="01001234567"),
                     self.page("restaurant:report", lang=lang)]
            for page in pages:
                self.assertEqual(page.status_code, 200)
                for marker in BRAND_MARKERS:
                    self.assertNotContains(page, marker)
        feed = json.loads(self.page("restaurant:kds_feed").content)
        self.assertTrue(feed["tickets"])
        self.assertTrue(all("brand" not in card for card in feed["tickets"]))
        invoice = self.pay(order)
        receipt = self.client.get(f"/print/sales/{invoice.pk}/", {"lang": "ar", "format": "receipt"})
        self.assertEqual(receipt.status_code, 200)
        self.assertNotContains(receipt, "data-print-brand")
        order.refresh_from_db()
        self.assertIsNone(order.brand)

    def test_a_posted_brand_is_refused_when_no_brand_exists_and_nothing_is_kept(self):
        before = self.moved()
        with self.assertRaisesMessage(ValidationError, "البراند ده مش من براندات الفرع"):
            services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main, brand="1")
        self.assertEqual(self.moved(), before)

    def test_the_report_reads_as_before_without_the_section_and_with_one_row_of_no_brand(self):
        order = self.take()
        self.pay(order)
        from django.utils import timezone

        data = period_report(timezone.localdate(), timezone.localdate())
        self.assertEqual((data["totals"]["orders"], data["totals"]["sales"]), (1, D("120.00")))
        self.assertEqual([(row["brand"], row["orders"], row["sales"]) for row in data["brands"]], [(None, 1, D("120.00"))])
        page = self.page("restaurant:report")
        self.assertNotContains(page, "data-r-brands")

    def test_the_board_offers_the_set_up_link_and_no_picker(self):
        page = self.page("restaurant:board")
        self.assertContains(page, "data-brands-link")
        self.assertNotContains(page, "data-brand-pick")


class BrandSetupScreenTests(BrandSetup):
    def test_the_first_brand_is_the_default_and_a_second_is_not(self):
        self.make_brands()
        self.assertEqual([(brand.name_ar, brand.is_default) for brand in Brand.objects.order_by("sort")], [("برجر هاوس", True), ("بيتزا نايل", False)])
        self.assertEqual(brands.default_for(self.main), self.bh)
        self.assertEqual(sorted(self.bh.categories.values_list("category_code", flat=True)), ["C-BG", "C-DR"])

    def test_one_default_per_entity_the_database_and_the_service(self):
        self.make_brands()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Brand.objects.create(name_ar="تالت", entity=self.main, is_default=True)  # the database itself
        brands.save_brand({"name_ar": "بيتزا نايل", "is_default": "on"}, self.owner, self.pn)  # the service moves it
        self.assertEqual(list(Brand.objects.filter(entity=self.main, is_default=True)), [self.pn])
        self.assertEqual(brands.default_for(self.main), self.pn)
        other = brands.save_brand({"name_ar": "برجر هاوس", "name_en": "BH"}, self.owner, entity=self.branch)  # another branch: its own name, its own default
        self.assertTrue(other.is_default)
        self.assertEqual(Brand.objects.filter(is_default=True).count(), 2)

    def test_a_name_is_unique_within_its_branch_only(self):
        self.make_brands()
        with self.assertRaisesMessage(ValidationError, "فيه براند بنفس الاسم"):
            brands.save_brand({"name_ar": "برجر هاوس"}, self.owner, entity=self.main)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Brand.objects.create(name_ar="برجر هاوس", entity=self.main)
        brands.save_brand({"name_ar": "برجر هاوس"}, self.owner, entity=self.branch)
        with self.assertRaisesMessage(ValidationError, "اكتب اسم البراند"):
            brands.save_brand({"name_ar": "  "}, self.owner, entity=self.main)

    def test_the_default_cannot_be_switched_off_while_another_brand_is_active(self):
        self.make_brands()
        with self.assertRaisesMessage(ValidationError, "ده البراند الافتراضي"):
            brands.save_brand({"name_ar": "برجر هاوس", "active": False}, self.owner, self.bh)
        self.assertTrue(Brand.objects.get(pk=self.bh.pk).active)
        brands.save_brand({"name_ar": "بيتزا نايل", "is_default": "on"}, self.owner, self.pn)  # move the default, then the first may go
        brands.save_brand({"name_ar": "برجر هاوس", "active": False}, self.owner, self.bh)
        self.assertEqual((Brand.objects.get(pk=self.bh.pk).active, Brand.objects.get(pk=self.bh.pk).is_default), (False, False))

    def test_switching_off_the_only_active_brand_takes_the_branch_back_to_no_brand(self):
        brand = brands.save_brand({"name_ar": "برجر هاوس"}, self.owner, entity=self.main)
        self.assertEqual(services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main).brand, brand)
        brands.save_brand({"name_ar": "برجر هاوس", "active": False}, self.owner, brand)
        self.assertFalse(Brand.objects.get(pk=brand.pk).is_default)
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main)
        self.assertIsNone(order.brand)
        board = self.page("restaurant:board")
        self.assertNotContains(board, "data-brand-pick")
        self.assertNotContains(self.page("restaurant:ticket", self._ticket(order).pk), "data-ticket-brand")

    def _ticket(self, order):
        services.add_item(order, self.koshary, self.owner, 1)
        return services.send_to_kitchen(order, self.owner)

    def test_a_brand_with_orders_is_switched_off_never_deleted(self):
        self.make_brands()
        order = self.take(self.pn)
        with self.assertRaisesMessage(ValidationError, "البراند ده عليه طلبات"):
            brands.delete_brand(self.pn, self.owner)
        with self.assertRaises(ProtectedError):
            Brand.objects.get(pk=self.pn.pk).delete()  # the column protects it too
        self.assertTrue(Brand.objects.filter(pk=self.pn.pk).exists())
        order.refresh_from_db()
        self.assertEqual(order.brand, self.pn)
        brands.save_brand({"name_ar": "بيتزا نايل", "active": False}, self.owner, self.pn)  # switched off: the order keeps it
        self.assertEqual(Order.objects.get(pk=order.pk).brand, self.pn)
        spare = brands.save_brand({"name_ar": "تجربة"}, self.owner, entity=self.main)
        brands.delete_brand(spare, self.owner)
        self.assertFalse(Brand.objects.filter(pk=spare.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete_brand").exists())

    def test_the_default_with_other_brands_cannot_be_deleted(self):
        self.make_brands()
        with self.assertRaisesMessage(ValidationError, "ده البراند الافتراضي"):
            brands.delete_brand(self.bh, self.owner)

    def test_the_set_up_screen_lists_adds_edits_and_switches_off(self):
        self.enter(self.manager)
        page = self.client.get(reverse("restaurant:brands"), {"lang": "ar"})
        self.assertContains(page, "data-brand-form")
        self.assertContains(page, "لسه مفيش براندات")
        posted = self.client.post(reverse("restaurant:brands"), {"lang": "ar", "name_ar": "برجر هاوس", "name_en": "Burger House", "header": "سطر", "sort": "1", "active": "on",
                                                              "categories": [self.cat_burger.pk, self.cat_drinks.pk]}, follow=True)
        self.assertContains(posted, "اتحفظ البراند")
        self.assertContains(posted, 'data-brand-row="برجر هاوس"')
        brand = Brand.objects.get(name_ar="برجر هاوس")
        self.assertEqual((brand.entity, brand.is_default, brand.header), (self.main, True, "سطر"))
        edit = self.client.get(reverse("restaurant:brands"), {"lang": "ar", "edit": brand.pk})
        self.assertContains(edit, 'value="برجر هاوس"')
        self.assertContains(edit, f'value="{self.cat_burger.pk}" checked')
        self.client.post(reverse("restaurant:brands"), {"lang": "ar", "pk": brand.pk, "name_ar": "برجر هاوس", "sort": "1", "categories": [self.cat_burger.pk]})  # no «active»
        self.assertFalse(Brand.objects.get(pk=brand.pk).active)  # the only brand: it may be switched off
        english = self.client.get(reverse("restaurant:brands"), {"lang": "en"})
        self.assertContains(english, "Brands")
        self.assertContains(english, "Choose the categories the brand sells")
        self.assertTrue(AuditLog.objects.filter(action="create_brand").exists() and AuditLog.objects.filter(action="change_brand").exists())

    def test_the_screen_refuses_a_bad_save_with_a_message_and_keeps_nothing(self):
        self.make_brands()
        self.enter(self.manager)
        refused = self.client.post(reverse("restaurant:brands"), {"lang": "ar", "name_ar": "بيتزا نايل", "active": "on"})
        self.assertContains(refused, "فيه براند بنفس الاسم")
        refused = self.client.post(reverse("restaurant:brands"), {"lang": "en", "name_ar": "x", "sort": "abc", "active": "on"})
        self.assertContains(refused, "The order must be a number")
        self.assertEqual(Brand.objects.count(), 2)
        deleted = self.client.post(reverse("restaurant:brands"), {"lang": "ar", "pk": self.bh.pk, "action": "delete"})
        self.assertContains(deleted, "ده البراند الافتراضي")
        self.assertTrue(Brand.objects.filter(pk=self.bh.pk).exists())

    def test_deleting_a_row_while_another_brand_is_open_for_editing_deletes_the_row_clicked(self):
        """Codex: the row's delete form posts to the page's own address, which keeps ``?edit=`` of the brand being edited."""
        self.make_brands()
        open_one = brands.save_brand({"name_ar": "براند مفتوح"}, self.owner, entity=self.main)
        clicked = brands.save_brand({"name_ar": "براند اتداس عليه"}, self.owner, entity=self.main)
        self.enter(self.manager)
        self.client.post(f"{reverse('restaurant:brands')}?lang=ar&edit={open_one.pk}", {"lang": "ar", "pk": clicked.pk, "action": "delete"})
        self.assertFalse(Brand.objects.filter(pk=clicked.pk).exists())
        self.assertTrue(Brand.objects.filter(pk=open_one.pk).exists())

    def test_another_branchs_brand_is_a_404_on_the_screen_and_a_branch_manager_cannot_move_one(self):
        self.make_brands()
        theirs = brands.save_brand({"name_ar": "براند المعادي"}, self.owner, entity=self.branch)
        self.work_in(None)
        self.assertEqual(self.client.get(reverse("restaurant:brands"), {"edit": theirs.pk}).status_code, 200)  # the whole group sees every branch
        self.enter(self.manager_a, self.main)
        self.assertEqual(self.client.get(reverse("restaurant:brands"), {"edit": theirs.pk}).status_code, 404)
        self.assertEqual(self.client.post(reverse("restaurant:brands"), {"pk": theirs.pk, "name_ar": "X"}).status_code, 404)
        self.assertEqual(Brand.objects.get(pk=theirs.pk).name_ar, "براند المعادي")
        page = self.client.get(reverse("restaurant:brands"), {"lang": "ar"})
        self.assertContains(page, 'data-brand-row="برجر هاوس"')
        self.assertNotContains(page, "براند المعادي")
        with working_in(self.main):
            with self.assertRaisesMessage(ValidationError, "البراند ده مش من براندات الفرع"):
                brands.save_brand({"name_ar": "جديد"}, self.owner, entity=self.branch)

    def test_in_whole_group_mode_a_new_brand_asks_for_its_branch(self):
        self.work_in(None)
        page = self.client.get(reverse("restaurant:brands"), {"lang": "ar"})
        self.assertContains(page, 'name="entity"')
        refused = self.client.post(reverse("restaurant:brands"), {"lang": "ar", "name_ar": "برجر هاوس", "active": "on"})
        self.assertContains(refused, "اختار الفرع")
        self.assertEqual(Brand.objects.count(), 0)
        self.client.post(reverse("restaurant:brands"), {"lang": "ar", "name_ar": "برجر هاوس", "active": "on", "entity": self.branch.pk})
        self.assertEqual(Brand.objects.get().entity, self.branch)

    def test_permissions_a_stock_keeper_and_a_waiter_are_refused_a_cashier_may_see_and_a_manager_edits(self):
        self.make_brands()
        for user in (self.keeper, self.waiter_user):
            self.enter(user)
            self.assertEqual(self.client.get(reverse("restaurant:brands")).status_code, 403, user.username)
            self.assertEqual(self.client.post(reverse("restaurant:brands"), {"name_ar": "x"}).status_code, 403, user.username)
        self.enter(self.cashier)  # may see (the invoices' right), may not change
        seen = self.client.get(reverse("restaurant:brands"), {"lang": "ar"})
        self.assertEqual(seen.status_code, 200)
        self.assertNotContains(seen, "data-brand-form")
        self.assertEqual(self.client.post(reverse("restaurant:brands"), {"name_ar": "x"}).status_code, 403)
        self.assertEqual(Brand.objects.count(), 2)
        self.enter(self.manager)
        self.client.post(reverse("restaurant:brands"), {"lang": "ar", "name_ar": "كافيه", "active": "on"})
        self.assertTrue(Brand.objects.filter(name_ar="كافيه").exists())
        self.assertFalse(AuditLog.objects.filter(action="create_brand", actor=self.keeper).exists())


class BrandOnOrderTests(BrandSetup):
    def setUp(self):
        super().setUp()
        self.make_brands()

    def test_a_takeaway_takes_the_picked_brand_and_the_default_when_none_is_picked(self):
        self.assertEqual(services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main).brand, self.bh)
        self.assertEqual(services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main, brand=self.pn).brand, self.pn)
        self.assertEqual(services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main, brand=str(self.pn.pk)).brand, self.pn)
        delivery = services.open_order(self.owner, kind=OrderKind.DELIVERY, entity=self.main, address="12 ش النصر", brand=self.pn)
        self.assertEqual(delivery.brand, self.pn)
        audit = AuditLog.objects.filter(action="open_order", object_id=str(delivery.pk)).get()
        self.assertEqual(audit.after_data["brand"], self.pn.pk)

    def test_the_board_picker_defaults_to_the_default_brand_and_opens_the_picked_one(self):
        page = self.page("restaurant:board")
        self.assertContains(page, "data-brand-pick", count=2)  # the takeaway form and the delivery form
        self.assertContains(page, f'<option value="{self.bh.pk}" selected>برجر هاوس</option>', count=2)
        self.assertContains(page, f'<option value="{self.pn.pk}">بيتزا نايل</option>', count=2)
        self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "takeaway", "brand": self.pn.pk})
        self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "delivery", "address": "شارع 9", "brand": self.bh.pk})
        self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "takeaway"})  # nothing picked
        self.assertEqual([order.brand for order in Order.objects.order_by("pk")], [self.pn, self.bh, self.bh])
        english = self.page("restaurant:board", lang="en")
        self.assertContains(english, "Burger House")

    def test_dine_in_always_takes_the_default_brand(self):
        self.enter()
        dine = services.open_order(self.owner, table=self.a_t1, brand=self.pn)  # whatever is passed
        self.assertEqual(dine.brand, self.bh)
        self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "dine_in", "table": self.t2.pk, "brand": self.pn.pk})
        self.assertEqual(Order.objects.get(table=self.t2).brand, self.bh)
        self.assertEqual(services.open_order(self.owner, table=self.a_t1).pk, dine.pk)  # joining the open order changes nothing
        brands.save_brand({"name_ar": "بيتزا نايل", "is_default": "on"}, self.owner, self.pn)
        self.assertEqual(Order.objects.get(pk=dine.pk).brand, self.bh)  # an order keeps the brand it opened with
        self.assertEqual(services.open_order(self.owner, table=self.b_t1).brand, None)  # the branch has no brand of its own

    def test_a_branch_with_its_own_brand_uses_it_for_its_dine_in_and_its_takeaway(self):
        theirs = brands.save_brand({"name_ar": "كافيه المعادي"}, self.owner, entity=self.branch)
        self.assertEqual(services.open_order(self.owner, table=self.b_t1).brand, theirs)
        self.assertEqual(services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.branch).brand, theirs)
        self.assertEqual(services.open_order(self.owner, table=self.a_t1).brand, self.bh)

    def test_a_crafted_brand_another_branchs_or_switched_off_or_not_a_number_refuses_the_order_and_keeps_nothing(self):
        theirs = brands.save_brand({"name_ar": "كافيه المعادي"}, self.owner, entity=self.branch)
        off = brands.save_brand({"name_ar": "قديم"}, self.owner, entity=self.main)
        brands.save_brand({"name_ar": "قديم", "active": False}, self.owner, off)
        self.enter()
        before = self.moved()
        for bad in (theirs.pk, off.pk, "abc", "999999", "1; drop"):
            with self.assertRaisesMessage(ValidationError, "البراند ده مش من براندات الفرع أو مش شغال"):
                services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main, brand=bad)
            self.client.post(reverse("restaurant:board"), {"lang": "ar", "kind": "takeaway", "brand": bad})
            self.client.post(reverse("restaurant:board"), {"lang": "en", "kind": "delivery", "address": "x", "brand": bad})
        self.assertEqual(self.moved(), before)
        self.assertEqual(Order.objects.filter(brand__in=[theirs, off]).count(), 0)
        english = self.client.post(reverse("restaurant:board"), {"lang": "en", "kind": "takeaway", "brand": theirs.pk}, follow=True)
        self.assertContains(english, "This brand is not one of the branch")

    def test_a_part_keeps_its_bills_brand_and_a_merged_order_keeps_the_receiving_ones(self):
        order = services.open_order(self.owner, table=self.a_t1, guests=2)
        services.add_item(order, self.koshary, self.owner, 2)
        services.add_item(order, self.tea, self.owner, 2)
        brands.save_brand({"name_ar": "بيتزا نايل", "is_default": "on"}, self.owner, self.pn)  # the default moves after the order opened
        tea = order.lines.get(item=self.tea)
        part = services.split_order(order, {tea.pk: "2"}, self.owner)
        self.assertEqual((part.split_from, part.brand), (order, self.bh))
        services.put_back(part, self.owner)
        second = services.open_order(self.owner, table=self.t2, guests=1)
        services.add_item(second, self.tea, self.owner, 1)
        self.assertEqual(second.brand, self.pn)
        merged = services.merge_orders(order, second, self.owner)
        self.assertEqual(merged.brand, self.bh)
        self.assertEqual(Order.objects.get(pk=second.pk).brand, self.pn)  # the folded-away order keeps its own, as a record

    def test_the_order_page_names_the_brand_only_when_there_is_one(self):
        order = self.take(self.pn)
        self.assertContains(self.page("restaurant:order", order.pk), 'data-order-brand>بيتزا نايل<')
        self.assertContains(self.page("restaurant:order", order.pk, lang="en"), "Pizza Nile")

    def test_the_public_qr_page_is_left_as_it_is(self):
        guest = Client()
        for brand in (self.bh, self.pn):
            page = guest.get(reverse("table_menu", args=[self.a_t1.qr_key]))
            self.assertEqual(page.status_code, 200)
            for marker in BRAND_MARKERS:
                self.assertNotContains(page, marker)
        order = services.open_order(self.owner, table=self.a_t1)
        self.assertEqual(order.brand, self.bh)  # a table's order, however it is opened, takes the default


class MenuTests(BrandSetup):
    def setUp(self):
        super().setUp()
        self.make_brands()

    def dishes(self, order, lang="ar"):
        page = self.page("restaurant:order", order.pk, lang=lang)
        text = page.content.decode()
        import re

        return set(re.findall(r'data-dish="([^"]+)"', text)), set(re.findall(r'data-menu-cat="(\d+)"', text)), text

    def test_the_brands_categories_filter_the_waiters_menu(self):
        burger_order, pizza_order = self.take(self.bh), self.take(self.pn)
        dishes, cats, _ = self.dishes(burger_order)
        self.assertIn("D-BURG", dishes)
        self.assertIn("D-TEA", dishes)
        self.assertNotIn("D-PIZ", dishes)
        self.assertNotIn("D-KOSH", dishes)  # no category: not on a brand's menu that has some
        self.assertEqual(cats, {str(self.cat_burger.pk), str(self.cat_drinks.pk)})
        dishes, cats, _ = self.dishes(pizza_order)
        self.assertTrue({"D-PIZ", "D-TEA"} <= dishes)
        self.assertFalse({"D-BURG", "D-KOSH"} & dishes)
        self.assertEqual(cats, {str(self.cat_pizza.pk), str(self.cat_drinks.pk)})

    def test_a_brand_with_no_categories_shows_the_whole_menu(self):
        everything = brands.save_brand({"name_ar": "كل حاجة"}, self.owner, entity=self.main)
        dishes, _, _ = self.dishes(self.take(everything))
        self.assertTrue({"D-BURG", "D-PIZ", "D-TEA", "D-KOSH"} <= dishes)

    def test_a_category_holds_what_sits_under_it_and_an_order_without_a_brand_sees_everything(self):
        starters = Category.objects.create(category_code="C-BG-S", name_ar="سندوتشات", parent=self.cat_burger)
        sub = make_item(item_code="D-SUB", item_name="ساندوتش", default_sale_price="40.00", is_stock_tracked=False, category=starters)
        dishes, _, _ = self.dishes(self.take(self.bh))
        self.assertIn("D-SUB", dishes)
        plain = Order.objects.create(number="R-OLD-1", kind=OrderKind.TAKEAWAY, opened_by=self.owner, entity=self.main)  # opened before brands
        dishes, _, _ = self.dishes(plain)
        self.assertTrue({"D-BURG", "D-PIZ", "D-KOSH", "D-SUB"} <= dishes)

    def test_the_filter_is_the_screen_not_a_rule_a_dish_outside_the_menu_can_still_be_added(self):
        order = self.take(self.bh)
        line = services.add_item(order, self.pizza, self.owner, 1)  # «كرر» and a crafted post do this
        self.assertEqual(line.item, self.pizza)


class PiecesTests(BrandSetup):
    def setUp(self):
        super().setUp()
        self.make_brands()
        self.order = self.take(self.bh)
        self.ticket = services.send_to_kitchen(self.order, self.owner)

    def test_the_kitchen_ticket_names_the_brand_and_not_its_header(self):
        page = self.page("restaurant:ticket", self.ticket.pk)
        self.assertContains(page, 'data-ticket-brand>برجر هاوس<')
        self.assertNotContains(page, "أحلى برجر في المعادي")
        self.assertContains(self.page("restaurant:ticket", self.ticket.pk, lang="en"), 'data-ticket-brand>Burger House<')

    def test_the_kitchen_screen_card_carries_the_brand(self):
        card = json.loads(self.page("restaurant:kds_feed").content)["tickets"][0]
        self.assertEqual(card["brand"], "برجر هاوس")
        english = json.loads(self.page("restaurant:kds_feed", lang="en").content)["tickets"][0]
        self.assertEqual(english["brand"], "Burger House")
        screen = self.page("restaurant:kds")
        self.assertContains(screen, "kds.js?v=r11-1")
        script = open("static/hesba/js/kds.js", encoding="utf-8").read()
        self.assertIn("card.brand", script)
        self.assertIn("data-card-brand", script)

    def test_the_printed_check_names_the_brand_and_its_header(self):
        page = self.page("restaurant:check", self.order.pk)
        self.assertContains(page, 'data-check-brand>برجر هاوس<')
        self.assertContains(page, 'data-check-brand-header>أحلى برجر في المعادي<')
        english = self.page("restaurant:check", self.order.pk, lang="en")
        self.assertContains(english, "Burger House")
        brands.save_brand({"name_ar": "بيتزا نايل", "header": ""}, self.owner, self.pn)
        plain = self.page("restaurant:check", self.take(self.pn).pk)
        self.assertContains(plain, "data-check-brand>")
        self.assertNotContains(plain, "data-check-brand-header")

    def test_the_cashier_receipt_names_the_brand_and_the_company_stays_the_issuer(self):
        invoice = self.pay(self.order)
        self.enter()
        receipt = self.client.get(f"/print/sales/{invoice.pk}/", {"lang": "ar", "format": "receipt"})
        self.assertContains(receipt, 'data-print-brand')
        self.assertContains(receipt, "برجر هاوس")
        self.assertContains(receipt, "أحلى برجر في المعادي")
        from printing.company import company_details

        details = company_details()
        self.assertContains(receipt, f'<h1 class="pr-company__name">{details["name"]}</h1>')  # the issuer is the company
        english = self.client.get(f"/print/sales/{invoice.pk}/", {"lang": "en", "format": "a4"})
        self.assertContains(english, "Burger House")
        other = self.take(self.pn)
        second = self.pay(other)
        self.assertContains(self.client.get(f"/print/sales/{second.pk}/", {"lang": "ar"}), "بيتزا نايل")
        ordinary = SalesInvoice.objects.exclude(pk__in=[invoice.pk, second.pk]).first()
        if ordinary:
            self.assertNotContains(self.client.get(f"/print/sales/{ordinary.pk}/", {"lang": "ar"}), "data-print-brand")

    def test_the_invoice_itself_is_unchanged_by_the_brand(self):
        invoice = self.pay(self.order)
        self.assertEqual((invoice.total_amount, invoice.tax_amount, invoice.paid_now), (D("120.00"), D("0.00"), D("120.00")))
        for text in (invoice.notes, " ".join(line.description or "" for line in invoice.lines.all())):
            self.assertNotIn("برجر هاوس", text)
            self.assertNotIn("Burger House", text)

    def test_a_part_paid_on_its_own_invoice_prints_its_bills_brand(self):
        dine = services.open_order(self.owner, table=self.a_t1, guests=2)
        services.add_item(dine, self.tea, self.owner, 2)
        services.add_item(dine, self.koshary, self.owner, 1)
        part = services.split_order(dine, {dine.lines.get(item=self.tea).pk: "2"}, self.owner)
        invoice = self.pay(part)
        self.enter()
        self.assertContains(self.client.get(f"/print/sales/{invoice.pk}/", {"lang": "ar", "format": "receipt"}), "برجر هاوس")


class ReportTests(BrandSetup):
    def setUp(self):
        super().setUp()
        self.make_brands()
        from django.utils import timezone

        self.today = timezone.localdate()

    def three_orders(self):
        a1, a2, b1 = self.take(self.bh, 3), self.take(self.bh, 2), self.take(self.pn, 2)  # 180.00 + 120.00 | 120.00
        for order in (a1, a2, b1):
            self.pay(order)

    def test_brand_a_two_orders_300_00_and_brand_b_one_order_120_00(self):
        before = StockMovement.objects.count()
        self.three_orders()
        data = period_report(self.today, self.today)
        self.assertEqual([(row["brand"], row["orders"], row["sales"], row["average"]) for row in data["brands"]],
                         [(self.bh, 2, D("300.00"), D("150.00")), (self.pn, 1, D("120.00"), D("120.00"))])
        self.assertEqual((data["totals"]["orders"], data["totals"]["sales"]), (3, D("420.00")))  # the overall figures are the sums
        self.assertEqual(sum(row["sales"] for row in data["brands"]), data["totals"]["sales"])
        self.assertEqual(sum(row["orders"] for row in data["brands"]), data["totals"]["orders"])
        page = self.page("restaurant:report")
        self.assertContains(page, "حسب البراند")
        self.assertContains(page, f'data-r-brand="{self.bh.pk}"')
        self.assertContains(page, "300.00")
        english = self.page("restaurant:report", lang="en")
        self.assertContains(english, "By brand")
        self.assertContains(english, "Pizza Nile")
        # one kitchen: every plate came from the main hall's stock, none from the branch's
        moves = StockMovement.objects.order_by("pk")
        self.assertGreater(moves.count(), before)
        new = list(moves[before:])
        self.assertTrue(new)
        self.assertEqual({move.location_id for move in new}, {self.location.pk})
        self.assertEqual(StockMovement.objects.filter(location=self.b_location, pk__in=[move.pk for move in new]).count(), 0)

    def test_both_brands_use_the_same_stock_the_same_rice_per_plate(self):
        rice_before = self.stock(self.rice, self.location)
        self.pay(self.take(self.bh, 2))
        after_a = self.stock(self.rice, self.location)
        self.pay(self.take(self.pn, 2))
        after_b = self.stock(self.rice, self.location)
        self.assertEqual(rice_before - after_a, D("0.400"))  # 2 plates × 0.200 kg
        self.assertEqual(after_a - after_b, D("0.400"))  # the other brand's 2 plates: the same
        self.assertEqual(self.stock(self.rice, self.b_location), D("10.000"))

    def test_an_order_from_before_the_brands_is_its_own_row_never_moved_to_the_default(self):
        old = Order.objects.create(number="R-OLD-2", kind=OrderKind.TAKEAWAY, opened_by=self.owner, entity=self.main)
        services.add_item(old, self.koshary, self.owner, 2)
        self.pay(old)
        self.pay(self.take(self.bh, 2))
        data = period_report(self.today, self.today)
        self.assertEqual([(row["brand"], row["orders"], row["sales"]) for row in data["brands"]], [(self.bh, 1, D("120.00")), (None, 1, D("120.00"))])
        self.assertContains(self.page("restaurant:report"), "من غير براند")
        self.assertContains(self.page("restaurant:report", lang="en"), "No brand")

    def test_a_part_counts_in_its_parents_order_and_brand(self):
        dine = services.open_order(self.owner, table=self.a_t1, guests=2)
        services.add_item(dine, self.koshary, self.owner, 2)
        services.add_item(dine, self.tea, self.owner, 2)
        part = services.split_order(dine, {dine.lines.get(item=self.tea).pk: "2"}, self.owner)
        brands.save_brand({"name_ar": "بيتزا نايل", "is_default": "on"}, self.owner, self.pn)
        self.pay(part)
        self.pay(dine)
        data = period_report(self.today, self.today)
        self.assertEqual([(row["brand"], row["orders"], row["sales"]) for row in data["brands"]], [(self.bh, 1, D("150.00"))])  # the bill once: 120.00 + 30.00
        self.assertEqual((data["totals"]["orders"], data["totals"]["sales"]), (1, D("150.00")))

    def test_the_section_is_there_once_a_brand_was_ever_set_up_even_when_all_are_off(self):
        self.pay(self.take(self.bh, 2))
        brands.save_brand({"name_ar": "بيتزا نايل", "active": False}, self.owner, self.pn)
        brands.save_brand({"name_ar": "برجر هاوس", "active": False}, self.owner, self.bh)  # the last active brand: allowed, the branch is back to no brand
        self.assertEqual(Brand.objects.filter(active=True).count(), 0)
        self.assertContains(self.page("restaurant:report"), "data-r-brands")

    def test_the_report_in_one_branch_counts_only_that_branchs_brands(self):
        theirs = brands.save_brand({"name_ar": "كافيه المعادي"}, self.owner, entity=self.branch)
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.branch)
        self.assertEqual(order.brand, theirs)
        services.add_item(order, self.cola, self.owner, 2)  # 40.00 from the branch's stock
        services.pay(Order.objects.get(pk=order.pk), self.manager_b, cashbox=self.b_cashbox, tendered=D("100"))
        self.pay(self.take(self.bh, 2))
        self.work_in(self.branch, self.manager_b)
        with working_in(self.branch):
            data = period_report(self.today, self.today)
        self.assertEqual([(row["brand"], row["sales"]) for row in data["brands"]], [(theirs, D("40.00"))])
        with working_in(self.main):
            data = period_report(self.today, self.today)
        self.assertEqual([(row["brand"], row["sales"]) for row in data["brands"]], [(self.bh, D("120.00"))])


class NothingPostsDifferentlyTests(BrandSetup):
    def bills(self, brand):
        invoices = []
        for plates, cola in ((3, 1), (2, 0)):
            order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, entity=self.main, brand=brand)
            services.add_item(order, self.koshary, self.owner, plates)
            if cola:
                services.add_item(order, self.cola, self.owner, cola)
            invoices.append(self.pay(order))
        return invoices

    def figures(self, invoices):
        return [(invoice.subtotal, invoice.tax_amount, invoice.total_amount, invoice.paid_now,
                 [(line.item.item_code, line.quantity, line.unit_sale_price, line.line_total_amount) for line in invoice.lines.order_by("line_number")]) for invoice in invoices]

    def delta(self, before, after):
        return {key: (after[key] - before[key]) if isinstance(after[key], int) else {loc: after[key].get(loc, 0) - before[key].get(loc, 0) for loc in after[key]}
                for key in ("invoices", "journal", "customer_ledger", "by_location", "cash", "stock", "runs")}

    def test_the_same_bills_with_and_without_a_brand_post_exactly_the_same(self):
        self.vat(True)  # VAT on: the tax is the company's registration, one for every brand
        start = self.fingerprint()
        plain = self.bills(None)
        middle = self.fingerprint()
        self.make_brands()
        branded = self.bills(self.pn)
        end = self.fingerprint()
        self.assertEqual([order.brand for order in Order.objects.order_by("pk")], [None, None, self.pn, self.pn])
        self.assertEqual(self.figures(plain), self.figures(branded))
        self.assertEqual(self.delta(start, middle), self.delta(middle, end))  # invoices, journal, ledger, cash, stock moves by location, runs
        self.assertEqual(self.delta(start, middle)["by_location"].get(self.b_location.pk, 0), 0)
        self.assertGreater(plain[0].tax_amount, D("0.00"))

    def test_the_brand_is_never_written_into_a_posted_document(self):
        self.make_brands()
        invoice = self.bills(self.pn)[0]
        self.assertNotIn("نايل", invoice.notes or "")
        from ledger.models import JournalEntry

        for entry in JournalEntry.objects.all():
            self.assertNotIn("نايل", str(getattr(entry, "memo", "") or "") + str(getattr(entry, "description", "") or ""))


class CallScreenTests(BrandSetup):
    def setUp(self):
        super().setUp()
        self.make_brands()
        self.sara = make_customer(customer_code="C-SARA", name="سارة أحمد", phone="01001234567")
        self.home = callcentre.save_address(self.sara, {"label": "البيت", "address": "12 شارع النصر"}, self.owner)
        self.work = callcentre.save_address(self.sara, {"label": "الشغل", "address": "5 شارع التحرير"}, self.owner)

    def act(self, action, user=None, lang="ar", **data):
        self.enter(user)
        return self.client.post(reverse("restaurant:call"), {"lang": lang, "action": action, **data}, follow=True)

    def test_the_call_screen_offers_the_brand_picker_on_each_open_form_defaulting_to_the_default(self):
        page = self.page("restaurant:call", phone="01001234567")
        self.assertContains(page, "data-call-brand", count=2)  # one on each saved address's open form
        self.assertContains(page, f'<option value="{self.bh.pk}" selected>برجر هاوس</option>', count=2)
        self.assertContains(page, "البراند")
        english = self.page("restaurant:call", phone="01001234567", lang="en")
        self.assertContains(english, "Brand")
        typed = self.page("restaurant:call", phone="01555555555", user=self.cashier)
        self.assertContains(typed, "data-typed-delivery-form")
        self.assertContains(typed, "data-call-brand")

    def test_open_the_order_with_the_chosen_brand_or_the_default(self):
        self.act("open", customer=self.sara.pk, address=self.home.pk, brand=self.pn.pk)
        self.act("open", customer=self.sara.pk, address=self.work.pk)
        self.act("open_typed", user=self.cashier, phone="01555555555", address="شارع جديد", brand=self.pn.pk)
        orders = list(Order.objects.order_by("pk"))
        self.assertEqual([(order.kind, order.brand) for order in orders], [(OrderKind.DELIVERY, self.pn), (OrderKind.DELIVERY, self.bh), (OrderKind.DELIVERY, self.pn)])
        self.assertContains(self.page("restaurant:order", orders[0].pk), "بيتزا نايل")

    def test_repeat_offers_the_past_orders_brand_and_opens_with_the_chosen_one(self):
        past = self.take(self.pn, 2, customer=self.sara)
        past.kind = OrderKind.DELIVERY
        past.address = "12 شارع النصر"
        past.save(update_fields=["kind", "address"])
        self.pay(past)
        page = self.page("restaurant:call", phone="01001234567")
        self.assertContains(page, f'<option value="{self.pn.pk}" selected>بيتزا نايل</option>')  # the repeat form: the brand it was sold under
        self.act("repeat", customer=self.sara.pk, order=past.pk, address=self.home.pk, brand=self.pn.pk)
        again = Order.objects.order_by("-pk").first()
        self.assertEqual((again.brand, again.kind, again.status), (self.pn, OrderKind.DELIVERY, OrderStatus.OPEN))
        self.assertTrue(again.lines.exists())
        self.act("repeat", customer=self.sara.pk, order=past.pk, address=self.home.pk, brand=self.bh.pk)  # repeated under the other brand
        self.assertEqual(Order.objects.order_by("-pk").first().brand, self.bh)

    def test_a_crafted_brand_on_the_call_screen_opens_nothing(self):
        theirs = brands.save_brand({"name_ar": "كافيه المعادي"}, self.owner, entity=self.branch)
        past = self.take(self.bh, 2, customer=self.sara)
        self.pay(past)
        before = self.moved()
        for bad in (theirs.pk, "abc"):
            response = self.act("open", customer=self.sara.pk, address=self.home.pk, brand=bad)
            self.assertContains(response, "البراند ده مش من براندات الفرع")
            self.act("repeat", customer=self.sara.pk, order=past.pk, address=self.home.pk, brand=bad)
            self.act("open_typed", user=self.cashier, phone="01555555555", address="شارع", brand=bad)
        self.assertEqual(self.moved(), before)


class MigrationAndSchemaTests(BrandSetup):
    def test_the_brand_column_is_empty_by_default_and_protects_its_brand(self):
        order = Order.objects.create(number="R-X-1", opened_by=self.owner, entity=self.main)
        self.assertIsNone(order.brand)
        self.make_brands()
        order.brand = self.bh
        order.save(update_fields=["brand"])
        with self.assertRaises(ProtectedError):
            self.bh.delete()
        self.assertEqual(list(self.bh.orders.values_list("pk", flat=True)), [order.pk])

    def test_the_migration_depends_on_the_call_centre_and_adds_one_table_and_one_column(self):
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(None, ignore_no_migrations=True)
        migration = loader.graph.nodes[("restaurant", "0011_brands")]
        self.assertIn(("restaurant", "0010_callcentre"), migration.dependencies)
        self.assertEqual([type(operation).__name__ for operation in migration.operations], ["CreateModel", "AddField", "AddConstraint", "AddConstraint"])
