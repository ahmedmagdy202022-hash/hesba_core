"""Manufacturing F5 (HG-136): quality checks, a hold before goods can be sold, release and reject.

The detergent plant: one batch is 20 kg of base at 20.00 for 10 cartons, so a carton costs 40.00 and an order of 50
cartons takes 100 kg (2,000.00) and no labour. The recipe needs release and has two criteria: the acidity, a number
between 6 and 8, and the seal, pass or fail. The stage list is the chemicals one (mixing, processing, quality check,
filling).

Every money case states its quantities, its stock operations and its journal: a hold and a release change no ledger
value; a reject is an ordinary adjustment out, which the projector's existing rule books to stock-count loss (5211).
"""

import json
import re
import uuid
from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from attachments.models import Attachment
from attachments.tests import PDF, upload
from batches.models import Batch
from batches.services import attach_purchase_batches, batch_positions
from hesba_testing.factories import (
    add_purchase_line,
    make_cashbox,
    make_customer,
    make_draft_purchase_invoice,
    make_item,
    make_location,
    make_role,
    make_supplier,
    make_user,
    make_user_profile,
    stock_in,
)
from inventory.models import StockMovement, StockOperation, StockOperationType
from inventory.services import cancel_stock_operation, get_item_authoritative_average_cost, get_item_location_stock_quantity, get_item_stock_value
from ledger import reports as gl
from ledger.models import JournalEntry, JournalLine
from ledger.projector import Projector
from master_data.models import Location
from permissions.models import Permission, RoleCode, RolePermission
from permissions.services import user_has_permission
from purchases.models import PurchaseLine
from purchases.services import create_purchase_return, post_purchase_invoice
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled

from . import orders, quality, quality_views, services, stages, trace
from .models import CheckKind, CheckResult, OrderStatus, ProductionOrder, QualityCheck, QualityCriterion, QualityResult, RunStatus
from .tests import person
from .tests_f11 import BakeryChart

TODAY = timezone.localdate()
BANNED = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")
LATIN_WORD = re.compile(r"^[A-Za-z]+$")


def limited(username, *codes):
    """A user whose own role holds exactly these permissions (no seeded role has stock transfer without stock adjustment)."""

    role = make_role(f"custom_{username}")
    for code in codes:
        RolePermission.objects.create(role=role, permission=Permission.objects.get(code=code))
    user = make_user(username=username)
    make_user_profile(user=user, role=role)
    return user


class QualityPlant(TestCase):
    ACTIVITY, SUB = "manufacturing", "chemicals"
    MODULES = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports,manufacturing"
    LOTS = True

    def setUp(self):
        self.profile = prepared_client(activity=self.ACTIVITY, sub_activity=self.SUB, modules=self.MODULES)
        set_capability_enabled(self.profile, "batches_expiry", self.LOTS)
        self.owner = person(RoleCode.OWNER, "qc_owner")
        self.plant = make_location(location_code="PLANT", name_ar="المصنع", name_en="Plant", is_default=True)
        self.cashbox = make_cashbox()
        self.base = make_item(item_code="RM-BASE", item_name="خامة منظف (كيلو)", is_stock_tracked=True)
        self.soap = make_item(item_code="FG-SOAP", item_name="كرتونة منظف", is_stock_tracked=True, default_sale_price="60")
        stock_in(self.base, self.plant, 400, "20.00", movement_date=TODAY)
        self.recipe = services.save_recipe({"product": self.soap, "output_quantity": "10", "shelf_life_days": "365"}, [(self.base, "20")], self.owner)
        quality.save_recipe_quality(self.recipe, self.owner, needs_release=True, rows=[
            {"name_ar": "درجة الحموضة", "name_en": "Acidity", "kind": "number", "min_value": "6", "max_value": "8"},
            {"name_ar": "إحكام الغطا", "name_en": "Seal", "kind": "pass_fail"}])
        self.recipe.refresh_from_db()
        self.acidity, self.seal = self.recipe.criteria.all()

    # ---- the floor ----

    def good(self, acidity="7", seal="pass"):
        return {self.acidity.pk: acidity, self.seal.pk: seal}

    def open(self, quantity="50", recipe=None):
        return orders.create_order({"recipe": recipe or self.recipe, "location": self.plant, "quantity": quantity, "labor_cost": "0",
                                    "overhead_cost": "0"}, self.owner)

    def made(self, quantity="50", recipe=None, **finish):
        """An order walked through every stage (its criteria recorded at the quality stage) and finished."""

        order = self.open(quantity, recipe)
        for _ in order.stages:
            quality.advance(order, self.owner, results=self.good())
            order.refresh_from_db()
        orders.finish(order, self.owner, **finish)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.DONE)
        return order

    def hold(self):
        return quality.hold_place(self.plant.entity_id, create=False)

    def at(self, place, item=None):
        return get_item_location_stock_quantity(item or self.soap, place)

    def check(self, order, passed="", rejected="", note="", results=None, user=None):
        return quality.check_order(order, user or self.owner, passed=passed, rejected=rejected, note=note,
                                   results=self.good() if results is None else results)

    def waiting(self, order):
        order.refresh_from_db()
        return quality.position(quality.order_subject(order))["waiting"]

    # ---- the books ----

    def books(self, *keys):
        Projector().rebuild()
        return {key: gl.control_balance(key) for key in keys or ("finished_goods", "raw_materials", "wip", "production_cost", "stock_loss", "stock_gain", "cogs", "inventory")}

    def journal(self, operation):
        """{control or account code: debit minus credit} of the entries a stock operation wrote (after a rebuild)."""

        Projector().rebuild()
        totals = {}
        for line in JournalLine.objects.filter(entry__source_type="stock_operation", entry__source_id=operation.pk).select_related("account"):
            key = line.account.control or line.account.code
            totals[key] = totals.get(key, D("0")) + line.debit - line.credit
        return {key: value for key, value in totals.items() if value}

    def counts(self):
        return {"movements": StockMovement.objects.count(), "operations": StockOperation.objects.count(), "checks": QualityCheck.objects.count()}

    def balanced(self):
        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])


# ---------------------------------------------------------------------------------------------------------------
# The hold
# ---------------------------------------------------------------------------------------------------------------


class HoldTests(QualityPlant):
    #: the books once the 50 cartons are on hold: 2,000.00 of finished goods, nothing left in work in progress, no loss
    HELD_BOOKS = {"finished_goods": D("2000.00"), "wip": D("0.00"), "stock_loss": D("0.00")}
    def test_fifty_cartons_finish_into_the_hold_place_and_the_plant_shows_none(self):
        self.assertIsNone(self.hold())                                             # made on first use, not before
        order = self.made()
        place = self.hold()
        self.assertEqual((place.location_code, place.entity_id, place.is_selling_location, place.is_receiving_location, place.is_default),
                         (f"QC-{self.plant.entity_id}", self.plant.entity_id, False, True, False))
        self.assertEqual((self.at(self.plant), self.at(place)), (D("0"), D("50")))  # the plant shows 0, the hold shows 50
        self.assertEqual(self.at(self.plant, self.base), D("300"))                  # the materials left the order's own location
        run = order.run
        self.assertEqual((run.location, run.output_quantity, run.total_cost, run.status), (self.plant, D("50.000"), D("2000.00"), RunStatus.POSTED))
        output = run.output_operation
        self.assertEqual((output.destination_location, output.quantity, output.unit_cost), (place, D("50.000"), D("40.0000")))
        self.assertEqual((get_item_authoritative_average_cost(self.soap), get_item_stock_value(self.soap)), (D("40.0000"), D("2000.00")))
        self.assertEqual(self.waiting(order), D("50.000"))
        self.assertEqual(self.books(*self.HELD_BOOKS), self.HELD_BOOKS)
        self.balanced()

    def test_the_till_the_invoice_form_and_the_offline_till_do_not_offer_the_hold_place(self):
        from sales.forms import SalesDraftForm
        from sales.models import SalesInvoice

        self.made()
        place = self.hold()
        make_location(location_code="SHOP", name_ar="المعرض", is_default=False)
        self.assertNotIn(place, SalesDraftForm(lang="ar").fields["selling_location"].queryset)       # the sales invoice form
        self.assertIn(self.plant, SalesDraftForm(lang="ar").fields["selling_location"].queryset)
        self.client.force_login(self.owner)
        self.tills_do_not_offer(place)
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual(self.at(place), D("50"))

    def offline_sale_from(self, place):
        body = {"key": str(uuid.uuid4()), "recorded_at": timezone.now().isoformat(), "customer": "", "location": str(place.pk), "cashbox": str(self.cashbox.pk),
                "discount": "0", "tendered": "60", "lines": [{"id": self.soap.pk, "qty": "1", "price": "60.00", "unit": None, "serial": None}]}
        return self.client.post(reverse("sales:pos_sync"), json.dumps(body), content_type="application/json")

    def tills_do_not_offer(self, place):
        # A factory's own activity has no till: the module gate closes both. The till's picker and the offline till are read on the bakery below.
        self.assertEqual(self.client.get(reverse("sales:pos")).status_code, 403)
        self.assertEqual(self.offline_sale_from(place).status_code, 403)

    def test_a_hold_place_ticked_as_selling_is_switched_back_before_anything_new_goes_on_hold(self):
        self.made(quantity="10")
        Location.objects.filter(pk=self.hold().pk).update(is_selling_location=True)
        self.made(quantity="10")
        self.assertFalse(self.hold().is_selling_location)
        self.assertEqual(Location.objects.filter(location_code__startswith="QC-").count(), 1)          # one place per entity, reused

    def test_a_recipe_without_release_finishes_into_the_plant_as_today(self):
        plain_item = make_item(item_code="FG-PLAIN", item_name="صابون سايل", is_stock_tracked=True)
        plain = services.save_recipe({"product": plain_item, "output_quantity": "10"}, [(self.base, "20")], self.owner)
        self.assertFalse(plain.needs_release)                                       # off unless ticked (owner question 13)
        before = self.counts()
        order = self.made(quantity="50", recipe=plain)
        self.assertEqual(self.at(self.plant, plain_item), D("50"))
        self.assertEqual(order.run.output_operation.destination_location, self.plant)
        self.assertFalse(Location.objects.filter(location_code__startswith="QC-").exists())             # no hold place was made
        self.assertIsNone(quality.order_subject(order))
        self.assertIsNone(quality.order_card(order))
        self.assertEqual(quality.waiting_rows(), [])
        after = self.counts()
        self.assertEqual((after["operations"] - before["operations"], after["checks"]), (2, 0))         # the issue and the output, as ever
        self.assertEqual(Batch.objects.get(production_run=order.run).location, self.plant)

    def test_produce_keeps_its_defaults_for_the_kitchen_and_the_demo_seed(self):
        # ``restaurant.kitchen`` and the demo seed call ``services.produce`` without ``output_location``: the product enters the run's location.
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        self.assertEqual((run.output_operation.destination_location, self.at(self.plant)), (self.plant, D("10")))
        self.assertIsNone(self.hold())

    def test_a_plain_run_of_a_recipe_that_needs_release_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "الوصفة دي محتاجة إفراج جودة قبل البيع"):
            quality.check_plain_run(self.recipe)
        self.client.force_login(self.owner)
        url = reverse("manufacturing:recipe", args=[self.recipe.pk])
        page = self.client.get(url)
        self.assertContains(page, "data-plain-run-held")
        self.assertNotContains(page, "data-produce-form")
        before = self.counts()
        answer = self.client.post(url, {"action": "produce", "batches": "1", "location": self.plant.pk})
        self.assertContains(answer, "الوصفة دي محتاجة إفراج جودة قبل البيع")
        self.assertEqual(self.counts(), before)

    def test_cancelling_the_run_while_it_is_on_hold_takes_the_goods_out_of_the_hold_place(self):
        order = self.made()
        services.cancel_run(order.run, self.owner, reason="تشغيلة غلط")
        self.assertEqual((self.at(self.hold()), self.at(self.plant), self.at(self.plant, self.base)), (D("0"), D("0"), D("400")))
        order.refresh_from_db()
        self.assertIsNone(quality.order_subject(order))
        self.assertEqual(quality.waiting_rows(), [])
        self.balanced()

    def test_two_orders_of_the_same_product_wait_side_by_side(self):
        first, second = self.made("50"), self.made("30")
        self.assertEqual(self.at(self.hold()), D("80"))
        self.assertEqual([(row["subject"].order, row["position"]["waiting"]) for row in quality.waiting_rows()], [(first, D("50.000")), (second, D("30.000"))])
        self.check(first, passed="50")
        self.assertEqual([(row["subject"].order, row["position"]["waiting"]) for row in quality.waiting_rows()], [(second, D("30.000"))])
        self.assertEqual((self.at(self.plant), self.at(self.hold())), (D("50"), D("30")))


# ---------------------------------------------------------------------------------------------------------------
# Release and reject
# ---------------------------------------------------------------------------------------------------------------


class ReleaseTests(QualityPlant):
    INVENTORY, INVENTORY_CODE = "finished_goods", "110503"

    def test_forty_eight_pass_and_two_are_rejected(self):
        order = self.made()
        place = self.hold()
        before, counted = self.books(), self.counts()
        check = self.check(order, passed="48", rejected="2", note="كرتونتين غطاهم مش محكم")
        self.assertEqual((check.kind, check.result, check.quantity, check.passed_quantity, check.rejected_quantity, check.destination),
                         (CheckKind.FINAL, CheckResult.PARTLY, D("50.000"), D("48.000"), D("2.000"), self.plant))
        self.assertEqual((self.at(self.plant), self.at(place)), (D("48"), D("0")))
        # Release: one ordinary transfer of 48 from the hold place to the plant, at the product's cost.
        release = check.release_operation
        self.assertEqual((release.operation_type, release.source_location, release.destination_location, release.quantity, release.unit_cost, release.status),
                         (StockOperationType.TRANSFER, place, self.plant, D("48.000"), D("40.0000"), "posted"))
        self.assertEqual(self.journal(release), {})                                  # no ledger value moves on a release
        # Reject: one ordinary adjustment out of 2 from the hold place, with the plan's reason.
        reject = check.reject_operation
        self.assertEqual((reject.operation_type, reject.adjustment_direction, reject.source_location, reject.quantity, reject.unit_cost),
                         (StockOperationType.ADJUSTMENT, "out", place, D("2.000"), D("40.0000")))
        self.assertIn(f"مرفوض في فحص الجودة {check.number}", reject.reason)
        self.assertEqual(self.journal(reject), {"stock_loss": D("80.00"), self.INVENTORY: D("-80.00")})   # debit 5211 80.00, credit finished goods 80.00
        lines = JournalLine.objects.filter(entry__source_id=reject.pk, entry__source_type="stock_operation").select_related("account")
        self.assertEqual(sorted((line.account.code, line.debit, line.credit) for line in lines),
                         sorted([("5211", D("80.00"), D("0.00")), (self.INVENTORY_CODE, D("0.00"), D("80.00"))]))
        # Stock: 48 cartons at 40.00 = 1,920.00; the average did not move.
        self.assertEqual((get_item_stock_value(self.soap), get_item_authoritative_average_cost(self.soap)), (D("1920.00"), D("40.0000")))
        after = self.books()
        self.assertEqual(after[self.INVENTORY] - before[self.INVENTORY], D("-80.00"))
        self.assertEqual(after["stock_loss"] - before["stock_loss"], D("80.00"))
        self.assertEqual({key: after[key] - before[key] for key in after if key not in (self.INVENTORY, "stock_loss")},
                         {key: D("0.00") for key in after if key not in (self.INVENTORY, "stock_loss")})
        now = self.counts()
        self.assertEqual((now["operations"] - counted["operations"], now["movements"] - counted["movements"], now["checks"] - counted["checks"]), (2, 3, 1))
        self.assertEqual(self.waiting(order), D("0"))
        self.assertEqual(quality.waiting_rows(), [])
        self.assertEqual([(row.name_ar, row.value, row.ok) for row in check.results.all()], [("درجة الحموضة", D("7.000"), True), ("إحكام الغطا", None, True)])
        self.balanced()
        with self.assertRaises(ValidationError):                                     # F11's rule: the product has left the place it entered
            services.cancel_run(order.run, self.owner, reason="بعد الإفراج")
        self.assertEqual((order.run.status, self.at(self.plant)), (RunStatus.POSTED, D("48")))

    def test_a_release_alone_changes_no_ledger_value_and_the_rest_stays_on_hold(self):
        order = self.made()
        before = self.books()
        check = self.check(order, passed="40")
        self.assertEqual((check.result, check.reject_operation, self.at(self.plant), self.at(self.hold()), self.waiting(order)),
                         (CheckResult.PARTLY, None, D("40"), D("10"), D("10.000")))
        self.assertEqual(self.books(), before)
        self.assertEqual(self.journal(check.release_operation), {})                  # the transfer's entry nets to nothing on every account
        self.assertEqual(get_item_stock_value(self.soap), D("2000.00"))
        # The ten still on hold are decided later: seven more pass and three are written off.
        later = self.check(order, passed="7", rejected="3")
        self.assertEqual((later.quantity, later.result), (D("10.000"), CheckResult.PARTLY))
        self.assertEqual((self.at(self.plant), self.at(self.hold()), self.waiting(order)), (D("47"), D("0"), D("0")))
        self.assertEqual(self.books("stock_loss")["stock_loss"], D("120.00"))        # 3 x 40.00
        self.balanced()

    def test_everything_passes(self):
        order = self.made()
        before = self.books()
        check = self.check(order, passed="50")
        self.assertEqual((check.result, check.reject_operation, self.at(self.plant), self.at(self.hold())), (CheckResult.PASSED, None, D("50"), D("0")))
        self.assertEqual(self.books(), before)
        self.balanced()

    def test_the_whole_lot_is_rejected_and_written_off(self):
        order = self.made()
        check = self.check(order, rejected="50", results={})                         # a reject needs no criterion answered
        self.assertEqual((check.result, check.release_operation, check.destination), (CheckResult.REJECTED, None, None))
        self.assertEqual((self.at(self.plant), self.at(self.hold()), get_item_stock_value(self.soap)), (D("0"), D("0"), D("0.00")))
        self.assertEqual(self.journal(check.reject_operation), {"stock_loss": D("2000.00"), self.INVENTORY: D("-2000.00")})
        with self.assertRaises(ValidationError):                                     # the goods have left the hold place (F11's rule)
            services.cancel_run(order.run, self.owner, reason="بعد الرفض")
        self.balanced()

    def test_keeping_the_goods_on_hold_needs_a_note_and_moves_nothing(self):
        order = self.made()
        before, books = self.counts(), self.books()
        with self.assertRaisesMessage(ValidationError, "اكتب ملاحظة توضّح سبب استمرار الحجز."):
            self.check(order, results={})
        self.assertEqual(self.counts(), before)
        check = self.check(order, note="مستنيين نتيجة المعمل", results={self.acidity.pk: "7"})   # a hold may record what is known so far
        self.assertEqual((check.result, check.passed_quantity, check.rejected_quantity, check.release_operation, check.reject_operation),
                         (CheckResult.HELD, D("0"), D("0"), None, None))
        after = self.counts()
        self.assertEqual((after["operations"], after["movements"], after["checks"]), (before["operations"], before["movements"], before["checks"] + 1))
        self.assertEqual((self.books(), self.waiting(order), check.results.count()), (books, D("50.000"), 1))

    def test_a_failed_criterion_cannot_be_passed_without_a_note(self):
        order = self.made()
        before = self.counts()
        with self.assertRaisesMessage(ValidationError, "في معيار غير مطابق؛ اكتب ملاحظة توضّح سبب قبول الكمية."):
            self.check(order, passed="50", results=self.good(acidity="9.5"))
        with self.assertRaisesMessage(ValidationError, "في معيار غير مطابق"):
            self.check(order, passed="48", rejected="2", results=self.good(seal="fail"))
        with self.assertRaisesMessage(ValidationError, "سجّل نتيجة كل معيار قبل الإفراج."):
            self.check(order, passed="50", results={self.acidity.pk: "7"})
        with self.assertRaisesMessage(ValidationError, "قيمة «درجة الحموضة» لازم تكون رقم."):
            self.check(order, passed="50", results=self.good(acidity="حمضي"))
        self.assertEqual((self.counts(), self.at(self.hold())), (before, D("50")))
        check = self.check(order, passed="50", note="الحموضة 8.2 ومقبولة بموافقة مدير الجودة", results=self.good(acidity="8.2"))
        self.assertEqual((check.result, self.at(self.plant)), (CheckResult.PASSED, D("50")))
        self.assertEqual([(row.name_ar, row.value, row.ok) for row in check.results.all()], [("درجة الحموضة", D("8.200"), False), ("إحكام الغطا", None, True)])

    def test_the_limits_are_inclusive_and_a_reject_needs_no_note(self):
        order = self.made()
        check = self.check(order, passed="10", results=self.good(acidity="8"))
        self.assertTrue(all(row.ok for row in check.results.all()))
        check = self.check(order, passed="10", results=self.good(acidity="6"))
        self.assertTrue(all(row.ok for row in check.results.all()))
        rejected = self.check(order, rejected="30", results=self.good(acidity="3", seal="fail"))
        self.assertEqual((rejected.result, [row.ok for row in rejected.results.all()]), (CheckResult.REJECTED, [False, False]))

    def test_quantities_are_checked_against_what_waits(self):
        order = self.made()
        before = self.counts()
        for passed, rejected, message in (("51", "", "الكمية المنتظرة 50 بس"), ("48", "3", "الكمية المنتظرة 50 بس"), ("-1", "", "لازم يكونوا أرقام مش سالبة"),
                                          ("كتير", "", "لازم يكونوا أرقام مش سالبة"), ("10", "-2", "لازم يكونوا أرقام مش سالبة")):
            with self.subTest(passed=passed, rejected=rejected), self.assertRaisesMessage(ValidationError, message):
                self.check(order, passed=passed, rejected=rejected)
        self.assertEqual(self.counts(), before)
        self.check(order, passed="50")
        with self.assertRaisesMessage(ValidationError, "مفيش كمية منتظرة الفحص هنا."):
            self.check(order, passed="1")

    def test_an_order_that_never_went_on_hold_has_nothing_to_check(self):
        plain = services.save_recipe({"product": make_item(item_code="FG-P2", item_name="معطر", is_stock_tracked=True), "output_quantity": "10"},
                                     [(self.base, "20")], self.owner)
        order = self.made(recipe=plain)
        with self.assertRaisesMessage(ValidationError, "مفيش كمية منتظرة الفحص هنا."):
            self.check(order, passed="10")

    def test_a_release_reversed_from_the_stock_screen_puts_the_goods_back_in_the_queue(self):
        order = self.made()
        check = self.check(order, passed="48", rejected="2")
        self.assertEqual(self.waiting(order), D("0"))
        cancel_stock_operation(check.release_operation_id, TODAY, "اتفرج عنه بالغلط", self.owner)      # the stock screen's own reversal
        self.assertEqual((self.at(self.plant), self.at(self.hold()), self.waiting(order)), (D("0"), D("48"), D("48.000")))
        check.refresh_from_db()
        self.assertEqual(quality.operation_state(check), {"released": False, "release_reversed": True, "written_off": True, "reject_reversed": False})
        self.assertEqual([row["subject"].order for row in quality.waiting_rows()], [order])
        again = self.check(order, passed="48")
        self.assertEqual((again.quantity, again.result, self.at(self.plant), self.waiting(order)), (D("48.000"), CheckResult.PASSED, D("48"), D("0")))
        # A reject reversed the same way brings its two cartons back to the hold place too.
        cancel_stock_operation(check.reject_operation_id, TODAY, "اترفضوا بالغلط", self.owner)
        self.assertEqual((self.at(self.hold()), self.waiting(order)), (D("2"), D("2.000")))
        self.assertEqual(self.books("stock_loss")["stock_loss"], D("0.00"))
        self.balanced()

    def test_what_waits_is_never_more_than_the_hold_place_holds(self):
        from inventory.services import transfer_stock

        order = self.made()
        transfer_stock("TR-HAND", TODAY, self.soap, self.hold(), self.plant, D("20"), self.owner, reason="نقل يدوي")   # someone moved 20 out by hand
        self.assertEqual(self.waiting(order), D("30.000"))
        with self.assertRaisesMessage(ValidationError, "الكمية المنتظرة 30 بس"):
            self.check(order, passed="50")
        self.check(order, passed="30")
        self.assertEqual((self.at(self.plant), self.at(self.hold())), (D("50"), D("0")))

    def test_recording_needs_the_transfer_right_and_a_reject_needs_the_adjustment_right_too(self):
        order = self.made()
        viewer = person(RoleCode.SITE_ENGINEER, "qc_viewer")
        self.assertTrue(user_has_permission(viewer, "inventory.view_stock") and not user_has_permission(viewer, quality.RELEASE))
        with self.assertRaises(PermissionDenied):
            self.check(order, passed="50", user=viewer)
        mover = limited("qc_mover", "inventory.view_stock", "inventory.transfer_stock")
        before = self.counts()
        with self.assertRaisesMessage(ValidationError, "رفض كمية وشطبها من المخزون محتاج صلاحية تسوية المخزون."):
            self.check(order, passed="48", rejected="2", user=mover)
        self.assertEqual((self.counts(), self.at(self.hold())), (before, D("50")))
        check = self.check(order, passed="48", user=mover)                           # a release alone needs only the transfer right
        self.assertEqual((check.checked_by, self.at(self.plant), self.waiting(order)), (mover, D("48"), D("2.000")))
        keeper = person(RoleCode.STOCK_KEEPER, "qc_keeper")                           # holds both rights and not the cost right
        self.assertFalse(user_has_permission(keeper, "inventory.view_cost"))
        self.check(order, rejected="2", user=keeper)
        self.assertEqual(self.at(self.hold()), D("0"))


class ReleaseOnARestaurantChartTests(BakeryChart, ReleaseTests):
    """A bakery with the module on: one inventory account, so the reject credits it; the loss account is the same."""

    INVENTORY, INVENTORY_CODE = "inventory", "1105"


class HoldOnARestaurantChartTests(BakeryChart, HoldTests):
    """The same hold on a bakery with the module on, where the till is open: its location picker never lists the hold place."""

    HELD_BOOKS = {"inventory": D("8000.00"), "stock_loss": D("0.00")}             # one inventory account: 300 kg of base (6,000.00) and the 50 cartons

    def tills_do_not_offer(self, place):
        till = self.client.get(reverse("sales:pos"))
        self.assertEqual(till.status_code, 200)
        self.assertNotIn(place, list(till.context["locations"]))                    # the till
        self.assertIn(self.plant, list(till.context["locations"]))
        # The offline till: the hold place holds 50 cartons and is still refused as a place to sell from; the plant, which
        # holds none, gets past the place and is refused only for the quantity.
        self.assertEqual(self.offline_sale_from(place).json(), {"status": "refused", "error": "المخزن أو الخزنة مش موجودين أو موقوفين."})
        self.assertIn("الكمية مش كفاية", self.offline_sale_from(self.plant).json()["error"])

    def test_the_hold_and_the_release_change_no_balance_on_this_chart_either(self):
        order = self.made()
        self.assertEqual((self.at(self.plant), self.at(self.hold())), (D("0"), D("50")))
        before = self.books()
        self.check(order, passed="50")
        self.assertEqual((self.books(), self.at(self.plant)), (before, D("50")))
        self.balanced()


# ---------------------------------------------------------------------------------------------------------------
# Lots follow the release (plan trap 14)
# ---------------------------------------------------------------------------------------------------------------


class LotTests(QualityPlant):
    def remaining(self, place):
        rows, _uncovered = batch_positions(items=[self.soap], locations=[place])
        return {row["batch"].batch_no: row["remaining"] for row in rows}

    def test_the_lot_is_written_at_the_hold_place_and_follows_the_release(self):
        order = self.made(lot_no="LOT-S1")
        place = self.hold()
        lot = Batch.objects.get(production_run=order.run)
        self.assertEqual((lot.location, lot.batch_no, lot.quantity, lot.expiry_date), (place, "LOT-S1", D("50.000"), TODAY + timedelta(days=365)))
        self.assertEqual((self.remaining(place), self.remaining(self.plant)), ({"LOT-S1": D("50")}, {}))
        self.assertEqual(quality.waiting_rows()[0]["lot"], lot)
        self.check(order, passed="48", rejected="2")
        lot.refresh_from_db()
        self.assertEqual(lot.location, self.plant)                                   # the lot went where the goods went
        self.assertEqual((self.remaining(self.plant), self.remaining(place)), ({"LOT-S1": D("48")}, {}))
        rows, _uncovered = batch_positions(items=[self.soap])                        # group-wide: one lot, 48 left
        self.assertEqual([(row["batch"].batch_no, row["remaining"]) for row in rows], [("LOT-S1", D("48"))])

    def test_a_hold_or_a_full_reject_leaves_the_lot_in_the_hold_place(self):
        order = self.made(lot_no="LOT-S2")
        self.check(order, note="مستنيين المعمل", results={})
        lot = Batch.objects.get(production_run=order.run)
        self.assertEqual(lot.location, self.hold())
        self.check(order, rejected="50", results={})
        lot.refresh_from_db()
        self.assertEqual((lot.location, self.remaining(self.hold())), (self.hold(), {"LOT-S2": D("0")}))

    def test_a_release_reversed_then_everything_rejected_brings_the_lot_back(self):
        order = self.made(lot_no="LOT-S3")
        check = self.check(order, passed="50")
        cancel_stock_operation(check.release_operation_id, TODAY, "رجوع", self.owner)
        self.check(order, rejected="50", results={})
        self.assertEqual(Batch.objects.get(production_run=order.run).location, self.hold())

    def test_the_trace_counts_sales_from_the_release_on_and_not_before_it(self):
        from sales.pos import checkout

        def sell(quantity):
            invoice, _change = checkout(lines=[{"item": self.soap, "quantity": D(quantity), "unit_sale_price": D("60"), "line_discount_amount": D("0"), "description": ""}],
                                        customer=make_customer(f"C-{quantity}", name=f"عميل {quantity}"), location=self.plant, cashbox=self.cashbox,
                                        discount=D("0"), tendered=D(quantity) * D("60"), user=self.owner)
            return invoice

        stock_in(self.soap, self.plant, 5, "40.00", movement_date=TODAY)             # five cartons from before, with no lot
        order = self.made(lot_no="LOT-S4")
        early = sell("3")                                                            # sold while the lot was still on hold
        self.check(order, passed="50")
        late = sell("10")
        lot = Batch.objects.get(production_run=order.run)
        sold = trace.sales_after(lot)
        self.assertEqual([row["invoice"] for row in sold["sales"]], [late])
        self.assertNotIn(early, [row["invoice"] for row in sold["sales"]])
        self.assertTrue(sold["open"])


class LotsOffTests(QualityPlant):
    LOTS = False

    def test_the_hold_and_the_release_work_without_lots(self):
        order = self.made()
        self.assertFalse(Batch.objects.exists())
        self.assertIsNone(quality.waiting_rows()[0]["lot"])
        self.check(order, passed="48", rejected="2")
        self.assertEqual((self.at(self.plant), self.at(self.hold())), (D("48"), D("0")))
        self.assertFalse(Batch.objects.exists())


# ---------------------------------------------------------------------------------------------------------------
# In process: the quality stage
# ---------------------------------------------------------------------------------------------------------------


class StageTests(QualityPlant):
    def at_quality(self):
        order = self.open()
        orders.advance(order, self.owner)
        orders.advance(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.stages[order.stage_index][0], "quality")
        return order

    def test_food_has_a_quality_stage_and_an_order_opened_before_keeps_its_list(self):
        self.assertEqual([stage[0] for stage in stages.stages_for("food")], ["prep", "cooking", "cooling", "quality", "packing"])
        self.assertEqual([stage[1] for stage in stages.stages_for("food") if stage[0] == "quality"], ["فحص جودة"])
        old = self.open()
        ProductionOrder.objects.filter(pk=old.pk).update(stages=[["prep", "تحضير وخلط", "Preparation & mixing"], ["cooking", "طهي / خَبز", "Cooking / baking"],
                                                                 ["cooling", "تبريد", "Cooling"], ["packing", "تعبئة وتغليف", "Filling & packing"]])
        old.refresh_from_db()
        for _ in range(4):
            quality.advance(old, self.owner)
            old.refresh_from_db()
        orders.finish(old, self.owner)
        old.refresh_from_db()
        self.assertEqual((old.status, old.quality_checks.count()), (OrderStatus.DONE, 0))   # four stages, no quality stage, no criteria asked

    def test_the_quality_stage_records_every_criterion_and_defects_stay_scrap(self):
        order = self.at_quality()
        self.assertEqual([c.name_ar for c in quality.stage_criteria(order)], ["درجة الحموضة", "إحكام الغطا"])
        before = self.counts()
        with self.assertRaisesMessage(ValidationError, "سجّل نتيجة كل معيار قبل إنهاء مرحلة الفحص."):
            quality.advance(order, self.owner, results={self.acidity.pk: "7"})
        with self.assertRaisesMessage(ValidationError, "في معيار غير مطابق"):
            quality.advance(order, self.owner, results=self.good(seal="fail"), good="49", defect="1")
        order.refresh_from_db()
        self.assertEqual((order.stage_index, self.counts()), (2, before))
        quality.advance(order, self.owner, results=self.good(seal="fail"), good="49", defect="1", note="كرتونة غطاها مش محكم")
        order.refresh_from_db()
        check = order.quality_checks.get()
        log = order.stage_logs.get(stage_key="quality")
        self.assertEqual((check.kind, check.stage_log, check.result, check.quantity, check.passed_quantity, check.rejected_quantity,
                          check.release_operation, check.reject_operation), (CheckKind.STAGE, log, CheckResult.PARTLY, D("50.000"), D("49.000"), D("1.000"), None, None))
        self.assertEqual([(row.name_ar, row.ok) for row in check.results.all()], [("درجة الحموضة", True), ("إحكام الغطا", False)])
        now = self.counts()
        self.assertEqual((now["operations"], now["movements"]), (before["operations"], before["movements"]))   # the stage check moves nothing
        quality.advance(order, self.owner)
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.run.output_quantity, order.run.scrap_quantity, self.at(self.hold())), (D("49.000"), D("1.000"), D("49")))   # scrap as before
        self.assertEqual(order.run.output_operation.unit_cost, D("40.8163"))         # the good units carry the whole 2,000.00

    def test_a_clean_stage_is_passed_and_other_stages_ask_nothing(self):
        order = self.open()
        quality.advance(order, self.owner)                                           # mixing: straight to ``orders.advance``
        quality.advance(order, self.owner)
        self.assertEqual(QualityCheck.objects.count(), 0)
        quality.advance(order, self.owner, results=self.good())
        self.assertEqual(QualityCheck.objects.get().result, CheckResult.PASSED)

    def test_a_recipe_with_no_criteria_passes_its_quality_stage_as_before(self):
        quality.save_recipe_quality(self.recipe, self.owner, needs_release=True, rows=[])
        order = self.at_quality()
        self.assertEqual(quality.stage_criteria(order), [])
        quality.advance(order, self.owner)
        order.refresh_from_db()
        self.assertEqual((order.stage_index, QualityCheck.objects.count()), (3, 0))

    def test_the_order_page_asks_the_criteria_at_the_quality_stage_only(self):
        self.client.force_login(self.owner)
        order = self.open()
        url = reverse("manufacturing:order", args=[order.pk])
        self.assertNotContains(self.client.get(url), "data-stage-criteria")
        self.client.post(url, {"action": "advance"})
        self.client.post(url, {"action": "advance"})
        page = self.client.get(url)
        self.assertContains(page, "data-stage-criteria")
        self.assertContains(page, f'name="crit_{self.acidity.pk}"')
        self.assertContains(page, "من 6 إلى 8")
        refused = self.client.post(url, {"action": "advance", f"crit_{self.acidity.pk}": "7"})
        self.assertContains(refused, "سجّل نتيجة كل معيار قبل إنهاء مرحلة الفحص.")
        self.assertContains(refused, 'value="7"')                                    # what was typed is kept
        self.client.post(url, {"action": "advance", f"crit_{self.acidity.pk}": "7", f"crit_{self.seal.pk}": "pass"})
        order.refresh_from_db()
        self.assertEqual((order.stage_index, order.quality_checks.get().result), (3, CheckResult.PASSED))
        self.assertContains(self.client.get(url), f'data-check="{order.quality_checks.get().number}"')


# ---------------------------------------------------------------------------------------------------------------
# Criteria
# ---------------------------------------------------------------------------------------------------------------


class CriteriaTests(QualityPlant):
    def test_the_list_is_replaced_and_empty_rows_are_skipped(self):
        quality.save_recipe_quality(self.recipe, self.owner, needs_release=False, rows=[
            {"name_ar": "", "name_en": "", "kind": "pass_fail"}, {"name_ar": "اللون", "name_en": "Colour", "kind": "pass_fail"},
            {"name_ar": "الوزن", "kind": "number", "min_value": "4.9", "max_value": ""}])
        self.recipe.refresh_from_db()
        self.assertFalse(self.recipe.needs_release)
        rows = list(self.recipe.criteria.all())
        self.assertEqual([(c.order, c.name_ar, c.kind, c.min_value, c.max_value) for c in rows],
                         [(1, "اللون", "pass_fail", None, None), (2, "الوزن", "number", D("4.900"), None)])
        self.assertEqual((quality.limits_text(rows[1], "ar"), quality.limits_text(rows[1], "en"), quality.limits_text(rows[0], "ar")), ("4.9 على الأقل", "at least 4.9", ""))
        self.assertEqual((quality.limits_text(self.acidity, "ar"), quality.limits_text(self.acidity, "en")), ("من 6 إلى 8", "6 to 8"))

    def test_bad_rows_are_refused_and_nothing_changes(self):
        for rows, message in (([{"name_ar": "", "name_en": "Colour"}], "اكتب اسم المعيار بالعربي في السطر 1."),
                              ([{"name_ar": "الوزن", "kind": "number", "min_value": "9", "max_value": "5"}], "حدود المعيار «الوزن»"),
                              ([{"name_ar": "الوزن", "kind": "number", "min_value": "تقيل"}], "حدود المعيار «الوزن»"),
                              ([{"name_ar": "الوزن", "kind": "weight"}], "نوع المعيار في السطر 1 مش صحيح.")):
            with self.subTest(rows=rows), self.assertRaisesMessage(ValidationError, message):
                quality.save_recipe_quality(self.recipe, self.owner, needs_release=False, rows=rows)
        self.recipe.refresh_from_db()
        self.assertEqual((self.recipe.needs_release, self.recipe.criteria.count()), (True, 2))

    def test_a_check_keeps_its_own_copy_when_the_criteria_change(self):
        order = self.made()
        check = self.check(order, passed="50")
        quality.save_recipe_quality(self.recipe, self.owner, needs_release=True, rows=[{"name_ar": "اللون", "kind": "pass_fail"}])
        self.assertEqual([(row.name_ar, row.name_en, row.kind, row.min_value, row.max_value, row.criterion_id) for row in check.results.all()],
                         [("درجة الحموضة", "Acidity", "number", D("6.000"), D("8.000"), None), ("إحكام الغطا", "Seal", "pass_fail", None, None, None)])
        self.assertEqual(QualityResult.objects.filter(quality_check__kind=CheckKind.STAGE).count(), 2)   # the stage's own record stays too

    def test_changing_the_criteria_needs_the_items_right(self):
        mover = limited("qc_mover2", "inventory.view_stock", "inventory.transfer_stock")
        with self.assertRaises(PermissionDenied):
            quality.save_recipe_quality(self.recipe, mover, needs_release=False, rows=[])
        with self.assertRaises(PermissionDenied):
            quality.save_item_criteria(self.base, mover, rows=[])
        self.recipe.refresh_from_db()
        self.assertTrue(self.recipe.needs_release)

    def test_saving_a_recipe_from_any_other_caller_leaves_its_quality_alone(self):
        services.save_recipe({"product": self.soap, "output_quantity": "10", "name": "اسم جديد"}, [(self.base, "20")], self.owner, self.recipe)
        self.recipe.refresh_from_db()
        self.assertEqual((self.recipe.needs_release, self.recipe.criteria.count()), (True, 2))


# ---------------------------------------------------------------------------------------------------------------
# Incoming checks: a purchase received into the hold place
# ---------------------------------------------------------------------------------------------------------------


class IncomingTests(QualityPlant):
    def setUp(self):
        super().setUp()
        self.place = quality.hold_place(self.plant.entity_id)
        self.supplier = make_supplier("SUP-CHEM", name="كيماويات النيل")
        quality.save_item_criteria(self.base, self.owner, rows=[{"name_ar": "التركيز", "name_en": "Concentration", "kind": "number", "min_value": "30", "max_value": "35"}])
        self.concentration = QualityCriterion.objects.get(item=self.base)

    def received(self, quantity="100", location=None, number="PI-QC1", lot=None):
        invoice = make_draft_purchase_invoice(supplier=self.supplier, location=location or self.place, invoice_number=number, invoice_date=TODAY)
        add_purchase_line(invoice, self.base, quantity, "20.00")
        if lot:
            attach_purchase_batches(invoice, [{"batch_no": lot, "expiry_date": TODAY + timedelta(days=200)}], self.owner)
        post_purchase_invoice(invoice.pk, self.owner)
        return PurchaseLine.objects.get(invoice_id=invoice.pk)                       # read afresh: its invoice is posted now

    def incoming(self, line, **kwargs):
        kwargs.setdefault("results", {self.concentration.pk: "32"})
        return quality.check_purchase_line(line, self.owner, **kwargs)

    def test_the_purchase_form_offers_the_hold_place_as_a_receiving_location(self):
        from purchases.forms import PurchaseDraftForm

        self.assertIn(self.place, PurchaseDraftForm(lang="ar").fields["receiving_location"].queryset)

    def test_a_hundred_kilos_in_ninety_five_passed_five_rejected_and_returned(self):
        line = self.received(lot="LOT-B7")
        self.assertEqual((self.at(self.place, self.base), self.at(self.plant, self.base)), (D("100"), D("400")))
        self.assertEqual([(row["subject"].line, row["position"]["waiting"]) for row in quality.waiting_rows()], [(line, D("100.000"))])
        before = self.books()
        check = self.incoming(line, passed="95", rejected="5", destination=self.plant, note="خمسة كيلو شكارة مقطوعة")
        self.assertEqual((check.kind, check.result, check.quantity, check.passed_quantity, check.rejected_quantity, check.destination, check.reject_operation),
                         (CheckKind.INCOMING, CheckResult.PARTLY, D("100.000"), D("95.000"), D("5.000"), self.plant, None))
        release = check.release_operation
        self.assertEqual((release.operation_type, release.source_location, release.destination_location, release.quantity),
                         (StockOperationType.TRANSFER, self.place, self.plant, D("95.000")))
        self.assertEqual((self.at(self.place, self.base), self.at(self.plant, self.base)), (D("5"), D("495")))   # the five stay on hold
        self.assertEqual(self.books(), before)                                       # the check moves no ledger value
        where = quality.position(quality.purchase_subject(line))
        self.assertEqual((where["waiting"], where["to_return"]), (D("0"), D("5.000")))
        self.assertEqual(quality.waiting_rows(), [])
        self.assertEqual([(row["line"], row["quantity"]) for row in quality.return_rows()], [(line, D("5.000"))])
        self.assertEqual(Batch.objects.get(purchase_line=line).location, self.plant)   # the bought lot followed the release
        # The five go back on an ordinary purchase return, which takes them from the invoice's receiving location: the hold place.
        create_purchase_return("PR-QC1", TODAY, line.invoice_id, [{"source_line": line, "quantity": D("5")}], "مرفوض في فحص الجودة", self.owner)
        self.assertEqual((self.at(self.place, self.base), self.at(self.plant, self.base)), (D("0"), D("495")))
        self.assertEqual((quality.return_rows(), quality.position(quality.purchase_subject(line))["to_return"]), ([], D("0")))
        self.balanced()

    def test_the_destination_must_be_a_real_place_of_the_same_entity(self):
        line = self.received()
        before = self.counts()
        from inventory.transfer_requests import transit_location

        for destination in (None, self.place, transit_location(self.plant.entity_id), make_location(location_code="OLD", active=False)):
            with self.subTest(destination=destination), self.assertRaisesMessage(ValidationError, "اختار المخزن اللي الكمية المقبولة هتتنقل له."):
                self.incoming(line, passed="100", destination=destination)
        self.assertEqual((self.counts(), self.at(self.place, self.base)), (before, D("100")))
        self.incoming(line, rejected="100", results={})                              # a reject needs no destination
        self.assertEqual(quality.position(quality.purchase_subject(line))["to_return"], D("100.000"))

    def test_the_items_own_criteria_apply_and_a_failed_one_needs_a_note(self):
        line = self.received()
        with self.assertRaisesMessage(ValidationError, "في معيار غير مطابق"):
            self.incoming(line, passed="100", destination=self.plant, results={self.concentration.pk: "28"})
        with self.assertRaisesMessage(ValidationError, "سجّل نتيجة كل معيار قبل الإفراج."):
            self.incoming(line, passed="100", destination=self.plant, results={})
        check = self.incoming(line, passed="100", destination=self.plant)
        self.assertEqual([(row.name_ar, row.value, row.ok) for row in check.results.all()], [("التركيز", D("32.000"), True)])

    def test_a_purchase_into_an_ordinary_warehouse_and_a_draft_are_not_checks(self):
        line = self.received(location=self.plant, number="PI-PLAIN")
        self.assertIsNone(quality.purchase_subject(line))
        draft = make_draft_purchase_invoice(supplier=self.supplier, location=self.place, invoice_number="PI-DRAFT", invoice_date=TODAY)
        add_purchase_line(draft, self.base, "10", "20.00")
        self.assertIsNone(quality.purchase_subject(PurchaseLine.objects.get(invoice_id=draft.pk)))
        self.assertEqual(quality.waiting_rows(), [])
        with self.assertRaisesMessage(ValidationError, "مفيش كمية منتظرة الفحص هنا."):
            self.incoming(line, passed="10", destination=self.plant)

    def test_a_delivery_returned_whole_without_a_check_stops_waiting(self):
        line = self.received()
        create_purchase_return("PR-ALL", TODAY, line.invoice_id, [{"source_line": line, "quantity": D("100")}], "رجعت كلها", self.owner)
        self.assertEqual((self.at(self.place, self.base), quality.waiting_rows(), quality.return_rows()), (D("0"), [], []))

    def test_a_delivery_returned_whole_does_not_wait_again_when_the_same_item_arrives_on_another_invoice(self):
        first = self.received(number="PI-QA")
        create_purchase_return("PR-QA", TODAY, first.invoice_id, [{"source_line": first, "quantity": D("100")}], "رجعت كلها", self.owner)
        second = self.received(number="PI-QB")                                       # the same item, another delivery: 100 in the hold place
        self.assertEqual(self.at(self.place, self.base), D("100"))
        self.assertEqual(quality.position(quality.purchase_subject(first))["waiting"], D("0"))
        self.assertEqual([(row["subject"].line, row["position"]["waiting"]) for row in quality.waiting_rows()], [(second, D("100.000"))])
        before = self.counts()
        with self.assertRaisesMessage(ValidationError, "مفيش كمية منتظرة الفحص هنا."):
            self.incoming(first, passed="100", destination=self.plant)               # the second delivery is not released on the first one's check
        self.assertEqual((self.counts(), self.at(self.place, self.base)), (before, D("100")))
        self.incoming(second, passed="100", destination=self.plant)
        self.assertEqual((self.at(self.place, self.base), quality.waiting_rows()), (D("0"), []))

    def test_a_part_sent_back_before_the_check_is_not_read_as_the_return_of_a_later_rejection(self):
        line = self.received()
        create_purchase_return("PR-QE", TODAY, line.invoice_id, [{"source_line": line, "quantity": D("8")}], "شكاير ناقصة", self.owner)
        where = quality.position(quality.purchase_subject(line))
        self.assertEqual((where["waiting"], where["to_return"], self.at(self.place, self.base)), (D("92.000"), D("0"), D("92")))
        with self.assertRaisesMessage(ValidationError, "الكمية المنتظرة 92"):
            self.incoming(line, passed="95", destination=self.plant)
        self.incoming(line, rejected="5", results={})
        where = quality.position(quality.purchase_subject(line))
        self.assertEqual((where["waiting"], where["to_return"]), (D("87.000"), D("5.000")))   # the eight went before the five were rejected
        create_purchase_return("PR-QF", TODAY, line.invoice_id, [{"source_line": line, "quantity": D("5")}], "مرفوض في فحص الجودة", self.owner)
        where = quality.position(quality.purchase_subject(line))
        self.assertEqual((where["waiting"], where["to_return"], self.at(self.place, self.base)), (D("87.000"), D("0"), D("87")))
        self.incoming(line, passed="87", destination=self.plant)
        self.assertEqual((self.at(self.place, self.base), self.at(self.plant, self.base), quality.waiting_rows(), quality.return_rows()),
                         (D("0"), D("487"), [], []))

    def test_another_deliverys_rejects_in_the_hold_place_are_never_released_on_this_ones_check(self):
        first = self.received(number="PI-QC")
        self.incoming(first, rejected="100", results={})                             # all of it stays in the hold place for its return
        second = self.received(quantity="40", number="PI-QD")
        self.assertEqual(self.at(self.place, self.base), D("140"))
        self.assertEqual([(row["subject"].line, row["position"]["waiting"]) for row in quality.waiting_rows()], [(second, D("40.000"))])
        self.assertEqual([(row["line"], row["quantity"]) for row in quality.return_rows()], [(first, D("100.000"))])
        with self.assertRaisesMessage(ValidationError, "الكمية المنتظرة 40"):
            self.incoming(second, passed="41", destination=self.plant)

    def test_a_cancelled_invoices_rejects_do_not_hold_back_the_next_delivery(self):
        from purchases.services import cancel_posted_purchase_invoice

        first = self.received(number="PI-QG")
        self.incoming(first, rejected="100", results={})
        cancel_posted_purchase_invoice(first.invoice_id, self.owner, "اتسجلت بالغلط")   # the receipt is reversed; the check stays as a record
        self.assertEqual((self.at(self.place, self.base), QualityCheck.objects.filter(purchase_line=first).count()), (D("0"), 1))
        self.assertEqual((quality.waiting_rows(), quality.return_rows()), ([], []))
        second = self.received(number="PI-QH")
        self.assertEqual([(row["subject"].line, row["position"]["waiting"]) for row in quality.waiting_rows()], [(second, D("100.000"))])
        self.incoming(second, passed="100", destination=self.plant)
        self.assertEqual((self.at(self.place, self.base), self.at(self.plant, self.base)), (D("0"), D("500")))

    def test_the_screens_of_an_incoming_check(self):
        line = self.received()
        self.client.force_login(self.owner)
        screen = self.client.get(reverse("manufacturing:quality"))
        self.assertContains(screen, f'data-waiting="{line.invoice.invoice_number}" data-waiting-kind="purchase"')
        self.assertContains(screen, "كيماويات النيل")
        viewer = person(RoleCode.SITE_ENGINEER, "qc_incoming_viewer")                 # sees stock, not purchases: no supplier name, no invoice link
        self.assertFalse(user_has_permission(viewer, "purchases.view_purchase_invoices"))
        self.client.force_login(viewer)
        plain = self.client.get(reverse("manufacturing:quality"))
        self.assertContains(plain, f'data-waiting="{line.invoice.invoice_number}"')
        self.assertNotContains(plain, "كيماويات النيل")
        self.assertNotContains(plain, reverse("purchases:detail", args=[line.invoice_id]))
        self.client.force_login(self.owner)
        url = reverse("manufacturing:quality_purchase_line", args=[line.pk])
        page = self.client.get(url)
        self.assertContains(page, 'name="destination"')
        self.assertContains(page, "data-item-criteria-form")
        self.assertContains(page, f'name="crit_{self.concentration.pk}"')
        answer = self.client.post(url, {"decision": "record", "passed": "95", "rejected": "5", "destination": self.plant.pk, f"crit_{self.concentration.pk}": "33"})
        check = QualityCheck.objects.get()
        self.assertRedirects(answer, f"{reverse('manufacturing:quality_check', args=[check.pk])}?lang=ar")
        detail = self.client.get(reverse("manufacturing:quality_check", args=[check.pk]))
        self.assertContains(detail, "data-stays-for-return")
        self.assertContains(self.client.get(reverse("manufacturing:quality")), f'data-return-line="{line.pk}"')
        # The item's criteria are edited on the same page.
        self.client.post(url, {"action": "criteria", "crit_name_ar_0": "التركيز", "crit_kind_0": "number", "crit_min_0": "31", "crit_max_0": "35",
                               "crit_name_ar_1": "اللون", "crit_kind_1": "pass_fail"})
        self.assertEqual([(c.name_ar, c.min_value) for c in QualityCriterion.objects.filter(item=self.base)], [("التركيز", D("31.000")), ("اللون", None)])
        refused = self.client.post(url, {"action": "criteria", "crit_name_ar_0": "التركيز", "crit_kind_0": "number", "crit_min_0": "40", "crit_max_0": "35"})
        self.assertContains(refused, "data-criteria-error")
        self.assertEqual(QualityCriterion.objects.filter(item=self.base).count(), 2)
        # A purchase line that is not in a hold place has no check page.
        other = self.received(location=self.plant, number="PI-PLAIN2")
        self.assertEqual(self.client.get(reverse("manufacturing:quality_purchase_line", args=[other.pk])).status_code, 404)


# ---------------------------------------------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------------------------------------------


def sentences(html):
    """The text of the paragraphs, labels, headings, buttons and messages, with what sits in ``<bdi>`` (codes, dates) left out."""

    html = html[html.index('class="op-title-row"'):]                                # the page itself, not the shell around it
    html = re.sub(r"<(script|style)\b.*?</\1>", " ", html, flags=re.S)
    html = re.sub(r"<bdi\b[^>]*>.*?</bdi>", " ", html, flags=re.S)
    found = []
    for tag in ("p", "small", "label", "h1", "h2", "h3", "button", "th", "summary"):
        for body in re.findall(rf"<{tag}\b[^>]*>(.*?)</{tag}>", html, flags=re.S):
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()
            if text:
                found.append(text)
    return found


def edges(text):
    """The first and last word of a line, without punctuation."""

    words = [word.strip(".,:;!?()«»\"'،؛·—-") for word in text.split()]
    words = [word for word in words if word]
    return (words[0], words[-1]) if words else ("", "")


class ScreenTests(QualityPlant):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def post_check(self, order, **data):
        fields = {f"crit_{self.acidity.pk}": "7", f"crit_{self.seal.pk}": "pass", "decision": "record"}
        fields.update(data)
        return self.client.post(reverse("manufacturing:quality_order", args=[order.pk]), fields)

    def test_the_quality_screen_lists_what_waits_in_both_languages(self):
        order = self.made(lot_no="LOT-S9")
        for lang, title, held, name in (("ar", "فحص الجودة", "الكمية المحجوزة", "حجز الجودة"), ("en", "Quality checks", "Quantity on hold", "Quality hold")):
            with self.subTest(lang=lang):
                page = self.client.get(reverse("manufacturing:quality"), {"lang": lang})
                self.assertEqual(page.status_code, 200)
                html = page.content.decode()
                self.assertIn(f"<h1>{title}</h1>", html)
                self.assertIn(held, html)
                self.assertIn(f'data-waiting="{order.number}" data-waiting-kind="order"', html)
                self.assertIn("LOT-S9", html)
                self.assertIn(f"<strong data-hold-place>{name}</strong>", html)
                self.assertIn("<td dir=\"ltr\" data-waiting-qty>50</td>", html)
                self.assertIn(f'href="{reverse("manufacturing:quality_order", args=[order.pk])}?lang={lang}"', html)
        self.assertContains(self.client.get(reverse("manufacturing:home")), "data-quality-nav")
        self.assertNotContains(self.client.get(reverse("manufacturing:quality"), {"lang": "en"}), "فحص الجودة")

    def test_an_empty_screen_offers_to_set_up_the_hold_place_for_purchases(self):
        page = self.client.get(reverse("manufacturing:quality"))
        self.assertContains(page, "data-waiting-empty")
        self.assertContains(page, "data-prepare-hold")
        self.assertIsNone(self.hold())                                               # opening the page makes nothing
        self.client.post(reverse("manufacturing:quality"), {"action": "prepare"})
        self.assertEqual(self.hold().location_code, f"QC-{self.plant.entity_id}")
        self.assertNotContains(self.client.get(reverse("manufacturing:quality")), "data-prepare-hold")

    def test_forty_eight_and_two_through_the_check_form(self):
        order = self.made()
        url = reverse("manufacturing:quality_order", args=[order.pk])
        page = self.client.get(url)
        self.assertContains(page, 'id="qc-passed" name="passed" value="50"')        # a plain number, never «50.000» or a thousands comma
        for marker in ('data-decision="record"', 'data-decision="hold"', 'data-decision="reject"', f'name="crit_{self.acidity.pk}"', "من 6 إلى 8", "المصنع"):
            self.assertContains(page, marker)
        answer = self.post_check(order, passed="48", rejected="2")
        check = QualityCheck.objects.get(kind=CheckKind.FINAL)
        self.assertRedirects(answer, f"{reverse('manufacturing:quality_check', args=[check.pk])}?lang=ar")
        self.assertEqual((check.passed_quantity, check.rejected_quantity, self.at(self.plant), self.at(self.hold())), (D("48.000"), D("2.000"), D("48"), D("0")))
        for lang, result, release in (("ar", "مقبول جزئيًا", "تحويل الإفراج"), ("en", "Partly passed", "Release transfer")):
            detail = self.client.get(reverse("manufacturing:quality_check", args=[check.pk]), {"lang": lang})
            self.assertContains(detail, f'data-check-result="partly">{result}</span>')
            self.assertContains(detail, release)
            self.assertContains(detail, f"{check.number}-REL")
            self.assertContains(detail, f"{check.number}-REJ")
        self.assertContains(self.client.get(url), "data-nothing-waiting")
        self.assertNotContains(self.client.get(url), "data-check-form")

    def test_the_hold_and_reject_buttons(self):
        order = self.made()
        refused = self.post_check(order, decision="hold", passed="50")
        self.assertContains(refused, "اكتب ملاحظة توضّح سبب استمرار الحجز.")
        self.assertEqual(QualityCheck.objects.filter(kind=CheckKind.FINAL).count(), 0)
        self.post_check(order, decision="hold", passed="50", note="مستنيين المعمل")   # «استمرار الحجز» ignores the quantity typed
        held = QualityCheck.objects.get(kind=CheckKind.FINAL)
        self.assertEqual((held.result, held.passed_quantity, self.at(self.hold())), (CheckResult.HELD, D("0"), D("50")))
        self.post_check(order, decision="reject", passed="50")                        # «ارفض الكمية كلها» rejects everything that waits
        rejected = QualityCheck.objects.filter(kind=CheckKind.FINAL).latest("pk")
        self.assertEqual((rejected.result, rejected.rejected_quantity, self.at(self.hold()), self.at(self.plant)), (CheckResult.REJECTED, D("50.000"), D("0"), D("0")))

    def test_a_refusal_keeps_what_was_typed(self):
        order = self.made()
        answer = self.post_check(order, passed="48", rejected="2", **{f"crit_{self.acidity.pk}": "9.5"})
        self.assertContains(answer, "data-check-error")
        self.assertContains(answer, "في معيار غير مطابق")
        self.assertContains(answer, 'name="passed" value="48"')
        self.assertContains(answer, 'value="9.5"')
        self.assertEqual(self.at(self.hold()), D("50"))

    def test_the_recipe_page_sets_release_and_the_criteria(self):
        plain = services.save_recipe({"product": make_item(item_code="FG-P3", item_name="معطر", is_stock_tracked=True), "output_quantity": "10"},
                                     [(self.base, "20")], self.owner)
        url = reverse("manufacturing:recipe", args=[plain.pk])
        page = self.client.get(url)
        self.assertContains(page, 'data-needs-release="0"')
        self.assertContains(page, "data-criteria-none")
        self.assertContains(page, "data-produce-form")
        self.client.post(url, {"action": "quality", "needs_release": "1", "crit_name_ar_0": "الرائحة", "crit_name_en_0": "Scent", "crit_kind_0": "pass_fail",
                               "crit_name_ar_1": "الكثافة", "crit_kind_1": "number", "crit_min_1": "1.01", "crit_max_1": "1.05"})
        plain.refresh_from_db()
        self.assertEqual((plain.needs_release, [(c.name_ar, c.kind) for c in plain.criteria.all()]), (True, [("الرائحة", "pass_fail"), ("الكثافة", "number")]))
        for lang, on, limits in (("ar", "محتاجة إفراج قبل البيع", "من 1.01 إلى 1.05"), ("en", "Needs release before sale", "1.01 to 1.05")):
            page = self.client.get(url, {"lang": lang})
            self.assertContains(page, f'data-needs-release="1">{on}</span>')
            self.assertContains(page, limits)
            self.assertContains(page, "data-recipe-criteria")
        refused = self.client.post(url, {"action": "quality", "crit_name_en_0": "Scent"})
        self.assertContains(refused, "data-quality-error")
        self.assertContains(refused, 'value="Scent"')
        plain.refresh_from_db()
        self.assertEqual((plain.needs_release, plain.criteria.count()), (True, 2))
        self.client.post(url, {"action": "quality"})                                  # unticked and no rows: straight into stock again
        plain.refresh_from_db()
        self.assertEqual((plain.needs_release, plain.criteria.count()), (False, 0))

    def test_the_order_page_says_the_product_will_be_held_then_shows_what_waits_and_the_checks(self):
        order = self.open()
        url = reverse("manufacturing:order", args=[order.pk])
        self.assertContains(self.client.get(url), "data-will-hold")
        for _ in order.stages:
            quality.advance(order, self.owner, results=self.good())
        self.client.post(url, {"action": "finish"})
        order.refresh_from_db()
        self.assertEqual((order.status, self.at(self.hold())), (OrderStatus.DONE, D("50")))
        page = self.client.get(url)
        self.assertNotContains(page, "data-will-hold")
        self.assertContains(page, "<strong data-hold-waiting>50</strong>")
        self.assertContains(page, "data-check-now")
        self.post_check(order, passed="48", rejected="2")
        for lang, word in (("ar", "اتفرج عنه"), ("en", "Released")):
            page = self.client.get(url, {"lang": lang})
            self.assertContains(page, word)
            self.assertContains(page, "<strong data-hold-released>48</strong>")
            self.assertContains(page, "<strong data-hold-rejected>2</strong>")
            self.assertContains(page, "<strong data-hold-waiting>0</strong>")
            self.assertNotContains(page, "data-check-now")
            self.assertContains(page, 'data-check-result="partly"')
        plain = services.save_recipe({"product": make_item(item_code="FG-P4", item_name="معطر", is_stock_tracked=True), "output_quantity": "10"}, [(self.base, "20")], self.owner)
        self.assertNotContains(self.client.get(reverse("manufacturing:order", args=[self.open(recipe=plain).pk])), "data-quality-card")

    def test_a_release_reversed_from_the_stock_screen_shows_on_the_check_and_the_order_waits_again(self):
        order = self.made()
        check = self.check(order, passed="50")
        cancel_stock_operation(check.release_operation_id, TODAY, "بالغلط", self.owner)
        self.assertContains(self.client.get(reverse("manufacturing:quality_check", args=[check.pk])), "data-release-reversed")
        self.assertContains(self.client.get(reverse("manufacturing:quality")), f'data-waiting="{order.number}"')
        self.assertContains(self.client.get(reverse("manufacturing:quality_order", args=[order.pk])), "data-check-reversed")

    def test_the_production_forms_do_not_offer_the_hold_place_and_an_order_cannot_be_opened_in_it(self):
        self.made(quantity="10")
        place = self.hold()
        for url in (reverse("manufacturing:orders"), reverse("manufacturing:recipe", args=[self.recipe.pk])):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertNotIn(place, list(page.context["locations"]))
                self.assertIn(self.plant, list(page.context["locations"]))
        with self.assertRaisesMessage(ValidationError, "حجز الجودة مكان انتظار للفحص مش مكان إنتاج"):
            orders.create_order({"recipe": self.recipe, "location": place, "quantity": "10"}, self.owner)
        from types import SimpleNamespace

        from . import make_to_order

        offered = list(make_to_order.locations_for(SimpleNamespace(entity=None)))     # the make-to-order form's own list
        self.assertEqual((place in offered, self.plant in offered), (False, True))
        lab = make_location(location_code="QC-LAB", name_ar="مخزن المعمل")             # a warehouse the owner named that way himself is still a warehouse
        self.assertIn(lab, list(quality.production_places(Location.objects.all())))
        self.assertIn(lab, list(quality.destinations(place)))
        self.assertFalse(quality.is_hold_place(lab))

    def test_the_run_page_says_the_product_is_on_hold_and_leads_to_its_check(self):
        order = self.made()
        for lang, text in (("ar", "منتج التشغيلة دي دخل حجز الجودة"), ("en", "entered the quality hold")):
            page = self.client.get(reverse("manufacturing:run", args=[order.run.pk]), {"lang": lang})
            self.assertContains(page, "data-run-held")
            self.assertContains(page, text)
            self.assertContains(page, f'{reverse("manufacturing:quality_order", args=[order.pk])}?lang={lang}')
        plain = services.save_recipe({"product": make_item(item_code="FG-P5", item_name="معطر", is_stock_tracked=True), "output_quantity": "10"}, [(self.base, "20")], self.owner)
        self.assertNotContains(self.client.get(reverse("manufacturing:run", args=[self.made(recipe=plain).run.pk])), "data-run-held")

    def test_the_pages_use_no_banned_word_and_no_arabic_line_starts_or_ends_with_a_latin_word(self):
        import pathlib

        from django.conf import settings

        root = pathlib.Path(settings.BASE_DIR)
        files = ["manufacturing/quality.py", "manufacturing/quality_views.py"] + [f"templates/manufacturing/{name}" for name in (
            "quality.html", "quality_check.html", "quality_check_detail.html", "_quality_inputs.html", "_quality_criteria_form.html",
            "_quality_checks_table.html", "_quality_order.html", "_quality_recipe.html")]
        for name in files:
            text = (root / name).read_text(encoding="utf-8")
            for word in BANNED:
                self.assertNotIn(word, text, name)
        for label, words in (("screens", quality_views.WORDS["ar"]), ("rules", quality.MESSAGES["ar"])):
            for key, text in words.items():
                first, last = edges(text)
                with self.subTest(words=label, key=key):
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)
                    self.assertFalse(first.startswith("{number") or last.endswith("number}"), text)   # a check's code never opens or closes the line
        order = self.made(lot_no="LOT-S8")
        check = self.check(order, passed="40", note="عشرة لسه في المعمل")
        pages = [reverse("manufacturing:quality"), reverse("manufacturing:quality_order", args=[order.pk]), reverse("manufacturing:quality_check", args=[check.pk]),
                 reverse("manufacturing:recipe", args=[self.recipe.pk]), reverse("manufacturing:order", args=[order.pk])]
        for url in pages:
            html = self.client.get(url).content.decode()
            for word in BANNED:
                self.assertNotIn(word, html, url)
            if "/quality/" not in url:
                continue                                                             # the older pages are not this change's to judge
            for text in sentences(html):
                first, last = edges(text)
                with self.subTest(url=url, text=text[:50]):
                    self.assertFalse(LATIN_WORD.match(first) or LATIN_WORD.match(last), text)


# ---------------------------------------------------------------------------------------------------------------
# The quality certificate
# ---------------------------------------------------------------------------------------------------------------


class CertificateTests(QualityPlant):
    def setUp(self):
        super().setUp()
        self.order = self.made()
        self.check_ = self.check(self.order, passed="50")
        self.url = reverse("manufacturing:quality_certificate", args=[self.check_.pk])
        self.client.force_login(self.owner)

    def send(self, **extra):
        return self.client.post(self.url, {"file": upload(PDF, "coa.pdf", "application/pdf"), "kind": "document", "stage": "report", **extra})

    def test_a_certificate_is_kept_on_the_check_and_shows_on_its_page(self):
        answer = self.send()
        self.assertEqual(answer.status_code, 201)
        row = Attachment.objects.get()
        self.assertEqual((row.owner, row.stage, row.kind, row.entity_id, row.uploaded_by), (self.check_, "report", "document", self.plant.entity_id, self.owner))
        page = self.client.get(reverse("manufacturing:quality_check", args=[self.check_.pk]))
        self.assertContains(page, f'data-attachment="{row.pk}"')
        self.assertContains(page, "شهادة الجودة")
        self.assertContains(page, "hesba/js/attachments.js")
        self.assertEqual(self.client.get(reverse("attachments:file", args=[row.pk])).status_code, 200)
        self.assertEqual(self.client.post(reverse("attachments:delete", args=[row.pk])).status_code, 200)
        self.assertFalse(Attachment.objects.exists())

    def test_only_the_certificate_category_and_only_real_files(self):
        self.assertEqual(self.send(stage="receive").status_code, 400)                 # a check takes a quality certificate, nothing else
        self.assertEqual(self.client.post(self.url, {"file": upload(b"<svg xmlns='http://www.w3.org/2000/svg'/>", "x.svg", "image/svg+xml")}).status_code, 400)
        self.assertEqual(self.client.post(self.url, {}).status_code, 400)
        self.assertFalse(Attachment.objects.exists())

    def test_a_viewer_reads_the_certificate_and_cannot_add_or_delete_one(self):
        self.send()
        row = Attachment.objects.get()
        viewer = person(RoleCode.SITE_ENGINEER, "qc_cert_viewer")
        self.client.force_login(viewer)
        page = self.client.get(reverse("manufacturing:quality_check", args=[self.check_.pk]))
        self.assertContains(page, f'data-attachment="{row.pk}"')
        self.assertNotContains(page, "data-att-upload")
        self.assertNotContains(page, "data-att-delete")
        self.assertEqual(self.client.get(reverse("attachments:file", args=[row.pk])).status_code, 200)
        self.assertEqual(self.send().status_code, 403)
        self.assertEqual(self.client.post(reverse("attachments:delete", args=[row.pk])).status_code, 403)
        self.client.force_login(person(RoleCode.CASHIER, "qc_cert_cashier"))          # no stock view: nothing at all
        self.assertEqual(self.client.get(reverse("attachments:file", args=[row.pk])).status_code, 403)
        self.assertEqual(self.send().status_code, 403)
        self.assertEqual(Attachment.objects.count(), 1)


# ---------------------------------------------------------------------------------------------------------------
# Permissions and entities
# ---------------------------------------------------------------------------------------------------------------


class PermissionTests(QualityPlant):
    def urls(self, order, check):
        return [reverse("manufacturing:quality"), reverse("manufacturing:quality_order", args=[order.pk]), reverse("manufacturing:quality_check", args=[check.pk])]

    def test_seeing_needs_stock_view_and_recording_needs_the_transfer_right(self):
        order, other = self.made(), self.made("30")
        check = self.check(other, passed="30")
        self.client.force_login(person(RoleCode.CASHIER, "qc_cashier"))
        for url in self.urls(order, check):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("manufacturing:quality_order", args=[order.pk]), {"decision": "record", "passed": "50"}).status_code, 403)
        viewer = person(RoleCode.SITE_ENGINEER, "qc_reader")
        self.client.force_login(viewer)
        for url in self.urls(order, check):
            with self.subTest(url=url, user="viewer"):
                self.assertEqual(self.client.get(url).status_code, 200)
        form = self.client.get(reverse("manufacturing:quality_order", args=[order.pk]))
        self.assertContains(form, "data-check-view-only")
        self.assertNotContains(form, "data-check-form")
        self.assertNotContains(self.client.get(reverse("manufacturing:quality")), "data-prepare-hold")
        before = self.counts()
        for url, data in ((reverse("manufacturing:quality_order", args=[order.pk]), {"decision": "record", "passed": "50"}),
                          (reverse("manufacturing:quality"), {"action": "prepare"}),
                          (reverse("manufacturing:recipe", args=[self.recipe.pk]), {"action": "quality"})):
            with self.subTest(url=url, user="viewer"):
                self.assertEqual(self.client.post(url, data).status_code, 403)
        self.assertEqual((self.counts(), self.at(self.hold())), (before, D("50")))
        self.recipe.refresh_from_db()
        self.assertTrue(self.recipe.needs_release)

    def test_a_user_with_the_transfer_right_alone_releases_but_is_not_offered_the_reject(self):
        order = self.made()
        mover = limited("qc_mover3", "inventory.view_stock", "inventory.transfer_stock")
        self.client.force_login(mover)
        url = reverse("manufacturing:quality_order", args=[order.pk])
        page = self.client.get(url)
        self.assertContains(page, 'data-decision="record"')
        self.assertNotContains(page, 'data-decision="reject"')
        self.assertNotContains(page, 'name="rejected"')
        fields = {f"crit_{self.acidity.pk}": "7", f"crit_{self.seal.pk}": "pass"}
        refused = self.client.post(url, {"decision": "reject", **fields})
        self.assertContains(refused, "رفض كمية وشطبها من المخزون محتاج صلاحية تسوية المخزون.")
        self.assertEqual(self.at(self.hold()), D("50"))
        self.client.post(url, {"decision": "record", "passed": "50", **fields})
        self.assertEqual((self.at(self.plant), self.at(self.hold())), (D("50"), D("0")))
        # The criteria and the release switch stay with the items right.
        self.assertNotContains(self.client.get(reverse("manufacturing:recipe", args=[self.recipe.pk])), "data-quality-form")
        self.assertEqual(self.client.post(reverse("manufacturing:recipe", args=[self.recipe.pk]), {"action": "quality"}).status_code, 403)

    def test_no_cost_is_shown_on_the_quality_screens(self):
        order = self.made()
        check = self.check(order, passed="48", rejected="2")
        keeper = person(RoleCode.STOCK_KEEPER, "qc_keeper2")
        self.assertFalse(user_has_permission(keeper, "inventory.view_cost"))
        self.client.force_login(keeper)
        for url in self.urls(order, check):
            for lang in ("ar", "en"):
                text = self.client.get(url, {"lang": lang}).content.decode()
                for figure in ("40.00", "1,920.00", "2,000.00", "80.00"):
                    self.assertNotIn(figure, text, url)


class EntityTests(QualityPlant):
    def test_another_entitys_hold_checks_and_orders_are_not_offered_and_return_404(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import save_entity

        branch = save_entity({"code": "BRQ", "name_ar": "فرع العاشر", "activity_slug": "manufacturing", "sub_activity_slug": "chemicals"}, self.owner)
        branch_plant = make_location(location_code="BR-PLANT-Q", name_ar="مصنع الفرع", name_en="Branch plant", entity=branch)
        stock_in(self.base, branch_plant, 400, "20.00", movement_date=TODAY)
        near = self.made("50")
        far = orders.create_order({"recipe": self.recipe, "location": branch_plant, "quantity": "30"}, self.owner)
        for _ in far.stages:
            quality.advance(far, self.owner, results=self.good())
        orders.finish(far, self.owner)
        far.refresh_from_db()
        main = Entity.objects.get(is_main=True)
        near_place, far_place = quality.hold_place(main.pk, create=False), quality.hold_place(branch.pk, create=False)
        self.assertEqual((near_place.entity, far_place.entity, near_place == far_place), (main, branch, False))   # one hold place per entity
        self.assertEqual((self.at(near_place), self.at(far_place)), (D("50"), D("30")))
        far_check = quality.check_order(far, self.owner, passed="10", results=self.good())
        self.assertEqual((self.at(branch_plant), self.at(far_place), self.at(self.plant)), (D("10"), D("20"), D("0")))
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": main.pk, "next": "/dashboard/"})
        screen = self.client.get(reverse("manufacturing:quality"))
        self.assertContains(screen, f'data-waiting="{near.number}"')
        self.assertNotContains(screen, far.number)
        self.assertNotContains(screen, far_check.number)
        with working_in(main):
            self.assertEqual([row["subject"].order for row in quality.waiting_rows()], [near])
            self.assertEqual(list(quality.scoped_checks().filter(kind=CheckKind.FINAL)), [])
        for url in (reverse("manufacturing:quality_order", args=[far.pk]), reverse("manufacturing:quality_check", args=[far_check.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(reverse("manufacturing:quality_order", args=[far.pk]), {"decision": "record", "passed": "20"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("manufacturing:quality_certificate", args=[far_check.pk]),
                                          {"file": upload(PDF, "coa.pdf", "application/pdf"), "kind": "document", "stage": "report"}).status_code, 404)
        self.assertEqual(self.at(far_place), D("20"))
        # In the branch the two swap places.
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        screen = self.client.get(reverse("manufacturing:quality"))
        self.assertContains(screen, f'data-waiting="{far.number}"')
        self.assertContains(screen, f'data-check="{far_check.number}"')
        self.assertNotContains(screen, near.number)
        self.assertEqual(self.client.get(reverse("manufacturing:quality_order", args=[near.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("manufacturing:quality_check", args=[far_check.pk])).status_code, 200)

    def test_an_incoming_release_cannot_go_to_another_entitys_warehouse(self):
        from entities.services import save_entity

        branch = save_entity({"code": "BRQ2", "name_ar": "فرع بدر", "activity_slug": "manufacturing", "sub_activity_slug": "chemicals"}, self.owner)
        far_store = make_location(location_code="BR-STORE-Q", name_ar="مخزن الفرع", entity=branch)
        place = quality.hold_place(self.plant.entity_id)
        invoice = make_draft_purchase_invoice(supplier=make_supplier("SUP-E"), location=place, invoice_number="PI-E1", invoice_date=TODAY)
        add_purchase_line(invoice, self.base, "10", "20.00")
        post_purchase_invoice(invoice.pk, self.owner)
        self.assertNotIn(far_store, list(quality.destinations(place)))
        with self.assertRaisesMessage(ValidationError, "اختار المخزن اللي الكمية المقبولة هتتنقل له."):
            quality.check_purchase_line(PurchaseLine.objects.get(invoice_id=invoice.pk), self.owner, passed="10", destination=far_store, results={})
        self.assertEqual(self.at(place, self.base), D("10"))

    def test_another_entitys_warehouse_that_carries_the_hold_code_is_never_used_as_the_hold_place(self):
        from entities.services import save_entity

        branch = save_entity({"code": "BRQ3", "name_ar": "فرع السادات", "activity_slug": "manufacturing", "sub_activity_slug": "chemicals"}, self.owner)
        branch_plant = make_location(location_code="BR-PLANT-3", name_ar="مصنع الفرع", entity=branch)
        stock_in(self.base, branch_plant, 400, "20.00", movement_date=TODAY)
        # a warehouse of the main entity that the owner happened to code as the branch's hold place would be
        named = make_location(location_code=f"QC-{branch.pk}", name_ar="مخزن قديم", entity=self.plant.entity, is_selling_location=True)
        far = orders.create_order({"recipe": self.recipe, "location": branch_plant, "quantity": "30"}, self.owner)
        for _ in far.stages:
            quality.advance(far, self.owner, results=self.good())
        message = f"الكود QC-{branch.pk} متسجل على مخزن تاني؛ غيّر كود المخزن ده الأول عشان مكان حجز الجودة يتعمل."
        with self.assertRaisesMessage(ValidationError, message):
            orders.finish(far, self.owner)
        far.refresh_from_db()
        named.refresh_from_db()
        self.assertEqual((far.status, far.run_id, named.is_selling_location, self.at(named)), (OrderStatus.IN_PROGRESS, None, True, D("0")))
        self.assertIsNone(quality.hold_place(branch.pk, create=False))
        self.assertFalse(quality.is_hold_place(named))
        named.location_code = "OLD-STORE"
        named.save(update_fields=["location_code"])
        orders.finish(far, self.owner)
        place = quality.hold_place(branch.pk, create=False)
        self.assertEqual((place.entity, self.at(place), self.at(branch_plant)), (branch, D("30"), D("0")))


class NothingElseMovesTests(QualityPlant):
    def test_a_hold_check_and_the_criteria_write_no_stock_cost_or_journal_row(self):
        order = self.made()
        Projector().rebuild()
        before = (StockMovement.objects.count(), StockOperation.objects.count(), JournalEntry.objects.count(), JournalLine.objects.count(),
                  get_item_stock_value(self.soap), get_item_authoritative_average_cost(self.soap))
        self.check(order, note="مستنيين المعمل", results={})
        quality.save_recipe_quality(self.recipe, self.owner, needs_release=True, rows=[{"name_ar": "اللون", "kind": "pass_fail"}])
        Projector().rebuild()
        self.assertEqual((StockMovement.objects.count(), StockOperation.objects.count(), JournalEntry.objects.count(), JournalLine.objects.count(),
                          get_item_stock_value(self.soap), get_item_authoritative_average_cost(self.soap)), before)
