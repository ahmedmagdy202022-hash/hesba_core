"""SHIFT-001: expected cash from the cashier's own movements; differences posted on purpose."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxDirection, CashboxOperation
from cashboxes.services import get_cashbox_balance
from closing.models import Period
from hesba_testing.factories import make_cashbox, make_cashbox_movement, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.services import record_customer_payment

from .models import Shift
from .services import close_shift, open_shift, post_difference, summary


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class ShiftSetup(TestCase):
    def setUp(self):
        prepared_client()
        Period.objects.create(period_code="S", name="s", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "shift_owner")
        self.cashier = person(RoleCode.CASHIER, "shift_cashier")
        self.location = make_location(location_code="SHOP", is_default=True)
        self.till = make_cashbox(cashbox_code="TILL", is_default=True)
        make_cashbox_movement(self.till, CashboxDirection.IN, D("1000.00"), movement_date=TODAY - timedelta(days=10))
        self.item = make_item(item_code="TEA", item_name="Tea", default_sale_price=D("50.00"))
        stock_in(self.item, self.location, 100, "30.00", movement_date=TODAY - timedelta(days=10))
        recalculate_item_average_cost(self.item)

    def pos_sale(self, qty, tendered):
        return self.client.post(reverse("sales:pos"), {"line_count": "1", "customer": "", "location": str(self.location.pk), "cashbox": str(self.till.pk),
                                                       "discount": "0", "tendered": tendered, "print": "0", "item_0": str(self.item.pk), "qty_0": qty, "price_0": "50.00"})


class ShiftTests(ShiftSetup):
    def test_expected_cash_counts_only_this_cashiers_movements_in_the_shift(self):
        self.client.force_login(self.cashier)
        self.client.post(reverse("shifts:mine"), {"action": "open", "cashbox": self.till.pk, "opening_float": "200"})
        shift = Shift.objects.get()
        self.pos_sale("3", "150")                   # +150
        self.pos_sale("2", "100")                   # +100
        record_customer_payment("CP-S", TODAY, make_customer(), self.till, D("40.00"), self.cashier)  # +40 collection
        record_customer_payment("CP-O", TODAY, make_customer(customer_code="O"), self.till, D("999.00"), self.owner)  # someone else: not counted
        data = summary(shift, timezone.now())
        self.assertEqual((data["expected"], data["invoices"], data["groups"]["sales"], data["groups"]["collections"]), ("490.00", 2, "250.00", "40.00"))
        page = self.client.get(reverse("shifts:mine"))
        self.assertContains(page, "data-shift-open")
        self.assertContains(page, "490.00")
        response = self.client.post(reverse("shifts:mine"), {"action": "close", "counted_cash": "480"})
        shift.refresh_from_db()
        self.assertRedirects(response, f"{reverse('shifts:detail', args=[shift.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertEqual((shift.status, shift.expected_cash, shift.counted_cash, shift.difference), ("closed", D("490.00"), D("480.00"), D("-10.00")))
        self.assertContains(self.client.get(reverse("shifts:detail", args=[shift.pk])), "عجز")
        self.assertEqual(set(AuditLog.objects.filter(module="shifts").values_list("action", flat=True)), {"open_shift", "close_shift"})

    def test_one_open_shift_per_cashier_and_refusals(self):
        open_shift(self.cashier, self.till, D("0"))
        with self.assertRaisesMessage(ValidationError, "عندك وردية مفتوحة"):
            open_shift(self.cashier, self.till, D("0"))
        with self.assertRaises(ValidationError):
            open_shift(self.owner, self.till, D("-1"))
        shift = Shift.objects.get()
        close_shift(shift, D("0"), self.cashier)
        with self.assertRaisesMessage(ValidationError, "مقفولة"):
            close_shift(shift, D("0"), self.cashier)

    def test_manager_posts_a_shortage_or_overage_once_and_the_cashier_cannot(self):
        shift = open_shift(self.cashier, self.till, D("100.00"))
        close_shift(shift, D("95.00"), self.cashier)
        before = get_cashbox_balance(self.till)
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.post(reverse("shifts:detail", args=[shift.pk])).status_code, 403)
        mine = open_shift(self.owner, self.till, D("0"))  # the manager is on a shift of their own
        self.client.force_login(self.owner)
        self.client.post(reverse("shifts:detail", args=[shift.pk]))
        shift.refresh_from_db()
        self.assertEqual(before - get_cashbox_balance(self.till), D("5.00"))
        self.assertEqual(CashboxOperation.objects.get(reference_number=f"SHIFT-{shift.pk}-DIFF").operation_type, "direct_out")
        with self.assertRaisesMessage(ValidationError, "اتسجل قبل كده"):
            post_difference(shift, self.owner)
        # The posted difference belongs to the settled shift, not to the manager's open one.
        self.assertEqual(summary(mine, timezone.now())["expected"], "0.00")
        over = open_shift(self.cashier, self.till, D("0"))
        close_shift(over, D("7.00"), self.cashier)
        post_difference(over, self.owner)
        self.assertEqual(CashboxOperation.objects.get(reference_number=f"SHIFT-{over.pk}-DIFF").operation_type, "direct_in")

    def test_privacy_pos_banner_and_english(self):
        shift = open_shift(self.owner, self.till, D("0"))
        close_shift(shift, D("0"), self.owner)
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("shifts:detail", args=[shift.pk])).status_code, 403)
        self.assertContains(self.client.get(reverse("sales:pos")), "افتح وردية")
        open_shift(self.cashier, self.till, D("50"))
        self.assertContains(self.client.get(reverse("sales:pos")), "وردية من")
        self.assertContains(self.client.get(reverse("shifts:mine"), {"lang": "en"}), "Close shift")
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "shift_store"))
        self.assertEqual(self.client.get(reverse("shifts:mine")).status_code, 403)


class ShiftBeforeSellingTests(TestCase):
    """AN4 (Ahmed: "محدش يقدر يسجل بيع غير لما يفتح وردية")."""

    def setUp(self):
        from decimal import Decimal as D

        from django.utils import timezone

        from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
        from permissions.models import RoleCode
        from reports.tests_dashboard import prepared_client

        self.profile = prepared_client()  # retail: a till, so the rule starts on
        self.cashier = make_user(username="gate_cashier")
        make_user_profile(user=self.cashier, role=make_seeded_role(RoleCode.CASHIER))
        self.client.force_login(self.cashier)
        self.location = make_location(location_code="SHOP", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        self.customer = make_customer()
        self.item = make_item(item_code="SHIRT", default_sale_price=D("100.00"))
        stock_in(self.item, self.location, 10, "50.00", movement_date=timezone.localdate())

    def till(self):
        from sales.pos import walk_in_customer

        return self.client.post(reverse("sales:pos"), {"lang": "ar", "line_count": "1", "customer": str(walk_in_customer().pk), "location": str(self.location.pk),
                                                        "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "100", "print": "0",
                                                        "item_0": str(self.item.pk), "qty_0": "1", "price_0": "100.00"}, follow=True)

    def test_the_rule_starts_on_wherever_there_is_a_till(self):
        from settings_core.capabilities import capability_enabled, fits, suggested

        self.assertTrue(capability_enabled("shift_required"))
        self.assertNotIn("shift_required", suggested("commercial", "wholesale"))
        self.assertFalse(fits("contracting", "shift_required"))

    def test_no_till_sale_without_a_shift_and_the_bar_opens_one_in_place(self):
        from sales.models import SalesInvoice

        page = self.client.get(reverse("sales:pos"), {"lang": "ar"}).content.decode()
        self.assertIn("data-shift-gate", page)
        self.assertIn('name="next" value="/sales/pos/', page)
        self.assertNotIn("data-pos-shift", page)  # TRACK-ACTS W03: one way to open it, not the bar and a second button
        response = self.till()
        self.assertContains(response, "افتح وردية الأول عشان تسجّل بيع.")
        self.assertFalse(SalesInvoice.objects.exists())
        opened = self.client.post(reverse("shifts:mine"), {"lang": "ar", "action": "open", "cashbox": self.cashbox.pk, "opening_float": "0", "next": "/sales/pos/?lang=ar"})
        self.assertRedirects(opened, "/sales/pos/?lang=ar", fetch_redirect_response=False)
        page = self.client.get(reverse("sales:pos"), {"lang": "ar"}).content.decode()
        self.assertNotIn("data-shift-gate", page)
        self.assertIn("data-pos-shift", page)  # the open shift, and the way to close it
        self.till()
        self.assertEqual(SalesInvoice.objects.get().status, "posted")

    def test_an_invoice_is_saved_as_a_draft_but_not_posted_without_a_shift(self):
        from sales.models import SalesInvoice
        from hesba_testing.factories import make_draft_sales_invoice, add_sales_line

        invoice = make_draft_sales_invoice(customer=self.customer, location=self.location, cashbox=self.cashbox, invoice_number="SI-GATE")
        add_sales_line(invoice, self.item, 1, "100.00")
        self.assertIn("data-shift-gate", self.client.get(reverse("sales:detail", args=[invoice.pk]), {"lang": "ar"}).content.decode())
        response = self.client.post(reverse("sales:post", args=[invoice.pk]), {"lang": "ar"}, follow=True)
        self.assertContains(response, "افتح وردية الأول")
        self.assertEqual(SalesInvoice.objects.get(pk=invoice.pk).status, "draft")

    def test_the_bar_only_returns_to_this_site(self):
        opened = self.client.post(reverse("shifts:mine"), {"lang": "ar", "action": "open", "cashbox": self.cashbox.pk, "opening_float": "0", "next": "https://evil.example/"})
        self.assertRedirects(opened, f"{reverse('shifts:mine')}?lang=ar", fetch_redirect_response=False)

    def test_the_owner_can_switch_the_rule_off(self):
        from sales.models import SalesInvoice
        from settings_core.capabilities import set_capability_enabled

        set_capability_enabled(self.profile, "shift_required", False)
        page = self.client.get(reverse("sales:pos"), {"lang": "ar"}).content.decode()
        self.assertNotIn("data-shift-gate", page)
        self.assertIn("data-pos-shift", page)  # the open shift, and the way to close it
        self.till()
        self.assertEqual(SalesInvoice.objects.get().status, "posted")
