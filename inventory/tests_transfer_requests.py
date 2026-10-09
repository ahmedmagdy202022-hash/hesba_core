"""HG-037: per-warehouse minimum/maximum, and transfers by request and receipt.

Main holds 40 of the item at 10 each; the branch holds 2 and should keep
between 5 and 20.
"""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import grant, make_item, make_location, make_role, make_seeded_role, make_user, make_user_profile
from ledger import reports as gl
from ledger.projector import Projector
from permissions.models import Permission, RoleCode
from reports.tests_dashboard import prepared_client

from . import transfer_requests as tr
from .models import LocationStockLevel, StockAdjustmentDirection, TransferRequestStatus
from .services import adjust_stock, get_item_location_stock_quantity, get_item_stock_quantity, get_item_stock_value


class RequestData(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = make_user(username="tr_owner")
        make_user_profile(user=self.owner, role=make_seeded_role(RoleCode.OWNER))
        self.keeper = make_user(username="tr_keeper")
        make_user_profile(user=self.keeper, role=make_seeded_role(RoleCode.STOCK_KEEPER))
        self.main = make_location(location_code="TR-MAIN", name_ar="الرئيسي", is_default=True)
        self.branch = make_location(location_code="TR-BR", name_ar="الفرع", name_en="Branch")
        self.item = make_item(item_code="TR-ITEM", item_name="صنف التحويل", is_stock_tracked=True)
        today = timezone.localdate()
        adjust_stock("TR-IN-1", today, self.item, self.main, StockAdjustmentDirection.IN, D("40"), "opening", self.owner, unit_cost=D("10"))
        adjust_stock("TR-IN-2", today, self.item, self.branch, StockAdjustmentDirection.IN, D("2"), "opening", self.owner, unit_cost=D("10"))

    def at(self, place):
        return get_item_location_stock_quantity(self.item, place)


class LevelTests(RequestData):
    def test_levels_flag_the_branch_and_suggest_the_main_store(self):
        tr.save_levels(self.branch, [(self.item, "5", "20")], self.owner)
        rows = tr.level_rows(self.branch, [self.main, self.branch])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row["below"])
        self.assertEqual((row["have"], row["refill"], row["source"], row["can_fill"]), (D("2"), D("18"), self.main, D("18")))
        self.assertEqual(tr.below_counts([self.main, self.branch]), {self.branch.pk: 1})
        # Blank both removes the level.
        tr.save_levels(self.branch, [(self.item, "", "")], self.owner)
        self.assertFalse(LocationStockLevel.objects.exists())

    def test_level_rules(self):
        for low, high in (("-1", "5"), ("10", "5"), ("abc", "")):
            with self.subTest(low=low, high=high), self.assertRaises(ValidationError):
                tr.save_levels(self.branch, [(self.item, low, high)], self.owner)

    def test_the_main_store_keeps_its_own_minimum_when_it_lends(self):
        tr.save_levels(self.branch, [(self.item, "5", "20")], self.owner)
        tr.save_levels(self.main, [(self.item, "30", "0")], self.owner)
        row = tr.level_rows(self.branch, [self.main])[0]
        self.assertEqual((row["spare"], row["can_fill"]), (D("10"), D("10")))   # 40 - its own minimum of 30

    def test_screens(self):
        tr.save_levels(self.branch, [(self.item, "5", "20")], self.owner)
        self.client.force_login(self.owner)
        detail = self.client.get(reverse("inventory:warehouse_detail", args=[self.branch.pk]) + "?lang=ar")
        self.assertContains(detail, "data-below-min")
        self.assertContains(detail, f'data-refill-from="{self.main.pk}"')
        hub = self.client.get(reverse("inventory:warehouses") + "?lang=en")
        self.assertContains(hub, 'data-below="1"')
        levels = self.client.post(reverse("inventory:warehouse_levels", args=[self.branch.pk]),
                                  {"lang": "ar", "item_0": str(self.item.pk), "min_0": "6", "max_0": "25"})
        self.assertEqual(levels.status_code, 302)
        self.assertEqual(LocationStockLevel.objects.get().max_quantity, D("25"))
        refill = self.client.get(reverse("inventory:transfer_request_new") + f"?to={self.branch.pk}&refill=1&lang=ar")
        self.assertEqual(refill.context["source"], self.main)
        self.assertEqual(refill.context["prefill"][0], (str(self.item.pk), "23"))   # 2 here, up to the new maximum of 25

    def test_a_viewer_cannot_set_levels(self):
        role = make_role(code="TR-VIEW")
        grant(role, Permission.objects.get(code="inventory.view_stock"))
        viewer = make_user(username="tr_viewer")
        make_user_profile(user=viewer, role=role)
        self.client.force_login(viewer)
        url = reverse("inventory:warehouse_levels", args=[self.branch.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {"item_0": str(self.item.pk), "min_0": "5"}).status_code, 403)


class RequestFlowTests(RequestData):
    def request(self, quantity="18"):
        return tr.create_request(self.main, self.branch, [(self.item, quantity)], self.keeper, "refill")

    def test_request_send_receive_moves_through_transit(self):
        request = self.request()
        self.assertEqual((self.at(self.main), self.at(self.branch)), (D("40"), D("2")))   # asking moves nothing
        tr.send(request, self.keeper)
        transit = tr.transit_location(self.main.entity_id)
        self.assertEqual((self.at(self.main), self.at(transit), self.at(self.branch)), (D("22"), D("18"), D("2")))
        self.assertEqual(get_item_stock_quantity(self.item), D("42"))   # the total never changes
        tr.receive(request, self.keeper)
        request.refresh_from_db()
        self.assertEqual(request.status, TransferRequestStatus.RECEIVED)
        self.assertEqual((self.at(self.main), self.at(transit), self.at(self.branch)), (D("22"), D("0"), D("20")))
        self.assertEqual(get_item_stock_value(self.item), D("420.00"))
        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])

    def test_partial_send_and_a_shortage_on_arrival(self):
        request = self.request()
        line = request.lines.get()
        tr.send(request, self.keeper, {line.pk: "15"})
        tr.receive(request, self.keeper, {line.pk: "14"})
        line.refresh_from_db()
        self.assertEqual((line.sent_quantity, line.received_quantity), (D("15"), D("14")))
        self.assertIsNotNone(line.loss_operation)
        transit = tr.transit_location(self.main.entity_id)
        self.assertEqual((self.at(self.main), self.at(transit), self.at(self.branch)), (D("25"), D("0"), D("16")))
        self.assertEqual(get_item_stock_quantity(self.item), D("41"))   # one unit lost on the way
        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])

    def test_a_shortage_needs_the_adjust_permission(self):
        role = make_role(code="TR-ONLY")
        for code in ("inventory.view_stock", "inventory.transfer_stock"):
            grant(role, Permission.objects.get(code=code))
        mover = make_user(username="tr_mover")
        make_user_profile(user=mover, role=role)
        request = self.request()
        tr.send(request, mover)
        line = request.lines.get()
        with self.assertRaisesMessage(ValidationError, "تسوية المخزون"):
            tr.receive(request, mover, {line.pk: "10"})
        tr.receive(request, mover)   # all of it arrived: no adjustment needed
        self.assertEqual(self.at(self.branch), D("20"))

    def test_rules(self):
        for source, destination, lines in ((None, self.branch, [(self.item, "1")]), (self.main, self.main, [(self.item, "1")]),
                                           (self.main, self.branch, []), (self.main, self.branch, [(self.item, "0")]),
                                           (self.main, self.branch, [(self.item, "1"), (self.item, "2")])):
            with self.subTest(lines=lines), self.assertRaises(ValidationError):
                tr.create_request(source, destination, lines, self.keeper)
        request = self.request(quantity="50")   # main has 40
        with self.assertRaisesMessage(ValidationError, "مش كفاية"):
            tr.send(request, self.keeper)
        with self.assertRaises(ValidationError):
            tr.receive(request, self.keeper)   # not sent yet
        self.assertEqual(self.at(self.main), D("40"))

    def test_cancel_before_and_after_sending(self):
        first = self.request()
        tr.cancel(first, self.keeper, "مش محتاجين")
        first.refresh_from_db()
        self.assertEqual(first.status, TransferRequestStatus.CANCELLED)
        second = self.request()
        tr.send(second, self.keeper)
        tr.cancel(second, self.keeper, "العربية اتأجلت")
        transit = tr.transit_location(self.main.entity_id)
        self.assertEqual((self.at(self.main), self.at(transit), self.at(self.branch)), (D("40"), D("0"), D("2")))
        third = self.request()
        tr.send(third, self.keeper)
        tr.receive(third, self.keeper)
        with self.assertRaises(ValidationError):
            tr.cancel(third, self.keeper, "late")
        with self.assertRaises(ValidationError):
            tr.cancel(first, self.keeper, "again")

    def test_the_screens_walk_the_flow(self):
        browser = Client()
        browser.force_login(self.keeper)
        made = browser.post(reverse("inventory:transfer_request_new"), {"lang": "ar", "source": str(self.main.pk), "destination": str(self.branch.pk),
                                                                       "item_0": str(self.item.pk), "qty_0": "5"})
        self.assertEqual(made.status_code, 302)
        from .models import TransferRequest

        request = TransferRequest.objects.get()
        url = reverse("inventory:transfer_request", args=[request.pk])
        page = browser.get(url + "?lang=ar")
        self.assertContains(page, "data-send")
        line = request.lines.get()
        self.assertEqual(browser.post(url, {"lang": "ar", "action": "send", f"qty_{line.pk}": "5"}).status_code, 302)
        self.assertContains(browser.get(url + "?lang=en"), "data-receive")
        self.assertEqual(browser.post(url, {"lang": "ar", "action": "receive"}).status_code, 302)
        self.assertEqual(self.at(self.branch), D("7"))
        listing = browser.get(reverse("inventory:transfer_requests") + "?show=all&lang=ar")
        self.assertContains(listing, request.number)
        self.assertContains(listing, "اتستلم")

    def test_who_may_send(self):
        cashier = make_user(username="tr_cashier")
        make_user_profile(user=cashier, role=make_seeded_role(RoleCode.CASHIER))
        request = self.request()
        browser = Client()
        browser.force_login(cashier)
        response = browser.post(reverse("inventory:transfer_request", args=[request.pk]), {"action": "send"})
        self.assertIn(response.status_code, (302, 403))
        self.assertEqual(self.at(self.main), D("40"))
