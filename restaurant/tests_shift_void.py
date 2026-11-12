"""Restaurants R13 (HG-094): the table bill takes the till's shift rule, and voiding a line the kitchen already has is controlled."""

from decimal import Decimal as D

from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from hesba_testing.factories import make_customer, make_item, stock_in
from inventory.models import StockMovement
from inventory.services import recalculate_item_average_cost
from ledger.models import JournalEntry
from ledger.projector import Projector
from manufacturing.models import ProductionRun
from permissions.models import RoleCode
from sales.models import CustomerLedgerEntry, CustomerPayment, SalesInvoice
from sales.pos import walk_in_customer
from settings_core.capabilities import capability_enabled, set_capability_enabled
from settings_core.models import ClientProfile
from shifts.services import close_shift, open_shift, summary

from . import kitchen
from . import services
from .insights import save_ingredients
from .models import Order, OrderLine, OrderStatus
from .tests import RestaurantSetup, person


TILL_REFUSAL = {"ar": "افتح وردية الأول عشان تسجّل بيع.", "en": "Open a shift first to record a sale."}


class ShiftRuleTests(RestaurantSetup):
    """With «وردية قبل البيع» on (a café's default), the table bill refuses and accepts exactly where the till does."""

    def setUp(self):
        super().setUp()
        self.cashier = person(RoleCode.CASHIER, "shift_cashier")
        self.grill = make_item(item_code="D-GRILL", item_name="مشويات", default_sale_price="120.00", is_stock_tracked=False)
        self.salad = make_item(item_code="D-SALAD", item_name="سلطة", default_sale_price="85.00", is_stock_tracked=False)
        self.client.force_login(self.cashier)

    def order(self, *items, table=None, **kwargs):
        order = services.open_order(self.owner, table=table or self.t1, **kwargs)
        for item, quantity in items:
            services.add_item(order, item, self.owner, quantity)
        return order

    def pay_from_screen(self, order, lang="ar", tendered="1000", **extra):
        return self.client.post(reverse("restaurant:order", args=[order.pk]),
                                {"lang": lang, "action": "pay", "cashbox": self.cashbox.pk, "discount": "0", "tendered": tendered, "print": "0", **extra}, follow=True)

    def till(self):
        """The till's own sale of one tea, posted the way its screen posts it (as in shifts/tests.py)."""

        return self.client.post(reverse("sales:pos"), {"lang": "ar", "line_count": "1", "customer": str(walk_in_customer().pk), "location": str(self.location.pk),
                                                        "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "15", "print": "0",
                                                        "item_0": str(self.tea.pk), "qty_0": "1", "price_0": "15.00"}, follow=True)

    def written(self):
        Projector().rebuild()
        return {"invoices": SalesInvoice.objects.count(), "payments": CustomerPayment.objects.count(), "cash": CashboxMovement.objects.count(),
                "stock": StockMovement.objects.count(), "journal": JournalEntry.objects.count(), "ledger": CustomerLedgerEntry.objects.count(),
                "plates": ProductionRun.objects.count(), "audit": AuditLog.objects.count()}

    def test_the_rule_is_on_for_a_cafe(self):
        self.assertTrue(capability_enabled("shift_required"))

    def test_without_a_shift_the_table_bill_is_refused_with_the_tills_words_and_nothing_is_written(self):
        rice = make_item(item_code="R-RICE", item_name="أرز", unit="كجم", is_stock_tracked=True)
        stock_in(rice, self.location, 20, "30.00")
        recalculate_item_average_cost(rice)
        save_ingredients(self.koshary, [(rice, "0.200")], self.owner)
        dish = kitchen.make_in_kitchen(self.koshary, self.owner)  # paying would make its plates first: it must not get that far
        order = self.order((dish, 2), (self.cola, 1))
        services.send_to_kitchen(order, self.owner)
        before = self.written()
        for lang in ("ar", "en"):
            with self.subTest(lang=lang):
                self.assertContains(self.pay_from_screen(order, lang), TILL_REFUSAL[lang])
        self.assertEqual(self.written(), before)
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice), (OrderStatus.OPEN, None))

    def test_the_pay_panel_says_why_and_opens_the_shift_in_place(self):
        order = self.order((self.grill, 1))
        url = reverse("restaurant:order", args=[order.pk])
        for lang, words in (("ar", "مفيش بيع يتسجل من غير وردية مفتوحة"), ("en", "No sale is recorded without an open shift")):
            with self.subTest(lang=lang):
                page = self.client.get(url, {"lang": lang}).content.decode()
                self.assertIn("data-pay-needs-shift", page)
                self.assertIn("data-shift-gate", page)
                self.assertIn(words, page)
                self.assertIn(f'name="next" value="{url}?lang={lang}"', page)
                self.assertNotIn("data-pay-form", page)  # no amounts to type before the refusal
        opened = self.client.post(reverse("shifts:mine"), {"lang": "ar", "action": "open", "cashbox": self.cashbox.pk, "opening_float": "0",
                                                           "next": f"{url}?lang=ar"})
        self.assertRedirects(opened, f"{url}?lang=ar", fetch_redirect_response=False)
        for lang, button in (("ar", "ادفع واقفل الطلب"), ("en", "Pay and close")):
            with self.subTest(lang=lang, shift="open"):
                page = self.client.get(url, {"lang": lang}).content.decode()
                self.assertIn("data-pay-form", page)
                self.assertIn(button, page)
                self.assertNotIn("data-shift-gate", page)
                self.assertIn(f'<option value="{self.cashbox.pk}" selected>', page)  # the bill goes to the shift's own cashbox

    def test_inside_an_open_shift_the_cash_belongs_to_that_shift(self):
        set_capability_enabled(ClientProfile.get_active(), "vat", True, user=self.owner)
        shift = open_shift(self.cashier, self.cashbox, D("500.00"))
        self.assertEqual(summary(shift, timezone.now())["expected"], "500.00")
        order = self.order((self.grill, 1), (self.salad, 1))
        self.pay_from_screen(order, tendered="250.00")
        order.refresh_from_db()
        # 120.00 + 85.00 = 205.00, VAT 14% per line 16.80 + 11.90 = 28.70: exactly 233.70 into the till, inside this shift.
        self.assertEqual((order.status, order.invoice.total_amount, order.invoice.tax_amount), (OrderStatus.PAID, D("233.70"), D("28.70")))
        movement = CashboxMovement.objects.get(sales_invoice=order.invoice)
        self.assertEqual((movement.cashbox, movement.amount, movement.created_by), (self.cashbox, D("233.70"), self.cashier))
        live = summary(shift, timezone.now())
        self.assertEqual((live["expected"], live["invoices"]), ("733.70", 1))
        set_capability_enabled(ClientProfile.get_active(), "vat", False, user=self.owner)
        second = self.order((self.grill, 1), (self.salad, 1), table=self.t2)
        self.pay_from_screen(second, tendered="205.00")
        self.assertEqual(summary(shift, timezone.now())["expected"], "938.70")  # + 205.00 with VAT off
        customer = make_customer(customer_code="C-ACC", name="شركة النور")
        on_account = self.order((self.grill, 1), table=self.t1, customer=customer)
        self.pay_from_screen(on_account, tendered="50.00", customer=customer.pk)
        on_account.refresh_from_db()
        self.assertEqual((on_account.invoice.total_amount, on_account.invoice.remaining_due), (D("120.00"), D("70.00")))
        self.assertEqual(summary(shift, timezone.now())["expected"], "988.70")  # only the 50.00 paid in cash reaches the drawer

    def test_the_till_and_the_table_refuse_and_accept_in_the_same_cases(self):
        profile = ClientProfile.get_active()
        shift = None

        def no_shift():
            pass

        def another_users_shift():
            open_shift(self.owner, self.cashbox, D("0"))

        def a_closed_shift():
            close_shift(open_shift(self.cashier, self.cashbox, D("0")), D("0"), self.cashier)

        def rule_off():
            set_capability_enabled(profile, "shift_required", False)

        def own_open_shift():
            nonlocal shift
            shift = open_shift(self.cashier, self.cashbox, D("0"))

        for name, arrange, accepted in (("no shift", no_shift, False), ("another user's shift", another_users_shift, False),
                                        ("a closed shift", a_closed_shift, False), ("the rule off", rule_off, True),
                                        ("their own open shift", own_open_shift, True)):
            with self.subTest(name):
                arrange()
                before = SalesInvoice.objects.count()
                till = self.till()
                till_sold = SalesInvoice.objects.count() - before
                order = self.order((self.tea, 1))
                table = self.pay_from_screen(order, tendered="15")
                table_sold = SalesInvoice.objects.count() - before - till_sold
                order.refresh_from_db()
                self.assertEqual((till_sold, table_sold), (1, 1) if accepted else (0, 0))
                self.assertEqual(order.status, OrderStatus.PAID if accepted else OrderStatus.OPEN)
                if not accepted:
                    self.assertContains(till, TILL_REFUSAL["ar"])
                    self.assertContains(table, TILL_REFUSAL["ar"])
                # tidy up for the next case
                Order.objects.filter(pk=order.pk, status=OrderStatus.OPEN).update(status=OrderStatus.CANCELLED)
                set_capability_enabled(profile, "shift_required", True)
                for open_one in self.owner.shifts.filter(status="open"):
                    close_shift(open_one, D("0"), self.owner)
        self.assertEqual(summary(shift, timezone.now())["invoices"], 2)  # with their own shift both sales are in it

    def test_a_waiter_never_sees_the_shift_bar_or_the_bill(self):
        waiter = person(RoleCode.WAITER, "shift_waiter")
        self.client.force_login(waiter)
        order = self.order((self.grill, 1))
        for lang in ("ar", "en"):
            with self.subTest(lang=lang):
                page = self.client.get(reverse("restaurant:order", args=[order.pk]), {"lang": lang}).content.decode()
                self.assertNotIn("data-shift-gate", page)  # opening a shift needs the selling right a waiter does not hold
                self.assertNotIn("data-pay-form", page)
                self.assertIn("data-send-kitchen", page)  # the floor's own actions stay
        self.assertEqual(self.pay_from_screen(order).status_code, 403)  # still the selling right's 403, not the shift message


    def test_a_crafted_pay_cannot_put_the_cash_in_another_entitys_cashbox(self):
        from entities.current import SESSION_KEY
        from entities.models import Entity
        from entities.services import main_entity
        from hesba_testing.factories import make_cashbox

        foreign = make_cashbox(cashbox_code="FAR", entity=Entity.objects.create(code="E2", name_ar="فرع تاني"))
        open_shift(self.cashier, self.cashbox, opening_float=D("0"))
        order = self.order((self.grill, 1))
        session = self.client.session
        session[SESSION_KEY] = main_entity().pk  # working in the main entity
        session.save()
        page = self.pay_from_screen(order, cashbox=foreign.pk)
        self.assertContains(page, "لازم يكون فيه مخزن بيع نشط")  # the form never lists it, so it is as if no cashbox was sent
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.OPEN)
        self.assertFalse(CashboxMovement.objects.filter(cashbox=foreign).exists())
        self.assertFalse(SalesInvoice.objects.exists())

class VoidTests(RestaurantSetup):
    """A line not sent yet goes freely; one the kitchen has needs the selling right and a reason, and is audited."""

    def setUp(self):
        super().setUp()
        self.waiter = person(RoleCode.WAITER, "void_waiter")
        self.cashier = person(RoleCode.CASHIER, "void_cashier")
        self.order = services.open_order(self.owner, table=self.t1, guests=2)
        self.sent = services.add_item(self.order, self.koshary, self.owner, 2)  # 2 × 60.00
        services.send_to_kitchen(self.order, self.owner)
        self.new = services.add_item(self.order, self.tea, self.owner, 1)  # 15.00, not sent
        self.url = reverse("restaurant:order", args=[self.order.pk])

    def void(self, user, line, lang="ar", **extra):
        self.client.force_login(user)
        return self.client.post(self.url, {"lang": lang, "action": "void", "line": line.pk, **extra}, follow=True)

    def total(self):
        return Order.objects.get(pk=self.order.pk).total

    def test_a_waiter_removes_a_line_the_kitchen_has_not_seen(self):
        self.assertEqual(self.total(), D("135.00"))
        self.void(self.waiter, self.new)
        self.assertFalse(OrderLine.objects.filter(pk=self.new.pk).exists())
        self.assertEqual(self.total(), D("120.00"))
        self.assertFalse(AuditLog.objects.filter(action="void_order_line").exists())  # nothing the kitchen made disappears

    def test_a_waiter_cannot_void_a_sent_line_even_with_a_reason_or_by_hand(self):
        self.assertEqual(self.void(self.waiter, self.sent, reason="الزبون مشي").status_code, 403)
        with self.assertRaises(PermissionDenied):
            services.void_line(self.sent, self.waiter, reason="الزبون مشي")
        self.sent.refresh_from_db()
        self.assertFalse(self.sent.voided)
        self.assertEqual(self.total(), D("135.00"))
        self.assertFalse(AuditLog.objects.filter(action="void_order_line").exists())

    def test_the_selling_right_still_needs_a_reason(self):
        for lang, words in (("ar", "اكتب سبب إلغاء السطر"), ("en", "Write why the line is voided")):
            with self.subTest(lang=lang):
                self.assertContains(self.void(self.cashier, self.sent, lang, reason="   "), words)
        self.sent.refresh_from_db()
        self.assertFalse(self.sent.voided)
        self.assertEqual(self.total(), D("135.00"))

    def test_with_the_right_and_a_reason_the_line_is_voided_and_audited(self):
        self.void(self.cashier, self.sent, reason="الطبق اتأخر والزبون رفضه")
        self.sent.refresh_from_db()
        self.assertTrue(self.sent.voided)
        self.assertEqual(self.total(), D("15.00"))  # only the tea is left
        log = AuditLog.objects.get(action="void_order_line")
        self.assertEqual((log.actor, log.module, log.object_id), (self.cashier, "restaurant", str(self.order.pk)))
        self.assertEqual(log.after_data, {"line": self.sent.pk, "item": "D-KOSH", "item_name": "كشري كبير", "quantity": "2.000", "amount": "120.00",
                                          "ticket": 1, "reason": "الطبق اتأخر والزبون رفضه"})
        self.assertEqual(timezone.localtime(log.created_at).date(), timezone.localdate())
        self.void(self.cashier, self.sent, reason="مرة تانية")  # voiding again changes nothing and audits nothing
        self.assertEqual(AuditLog.objects.filter(action="void_order_line").count(), 1)
        self.assertEqual(StockMovement.objects.filter(item=self.koshary).count(), 0)  # no write-off in this task
        invoice, _ = services.pay(self.order, self.owner, cashbox=self.cashbox, tendered=D("15"))
        self.assertEqual((invoice.total_amount, invoice.lines.count()), (D("15.00"), 1))  # the void never reaches the bill

    def test_crafted_posts_cannot_reach_another_order_or_a_closed_one(self):
        other = services.open_order(self.owner, table=self.t2)
        theirs = services.add_item(other, self.koshary, self.owner)
        services.send_to_kitchen(other, self.owner)
        self.void(self.cashier, theirs, reason="غلط")  # the line id belongs to table 2, the URL to table 1
        theirs.refresh_from_db()
        self.assertFalse(theirs.voided)
        self.assertEqual((self.total(), Order.objects.get(pk=other.pk).total), (D("135.00"), D("60.00")))
        services.pay(other, self.owner, cashbox=self.cashbox, tendered=D("60"))
        self.client.force_login(self.cashier)
        page = self.client.post(reverse("restaurant:order", args=[other.pk]), {"lang": "ar", "action": "void", "line": theirs.pk, "reason": "غلط"}, follow=True)
        self.assertContains(page, "الطلب ده اتقفل")
        theirs.refresh_from_db()
        self.assertFalse(theirs.voided)
        self.assertFalse(AuditLog.objects.filter(action="void_order_line").exists())

    def test_the_order_screen_offers_each_void_only_to_who_may_use_it(self):
        for user, may_void_sent in ((self.waiter, False), (self.cashier, True)):
            for lang, remove, void, reason in (("ar", "شيل", "إلغاء السطر", "سبب الإلغاء"), ("en", "Remove", "Void line", "Why it is voided")):
                with self.subTest(user=user.username, lang=lang):
                    self.client.force_login(user)
                    page = self.client.get(self.url, {"lang": lang}).content.decode()
                    self.assertIn(f">{remove}</button>", page)  # the unsent tea
                    self.assertEqual("data-void-sent" in page, may_void_sent)
                    self.assertEqual(f">{void}</button>" in page, may_void_sent)
                    self.assertEqual(f'name="reason" required maxlength="255" placeholder="{reason}"' in page, may_void_sent)
