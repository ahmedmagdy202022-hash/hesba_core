"""STOCKTAKE-001: a count posts the differences as ordinary stock adjustments."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from closing.models import Period, PeriodStatus
from hesba_testing.factories import make_cashbox, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client, sell

from .models import StockMovement, StockMovementType, StockOperation
from .services import get_item_location_stock_quantity, get_item_stock_value, recalculate_item_average_cost
from .stocktake import next_count_number, post_stock_count, variance_totals, variances


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class StockCountServiceTests(TestCase):
    def setUp(self):
        self.period = Period.objects.create(period_code="ST", name="st", start_date=TODAY - timedelta(days=40), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "st_owner")
        self.shop = make_location(location_code="SHOP")
        self.store = make_location(location_code="BACK", name_ar="المخزن الخلفي")
        self.rice = make_item(item_code="RICE", item_name="Rice")
        self.oil = make_item(item_code="OIL", item_name="Oil")
        self.tea = make_item(item_code="TEA", item_name="Tea")
        for item, cost in ((self.rice, "20.00"), (self.oil, "60.00"), (self.tea, "15.00")):
            stock_in(item, self.shop, 10, cost, movement_date=TODAY - timedelta(days=30))
            recalculate_item_average_cost(item)
        stock_in(self.rice, self.store, 7, "20.00", movement_date=TODAY - timedelta(days=30))

    def test_surplus_shortage_and_matches(self):
        counts = {self.rice: D("12"), self.oil: D("7"), self.tea: D("10")}
        rows = variances(self.shop, counts)
        self.assertEqual([(row["item"].item_code, row["diff"], row["value"]) for row in rows], [("OIL", D("-3"), D("-180.00")), ("RICE", D("2"), D("40.00")), ("TEA", D("0"), D("0.00"))])
        self.assertEqual(variance_totals(rows), {"counted": 3, "changed": 2, "gain": D("40.00"), "loss": D("180.00"), "net": D("-140.00")})

        number, operations = post_stock_count(location=self.shop, count_date=TODAY, counts=counts, user=self.owner, note="جرد آخر الشهر")
        self.assertEqual(number, f"CNT-{TODAY:%Y%m%d}-01")
        self.assertEqual(sorted(op.reference_number for op in operations), [f"{number}/OIL", f"{number}/RICE"])
        self.assertEqual(get_item_location_stock_quantity(self.rice, self.shop), D("12"))
        self.assertEqual(get_item_location_stock_quantity(self.oil, self.shop), D("7"))
        self.assertEqual(get_item_location_stock_quantity(self.tea, self.shop), D("10"))
        # The other location is not touched, and value moves at average cost.
        self.assertEqual(get_item_location_stock_quantity(self.rice, self.store), D("7"))
        self.assertEqual(get_item_stock_value(self.oil), D("420.0000"))
        types = dict(StockMovement.objects.filter(stock_operation__reference_number__startswith=number).values_list("item__item_code", "movement_type"))
        self.assertEqual(types, {"OIL": StockMovementType.ADJUSTMENT_OUT, "RICE": StockMovementType.ADJUSTMENT_IN})
        self.assertIn("جرد آخر الشهر", operations[0].reason)
        self.assertEqual(next_count_number(TODAY), f"CNT-{TODAY:%Y%m%d}-02")

    def test_difference_is_taken_at_posting_time(self):
        # The sheet showed 10 oil; a sale of 2 happened before posting; 7 are on the shelf.
        sell(self.oil, self.shop, make_cashbox(cashbox_code="ST-CASH"), 2, "80.00", "160.00", self.owner, number="ST-S1")
        _, operations = post_stock_count(location=self.shop, count_date=TODAY, counts={self.oil: D("7")}, user=self.owner)
        self.assertEqual(operations[0].quantity, D("1"))
        self.assertEqual(get_item_location_stock_quantity(self.oil, self.shop), D("7"))

    def test_nothing_posts_when_one_line_fails(self):
        self.period.status = PeriodStatus.CLOSED
        self.period.save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            post_stock_count(location=self.shop, count_date=TODAY, counts={self.rice: D("9"), self.oil: D("9")}, user=self.owner)
        self.assertFalse(StockOperation.objects.exists())
        with self.assertRaises(ValidationError):
            post_stock_count(location=self.shop, count_date=TODAY, counts={}, user=self.owner)

    def test_needs_the_adjust_permission(self):
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            post_stock_count(location=self.shop, count_date=TODAY, counts={self.rice: D("1")}, user=person(RoleCode.CASHIER, "st_cashier"))
        self.assertEqual(get_item_location_stock_quantity(self.rice, self.shop), D("10"))


class StockCountScreenTests(TestCase):
    def setUp(self):
        prepared_client()
        Period.objects.create(period_code="SS", name="ss", start_date=TODAY - timedelta(days=40), end_date=TODAY + timedelta(days=5))
        self.shop = make_location(location_code="SHOP", is_default=True)
        self.rice = make_item(item_code="RICE", item_name="Rice", barcode="6221234567890")
        self.oil = make_item(item_code="OIL", item_name="Oil")
        make_item(item_code="SERVICE", item_name="Delivery", is_stock_tracked=False)
        for item in (self.rice, self.oil):
            stock_in(item, self.shop, 10, "5.00", movement_date=TODAY - timedelta(days=10))
            recalculate_item_average_cost(item)
        self.url = reverse("inventory:stocktake")

    def test_sheet_review_and_post(self):
        self.client.force_login(person(RoleCode.OWNER, "ss_owner"))
        sheet = self.client.get(self.url)
        self.assertContains(sheet, "جرد المخزون")
        self.assertContains(sheet, f'name="count_{self.rice.pk}"')
        self.assertNotContains(sheet, "Delivery")  # not stock-tracked
        self.assertContains(sheet, "طباعة كشف الجرد")
        self.assertContains(self.client.get(self.url, {"q": "6221234567890"}), "Rice")

        data = {"lang": "ar", "location": self.shop.pk, "count_date": TODAY.isoformat(), "action": "review", f"count_{self.rice.pk}": "8", f"count_{self.oil.pk}": ""}
        review = self.client.post(self.url, data)
        self.assertEqual(review.context["stage"], "review")
        self.assertEqual(review.context["totals"]["net"], D("-10.00"))
        self.assertContains(review, "ترحيل الجرد")
        self.assertEqual(len(review.context["rows"]), 1)  # the blank oil box was not counted
        self.assertEqual(get_item_location_stock_quantity(self.rice, self.shop), D("10"))  # review changes nothing

        data["action"] = "post"
        posted = self.client.post(self.url, data)
        self.assertRedirects(posted, "/inventory/operations/?lang=ar", fetch_redirect_response=False)
        self.assertEqual(get_item_location_stock_quantity(self.rice, self.shop), D("8"))
        self.assertEqual(get_item_location_stock_quantity(self.oil, self.shop), D("10"))

    def test_bad_quantities_are_reported_not_posted(self):
        self.client.force_login(person(RoleCode.OWNER, "ss_owner2"))
        page = self.client.post(self.url, {"lang": "ar", "location": self.shop.pk, "count_date": TODAY.isoformat(), "action": "post", f"count_{self.rice.pk}": "-1", f"count_{self.oil.pk}": "abc"})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "كميات غير صحيحة")
        self.assertFalse(StockOperation.objects.exists())
        empty = self.client.post(self.url, {"lang": "ar", "location": self.shop.pk, "action": "review"})
        self.assertContains(empty, "اكتب كمية معدودة")

    def test_stock_keeper_counts_without_seeing_cost(self):
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "ss_keeper"))
        review = self.client.post(self.url, {"lang": "en", "location": self.shop.pk, "count_date": TODAY.isoformat(), "action": "review", f"count_{self.rice.pk}": "11"})
        self.assertContains(review, "Post count")
        self.assertNotContains(review, "Difference value")
        self.assertNotContains(review, "Net difference")

    def test_permissions_and_links(self):
        self.client.force_login(person(RoleCode.CASHIER, "ss_cashier"))
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.client.force_login(person(RoleCode.OWNER, "ss_owner3"))
        self.assertContains(self.client.get(reverse("inventory:stock")), self.url)
        self.assertContains(self.client.get(reverse("inventory:operations")), self.url)
