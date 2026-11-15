"""Restaurants R10 (HG-119): pre-orders with a pick-up time and a deposit («طلبات مسبقة»).

R8's set-up (``tests_reservations.ReservationSetup``): the main entity (hall «HALL», till «TILL», card «CARD») and the Maadi
branch («BR-HALL», «BR-TILL»), the clock on Wednesday 11 Nov 2026 at noon in Cairo, VAT on at 14%, the «العربون» capability on,
Nour («C-NOUR») a named customer. The birthday cake «تورتة عيد ميلاد» is **450.00** on the menu (a service item), so one cake is
**513.00** with VAT and 450.00 without.

* **deposit 200.00:** Nour's account −200.00 and the till +200.00 when it is taken. «اتسلّم» opens a takeaway order at the agreed
  450.00; paying it in Nour's name, the deposit settles 200.00 and the drawer takes **313.00** (VAT on) or **250.00** (VAT off);
* **made the evening before:** «كيكة شوكولاتة» is made in the kitchen (0.500 kg of rice a cake, for the sake of the recipe).
  «حضّر» on Wednesday makes one into the hall's store; Thursday's bill sells it from there: one production run in all, the
  stock 0 → 1 → 0, the rice down once;
* **cancelling:** late (tonight) forfeits 200.00 into a «رسوم إلغاء» invoice of 175.44 + 24.56 (VAT on) or 200.00 (VAT off);
  early (Friday) refunds it through ``deposits.refund``.
"""

import re
from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse

from audit.models import AuditLog
from cashboxes.services import get_cashbox_balance
from deposits import services as deposits
from deposits.models import Deposit, DepositStatus
from entities.current import working_in
from hesba_testing.factories import make_customer, make_item
from inventory.models import StockMovement
from inventory.services import get_item_location_stock_quantity
from ledger import reports as gl
from ledger.projector import Projector
from manufacturing.models import ProductionRun
from sales.models import CustomerPayment, SalesInvoice, SalesLine
from sales.pos import walk_in_customer
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile
from settlements.models import InvoiceSettlement

from . import kitchen, preorders, services
from .insights import save_ingredients
from .models import Order, OrderKind, OrderLine, OrderStatus, PreOrder, PreOrderStatus
from .tests_reservations import ReservationSetup, at, balance


class PreOrderSetup(ReservationSetup):
    def setUp(self):
        super().setUp()
        self.cake = make_item(item_code="D-CAKE", item_name="تورتة عيد ميلاد", default_sale_price="450.00", is_stock_tracked=False)

    def preorder(self, starts_at=None, rows=None, user=None, entity="main", **extra):
        data = {"customer": self.nour, "starts_at": starts_at or at(1, "18:00"), "notes": "كل سنة وانتي طيبة يا مريم", **extra}
        rows = rows if rows is not None else [{"item": self.cake, "quantity": "1", "unit_price": ""}]
        return preorders.save_preorder(data, rows, user or self.owner, entity=self.main if entity == "main" else entity)

    def deposit_on(self, preorder, amount="200.00"):
        return preorders.take_deposit(preorder, self.cashbox, D(amount), self.owner)

    def collect(self, preorder):
        return preorders.collect(preorder, self.owner)

    def pay(self, order, **extra):
        extra.setdefault("tendered", D("313.00"))
        return services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, **extra)

    def day(self, days=1):
        return at(days, "00:00").date().isoformat()

    def screen(self, user=None, **query):
        self.enter(user)
        return self.client.get(reverse("restaurant:preorders"), {"lang": "ar", "day": self.day(), **query})

    def post(self, action, preorder=None, user=None, **data):
        self.enter(user)
        payload = {"lang": "ar", "action": action, "day_view": self.day(), **data}
        if preorder is not None:
            payload["pk"] = preorder.pk
        return self.client.post(reverse("restaurant:preorders"), payload, follow=True)

    def assert_balanced(self):
        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertEqual(gl.control_balance("receivable", entity=self.main), D("0.00"))


# ---- the deposit settles the collected order's bill -----------------------------------------------------------------


class DepositAtCollectionTests(PreOrderSetup):
    def test_the_200_deposit_moves_the_customer_minus_200_and_the_till_plus_200(self):
        booked = self.preorder()
        deposit = self.deposit_on(booked)
        self.assertEqual((deposit.status, deposit.target, deposit.payment.amount, deposit.payment.customer), (DepositStatus.HELD, booked, D("200.00"), self.nour))
        self.assertEqual(balance(self.nour), D("-200.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("200.00"))
        self.assertContains(self.screen(), 'data-deposit="held"')

    def test_vat_on_the_bill_is_513_the_deposit_settles_200_and_the_rest_is_313(self):
        booked = self.preorder()
        self.deposit_on(booked)
        collected = self.collect(booked)
        order = collected.order
        self.assertEqual((collected.status, order.kind, order.customer, order.status, order.entity), (PreOrderStatus.COLLECTED, OrderKind.TAKEAWAY, self.nour, OrderStatus.OPEN, self.main))
        self.assertEqual([(line.item, line.quantity, line.unit_price) for line in order.lines.all()], [(self.cake, D("1.000"), D("450.00"))])
        self.assertEqual(services.deposit_due(order, self.nour, D("513.00"))["due"], D("313.00"))
        invoice, change = self.pay(order)
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount, invoice.paid_now, invoice.customer),
                         (D("450.00"), D("63.00"), D("513.00"), D("313.00"), self.nour))
        self.assertEqual(change, D("0.00"))
        self.assertEqual(list(InvoiceSettlement.objects.filter(invoice=invoice).values_list("amount", "source")), [(D("200.00"), "deposit")])
        deposit = Deposit.objects.get()
        self.assertEqual((deposit.status, deposit.invoice), (DepositStatus.APPLIED, invoice))
        self.assertEqual(balance(self.nour), D("0.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("513.00"))  # 200.00 when booked + 313.00 at collection
        self.assertTrue(AuditLog.objects.filter(action="preorder_deposit_at_bill", object_type="restaurant.PreOrder", object_id=str(booked.pk)).exists())
        self.assertFalse(AuditLog.objects.filter(action="reservation_deposit_at_bill").exists())
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice), (OrderStatus.PAID, invoice))
        self.assert_balanced()

    def test_vat_off_the_bill_is_450_and_the_rest_is_250(self):
        self.vat(False)
        booked = self.preorder()
        self.deposit_on(booked)
        invoice, change = self.pay(self.collect(booked).order, tendered=D("300.00"))
        self.assertEqual((invoice.total_amount, invoice.tax_amount, invoice.paid_now, change), (D("450.00"), D("0.00"), D("250.00"), D("50.00")))
        self.assertEqual(list(InvoiceSettlement.objects.values_list("amount", "source")), [(D("200.00"), "deposit")])
        self.assertEqual((balance(self.nour), get_cashbox_balance(self.cashbox)), (D("0.00"), D("450.00")))
        self.assert_balanced()

    def test_the_bill_box_shows_the_preorder_deposit_and_asks_for_the_rest(self):
        booked = self.preorder()
        self.deposit_on(booked)
        response = self.post("collect", booked)
        order = PreOrder.objects.get().order
        self.assertRedirects(response, reverse("restaurant:order", args=[order.pk]) + "?lang=ar")
        self.assertContains(response, 'data-bill-deposit="200.00"')
        self.assertContains(response, "عربون الطلب المسبق")
        self.assertNotContains(response, "عربون الحجز")
        self.assertContains(response, 'name="tendered" value="313.00"')
        paid = self.pay_from_screen(order, self.cashbox, tendered="313.00", print="0")
        self.assertContains(paid, "اتقفل الطلب")
        order.refresh_from_db()
        self.assertEqual((order.invoice.total_amount, order.invoice.paid_now), (D("513.00"), D("313.00")))
        self.assertEqual(Deposit.objects.get().status, DepositStatus.APPLIED)

    def test_paid_in_another_customers_name_the_deposit_stays_held(self):
        booked = self.preorder()
        self.deposit_on(booked)
        other = make_customer(customer_code="C-OTHER", name="عميل تاني")
        invoice, _ = self.pay(self.collect(booked).order, customer=other, tendered=D("513.00"))
        self.assertEqual(invoice.paid_now, D("513.00"))
        self.assertEqual(Deposit.objects.get().status, DepositStatus.HELD)
        self.assertEqual(balance(self.nour), D("-200.00"))

    def test_a_held_deposit_before_collection_never_touches_another_bill(self):
        booked = self.preorder()
        self.deposit_on(booked)
        order = services.open_order(self.owner, kind=OrderKind.TAKEAWAY, customer=self.nour, entity=self.main)
        services.add_item(order, self.cake, self.owner, 1)
        invoice, _ = self.pay(order, tendered=D("513.00"))
        self.assertEqual(invoice.paid_now, D("513.00"))
        self.assertEqual(Deposit.objects.get().status, DepositStatus.HELD)

    def test_no_deposit_on_a_ready_preorder(self):
        booked = preorders.mark_ready(self.preorder(), self.owner)
        with self.assertRaisesMessage(ValidationError, "العربون يتاخد على حجز لسه محجوز أو مؤكد بس"):
            self.deposit_on(booked)  # S5's own refusal, unchanged
        self.assertNotContains(self.screen(), "data-deposit-take")


# ---- made the evening before, sold from stock ----------------------------------------------------------------------


class PreparedAheadTests(PreOrderSetup):
    def setUp(self):
        super().setUp()
        chocolate = make_item(item_code="D-CHOC", item_name="كيكة شوكولاتة", default_sale_price="450.00", is_stock_tracked=False)
        save_ingredients(chocolate, [(self.rice, "0.500")], self.owner)
        self.choc = kitchen.make_in_kitchen(chocolate, self.owner)
        self.booked = self.preorder(rows=[{"item": self.choc, "quantity": "1"}])

    def stock(self, item):
        return get_item_location_stock_quantity(item, self.location)

    def test_a_cake_prepared_the_evening_before_is_sold_from_stock_and_never_made_twice(self):
        """The owner may adjust stock, so the bill would make a missing cake by itself (``prepare_shortfall``): that it makes
        none here is only because «حضّر» put one in the hall's store the evening before."""

        self.deposit_on(self.booked)
        row = preorders.production(at(1, "00:00").date())[0]
        self.assertEqual((row["item"], row["quantity"], row["kitchen"], row["in_stock"], row["missing"]), (self.choc, D("1.000"), True, D("0"), D("1.000")))
        self.assertEqual((self.stock(self.choc), self.stock(self.rice), ProductionRun.objects.count()), (D("0"), D("20"), 0))
        made = self.post("prepare", make_view=self.day(), item=self.choc.pk, entity=self.main.pk, quantity="1")
        self.assertContains(made, "اتحضّر 1 من «كيكة شوكولاتة»")
        run = ProductionRun.objects.get()
        self.assertEqual((run.output_quantity, run.location), (D("1"), self.location))
        self.assertEqual((self.stock(self.choc), self.stock(self.rice)), (D("1"), D("19.5")))
        invoice, _ = self.pay(self.collect(self.booked).order)
        self.assertEqual((invoice.total_amount, invoice.paid_now), (D("513.00"), D("313.00")))
        self.assertEqual(ProductionRun.objects.count(), 1)  # no second run at the bill
        self.assertEqual((self.stock(self.choc), self.stock(self.rice)), (D("0"), D("19.5")))  # the cake left stock; the rice went down once
        self.assertTrue(SalesLine.objects.filter(invoice=invoice, item=self.choc, quantity=D("1")).exists())
        self.assertEqual(StockMovement.objects.filter(item=self.choc, quantity__lt=0).count() + StockMovement.objects.filter(item=self.choc, quantity__gt=0).count(), 2)

    def test_without_the_evening_batch_the_bill_makes_it_once_at_payment(self):
        self.pay(self.collect(self.booked).order, tendered=D("513.00"))
        self.assertEqual(ProductionRun.objects.count(), 1)
        self.assertEqual((self.stock(self.choc), self.stock(self.rice)), (D("0"), D("19.5")))

    def test_prepare_goes_to_the_preorders_branch_store_and_follows_kitchen_prepares_permissions(self):
        theirs = self.preorder(rows=[{"item": self.choc, "quantity": "2"}], entity=self.branch)
        self.work_in(None)  # the whole group: the list is per branch
        page = self.client.get(reverse("restaurant:preorders"), {"lang": "ar", "make": self.day()})
        self.assertContains(page, f'data-production-branch="{self.branch.pk}"')
        self.client.post(reverse("restaurant:preorders"), {"lang": "ar", "action": "prepare", "make_view": self.day(), "item": self.choc.pk,
                                                          "entity": self.branch.pk, "quantity": "2"})
        self.assertEqual(get_item_location_stock_quantity(self.choc, self.b_location), D("2"))
        self.assertEqual(self.stock(self.choc), D("0"))
        self.assertEqual(theirs.entity, self.branch)
        self.assertNotContains(self.screen(self.waiter_user, make=self.day()), "data-prepare")
        self.assertEqual(self.post("prepare", user=self.waiter_user, make_view=self.day(), item=self.choc.pk, entity=self.main.pk, quantity="1").status_code, 403)
        self.assertEqual(ProductionRun.objects.count(), 1)

    def test_a_dish_no_preorder_wants_that_day_cannot_be_prepared_from_the_list(self):
        response = self.post("prepare", make_view=self.day(3), item=self.choc.pk, entity=self.main.pk, quantity="1")
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProductionRun.objects.exists())


# ---- cancelling with the S5 policy ---------------------------------------------------------------------------------


class CancelTests(PreOrderSetup):
    def fee(self):
        invoice = SalesInvoice.objects.get(invoice_number__startswith="FEE-")
        return invoice, (invoice.subtotal, invoice.tax_amount, invoice.total_amount)

    def test_a_late_cancellation_forfeits_200_with_vat_inside_175_44_plus_24_56(self):
        booked = self.preorder(at(0, "18:00"))  # tonight: inside the 24 hours
        self.deposit_on(booked)
        result = preorders.cancel(booked, PreOrderStatus.CANCELLED, self.owner)
        invoice, figures = self.fee()
        self.assertEqual((result["outcome"], result["invoice"]), ("forfeited", invoice))
        self.assertEqual(figures, (D("175.44"), D("24.56"), D("200.00")))
        self.assertEqual(invoice.lines.get().item.item_code, deposits.FEE_ITEM_CODE)
        self.assertEqual(Deposit.objects.get().status, DepositStatus.FORFEITED)
        self.assertEqual(list(InvoiceSettlement.objects.values_list("invoice_id", "amount", "source")), [(invoice.pk, D("200.00"), "deposit")])
        self.assertEqual((balance(self.nour), get_cashbox_balance(self.cashbox)), (D("0.00"), D("200.00")))
        booked.refresh_from_db()
        self.assertEqual(booked.status, PreOrderStatus.CANCELLED)
        self.assert_balanced()
        self.assertEqual(gl.control_balance("vat_out", entity=self.main), D("-24.56"))

    def test_a_late_cancellation_with_vat_off_forfeits_200(self):
        self.vat(False)
        booked = self.preorder(at(0, "18:00"))
        self.deposit_on(booked)
        self.assertEqual(preorders.cancel(booked, PreOrderStatus.CANCELLED, self.owner)["outcome"], "forfeited")
        self.assertEqual(self.fee()[1], (D("200.00"), D("0.00"), D("200.00")))
        self.assertEqual(balance(self.nour), D("0.00"))

    def test_a_no_show_forfeits_whatever_the_hours_even_when_ready(self):
        booked = self.preorder(at(5, "18:00"))
        self.deposit_on(booked)
        preorders.mark_ready(booked, self.owner)
        self.assertEqual(preorders.cancel(booked, PreOrderStatus.NO_SHOW, self.owner)["outcome"], "forfeited")
        self.assertEqual(self.fee()[1][2], D("200.00"))

    def test_an_early_cancellation_refunds_through_deposits_refund(self):
        booked = self.preorder(at(2, "18:00"))  # Friday: more than 24 hours ahead
        self.deposit_on(booked)
        with mock.patch.object(deposits, "refund", wraps=deposits.refund) as refund:
            result = preorders.cancel(booked, PreOrderStatus.CANCELLED, self.owner, reason="اعتذار")
        refund.assert_called_once()
        self.assertEqual(result["outcome"], "refunded")
        self.assertEqual(deposits.latest_for(booked).status, DepositStatus.REFUNDED)
        self.assertEqual((balance(self.nour), get_cashbox_balance(self.cashbox)), (D("0.00"), D("0.00")))
        self.assertEqual(CustomerPayment.objects.get().status, "cancelled")
        self.assertFalse(SalesInvoice.objects.filter(invoice_number__startswith="FEE-").exists())

    def test_cancel_and_no_show_from_the_screen_are_audited(self):
        booked = self.preorder(at(2, "18:00"))
        self.post("cancel", booked, reason="غيّر رأيه")
        booked.refresh_from_db()
        self.assertEqual(booked.status, PreOrderStatus.CANCELLED)
        self.assertEqual(AuditLog.objects.get(action="cancel_preorder").reason, "غيّر رأيه")
        other = self.preorder(at(1, "10:00"))
        self.post("no_show", other)
        other.refresh_from_db()
        self.assertEqual(other.status, PreOrderStatus.NO_SHOW)

    def test_a_waiter_cancel_leaves_the_deposit_held_and_the_owner_forfeits_it_later(self):
        booked = self.preorder(at(0, "18:00"))
        self.deposit_on(booked)
        self.assertEqual(preorders.cancel(booked, PreOrderStatus.NO_SHOW, self.waiter_user)["outcome"], "forfeit_not_allowed")
        self.assertEqual(Deposit.objects.get().status, DepositStatus.HELD)
        self.assertContains(self.screen(self.owner, day=self.day(0)), "data-deposit-forfeit")
        self.post("deposit_forfeit", booked, self.owner)
        self.assertEqual(Deposit.objects.get().status, DepositStatus.FORFEITED)


# ---- the rules -------------------------------------------------------------------------------------------------------


class RuleTests(PreOrderSetup):
    def test_a_walk_in_customer_is_refused(self):
        for customer in (walk_in_customer(), None, ""):
            with self.subTest(customer=customer), self.assertRaisesMessage(ValidationError, "لازم يكون باسم عميل مسجل"):
                self.preorder(customer=customer)
        self.assertFalse(PreOrder.objects.exists())
        booked = self.preorder()
        with self.assertRaisesMessage(ValidationError, "العميل النقدي مينفعش يدفع عربون"):
            deposits.take(booked, walk_in_customer(), self.cashbox, D("200.00"), self.owner)  # S5's own refusal, unchanged

    def test_the_name_and_phone_default_to_the_customers(self):
        booked = self.preorder()
        self.assertEqual((booked.name, booked.phone, booked.entity, booked.created_by), ("نور حسن", "01001112222", self.main, self.owner))
        self.assertTrue(booked.number.startswith("PRE-20261111-"))

    def test_the_agreed_price_is_kept_after_the_menu_price_changes(self):
        booked = self.preorder(rows=[{"item": self.cake, "quantity": "1", "unit_price": ""}, {"item": self.grill, "quantity": "2", "unit_price": "100"}])
        self.cake.default_sale_price = D("500.00")
        self.cake.save(update_fields=["default_sale_price"])
        self.assertEqual([line.unit_price for line in booked.lines.all()], [D("450.00"), D("100.00")])
        order = self.collect(booked).order
        self.assertEqual([(line.item, line.unit_price) for line in order.lines.all()], [(self.cake, D("450.00")), (self.grill, D("100.00"))])
        self.vat(False)
        invoice, _ = self.pay(order, tendered=D("650.00"))
        self.assertEqual(invoice.total_amount, D("650.00"))  # 450.00 + 2 × 100.00, not the menu's 500.00 and 224.00
        self.assertEqual(sorted(invoice.lines.values_list("unit_sale_price", flat=True)), [D("100.00"), D("450.00")])

    def test_a_dish_added_from_the_menu_after_collection_is_not_merged_into_the_agreed_line(self):
        self.cake.default_sale_price = D("500.00")
        self.cake.save(update_fields=["default_sale_price"])
        booked = self.preorder(rows=[{"item": self.cake, "quantity": "1", "unit_price": "450"}])
        order = self.collect(booked).order
        services.add_item(order, self.cake, self.owner, 1)  # a second cake bought on the spot, at the menu price
        self.assertEqual(sorted((line.quantity, line.unit_price) for line in order.lines.all()), [(D("1.000"), D("450.00")), (D("1.000"), D("500.00"))])

    def test_collecting_twice_is_refused(self):
        booked = self.preorder()
        self.collect(booked)
        with self.assertRaisesMessage(ValidationError, "اتسلّم خلاص"):
            self.collect(booked)
        self.assertEqual(Order.objects.count(), 1)

    def test_a_cancelled_or_no_show_preorder_cannot_be_collected(self):
        for status in (PreOrderStatus.CANCELLED, PreOrderStatus.NO_SHOW):
            booked = self.preorder()
            preorders.cancel(booked, status, self.owner)
            with self.subTest(status=status), self.assertRaisesMessage(ValidationError, "الخطوة دي مش متاحة"):
                self.collect(booked)
        self.assertFalse(Order.objects.exists())

    def test_steps_and_edits(self):
        booked = self.preorder()
        self.assertEqual(preorders.confirm(booked, self.owner).status, PreOrderStatus.CONFIRMED)
        with self.assertRaisesMessage(ValidationError, "التأكيد للطلب اللي لسه محجوز بس"):
            preorders.confirm(booked, self.owner)
        changed = preorders.save_preorder({"customer": self.nour, "starts_at": at(1, "19:00")}, [{"item": self.cake, "quantity": "2", "unit_price": "400"}], self.owner, booked)
        self.assertEqual(([(line.quantity, line.unit_price) for line in changed.lines.all()], changed.starts_at), ([(D("2.000"), D("400.00"))], at(1, "19:00")))
        self.assertEqual(AuditLog.objects.get(action="change_preorder").before_data["lines"], [[self.cake.pk, "1.000", "450.00", ""]])
        preorders.mark_ready(booked, self.owner)
        with self.assertRaisesMessage(ValidationError, "مينفعش يتعدّل"):
            preorders.save_preorder({"customer": self.nour, "starts_at": at(1, "19:00")}, [{"item": self.cake}], self.owner, booked)

    def test_the_customer_is_frozen_while_a_deposit_is_held(self):
        booked = self.preorder()
        self.deposit_on(booked)
        other = make_customer(customer_code="C-OTHER", name="عميل تاني")
        with self.assertRaisesMessage(ValidationError, "العميل مينفعش يتغيّر"):
            preorders.save_preorder({"customer": other, "starts_at": at(1, "18:00")}, [{"item": self.cake}], self.owner, booked)

    def test_bad_lines_are_refused(self):
        for rows, text in (([], "ضيف صنف واحد"), ([{"item": "", "quantity": "", "unit_price": ""}], "ضيف صنف واحد"), ([{"item": "999999"}], "اختار صنف من المنيو"),
                           ([{"item": self.cake, "quantity": "0"}], "كمية «تورتة عيد ميلاد»"), ([{"item": self.cake, "unit_price": "-1"}], "سعر «تورتة عيد ميلاد»"),
                           ([{"item": self.burger}], "درجة الاستواء")):  # the burger's doneness is a required choice
            with self.subTest(rows=rows), self.assertRaisesMessage(ValidationError, text):
                self.preorder(rows=rows)
        self.assertFalse(PreOrder.objects.exists())

    def test_the_production_list_sums_only_that_days_live_preorders(self):
        thursday = at(1, "00:00").date()
        self.preorder(at(1, "10:00"), rows=[{"item": self.cake, "quantity": "2"}])
        self.preorder(at(1, "18:00"), rows=[{"item": self.cake, "quantity": "1"}, {"item": self.grill, "quantity": "3"}])
        preorders.mark_ready(self.preorder(at(1, "20:00"), rows=[{"item": self.cake, "quantity": "1"}]), self.owner)
        self.preorder(at(2, "10:00"), rows=[{"item": self.cake, "quantity": "5"}])  # another day
        preorders.cancel(self.preorder(at(1, "11:00"), rows=[{"item": self.cake, "quantity": "4"}]), PreOrderStatus.CANCELLED, self.owner)
        self.collect(self.preorder(at(1, "12:00"), rows=[{"item": self.cake, "quantity": "7"}]))
        self.preorder(at(1, "13:00"), rows=[{"item": self.cake, "quantity": "9"}], entity=self.branch)  # another branch
        with working_in(self.main):
            rows = preorders.production(thursday)
        self.assertEqual([(row["item"], row["quantity"], row["kitchen"]) for row in rows], [(self.cake, D("4.000"), False), (self.grill, D("3.000"), False)])
        with working_in(None):
            group = preorders.production(thursday)
        self.assertEqual(sorted((row["entity"].pk, row["item"].pk, row["quantity"]) for row in group),
                         sorted([(self.main.pk, self.cake.pk, D("4.000")), (self.main.pk, self.grill.pk, D("3.000")), (self.branch.pk, self.cake.pk, D("9.000"))]))
        page = self.screen(make=thursday.isoformat())
        self.assertContains(page, 'data-wanted="4"')
        self.assertContains(page, "data-not-kitchen")
        self.assertContains(self.screen(make=at(4, "00:00").date().isoformat()), "data-no-production")

    def test_the_production_list_defaults_to_tomorrow(self):
        self.preorder(at(1, "10:00"), rows=[{"item": self.cake, "quantity": "2"}])
        page = self.screen()
        self.assertContains(page, f'value="{self.day(1)}" dir="ltr" data-make-day')
        self.assertContains(page, 'data-wanted="2"')


# ---- permissions, branches, the screen -------------------------------------------------------------------------------


class PermissionTests(PreOrderSetup):
    def test_a_stock_keeper_is_refused_403(self):
        booked = self.preorder()
        self.enter(self.keeper)
        url = reverse("restaurant:preorders")
        self.assertEqual(self.client.get(url).status_code, 403)
        for action in ("save", "confirm", "ready", "collect", "cancel", "no_show", "deposit", "prepare"):
            self.assertEqual(self.client.post(url, {"action": action, "pk": booked.pk, "customer": self.nour.pk, "item": self.cake.pk}).status_code, 403, action)
        booked.refresh_from_db()
        self.assertEqual((booked.status, PreOrder.objects.count(), Order.objects.count(), Deposit.objects.count()), (PreOrderStatus.BOOKED, 1, 0, 0))

    def test_who_takes_orders_sees_and_changes_preorders(self):
        booked = self.preorder()
        for user in (self.waiter_user, self.cashier, self.manager, self.owner):
            page = self.screen(user)
            self.assertContains(page, booked.number, msg_prefix=user.username)
            self.assertContains(page, "data-preorder-form", msg_prefix=user.username)
        self.post("confirm", booked, self.waiter_user)
        booked.refresh_from_db()
        self.assertEqual(booked.status, PreOrderStatus.CONFIRMED)
        self.post("save", user=self.cashier, customer=self.nour.pk, day=self.day(2), time="17:00", notes="عيد ميلاد سعيد", item=[self.cake.pk, ""], quantity=["1", ""],
                  unit_price=["420", ""], line_note=["شوكولاتة", ""])
        made = PreOrder.objects.get(notes="عيد ميلاد سعيد")
        self.assertEqual((made.created_by, [(l.item, l.unit_price, l.note) for l in made.lines.all()]), (self.cashier, [(self.cake, D("420.00"), "شوكولاتة")]))

    def test_the_board_links_to_the_preorders_next_to_the_reservations(self):
        for user in (self.waiter_user, self.cashier, self.manager):
            self.enter(user)
            board = self.client.get(reverse("restaurant:board")).content.decode()
            self.assertIn("data-preorders-link", board, user.username)
            self.assertLess(board.index("data-reservations-link"), board.index("data-preorders-link"))

    def test_deposit_buttons_follow_the_deposit_permissions(self):
        booked = self.preorder()
        self.assertNotContains(self.screen(self.waiter_user), "data-deposit-take")  # a waiter never takes money
        self.assertContains(self.screen(self.owner), "data-deposit-take")
        self.assertEqual(self.post("deposit", booked, self.waiter_user, amount="50", cashbox=self.cashbox.pk).status_code, 403)
        self.assertFalse(Deposit.objects.exists())
        self.post("deposit", booked, self.owner, amount="200", cashbox=self.cashbox.pk)
        self.assertEqual(deposits.held_for(booked).payment.amount, D("200.00"))

    def test_another_branchs_cashbox_and_the_feature_off_are_refused(self):
        booked = self.preorder()
        with self.assertRaisesMessage(ValidationError, "اختار خزنة من خزن فرع الطلب"):
            preorders.take_deposit(booked, self.b_cashbox, D("200.00"), self.owner)
        set_capability_enabled(ClientProfile.get_active(), "deposits", False, user=self.owner)
        with self.assertRaisesMessage(ValidationError, "العربون مقفول"):
            self.deposit_on(booked)
        self.assertNotContains(self.screen(), "data-deposit-take")


class BranchTests(PreOrderSetup):
    def test_another_branchs_preorder_is_a_404_and_never_listed(self):
        ours = self.preorder()
        theirs = self.preorder(at(1, "19:00"), entity=self.branch, name="ضيف المعادي")
        self.assertEqual(theirs.entity, self.branch)
        page = self.screen()
        self.assertContains(page, ours.number)
        self.assertNotContains(page, theirs.number)
        self.assertNotContains(page, "ضيف المعادي")
        self.assertEqual(self.screen(edit=theirs.pk).status_code, 404)
        for action in ("confirm", "ready", "collect", "cancel", "no_show", "deposit"):
            self.assertEqual(self.post(action, theirs).status_code, 404, action)
        theirs.refresh_from_db()
        self.assertEqual((theirs.status, Order.objects.count()), (PreOrderStatus.BOOKED, 0))
        with working_in(self.main):
            with self.assertRaisesMessage(ValidationError, "مش من طلبات الفرع اللي شغال فيه"):
                preorders.collect(theirs, self.owner)
            with self.assertRaisesMessage(ValidationError, "مش من طلبات الفرع اللي شغال فيه"):
                preorders.cancel(theirs, PreOrderStatus.CANCELLED, self.owner)

    def test_a_branch_preorder_opens_its_order_in_its_branch(self):
        theirs = self.preorder(entity=self.branch)
        order = preorders.collect(theirs, self.owner).order
        self.assertEqual((order.entity, order.kind), (self.branch, OrderKind.TAKEAWAY))

    def test_the_whole_group_names_the_branch_of_a_new_one(self):
        self.work_in(None)
        url = reverse("restaurant:preorders")
        data = {"lang": "ar", "action": "save", "customer": self.nour.pk, "day": self.day(), "time": "17:00", "item": self.cake.pk, "quantity": "1"}
        self.client.post(url, data)
        self.assertFalse(PreOrder.objects.exists())  # no branch named
        self.client.post(url, {**data, "entity": self.branch.pk})
        self.assertEqual(PreOrder.objects.get().entity, self.branch)


class ScreenTests(PreOrderSetup):
    def test_the_day_view_lists_by_time_and_filters_by_day(self):
        late = self.preorder(at(1, "20:00"), name="بدري متأخر")
        early = self.preorder(at(1, "09:00"), name="بدري خالص")
        other_day = self.preorder(at(2, "09:00"), name="بعد بكرة")
        page = self.screen().content.decode()
        self.assertLess(page.index(early.number), page.index(late.number))
        self.assertNotIn(other_day.number, page)
        self.assertContains(self.screen(day=self.day(2)), other_day.number)
        self.assertContains(self.screen(day="not-a-day"), "مفيش طلبات مسبقة للاستلام في اليوم ده")  # today: none

    def test_the_card_shows_the_lines_the_total_and_the_writing(self):
        self.preorder(rows=[{"item": self.cake, "quantity": "1", "unit_price": "430"}])
        page = self.screen()
        self.assertContains(page, 'data-line-price="430.00"')
        self.assertContains(page, 'data-preorder-total="430.00"')
        self.assertContains(page, "كل سنة وانتي طيبة يا مريم")

    def test_arabic_and_english(self):
        self.preorder()
        arabic = self.screen()
        for text in ("طلبات مسبقة", "طلب مسبق جديد", "اتسلّم", "جاهز", "ما جاش", "قائمة التحضير", 'dir="rtl"'):
            self.assertContains(arabic, text)
        self.enter()
        english = self.client.get(reverse("restaurant:preorders"), {"lang": "en", "day": self.day()})
        for text in ("Pre-orders", "New pre-order", "Collected", "Ready", "No-show", "Production list", 'dir="ltr"'):
            self.assertContains(english, text)

    def test_an_edit_from_the_screen_and_a_bad_form_keeps_what_was_typed(self):
        booked = self.preorder()
        form = self.screen(edit=booked.pk)
        self.assertContains(form, 'value="450.00"')
        self.post("save", booked, customer=self.nour.pk, day=self.day(), time="18:00", item=[self.cake.pk], quantity=["2"], unit_price=["450.00"], line_note=[""])
        self.assertEqual(booked.lines.get().quantity, D("2.000"))
        refused = self.post("save", customer=self.nour.pk, day=self.day(), time="18:00", notes="مكتوب", item=[""], quantity=[""], unit_price=[""], line_note=[""])
        self.assertContains(refused, "ضيف صنف واحد على الأقل")
        self.assertContains(refused, 'value="مكتوب"')

    def test_the_stylesheet_uses_the_tokens_only(self):
        from django.contrib.staticfiles import finders

        with open(finders.find("hesba/css/restaurant_preorders.css"), encoding="utf-8") as sheet:
            css = sheet.read()
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", css), [])
        self.assertNotRegex(css, r"rgba?\(")
        with open(finders.find("hesba/css/tokens.css"), encoding="utf-8") as sheet:
            defined = set(re.findall(r"(--hs-[\w-]+)\s*:", sheet.read()))
        self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])

    def test_the_wording(self):
        from .views_preorders import PRE_WORDS

        texts = [value for value in list(PRE_WORDS["ar"].values()) + [v for v in preorders.MESSAGES["ar"].values() if isinstance(v, str)]]
        for banned in ("ورشة", "الأسطى", "بتاع", "دوس", "لينك"):
            self.assertFalse([text for text in texts if banned in text], banned)
        latin = re.compile(r"^[A-Za-z]|[A-Za-z][.!؟?]?$")
        self.assertEqual([text for text in texts if latin.search(text.strip())], [])


class NoPreOrderTests(PreOrderSetup):
    def test_with_no_preorder_the_board_bill_ticket_and_report_are_as_before(self):
        order = self.grills(services.open_order(self.owner, table=self.a_t1, guests=4, customer=self.nour))
        ticket = services.send_to_kitchen(order, self.owner)
        self.enter()
        page = self.client.get(reverse("restaurant:order", args=[order.pk]), {"lang": "ar"})
        self.assertNotContains(page, "data-bill-deposit")
        self.assertNotContains(page, "عربون")
        self.assertNotContains(self.client.get(reverse("restaurant:ticket", args=[ticket.pk]), {"lang": "ar"}), "طلب مسبق")
        self.assertNotContains(self.client.get(reverse("restaurant:check", args=[order.pk]), {"lang": "ar"}), "عربون")
        board = self.client.get(reverse("restaurant:board"), {"lang": "ar"})
        self.assertNotContains(board, "data-preorder=")
        self.assertEqual(self.client.get(reverse("restaurant:report"), {"lang": "ar"}).status_code, 200)
        invoice, change = services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, tendered=D("600.00"))
        self.assertEqual((invoice.total_amount, invoice.paid_now, change), (D("510.72"), D("510.72"), D("89.28")))
        self.assertFalse(InvoiceSettlement.objects.exists())
        self.assertFalse(AuditLog.objects.filter(object_type="restaurant.PreOrder").exists())
        self.assertEqual(OrderLine.objects.filter(order=order).get().unit_price, D("112.00"))
