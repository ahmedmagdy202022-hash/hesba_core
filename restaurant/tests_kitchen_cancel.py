"""HG-107: «إلغاء التحضير», a kitchen preparation is cancelled whole from the dish's page (closes HG-102, question 1).

A plate prepared in the kitchen is an ordinary production run (``restaurant.kitchen`` → ``manufacturing.services.produce``).
A restaurant has the manufacturing module off, so the run's page is closed and nothing cancelled a preparation whole.
The stock screen «التحويلات والتسويات» therefore kept reversing a kitchen run's operations one at a time there
(``manufacturing.guards``, HG-102), which leaves a wrong average: 3 plates at 11.00, 2 sold, the recipe grows to 14.00 a
plate, 3 more made: 4 plates worth 53.00; reversing the first preparation's output from the screen left 1 plate worth
20.00 (pinned by ``tests_kitchen.KitchenRunOperationTests`` before this change).

Now the dish's page lists the dish's recent preparations and cancels one whole through ``cancel_run`` unchanged
(HG-100's rule included: once some plates have left, it is refused), under the same held action as the run's own page.
The stock screen refuses a preparation's operations and sends the person to the dish's page; where neither page can
open it reverses them as before.

The kitchen is ``tests_kitchen``'s: rice 20 kg at 30.00, lentils 10 kg at 50.00, a large koshary of 0.200 kg of rice
and 0.100 kg of lentils (11.00 a plate), and 10 cans of cola at 9.00 that nobody touches. The clock stands still on a
Wednesday at noon: nothing here depends on the hour or on the day of the week.
"""

import html
import re
from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal as D
from unittest import expectedFailure, mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from approvals.models import ApprovalRequest
from audit.models import AuditLog
from config.money import money_round
from hesba_testing.factories import make_item, make_location, stock_in
from inventory.models import StockMovement, StockOperation
from inventory.services import adjust_stock, get_item_authoritative_average_cost, get_item_stock_value
from ledger import reports as gl
from ledger.models import JournalEntry, JournalLine
from ledger.projector import Projector
from manufacturing.models import ProductionRun, Recipe, RecipeLine, RunStatus
from permissions.models import RoleCode
from permissions.services import user_has_permission

from . import kitchen
from .insights import save_ingredients
from .tests import person
from .tests_kitchen import KitchenSetup

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=dt_timezone.utc)   # a Wednesday, 12:00 in Cairo
DAY = date(2026, 11, 11)

DISH_AR = ("الحركة دي تابعة لتحضير «{dish}» رقم {number}، ومينفعش تتعكس من شاشة التحويلات والتسويات. "
           "ألغِ التحضير من صفحة الطبق، والأطباق بتخرج من المخزن وكل مكوّناتها بترجع مع بعض.")
DISH_EN = ("This operation belongs to the kitchen preparation {number} of “{dish}” and cannot be reversed from the transfers and adjustments screen. "
           "Cancel the preparation from the dish's page: its plates leave stock and all of their ingredients come back, together.")
REFUSED_AR = ("مينفعش تلغي التشغيلة دي، عشان في كمية من «{item}» خرجت من المخزن بعد ما التشغيلة اتسجلت "
              "(بيع أو تحويل أو تسوية أو استخدام في تشغيلة تانية). لو الرصيد محتاج تصحيح، اعمل تسوية مخزون عادية.")
CANNOT_AR = ("مينفعش يتلغي: في أطباق من «{dish}» خرجت من المخزن بعد التحضير ده (بيع أو تسوية أو تحويل). "
             "لو الرصيد محتاج تصحيح، اعمل تسوية مخزون عادية.")

ERROR_BOX = re.compile(r'<div class="op-message op-message-error"[^>]*>(.*?)</div>', re.S)
OWNER_LINK = re.compile(r'<a href="([^"]+)" data-owner-link>([^<]+)</a>')


class CancelSetup(KitchenSetup):
    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        self.assertEqual(timezone.localdate(), DAY)
        super().setUp()
        self.dish = kitchen.make_in_kitchen(self.koshary, self.owner)
        self.page_url = reverse("restaurant:dish", args=[self.dish.pk])

    # ---- reading the books ---------------------------------------------------------------------------------------

    def position(self, item):
        """(units at the hall, stock value, average cost) as Decimals."""

        return (self.stock(item), money_round(get_item_stock_value(item)), get_item_authoritative_average_cost(item))

    def kitchen_position(self):
        return {"plates": self.position(self.dish), "rice": self.position(self.rice), "lentil": self.position(self.lentil),
                "cola": self.position(self.cola)}

    def books(self, *, inventory, cogs):
        """The journal after the scenario: balanced, every control agrees with its report, and the inventory control and
        the cost of sales hold exactly what is said (the café chart has one inventory account)."""

        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        check = gl.reconciliation()
        self.assertTrue(check["balanced"])
        self.assertEqual([row for row in check["rows"] if row["difference"]], [])
        self.assertEqual({key: gl.control_balance(key) for key in ("inventory", "cogs")}, {"inventory": D(inventory), "cogs": D(cogs)})

    def everything(self):
        """All that a cancel could change: stock rows, operations, every item's position, the runs, the journal, the
        audit log and the approval requests."""

        Projector().rebuild()
        totals = JournalLine.objects.aggregate(d=Sum("debit"), c=Sum("credit"))
        return {"movements": StockMovement.objects.count(), "operations": StockOperation.objects.count(),
                "cancelled": StockOperation.objects.filter(status="cancelled").count(), "position": self.kitchen_position(),
                "runs": list(ProductionRun.objects.order_by("pk").values_list("pk", "status", "cancellation_reason")),
                "entries": JournalEntry.objects.count(), "debit": totals["d"], "credit": totals["c"],
                "audit": AuditLog.objects.count(), "requests": ApprovalRequest.objects.count()}

    # ---- the page ------------------------------------------------------------------------------------------------

    def cancel(self, run, reason="اتحضّر بالغلط", user=None, lang="ar", dish=None, follow=True, **extra):
        self.client.force_login(user or self.owner)
        url = reverse("restaurant:dish", args=[(dish or self.dish).pk])
        data = {"lang": lang, "action": "cancel_preparation", "run": str(run.pk if hasattr(run, "pk") else run), "reason": reason}
        data.update(extra)
        return self.client.post(url, data, follow=follow)

    def errors(self, page):
        return [html.unescape(re.sub(r"<[^>]+>", "", text)).strip() for text in ERROR_BOX.findall(page.content.decode())]

    def two_preparations(self):
        """3 plates at 11.00, 2 sold, the recipe grows to 14.00 a plate, 3 more made: 4 plates worth 53.00, 13.25 each."""

        first = kitchen.prepare(self.dish, "3", self.owner)
        self.pay(self.order(self.dish, 2))
        save_ingredients(self.dish, [(self.rice, "0.300"), (self.lentil, "0.100")], self.owner)
        second = kitchen.prepare(self.dish, "3", self.owner)
        self.assertEqual(self.position(self.dish), (D("4.000"), D("53.00"), D("13.2500")))
        return first, second

    def operations(self, run):
        return [run.output_operation] + [row.operation for row in run.consumptions.select_related("operation").order_by("pk")]

    def from_the_stock_screen(self, operation, lang="ar", user=None):
        self.client.force_login(user or self.owner)
        return self.client.post(reverse("inventory:operation_cancel", args=[operation.pk]),
                                {"lang": lang, "reversal_date": DAY.isoformat(), "reason": "اتحضّر بالغلط"}, follow=True)


# -------------------------------------------------------------------------------------------------------------------
# The dish's page: its recent preparations
# -------------------------------------------------------------------------------------------------------------------


class PreparationListTests(CancelSetup):
    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_the_dish_page_lists_its_recent_preparations_newest_first(self):
        first = kitchen.prepare(self.dish, "2", self.owner)
        second = kitchen.prepare(self.dish, "1.5", self.owner)
        other = kitchen.make_in_kitchen(self.tea_with_ingredients(), self.owner)
        kitchen.prepare(other, "1", self.owner)                          # another dish's preparation is not listed
        self.client.force_login(self.owner)
        page = self.client.get(self.page_url)
        self.assertEqual([row["run"].pk for row in page.context["preparations"]], [second.pk, first.pk])
        body = page.content.decode()
        self.assertLess(body.index(f'data-preparation="{second.pk}"'), body.index(f'data-preparation="{first.pk}"'))
        for run, plates in ((first, "2"), (second, "1.5")):
            with self.subTest(run=run.number):
                block = self.block(body, run)
                for text in (run.number, DAY.isoformat(), plates, "11.0000", self.owner.username, "إلغاء التحضير", "سبب الإلغاء"):
                    self.assertIn(text, block)
                self.assertIn('name="reason"', block)
                self.assertIn(" required", block)
        english = self.client.get(self.page_url + "?lang=en").content.decode()
        for text in ("Recent preparations", "Cancel the preparation", "Reason for cancelling"):
            self.assertIn(text, english)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_only_the_last_ten_are_listed(self):
        runs = [kitchen.prepare(self.dish, "0.1", self.owner) for _ in range(12)]
        self.client.force_login(self.owner)
        page = self.client.get(self.page_url)
        self.assertEqual([row["run"].pk for row in page.context["preparations"]], [run.pk for run in reversed(runs)][:10])

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_a_refused_one_says_why_and_a_cancelled_one_shows_its_reason(self):
        first, second = self.two_preparations()
        self.client.force_login(self.owner)
        body = self.client.get(self.page_url).content.decode()
        refused = self.block(body, first)
        self.assertNotIn("data-cancel-preparation", refused)
        self.assertIn("data-cannot-cancel", refused)
        self.assertIn(html.escape(CANNOT_AR.format(dish=self.dish.item_name), quote=False), refused)
        self.assertIn("data-cancel-preparation", self.block(body, second))
        self.cancel(second, reason="الطبخة اتحرقت")
        body = self.client.get(self.page_url).content.decode()
        cancelled = self.block(body, second)
        self.assertIn('data-status="cancelled"', cancelled)
        self.assertIn("الطبخة اتحرقت", cancelled)
        self.assertNotIn("data-cancel-preparation", cancelled)
        english = self.client.get(self.page_url + "?lang=en").content.decode()
        self.assertIn("Cannot be cancelled: some “كشري كبير” left stock after this preparation", self.block(english, first))

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_who_may_not_cancel_sees_the_list_without_the_button(self):
        kitchen.prepare(self.dish, "2", self.owner)
        viewer = self.clerk("inventory.view_cost", username="cost_viewer")
        self.client.force_login(viewer)
        page = self.client.get(self.page_url)
        self.assertEqual(len(page.context["preparations"]), 1)
        self.assertNotContains(page, "data-cancel-preparation")

    # helpers

    def block(self, body, run):
        start = body.index(f'data-preparation="{run.pk}"')
        return body[start:body.index("</li>", start)]

    def tea_with_ingredients(self):
        save_ingredients(self.tea, [(self.rice, "0.010")], self.owner)
        return self.tea

    def clerk(self, *codes, username):
        from approvals.tests import clerk_with

        return clerk_with(*codes, username=username)


# -------------------------------------------------------------------------------------------------------------------
# Cancelling: the money and the stock
# -------------------------------------------------------------------------------------------------------------------


class CancelMoneyTests(CancelSetup):
    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_a_fresh_preparation_is_cancelled_whole(self):
        run = kitchen.prepare(self.dish, "5", self.owner)
        before = self.everything()
        self.assertEqual(before["position"], {"plates": (D("5.000"), D("55.00"), D("11.0000")), "rice": (D("19.000"), D("570.00"), D("30.0000")),
                                              "lentil": (D("9.500"), D("475.00"), D("50.0000")), "cola": (D("10.000"), D("90.00"), D("9.0000"))})
        self.books(inventory="1190.00", cogs="0.00")
        page = self.cancel(run, reason="الطبخة اتحرقت")
        self.assertContains(page, "اتلغى التحضير: الأطباق خرجت من المخزن وكل المكوّنات رجعت بتكلفتها.")
        run.refresh_from_db()
        self.assertEqual((run.status, run.cancellation_reason), (RunStatus.CANCELLED, "الطبخة اتحرقت"))
        after = self.everything()
        self.assertEqual(after["position"], {"plates": (D("0.000"), D("0.00"), D("0")), "rice": (D("20.000"), D("600.00"), D("30.0000")),
                                             "lentil": (D("10.000"), D("500.00"), D("50.0000")), "cola": (D("10.000"), D("90.00"), D("9.0000"))})
        self.assertEqual((after["movements"] - before["movements"], after["cancelled"] - before["cancelled"]), (3, 3))
        # every ingredient came back at the cost it left at
        for operation in self.operations(run):
            back = StockMovement.objects.get(reversal_of__stock_operation=operation)
            self.assertEqual(back.unit_cost, operation.unit_cost)
        cancel = AuditLog.objects.get(action="cancel_production")
        self.assertEqual((cancel.actor, cancel.object_id), (self.owner, str(run.pk)))
        self.books(inventory="1190.00", cogs="0.00")

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_two_preparations_the_first_is_refused_and_the_second_cancelled(self):
        first, second = self.two_preparations()
        self.books(inventory="1168.00", cogs="22.00")                 # cola 90.00 + rice 555.00 + lentils 470.00 + plates 53.00
        before = self.everything()
        self.assertEqual(before["position"]["rice"], (D("18.500"), D("555.00"), D("30.0000")))
        self.assertEqual(before["position"]["lentil"], (D("9.400"), D("470.00"), D("50.0000")))
        page = self.cancel(first)
        self.assertEqual(self.errors(page), [REFUSED_AR.format(item=self.dish.item_name)])
        self.assertEqual(self.everything(), before)                   # nothing at all moved

        self.cancel(second, reason="اتحضّر زيادة")
        second.refresh_from_db()
        self.assertEqual(second.status, RunStatus.CANCELLED)
        after = self.everything()
        self.assertEqual(after["position"], {"plates": (D("1.000"), D("11.00"), D("11.0000")), "rice": (D("19.400"), D("582.00"), D("30.0000")),
                                             "lentil": (D("9.700"), D("485.00"), D("50.0000")), "cola": (D("10.000"), D("90.00"), D("9.0000"))})
        self.books(inventory="1168.00", cogs="22.00")
        first.refresh_from_db()
        self.assertEqual(first.status, RunStatus.POSTED)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_the_english_refusal(self):
        first, _ = self.two_preparations()
        before = self.everything()
        page = self.cancel(first, lang="en")
        self.assertEqual(self.errors(page), ["This run cannot be cancelled: some “كشري كبير” has left the location since the run was recorded "
                                             "(a sale, a transfer, an adjustment or use in another run). To correct the stock, record an ordinary stock adjustment."])
        self.assertEqual(self.everything(), before)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_a_preparation_made_at_payment_is_refused_once_sold(self):
        self.pay(self.order(self.dish, 2))                            # nothing ready: 2 plates made on the spot, then sold
        run = ProductionRun.objects.get()
        self.assertEqual((run.output_quantity, self.position(self.dish)), (D("2.000"), (D("0.000"), D("0.00"), D("0"))))
        before = self.everything()
        page = self.cancel(run)
        self.assertEqual(self.errors(page), [REFUSED_AR.format(item=self.dish.item_name)])
        self.assertEqual(self.everything(), before)
        self.books(inventory="1168.00", cogs="22.00")

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_the_service_cancels_and_refuses_the_same_way(self):
        first, second = self.two_preparations()
        before = self.everything()
        with self.assertRaisesMessage(ValidationError, REFUSED_AR.format(item=self.dish.item_name)):
            kitchen.cancel_preparation(first, self.owner, "اتحضّر بالغلط")
        self.assertEqual(self.everything(), before)
        done = kitchen.cancel_preparation(second, self.owner, "اتحضّر بالغلط", dish=self.dish)
        self.assertEqual(done.status, RunStatus.CANCELLED)
        self.assertEqual(self.position(self.dish), (D("1.000"), D("11.00"), D("11.0000")))


# -------------------------------------------------------------------------------------------------------------------
# Refusals: nothing moves
# -------------------------------------------------------------------------------------------------------------------


class RefusalTests(CancelSetup):
    def setUp(self):
        super().setUp()
        self.run_ = kitchen.prepare(self.dish, "5", self.owner)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_no_reason_is_refused_with_nothing_moved(self):
        before = self.everything()
        for reason in ("", "   "):
            with self.subTest(reason=reason):
                page = self.cancel(self.run_, reason=reason)
                self.assertEqual(self.errors(page), ["اكتب سبب الإلغاء."])
                self.assertEqual(self.everything(), before)
        with self.assertRaises(ValidationError):
            kitchen.cancel_preparation(self.run_, self.owner, "")
        self.assertEqual(self.everything(), before)

    def test_a_get_moves_nothing(self):
        before = self.everything()
        self.client.force_login(self.owner)
        page = self.client.get(self.page_url, {"action": "cancel_preparation", "run": self.run_.pk, "reason": "x"})
        self.assertEqual(page.status_code, 200)
        self.assertEqual(self.everything(), before)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_a_crafted_post_for_another_dish_or_a_run_that_is_not_a_preparation_is_not_found(self):
        tea = self.tea
        save_ingredients(tea, [(self.rice, "0.010")], self.owner)
        tea = kitchen.make_in_kitchen(tea, self.owner)
        teas = kitchen.prepare(tea, "2", self.owner)
        plain = self.plain_run()
        before = self.everything()
        for label, run, dish in (("another dish's preparation on this page", teas, self.dish),
                                 ("this dish's preparation on another dish's page", self.run_, tea),
                                 ("a run that is not a kitchen preparation", plain, self.dish),
                                 ("no such run", 999999, self.dish), ("not a number", "abc", self.dish), ("empty", "", self.dish)):
            with self.subTest(label):
                response = self.cancel(run, dish=dish, follow=False)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(self.everything(), before)
        # the ingredients of the page were not touched by a cancel's POST either
        self.assertEqual(self.dish.dish_ingredients.count(), 2)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_the_service_refuses_what_is_not_this_dishs_preparation(self):
        save_ingredients(self.tea, [(self.rice, "0.010")], self.owner)
        tea = kitchen.make_in_kitchen(self.tea, self.owner)
        plain = self.plain_run()
        before = self.everything()
        for run, dish in ((self.run_, tea), (plain, None), (plain, self.dish)):
            with self.subTest(run=run.number, dish=dish), self.assertRaisesMessage(ValidationError, "التحضير ده مش من تحضيرات الطبق ده."):
                kitchen.cancel_preparation(run, self.owner, "اتحضّر بالغلط", dish=dish)
        self.assertEqual(self.everything(), before)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_an_already_cancelled_preparation_is_not_cancelled_twice(self):
        self.cancel(self.run_)
        before = self.everything()
        page = self.cancel(self.run_)
        self.assertEqual(self.errors(page), ["التشغيلة دي اتلغت بالفعل."])
        self.assertEqual(self.everything(), before)

    def plain_run(self):
        """A run of an ordinary recipe of the same plate: not a kitchen preparation."""

        recipe = Recipe.objects.create(code="PLAIN-1", product=self.dish, name="plain", output_quantity=D("1"), created_by=self.owner)
        RecipeLine.objects.create(recipe=recipe, component=self.rice, quantity=D("0.100"))
        from manufacturing.services import produce

        return produce(recipe, self.owner, batches="1", location=self.location)


class PermissionTests(CancelSetup):
    def setUp(self):
        super().setUp()
        self.run_ = kitchen.prepare(self.dish, "5", self.owner)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_a_cashier_and_a_waiter_are_refused_and_nothing_moves(self):
        before = self.everything()
        for role in (RoleCode.CASHIER, RoleCode.WAITER):
            user = person(role, f"cancel_{role}")
            with self.subTest(role=role):
                self.assertEqual(self.cancel(self.run_, user=user, follow=False).status_code, 403)
                with self.assertRaises(PermissionDenied):
                    kitchen.cancel_preparation(self.run_, user, "اتحضّر بالغلط")
                self.assertEqual(self.everything(), before)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_who_may_see_costs_but_not_prepare_is_refused(self):
        from approvals.tests import clerk_with

        viewer = clerk_with("inventory.view_cost", "master_data.manage_items", username="no_adjust")
        before = self.everything()
        page = self.cancel(self.run_, user=viewer)
        self.assertEqual(self.errors(page), ["إلغاء التحضير محتاج صلاحية إدارة الأصناف وصلاحية تسوية المخزون."])
        self.assertEqual(self.everything(), before)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_a_stock_keeper_and_a_manager_may_cancel_as_prepare_allows(self):
        for role in (RoleCode.STOCK_KEEPER, RoleCode.MANAGER):
            user = person(role, f"cancel_{role}")
            self.assertTrue(user_has_permission(user, kitchen.MANAGE) and user_has_permission(user, "inventory.adjust_stock"))
            run = kitchen.prepare(self.dish, "1", user)                    # prepare allows them
            with self.subTest(role=role):
                self.assertEqual(kitchen.cancel_preparation(run, user, "اتحضّر بالغلط").status, RunStatus.CANCELLED)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_the_owner_cancels_from_the_page(self):
        self.cancel(self.run_)
        self.run_.refresh_from_db()
        self.assertEqual(self.run_.status, RunStatus.CANCELLED)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_the_payment_stand_in_does_not_allow_a_cancel(self):
        from approvals.tests import clerk_with

        clerk = clerk_with("inventory.view_cost", "master_data.manage_items", kitchen.PREPARE_AT_PAYMENT, username="stand_in")
        before = self.everything()
        with self.assertRaises(PermissionDenied):
            kitchen.cancel_preparation(self.run_, clerk, "اتحضّر بالغلط")
        self.assertEqual(self.everything(), before)


class EntityScopeTests(CancelSetup):
    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_another_entitys_preparation_is_not_found_and_nothing_moves(self):
        from entities.services import save_entity

        run = kitchen.prepare(self.dish, "5", self.owner)                 # the main entity's hall
        branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي", "activity_slug": "restaurants", "sub_activity_slug": "cafe"}, self.owner)
        make_location(location_code="BR-HALL", name_ar="صالة الفرع", name_en="Branch hall", entity=branch, is_selling_location=True)
        before = self.everything()
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": branch.pk, "next": "/dashboard/"})
        self.assertEqual(self.client.get(self.page_url).context["preparations"], [])
        self.assertEqual(self.client.post(self.page_url, {"action": "cancel_preparation", "run": run.pk, "reason": "من الفرع"}).status_code, 404)
        self.assertEqual(self.everything(), before)
        from entities.current import working_in

        with working_in(branch), self.assertRaisesMessage(ValidationError, "التحضير ده مش من تحضيرات الطبق ده."):
            kitchen.cancel_preparation(run, self.owner, "من الفرع")
        self.assertEqual(self.everything(), before)


# -------------------------------------------------------------------------------------------------------------------
# A second person's approval (FB-27): the same held action as the run's own page
# -------------------------------------------------------------------------------------------------------------------


class ApprovalTests(CancelSetup):
    ACTION = "manufacturing.cancel_run"

    def setUp(self):
        super().setUp()
        from approvals.tests import clerk_with

        self.manager = person(RoleCode.MANAGER, "kc_manager")
        self.cook = clerk_with("master_data.manage_items", "inventory.adjust_stock", "inventory.view_cost", username="kc_cook")
        self.run_ = kitchen.prepare(self.dish, "5", self.owner)

    def hold(self):
        from approvals.services import set_held_actions

        set_held_actions([self.ACTION], self.owner)

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_nothing_is_held_until_the_owner_says_so(self):
        self.cancel(self.run_, user=self.cook)
        self.run_.refresh_from_db()
        self.assertEqual(self.run_.status, RunStatus.CANCELLED)
        self.assertFalse(ApprovalRequest.objects.exists())

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_a_held_cancel_waits_and_nothing_moves_then_applies_once_approved(self):
        from approvals import services as approvals
        from approvals.actions import ACTIONS

        self.hold()
        before = self.everything()
        page = self.cancel(self.run_, user=self.cook, reason="الطبخة اتحرقت")
        self.assertContains(page, "محتاجة موافقة")
        request = ApprovalRequest.objects.get()
        self.assertEqual((request.action, request.object_id, request.object_label, request.data, request.requested_by, request.status),
                         (self.ACTION, self.run_.pk, self.run_.number, {"reason": "الطبخة اتحرقت", "lang": "ar"}, self.cook, "pending"))
        waiting = self.everything()
        self.assertEqual({key: value for key, value in waiting.items() if key not in ("audit", "requests")},
                         {key: value for key, value in before.items() if key not in ("audit", "requests")})
        self.assertEqual((waiting["requests"], waiting["audit"] - before["audit"]), (1, 1))     # the request and its audit row only
        self.assertContains(self.client.get(self.page_url), "data-awaiting-approval")
        self.cancel(self.run_, user=self.cook)                           # asking again does not ask twice
        self.assertEqual(ApprovalRequest.objects.count(), 1)

        # the approvals screen links to the dish's page (the run's page is closed here) and it opens
        self.assertEqual(ACTIONS[self.ACTION].url(self.run_.pk), self.page_url)
        self.client.force_login(self.manager)
        inbox = self.client.get("/approvals/")
        self.assertContains(inbox, f'data-approval="{request.pk}"')
        self.assertContains(inbox, f'href="{self.page_url}?lang=ar"')
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.page_url).status_code, 200)

        done = approvals.approve(request.pk, self.manager)
        self.assertEqual((done.status, done.decided_by, done.note), ("done", self.manager, ""))
        self.run_.refresh_from_db()
        self.assertEqual((self.run_.status, self.run_.cancellation_reason), (RunStatus.CANCELLED, "الطبخة اتحرقت"))
        self.assertEqual(AuditLog.objects.get(action="cancel_production").actor, self.cook)     # in the requester's name
        self.assertEqual(self.everything()["position"]["plates"], (D("0.000"), D("0.00"), D("0")))
        self.books(inventory="1190.00", cogs="0.00")

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_a_cancel_that_would_be_refused_is_said_at_once_and_not_sent(self):
        self.hold()
        self.pay(self.order(self.dish, 1))
        before = self.everything()
        page = self.cancel(self.run_, user=self.cook)
        self.assertEqual(self.errors(page), [REFUSED_AR.format(item=self.dish.item_name)])
        self.assertEqual(self.everything(), before)
        self.assertFalse(ApprovalRequest.objects.exists())

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_plates_sold_while_the_request_waited_make_the_approval_fail_with_nothing_moved(self):
        from approvals import services as approvals

        self.hold()
        self.cancel(self.run_, user=self.cook)
        self.pay(self.order(self.dish, 1))
        before = self.everything()
        done = approvals.approve(ApprovalRequest.objects.get().pk, self.manager)
        self.assertEqual(done.status, "failed")
        self.assertIn("خرجت من المخزن", done.note)
        after = self.everything()
        self.assertEqual({key: value for key, value in after.items() if key != "audit"}, {key: value for key, value in before.items() if key != "audit"})

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_a_waiting_stock_reversal_of_a_preparation_fails_with_the_dish_message(self):
        from approvals import services as approvals

        held = ApprovalRequest.objects.create(action="inventory.reverse_operation", object_id=self.run_.output_operation_id,
                                              object_label="x", requested_by=self.cook,
                                              data={"reversal_date": DAY.isoformat(), "reason": "اتحضّر بالغلط"})
        before = self.everything()
        done = approvals.approve(held.pk, self.manager)
        self.assertEqual((done.status, done.note), ("failed", DISH_EN.format(number=self.run_.number, dish=self.dish.item_name)))
        after = self.everything()
        self.assertEqual({key: value for key, value in after.items() if key != "audit"}, {key: value for key, value in before.items() if key != "audit"})


# -------------------------------------------------------------------------------------------------------------------
# The stock screen: refuses a preparation's operations and sends the person to the dish's page
# -------------------------------------------------------------------------------------------------------------------


class StockScreenTests(CancelSetup):
    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_with_manufacturing_off_the_screen_refuses_and_links_to_the_dish_page(self):
        from manufacturing import guards

        run = kitchen.prepare(self.dish, "5", self.owner)
        self.assertFalse(guards.pages_open())
        before = self.everything()
        for lang, text, link in (("ar", DISH_AR, "افتح صفحة الطبق"), ("en", DISH_EN, "Open the dish")):
            for operation in self.operations(run):
                with self.subTest(lang=lang, operation=operation.reference_number):
                    page = self.from_the_stock_screen(operation, lang)
                    self.assertEqual(self.errors(page), [text.format(number=run.number, dish=self.dish.item_name)])
                    links = OWNER_LINK.findall(page.content.decode())
                    self.assertEqual(links, [(f"{self.page_url}?lang={lang}", link)] * 3)
                    self.assertEqual(self.everything(), before)
        listed = self.client.get(reverse("inventory:operations"))
        self.assertEqual([row.owner.kind for row in listed.context["page"].object_list], ["dish", "dish", "dish"])
        self.assertContains(listed, f"تابعة لتحضير «{self.dish.item_name}»")
        self.assertNotContains(listed, "/reverse/")                      # no reversal form left for them
        self.assertEqual(self.client.get(f"{self.page_url}?lang=ar").status_code, 200)   # and the link opens

    @expectedFailure  # HG-107: passes once «إلغاء التحضير» is built
    def test_the_wrong_average_of_hg_102_is_no_longer_possible(self):
        first, _ = self.two_preparations()
        before = self.everything()
        page = self.from_the_stock_screen(first.output_operation)
        self.assertEqual(self.errors(page), [DISH_AR.format(number=first.number, dish=self.dish.item_name)])
        self.assertEqual(self.everything(), before)
        self.assertEqual(self.position(self.dish), (D("4.000"), D("53.00"), D("13.2500")))

    def test_an_ordinary_adjustment_in_a_restaurant_is_still_reversed_from_the_screen(self):
        kitchen.prepare(self.dish, "2", self.owner)
        spill = adjust_stock("SPILL-1", DAY, self.rice, self.location, "out", "1", "اتكبّ", self.owner)
        self.assertEqual(self.position(self.rice), (D("18.600"), D("558.00"), D("30.0000")))
        page = self.from_the_stock_screen(spill)
        self.assertContains(page, "تم عكس حركة المخزون.")
        self.assertEqual(self.position(self.rice), (D("19.600"), D("588.00"), D("30.0000")))
        self.books(inventory="1190.00", cogs="0.00")

    def test_with_manufacturing_on_the_screen_still_links_to_the_run(self):
        from manufacturing import guards
        from settings_core.setup_services import set_module_enabled

        set_module_enabled(self.profile, "manufacturing", True, self.owner)
        run = kitchen.prepare(self.dish, "5", self.owner)
        mine = sorted(operation.pk for operation in self.operations(run))
        self.assertEqual({owner.kind for owner in guards.guarded(mine).values()}, {"run"})
        self.assertEqual({owner.url for owner in guards.guarded(mine).values()}, {reverse("manufacturing:run", args=[run.pk])})
        from approvals.actions import ACTIONS

        self.assertEqual(ACTIONS["manufacturing.cancel_run"].url(run.pk), reverse("manufacturing:run", args=[run.pk]))

    def test_with_neither_page_open_the_screen_reverses_as_before(self):
        # Both modules off after use (a business that changed activity): nobody is sent to a closed page.
        from manufacturing import guards
        from settings_core.models import FeatureFlag
        from settings_core.setup_services import module_flag_code

        run = kitchen.prepare(self.dish, "5", self.owner)
        FeatureFlag.objects.filter(code=module_flag_code("tables_orders")).update(enabled=False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.page_url).status_code, 403)
        mine = [operation.pk for operation in self.operations(run)]
        self.assertEqual(guards.guarded(mine), {})
        for operation in self.operations(run):
            self.assertContains(self.from_the_stock_screen(operation), "تم عكس حركة المخزون.")
        self.assertEqual(self.position(self.dish), (D("0.000"), D("0.00"), D("0")))
        self.books(inventory="1190.00", cogs="0.00")
