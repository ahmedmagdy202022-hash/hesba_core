"""AN8 (Ahmed: "ضيف طرق دفع زي دفع كاش او فيزا"): a card payment at the till lands in the card machine's
cashbox, pays the total exactly, and is never counted as cash in the drawer or the shift."""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse

from cashboxes.services import get_cashbox_balance
from hesba_testing.factories import make_cashbox
from sales.models import SalesInvoice
from shifts.services import open_shift_for, summary

from .pos_buttons import card_cashbox, save_card_cashbox
from .tests_pos import POS, PosTests


class CardAtTheTillTests(TestCase):
    setUp = PosTests.setUp
    sell = PosTests.sell

    def card_sale(self, tendered="0"):
        self.machine = make_cashbox(cashbox_code="CARD", name_ar="ماكينة الفيزا")
        save_card_cashbox(self.machine)
        data = {"line_count": "1", "item_0": str(self.shirt.pk), "qty_0": "1", "price_0": "240.00", "customer": "", "location": str(self.location.pk),
                "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": tendered, "print": "0", "pay_method": "card"}
        return self.client.post(POS, data)

    def test_a_card_sale_goes_to_the_card_machine_and_pays_the_total(self):
        self.card_sale(tendered="500")  # whatever the box said, a card pays the total: no change
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.cashbox, invoice.paid_now, invoice.remaining_due), (self.machine, D("240.00"), D("0.00")))
        self.assertEqual(get_cashbox_balance(self.machine), D("240.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("0.00"))
        today = self.client.get(POS, {"lang": "ar"}).context["summary"]
        self.assertEqual((today["total"], today["cash"], today["card"]), (D("240.00"), D("0.00"), D("240.00")))
        self.assertEqual(D(summary(open_shift_for(self.cashier))["cash_in"]), D("0.00"))  # not one pound of it in the drawer

    def test_the_shift_counts_only_the_drawer(self):
        self.card_sale()
        self.sell([(self.belt, "1", "120.00")], "120")
        shift = open_shift_for(self.cashier)
        self.assertEqual(D(summary(shift)["expected"]), shift.opening_float + D("120.00"))  # the float and the cash sale, never the card

    def test_cash_stays_as_it_was(self):
        self.sell([(self.shirt, "1", "240.00")], "300")
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.cashbox, invoice.paid_now), (self.cashbox, D("240.00")))
        page = self.client.get(POS, {"lang": "ar"}).content.decode()
        self.assertNotIn("data-pay=\"card\"", page)  # no card cashbox chosen: no card button

    def test_no_card_cashbox_means_no_card_sale(self):
        data = {"line_count": "1", "item_0": str(self.shirt.pk), "qty_0": "1", "price_0": "240.00", "location": str(self.location.pk),
                "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "240", "print": "0", "pay_method": "card"}
        self.client.post(POS, data)
        self.assertFalse(SalesInvoice.objects.exists())

    def test_the_till_settings_pick_the_card_cashbox_but_never_the_drawer(self):
        from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
        from permissions.models import RoleCode

        owner = make_user(username="card_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        url = reverse("sales:pos_buttons")
        machine = make_cashbox(cashbox_code="CARD2", name_ar="ماكينة")
        drawer = make_cashbox(cashbox_code="DRAWER", name_ar="الدرج", is_default=True)
        self.client.post(url, {"action": "card", "card_cashbox": drawer.pk, "lang": "ar"})
        self.assertIsNone(card_cashbox())
        self.client.post(url, {"action": "card", "card_cashbox": machine.pk, "lang": "ar"})
        self.assertEqual(card_cashbox(), machine)
        self.assertIn('data-pos-card', self.client.get(url, {"lang": "ar"}).content.decode())
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.post(url, {"action": "card", "card_cashbox": ""}).status_code, 403)
        self.assertEqual(card_cashbox(), machine)


class CardMachineAlertTests(TestCase):
    def test_the_card_machine_never_reads_as_a_drawer_running_low(self):
        from reports.tests_dashboard import prepared_client

        from django.utils import timezone

        prepared_client()
        machine = make_cashbox(cashbox_code="CARD", name_ar="ماكينة الفيزا")
        drawer = make_cashbox(cashbox_code="LOW", name_ar="درج فاضي")
        save_card_cashbox(machine)
        from reports.dashboard_data import build_alerts

        keys = [alert["key"] for alert in build_alerts({"cashboxes.view_finance"}, timezone.localdate())]
        self.assertIn(f"cashbox_low_{drawer.pk}", keys)
        self.assertNotIn(f"cashbox_low_{machine.pk}", keys)


class CardMachineIsNoDrawerTests(TestCase):
    setUp = PosTests.setUp

    def test_the_till_and_the_shift_never_offer_the_card_machine(self):
        machine = make_cashbox(cashbox_code="CARD", name_ar="ماكينة الفيزا")
        save_card_cashbox(machine)
        till = self.client.get(POS, {"lang": "ar"}).content.decode()
        self.assertNotIn(f'<option value="{machine.pk}"', till)
        shifts = self.client.get(reverse("shifts:mine"), {"lang": "ar"}).content.decode()
        self.assertNotIn(f'value="{machine.pk}"', shifts)
