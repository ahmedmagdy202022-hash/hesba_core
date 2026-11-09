"""S6 (HG-071): gift cards sold as a liability, spent as a payment on an invoice or at the till, written off by hand.

Every test asserts the money: the cash box, the gift-card box, the customer's ledger, the ledger's 2110 (unspent
gift cards), 1101 (cash), 1103 (receivables), 4101 (revenue), 2103 (output VAT) and 4201 (sundry income), and the
"due" totals a redemption must count once.
"""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from approvals.models import ApprovalRequest, ApprovalStatus
from approvals.services import approve, set_held_actions
from audit.models import AuditLog
from cashboxes.models import Cashbox, CashboxKind, CashboxMovement, CashboxOperationType
from cashboxes.services import create_cashbox_operation
from closing.models import PeriodStatus
from closing.services import get_period_for_date
from entities import scope as entity_scope
from hesba_testing.factories import grant, make_cashbox, make_customer, make_item, make_location, make_role, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.models import StockMovement
from inventory.services import recalculate_item_average_cost
from ledger import reports as ledger_reports
from ledger.models import Account, JournalLine
from ledger.projector import Projector
from permissions.models import Permission, RoleCode
from reports.digest import daily_digest
from reports.functional_views import _posted_totals
from reports.reps import rep_performance
from reports.selectors import cashbox_report
from reports.tests_dashboard import prepared_client
from sales.models import CustomerPayment, SalesInvoice
from sales.pos import checkout, today_summary, walk_in_customer
from sales.services import cancel_customer_payment, cancel_posted_sales_invoice, create_sales_draft, post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile
from settlements.models import InvoiceSettlement
from staff.services import save_employee
from taxes.models import SalesLineTax

from . import services
from .models import EntryKind, GiftCard, GiftCardEntry, GiftCardStatus

TODAY = timezone.localdate()
ZERO = D("0.00")


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def customer_balance(customer):
    agg = customer.ledger_entries.aggregate(i=Sum("due_increase"), d=Sum("due_decrease"))
    return customer.opening_balance + (agg["i"] or ZERO) - (agg["d"] or ZERO)


def box_balance(cashbox):
    moves = CashboxMovement.objects.filter(cashbox=cashbox)
    return cashbox.opening_balance + (moves.filter(direction="in").aggregate(t=Sum("amount"))["t"] or ZERO) - (moves.filter(direction="out").aggregate(t=Sum("amount"))["t"] or ZERO)


def bal(code, day=None, rebuild=True):
    """Debit-positive balance of a ledger account up to ``day`` (a liability or income shows negative)."""

    if rebuild:
        Projector().rebuild()
    rows = JournalLine.objects.filter(account__code=code)
    if day is not None:
        rows = rows.filter(entry__entry_date__lte=day)
    agg = rows.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or ZERO) - (agg["c"] or ZERO)


class GiftSetup(TestCase):
    modules = "customers,items_services,sales_operations,cashboxes,reports,inventory,employees_technicians"

    def setUp(self):
        self.profile = prepared_client("commercial", "retail", self.modules)
        self.owner = person(RoleCode.OWNER, "gc_owner")
        self.cashier = person(RoleCode.CASHIER, "gc_cashier")
        self.keeper = person(RoleCode.STOCK_KEEPER, "gc_keeper")
        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cash = make_cashbox(cashbox_code="CASH", is_default=True)
        self.customer = make_customer(customer_code="C-GC", name="مدام سعاد", phone="01001234567")
        self.item = make_item(item_code="SHIRT", item_name="قميص", default_sale_price="300.00")
        stock_in(self.item, self.location, 50, "120.00", movement_date=TODAY - timedelta(days=1))
        recalculate_item_average_cost(self.item)
        self.service = make_item(item_code="SRV-GC", item_name="خدمة", is_stock_tracked=False)
        set_capability_enabled(self.profile, "gift_cards", True, user=self.owner)  # off by default
        self.number = 0

    def sell(self, value="500.00", user=None, **extra):
        return services.sell(D(value), extra.pop("cashbox", self.cash), user or self.owner, **extra)

    def line(self, price, item=None, quantity="1"):
        return {"item": item or self.item, "quantity": D(quantity), "unit_sale_price": D(price), "line_discount_amount": ZERO, "description": ""}

    def till(self, price, tendered, gift=None, customer=None, item=None):
        invoice, change = checkout(lines=[self.line(price, item)], customer=customer or walk_in_customer(), location=self.location, cashbox=self.cash,
                                   discount=ZERO, tendered=D(tendered), user=self.owner, gift=gift)
        return invoice, change

    def invoice(self, total="1000.00", customer=None, location=None):
        self.number += 1
        draft = create_sales_draft(
            {"invoice_number": f"SI-GC-{self.number}", "invoice_date": TODAY, "customer": customer or self.customer, "selling_location": location or self.location,
             "cashbox": None, "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO},
            [self.line(total, self.service)], self.owner)
        post_sales_invoice(draft.pk, self.owner)
        draft.refresh_from_db()
        return draft

    def box(self, entity=None):
        from entities.services import main_entity

        return Cashbox.objects.get(kind=CashboxKind.GIFT_CARD, entity=entity or main_entity())

    def assert_box_mirrors_cards(self, entity=None):
        """HG-071: the box's balance is always minus the sum of the live cards' balances."""

        from entities.services import main_entity

        entity = entity or main_entity()
        self.assertEqual(box_balance(self.box(entity)), -services.outstanding_for(entity))


class SellTests(GiftSetup):
    def test_selling_a_card_takes_cash_books_2110_and_no_revenue_no_vat(self):
        set_capability_enabled(self.profile, "vat", True, user=self.owner)
        card = self.sell("500.00", holder_name="منى", holder_phone="01112223334", expires_on=TODAY + timedelta(days=365))
        self.assertEqual((card.number, card.face_value, card.status, card.entity.is_main), ("GC-00001", D("500.00"), GiftCardStatus.ACTIVE, True))
        self.assertEqual(len(card.code), 12)
        self.assertEqual(box_balance(self.cash), D("500.00"))
        self.assertEqual(box_balance(self.box()), D("-500.00"))
        self.assertEqual(services.balance(card), D("500.00"))
        self.assertEqual(bal("2110"), D("-500.00"))
        self.assertEqual(bal("1101", rebuild=False), D("500.00"))
        self.assertEqual(bal("4101", rebuild=False), ZERO)
        self.assertEqual(bal("2103", rebuild=False), ZERO)
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertFalse(SalesLineTax.objects.exists())
        self.assertEqual(Account.objects.get(code="2110").control, "gift_card_liability")
        result = ledger_reports.reconciliation()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["balanced"])
        self.assert_box_mirrors_cards()
        self.assertTrue(AuditLog.objects.filter(module="giftcards", action="sell_gift_card", object_id=str(card.pk)).exists())

    def test_codes_are_random_and_numbers_sequential_and_lookup_is_forgiving(self):
        first, second = self.sell("100"), self.sell("200")
        self.assertEqual((first.number, second.number), ("GC-00001", "GC-00002"))
        self.assertNotEqual(first.code, second.code)
        self.assertTrue(set(first.code) <= set(services.CODE_ALPHABET))
        self.assertEqual(services.find(services.pretty(first.code).lower()), first)
        self.assertIsNone(services.find("NOPE"))

    def test_a_cashier_with_giftcards_sell_but_not_move_cash_sells_and_a_keeper_cannot(self):
        from permissions.services import user_has_permission

        self.assertTrue(user_has_permission(self.cashier, "giftcards.sell"))
        self.assertFalse(user_has_permission(self.cashier, "cashboxes.move_cash"))
        card = self.sell("150.00", user=self.cashier)
        self.assertEqual(box_balance(self.cash), D("150.00"))
        self.assertEqual(services.balance(card), D("150.00"))
        with self.assertRaises(PermissionDenied):
            self.sell("150.00", user=self.keeper)
        # A direct cash operation still needs cashboxes.move_cash: the flag is the gift-card service's only.
        with self.assertRaises(PermissionDenied):
            create_cashbox_operation("DIRECT-1", TODAY, CashboxOperationType.DIRECT_OUT, D("10"), "سحب", self.cashier, source_cashbox=self.cash)
        self.assertEqual(GiftCard.objects.count(), 1)

    def test_bad_input_is_refused_in_both_languages(self):
        with self.assertRaisesMessage(ValidationError, "مبلغ الكارت مش صحيح"):
            self.sell("0")
        with self.assertRaisesMessage(ValidationError, "Invalid card amount"):
            services.sell("abc", self.cash, self.owner, lang="en")
        with self.assertRaisesMessage(ValidationError, "after today"):
            services.sell(D("10"), self.cash, self.owner, expires_on=TODAY, lang="en")
        cheques = make_cashbox(cashbox_code="CHQ", kind=CashboxKind.CHEQUES_IN)
        with self.assertRaisesMessage(ValidationError, "Choose the cashbox"):
            services.sell(D("10"), cheques, self.owner, lang="en")
        self.assertFalse(GiftCard.objects.exists())


class RedeemTests(GiftSetup):
    def test_partial_redemption_at_the_till_for_a_named_customer_of_a_rep(self):
        rep = save_employee({"name": "سمير المندوب", "commission_percent": "5"}, self.owner)
        self.customer.sales_rep = rep
        self.customer.save()
        card = self.sell("500.00")
        invoice, change = self.till("300.00", "100.00", gift=(card, D("200.00")), customer=self.customer)
        self.assertEqual(change, ZERO)
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due), (D("300.00"), D("100.00"), D("200.00")))  # frozen
        row = InvoiceSettlement.objects.get(invoice=invoice)
        self.assertEqual((row.amount, row.source, row.customer_payment.cashbox), (D("200.00"), "gift_card", self.box()))
        self.assertEqual(box_balance(self.cash), D("600.00"))  # 500 for the card + 100 cash
        self.assertEqual(box_balance(self.box()), D("-300.00"))
        self.assertEqual(customer_balance(self.customer), ZERO)
        self.assertEqual(services.balance(card), D("300.00"))
        self.assertEqual(bal("2110"), D("-300.00"))
        self.assertEqual(bal("4101", rebuild=False), D("-300.00"))
        self.assertEqual(bal("1103", rebuild=False), ZERO)
        self.assertEqual(bal("5101", rebuild=False), D("120.00"))  # stock out as usual
        self.assertTrue(StockMovement.objects.filter(sales_invoice=invoice, movement_type="sale_out").exists())
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        self.assert_box_mirrors_cards()
        # The "due" totals: the redeemed part is applied, not due, and paid_now stays the cash.
        totals = _posted_totals(SalesInvoice.objects.filter(pk=invoice.pk))
        self.assertEqual((totals["total"], totals["paid"], totals["applied"], totals["due"]), (D("300.00"), D("100.00"), D("200.00"), ZERO))
        digest = daily_digest(self.owner, TODAY)
        self.assertEqual((digest["sales"]["credit"], digest["sales"]["cash"], digest["sales"]["applied"]), (ZERO, D("100.00"), D("200.00")))
        summary = today_summary(self.owner)
        self.assertEqual((summary["credit"], summary["cash"], summary["applied"]), (ZERO, D("100.00"), D("200.00")))
        row = {line["rep"]: line for line in rep_performance(TODAY, TODAY)[0]}[rep]
        self.assertEqual((row["collected"], row["credit_sold"]), (D("300.00"), ZERO))  # C + R, counted once

    def test_the_walk_in_pays_cash_and_card_and_owes_nothing(self):
        card = self.sell("500.00")
        walk_in = walk_in_customer()
        invoice, change = self.till("300.00", "120.00", gift=(card, D("200.00")))
        self.assertEqual(change, D("20.00"))  # change is on the cash part only
        self.assertEqual((invoice.paid_now, invoice.remaining_due), (D("100.00"), D("200.00")))
        self.assertEqual(customer_balance(walk_in), ZERO)
        self.assertEqual(services.balance(card), D("300.00"))
        # Short of the total with no card is still refused, and so is a card that does not close the gap.
        with self.assertRaisesMessage(ValidationError, "err_walk_in_credit"):
            self.till("300.00", "100.00")
        with self.assertRaisesMessage(ValidationError, "err_walk_in_credit"):
            self.till("300.00", "50.00", gift=(card, D("200.00")))
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertEqual(customer_balance(walk_in), ZERO)
        self.assertEqual(services.balance(card), D("300.00"))

    def test_a_card_paying_the_whole_invoice_with_vat_is_taxed_as_any_sale(self):
        set_capability_enabled(self.profile, "vat", True, user=self.owner)
        card = self.sell("500.00")
        invoice, change = self.till("100.00", "0", gift=(card, D("114.00")), item=self.service)
        self.assertEqual((invoice.total_amount, invoice.tax_amount, invoice.paid_now, invoice.remaining_due), (D("114.00"), D("14.00"), ZERO, D("114.00")))
        self.assertEqual(bal("2103"), D("-14.00"))
        self.assertEqual(bal("4101", rebuild=False), D("-100.00"))
        self.assertEqual(bal("2110", rebuild=False), D("-386.00"))
        self.assertEqual(customer_balance(walk_in_customer()), ZERO)
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_redeeming_on_a_posted_invoice_page_settles_it(self):
        card = self.sell("500.00")
        invoice = self.invoice("800.00")
        entry = services.redeem(card, invoice, D("500.00"), self.cashier)
        self.assertEqual((entry.kind, entry.amount, entry.invoice, entry.payment.customer), (EntryKind.REDEEMED, D("500.00"), invoice, self.customer))
        self.assertEqual(customer_balance(self.customer), D("300.00"))
        self.assertEqual(services.balance(card), ZERO)
        self.assertEqual(bal("2110"), ZERO)
        self.assertEqual(bal("1103", rebuild=False), D("300.00"))
        self.assertEqual(_posted_totals(SalesInvoice.objects.filter(pk=invoice.pk))["due"], D("300.00"))
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_more_than_the_balance_or_the_due_is_refused_and_nothing_moves(self):
        card = self.sell("500.00")
        invoice = self.invoice("1000.00")
        with self.assertRaisesMessage(ValidationError, "رصيد الكارت مش كفاية؛ الرصيد 500.00"):
            services.redeem(card, invoice, D("600.00"), self.owner)
        small = self.invoice("100.00")
        with self.assertRaisesMessage(ValidationError, "more than what is left on the invoice (100.00)"):
            services.redeem(card, small, D("150.00"), self.owner, lang="en")
        self.assertEqual((CustomerPayment.objects.count(), InvoiceSettlement.objects.count(), services.balance(card)), (0, 0, D("500.00")))
        # At the till a refused card takes the whole sale back with it.
        with self.assertRaisesMessage(ValidationError, "500.00"):
            self.till("1000.00", "400.00", gift=(card, D("600.00")))
        self.assertEqual(SalesInvoice.objects.filter(invoice_number__startswith="POS-").count(), 0)
        self.assertEqual(StockMovement.objects.filter(movement_type="sale_out").count(), 0)

    def test_an_expired_card_still_pays_and_expiry_alone_makes_no_income(self):
        card = self.sell("500.00", expires_on=TODAY + timedelta(days=1))
        GiftCard.objects.filter(pk=card.pk).update(expires_on=TODAY - timedelta(days=30))  # its date has passed
        self.assertEqual(bal("4201"), ZERO)
        self.assertEqual(bal("2110", rebuild=False), D("-500.00"))
        services.redeem(card, self.invoice("200.00"), D("200.00"), self.owner)
        self.assertEqual(services.balance(card), D("300.00"))
        self.assertEqual(bal("4201"), ZERO)

    def test_a_cancelled_sale_or_unknown_card_cannot_pay(self):
        card = self.sell("100.00")
        services.reverse(card.entries.get(kind=EntryKind.SOLD), TODAY, "غلط", self.owner)
        with self.assertRaisesMessage(ValidationError, "sale was cancelled"):
            services.redeem(card, self.invoice("100.00"), D("50"), self.owner, lang="en")
        with self.assertRaisesMessage(ValidationError, "No gift card has this code"):
            services.redeem(services.find("XXXX"), self.invoice("100.00"), D("50"), self.owner, lang="en")


class ReversalTests(GiftSetup):
    def test_cancelling_a_redemption_puts_the_money_back_on_the_card_and_the_invoice_is_due(self):
        card = self.sell("500.00")
        invoice = self.invoice("300.00")
        entry = services.redeem(card, invoice, D("200.00"), self.owner)
        services.cancel_redemption(entry, self.cashier, "اتدفع غلط")
        entry.payment.refresh_from_db()
        self.assertEqual(entry.payment.status, "cancelled")
        self.assertEqual(box_balance(self.box()), D("-500.00"))
        self.assertEqual(customer_balance(self.customer), D("300.00"))
        self.assertEqual(services.balance(card), D("500.00"))
        self.assertEqual(_posted_totals(SalesInvoice.objects.filter(pk=invoice.pk))["due"], D("300.00"))  # the row is no longer counted
        self.assertEqual(GiftCardEntry.objects.filter(kind=EntryKind.REVERSED, reverses=entry).count(), 1)
        self.assertEqual(bal("2110"), D("-500.00"))
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        self.assert_box_mirrors_cards()

    def test_cancelling_the_payment_from_the_collections_screen_does_the_same(self):
        card = self.sell("500.00")
        entry = services.redeem(card, self.invoice("300.00"), D("300.00"), self.owner)
        cancel_customer_payment(entry.payment_id, self.owner, "من شاشة التحصيل")
        self.assertEqual(services.balance(card), D("500.00"))
        self.assertTrue(GiftCardEntry.objects.filter(kind=EntryKind.REVERSED, reverses=entry).exists())

    def test_a_redemption_in_a_closed_month_cannot_be_cancelled(self):
        card = self.sell("500.00")
        entry = services.redeem(card, self.invoice("300.00"), D("200.00"), self.owner)
        period = get_period_for_date(TODAY)
        period.status, period.closed_at = PeriodStatus.CLOSED, timezone.now()
        period.save(update_fields=["status", "closed_at"])
        with self.assertRaises(ValidationError):
            services.cancel_redemption(entry, self.owner, "متأخر")
        self.assertEqual(services.balance(card), D("300.00"))

    def test_cancelling_an_invoice_paid_by_card_gives_the_card_its_money_back(self):
        """HG-071 deviation D3: the redemption payment is cancelled with the invoice."""

        card = self.sell("500.00")
        walk_in = walk_in_customer()
        invoice, _ = self.till("300.00", "100.00", gift=(card, D("200.00")))
        cancel_posted_sales_invoice(invoice.pk, self.owner, "غلط")
        self.assertEqual(customer_balance(walk_in), ZERO)
        self.assertEqual(box_balance(self.box()), D("-500.00"))
        self.assertEqual(box_balance(self.cash), D("500.00"))  # the 100 cash went back
        self.assertEqual(services.balance(card), D("500.00"))
        self.assertEqual(bal("2110"), D("-500.00"))
        self.assertEqual(bal("4101", rebuild=False), ZERO)
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        self.assert_box_mirrors_cards()

    def test_cancelling_the_sale_returns_the_cash_only_while_the_card_is_untouched(self):
        card = self.sell("500.00")
        entry = services.redeem(card, self.invoice("100.00"), D("100.00"), self.owner)
        with self.assertRaisesMessage(ValidationError, "cancel that first"):
            services.reverse(card.entries.get(kind=EntryKind.SOLD), TODAY, "غلط", self.owner, lang="en")
        services.cancel_redemption(entry, self.owner, "غلط")
        services.reverse(card.entries.get(kind=EntryKind.SOLD), TODAY, "غلط", self.owner)
        card.refresh_from_db()
        self.assertEqual(card.status, GiftCardStatus.CANCELLED)
        self.assertEqual((box_balance(self.cash), box_balance(self.box()), services.balance(card)), (ZERO, ZERO, ZERO))
        self.assertEqual(bal("2110"), ZERO)
        self.assertEqual(bal("1101", rebuild=False), ZERO)
        with self.assertRaises(PermissionDenied):  # reversing is a manager's
            services.reverse(self.sell("10").entries.get(kind=EntryKind.SOLD), TODAY, "x", self.cashier)

    def test_the_generic_operations_screen_refuses_a_gift_card_operation(self):
        card = self.sell("500.00")
        operation = card.entries.get(kind=EntryKind.SOLD).operation
        self.client.force_login(self.owner)
        page = self.client.post(reverse("cashboxes:operation_cancel", args=[operation.pk]), {"lang": "en", "reversal_date": TODAY.isoformat(), "reason": "x"}, follow=True)
        self.assertContains(page, "belongs to a gift card")
        operation.refresh_from_db()
        self.assertEqual(operation.status, "posted")


class BreakageTests(GiftSetup):
    def test_breakage_of_the_rest_is_sundry_income_and_reversing_it_puts_the_liability_back(self):
        card = self.sell("500.00")
        services.redeem(card, self.invoice("200.00"), D("200.00"), self.owner)
        entry = services.breakage(card, self.owner, "عدّى سنة")
        self.assertEqual((entry.amount, services.balance(card)), (D("300.00"), ZERO))
        self.assertEqual(box_balance(self.box()), ZERO)
        self.assertEqual(bal("2110"), ZERO)
        self.assertEqual(bal("4201", rebuild=False), D("-300.00"))
        self.assertEqual(bal("3102", rebuild=False), ZERO)  # not the owner's account
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        with self.assertRaisesMessage(ValidationError, "Nothing is left"):
            services.breakage(card, self.owner, "تاني", lang="en")
        later = TODAY + timedelta(days=1)
        services.reverse(entry, later, "العميل رجع", self.owner)  # the box goes negative again: allowed for this kind
        self.assertEqual(services.balance(card), D("300.00"))
        self.assertEqual(box_balance(self.box()), D("-300.00"))
        self.assertEqual(bal("2110", TODAY), ZERO)
        self.assertEqual(bal("4201", TODAY, rebuild=False), D("-300.00"))
        self.assertEqual(bal("2110", later, rebuild=False), D("-300.00"))
        self.assertEqual(bal("4201", later, rebuild=False), ZERO)
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        self.assert_box_mirrors_cards()

    def test_breakage_needs_a_reason_and_the_breakage_permission(self):
        card = self.sell("50.00")
        with self.assertRaisesMessage(ValidationError, "اكتب السبب"):
            services.breakage(card, self.owner, " ")
        with self.assertRaises(PermissionDenied):
            services.breakage(card, self.cashier, "x")
        self.assertEqual(services.balance(card), D("50.00"))

    def test_breakage_is_held_for_approval_when_the_owner_holds_the_action(self):
        card = self.sell("80.00")
        set_held_actions(["giftcards.breakage"], self.owner)
        role = make_role(code="CLERK-GC", name_ar="مسؤول", name_en="Clerk")
        for code in ("giftcards.sell", "giftcards.breakage"):
            grant(role, Permission.objects.get(code=code))
        clerk = make_user(username="gc_clerk")
        make_user_profile(user=clerk, role=role)
        self.client.force_login(clerk)
        page = self.client.post(reverse("giftcards:detail", args=[card.pk]), {"lang": "ar", "action": "breakage", "reason": "منتهي"}, follow=True)
        self.assertContains(page, "محتاجة موافقة")
        self.assertEqual(services.balance(card), D("80.00"))
        request = ApprovalRequest.objects.get(action="giftcards.breakage", object_id=card.pk, status=ApprovalStatus.PENDING)
        approve(request.pk, self.owner)
        request.refresh_from_db()
        self.assertEqual(request.status, ApprovalStatus.DONE)
        self.assertEqual(services.balance(card), ZERO)
        self.assertEqual(bal("4201"), D("-80.00"))


class BoxIsHeldTests(GiftSetup):
    def test_the_gift_card_box_is_in_no_picker_report_closing_or_till(self):
        self.sell("500.00")
        box = self.box()
        self.assertNotIn(box, entity_scope.cashboxes(Cashbox.objects))
        self.assertNotIn(box.pk, [row["cashbox"].pk if hasattr(row.get("cashbox"), "pk") else row.get("cashbox_id") for row in cashbox_report()])
        self.assertEqual(sum((row["balance"] for row in cashbox_report()), ZERO), D("500.00"))  # closing sums this report
        self.client.force_login(self.owner)
        till = self.client.get(reverse("sales:pos") + "?lang=ar")
        self.assertNotIn(box, list(till.context["cashboxes"]))
        from master_data.forms import CashboxForm

        self.assertNotIn(CashboxKind.GIFT_CARD, [value for value, _ in CashboxForm(lang="en").fields["kind"].choices])


class MembershipTests(GiftSetup):
    modules = "customers,items_services,sales_operations,cashboxes,reports,inventory"

    def setUp(self):
        super().setUp()
        from promotions.models import Promotion

        set_capability_enabled(self.profile, "promotions", True, user=self.owner)
        self.gym = make_item(item_code="MEMBER", item_name="اشتراك شهري", is_stock_tracked=False)
        self.promo = Promotion.objects.create(name="خصم الأعضاء", kind="percent", percent=D("10"), membership_service=self.gym, created_by=self.owner)

    def contract(self, customer, status="active", starts=None, ends=None):
        from agreements.models import ServiceContract

        return ServiceContract.objects.create(number=f"SC-{ServiceContract.objects.count() + 1}", customer=customer, service=self.gym, amount=D("200"),
                                              starts_on=starts or TODAY - timedelta(days=10), ends_on=ends, status=status, created_by=self.owner)

    def test_a_member_gets_the_discount_and_the_invoice_records_it(self):
        from promotions.models import InvoicePromotion
        from promotions.services import apply

        self.contract(self.customer)
        lines, promo, off = apply([self.line("300.00")], customer=self.customer)
        self.assertEqual((promo, off, lines[0]["line_discount_amount"]), (self.promo, D("30.00"), D("30.00")))
        invoice, _ = checkout(lines=lines, customer=self.customer, location=self.location, cashbox=self.cash, discount=ZERO, tendered=D("270.00"),
                              user=self.owner, promotion=(promo, off, ""))
        self.assertEqual(invoice.total_amount, D("270.00"))
        self.assertEqual(InvoicePromotion.objects.get(invoice=invoice).discount, D("30.00"))

    def test_no_contract_a_paused_one_or_outside_its_period_gets_nothing(self):
        from promotions.services import apply

        other = make_customer(customer_code="C-NO", name="مش عضو")
        paused = make_customer(customer_code="C-PAUSE", name="موقوف")
        early = make_customer(customer_code="C-EARLY", name="لسه")
        ended = make_customer(customer_code="C-END", name="خلص")
        self.contract(paused, status="paused")
        self.contract(early, starts=TODAY + timedelta(days=1))
        self.contract(ended, starts=TODAY - timedelta(days=60), ends=TODAY - timedelta(days=1))
        for customer in (None, walk_in_customer(), other, paused, early, ended):
            with self.subTest(customer=getattr(customer, "customer_code", None)):
                _lines, promo, off = apply([self.line("300.00")], customer=customer)
                self.assertEqual((promo, off), (None, ZERO))
        # The contract's own last day still counts.
        last_day = make_customer(customer_code="C-LAST", name="آخر يوم")
        self.contract(last_day, ends=TODAY)
        self.assertEqual(apply([self.line("300.00")], customer=last_day)[2], D("30.00"))

    def test_the_till_quote_and_the_checkout_agree_for_a_member(self):
        self.contract(self.customer)
        from shifts.services import open_shift

        open_shift(self.owner, self.cash, "0")
        self.client.force_login(self.owner)
        cart = {"lang": "en", "line_count": "1", "item_0": str(self.item.pk), "qty_0": "1", "price_0": "300.00", "unit_0": ""}
        quote = self.client.post(reverse("sales:pos_promotion"), {**cart, "customer": str(self.customer.pk)}).json()
        self.assertEqual(quote["discount"], "30.00")
        stranger = self.client.post(reverse("sales:pos_promotion"), {**cart, "customer": str(walk_in_customer().pk)}).json()
        self.assertEqual(stranger["discount"], "0.00")
        sale = self.client.post(reverse("sales:pos") + "?lang=en", {**cart, "customer": str(self.customer.pk), "location": str(self.location.pk),
                                                                    "cashbox": str(self.cash.pk), "discount": "0", "tendered": "270.00",
                                                                    "promotion_seen": "30.00", "cart_json": "[]"})
        self.assertEqual(sale.status_code, 302, sale.context and [str(m) for m in sale.context["messages"]])
        self.assertEqual(SalesInvoice.objects.get().total_amount, D("270.00"))


class CapabilityOffTests(GiftSetup):
    def test_with_the_capability_off_nothing_is_offered_and_the_till_is_unchanged(self):
        set_capability_enabled(self.profile, "gift_cards", False, user=self.owner)
        with self.assertRaisesMessage(ValidationError, "switched off"):
            services.sell(D("100"), self.cash, self.owner, lang="en")
        self.assertFalse(Cashbox.objects.filter(kind=CashboxKind.GIFT_CARD).exists())
        self.client.force_login(self.owner)
        till = self.client.get(reverse("sales:pos") + "?lang=ar")
        self.assertNotContains(till, "data-pos-gift-row")
        self.assertNotEqual(self.client.get(reverse("giftcards:list")).status_code, 200)
        invoice = self.invoice("100.00")
        self.assertNotContains(self.client.get(reverse("sales:detail", args=[invoice.pk])), "data-gift-card-box")
        # A plain till sale books exactly as before.
        sale, change = self.till("300.00", "300.00")
        self.assertEqual((sale.paid_now, sale.remaining_due, change), (D("300.00"), ZERO, ZERO))
        self.assertEqual(bal("2110"), ZERO)
        self.assertTrue(ledger_reports.reconciliation()["ok"])


class EntityTests(GiftSetup):
    def setUp(self):
        super().setUp()
        from entities.services import save_entity

        self.branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي"}, self.owner)
        self.branch_store = make_location(location_code="BR-SHOP", name_ar="محل الفرع", entity=self.branch, is_selling_location=True)
        self.branch_cash = make_cashbox(cashbox_code="BR-CASH", name_ar="خزنة الفرع", entity=self.branch)

    def test_a_card_belongs_to_the_entity_that_took_its_money_and_pays_only_there(self):
        main_card = self.sell("300.00")
        branch_card = self.sell("200.00", cashbox=self.branch_cash)
        self.assertEqual(branch_card.entity, self.branch)
        self.assertEqual(box_balance(self.box(self.branch)), D("-200.00"))
        branch_invoice = self.invoice("150.00", location=self.branch_store)
        with self.assertRaisesMessage(ValidationError, "sold in another entity"):
            services.redeem(main_card, branch_invoice, D("100"), self.owner, lang="en")
        with self.assertRaisesMessage(ValidationError, "فرع تاني"):
            services.redeem(main_card, branch_invoice, D("100"), self.owner)
        services.redeem(branch_card, branch_invoice, D("150.00"), self.owner)
        self.assertEqual((services.balance(main_card), services.balance(branch_card)), (D("300.00"), D("50.00")))
        self.assert_box_mirrors_cards()
        self.assert_box_mirrors_cards(self.branch)
        self.assertTrue(ledger_reports.reconciliation()["ok"])
        from entities.current import working_in

        with working_in(self.branch):
            self.assertEqual(list(entity_scope.scope(GiftCard.objects.all(), entity_scope.GIFT_CARD)), [branch_card])


class ScreenTests(GiftSetup):
    def test_selling_from_the_screen_prints_the_receipt_in_both_languages(self):
        self.client.force_login(self.cashier)
        page = self.client.post(reverse("giftcards:list"), {"lang": "ar", "face_value": "250", "cashbox": str(self.cash.pk), "holder_name": "منى"}, follow=True)
        card = GiftCard.objects.get()
        self.assertContains(page, "data-gift-receipt")
        self.assertContains(page, services.pretty(card.code))
        self.assertContains(page, "إيصال الكارت")
        english = self.client.get(reverse("giftcards:receipt", args=[card.pk]) + "?lang=en")
        self.assertContains(english, "Card receipt")
        listing = self.client.get(reverse("giftcards:list") + "?lang=en")
        self.assertContains(listing, card.number)
        self.assertContains(listing, "Held on all cards")
        bad = self.client.post(reverse("giftcards:list"), {"lang": "en", "face_value": "0", "cashbox": str(self.cash.pk)}, follow=True)
        self.assertContains(bad, "Invalid card amount")
        self.client.force_login(self.keeper)
        self.assertEqual(self.client.get(reverse("giftcards:list")).status_code, 403)

    def test_paying_an_invoice_from_its_page_and_at_the_till(self):
        card = self.sell("500.00")
        invoice = self.invoice("300.00")
        self.client.force_login(self.cashier)
        detail = self.client.get(reverse("sales:detail", args=[invoice.pk]) + "?lang=ar")
        self.assertContains(detail, "data-gift-card-box")
        self.client.post(reverse("giftcards:pay_invoice", args=[invoice.pk]), {"lang": "ar", "code": services.pretty(card.code), "amount": "120"})
        self.assertEqual(services.balance(card), D("380.00"))
        refused = self.client.post(reverse("giftcards:pay_invoice", args=[invoice.pk]), {"lang": "en", "code": card.code, "amount": "900"}, follow=True)
        self.assertContains(refused, "more than what is left")
        till = self.client.get(reverse("sales:pos") + "?lang=ar")
        self.assertContains(till, "data-pos-gift-row")
        from shifts.services import open_shift

        open_shift(self.owner, self.cash, "0")
        self.client.force_login(self.owner)
        sale = self.client.post(reverse("sales:pos") + "?lang=ar", {"lang": "ar", "line_count": "1", "item_0": str(self.item.pk), "qty_0": "1", "price_0": "300.00",
                                                                    "unit_0": "", "customer": str(walk_in_customer().pk), "location": str(self.location.pk),
                                                                    "cashbox": str(self.cash.pk), "discount": "0", "tendered": "0",
                                                                    "gift_code": card.code, "gift_amount": "300", "cart_json": "[]"})
        self.assertEqual(sale.status_code, 302, sale.context and [str(m) for m in sale.context["messages"]])
        self.assertEqual(services.balance(card), D("80.00"))
        self.assertEqual(customer_balance(walk_in_customer()), ZERO)
        page = self.client.get(reverse("giftcards:detail", args=[card.pk]) + "?lang=en")
        self.assertContains(page, "Spent on an invoice")


class ProjectorTests(GiftSetup):
    def test_the_journal_rebuilt_from_scratch_reconciles_after_every_path(self):
        card = self.sell("500.00")
        second = self.sell("100.00")
        self.till("300.00", "100.00", gift=(card, D("200.00")))
        entry = services.redeem(second, self.invoice("60.00"), D("60.00"), self.owner)
        services.cancel_redemption(entry, self.owner, "غلط")
        services.breakage(second, self.owner, "منتهي")
        Projector().rebuild()
        result = ledger_reports.reconciliation()
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["balanced"])
        self.assertEqual(bal("2110"), D("-300.00"))
        self.assertEqual(bal("4201", rebuild=False), D("-100.00"))
        self.assertEqual(bal("1199", rebuild=False), ZERO)  # nothing in suspense
        self.assert_box_mirrors_cards()


class TillEdgeTests(GiftSetup):
    def test_a_role_that_sells_but_may_not_take_cards_gets_a_message_not_a_403(self):
        from shifts.services import open_shift

        card = self.sell("500.00")
        role = make_role(code="SELLER-GC", name_ar="بياع", name_en="Seller")
        grant(role, Permission.objects.get(code="sales.create_sales_invoice"))
        seller = make_user(username="gc_seller")
        make_user_profile(user=seller, role=role)
        open_shift(seller, self.cash, "0")
        self.client.force_login(seller)
        till = self.client.get(reverse("sales:pos") + "?lang=en")
        self.assertNotContains(till, "data-pos-gift-row")
        sale = self.client.post(reverse("sales:pos") + "?lang=en", {"lang": "en", "line_count": "1", "item_0": str(self.item.pk), "qty_0": "1", "price_0": "300.00",
                                                                    "unit_0": "", "customer": str(walk_in_customer().pk), "location": str(self.location.pk),
                                                                    "cashbox": str(self.cash.pk), "discount": "0", "tendered": "100",
                                                                    "gift_code": card.code, "gift_amount": "200", "cart_json": "[]"})
        self.assertEqual(sale.status_code, 200)
        self.assertContains(sale, "You may not take payment by gift card")
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual(services.balance(card), D("500.00"))



class ReviewFixTests(GiftSetup):
    """Independent review of 7a4e15a: each finding pinned with the money it protects."""

    def snapshot(self, card, customer):
        return {"cash": box_balance(self.cash), "box": box_balance(self.box()), "card": services.balance(card), "customer": customer_balance(customer)}

    def ret(self, invoice, quantity="1", number="SR-GC-1"):
        from sales.services import create_sales_return

        return create_sales_return(number, TODAY, invoice.pk, [{"source_line": invoice.lines.get(), "quantity": D(quantity)}], "رجّع", self.owner)

    # ---- 1a: no return on a card-paid invoice (D7)
    def test_returns_on_card_paid_invoices_are_refused_for_walk_in_and_named_full_and_partial(self):
        from sales.models import SalesReturn

        card = self.sell("1000.00")
        walk_in = walk_in_customer()
        walk_sale, _ = self.till("300.00", "100.00", gift=(card, D("200.00")), item=self.item)
        named_sale, _ = checkout(lines=[self.line("300.00", quantity="2")], customer=self.customer, location=self.location, cashbox=self.cash, discount=ZERO,
                                 tendered=D("100.00"), user=self.owner, gift=(card, D("500.00")))
        before_walk, before_named = self.snapshot(card, walk_in), self.snapshot(card, self.customer)
        self.assertEqual(before_named, {"cash": D("1200.00"), "box": D("-300.00"), "card": D("300.00"), "customer": ZERO})
        for invoice, quantity in ((walk_sale, "1"), (named_sale, "2"), (named_sale, "1")):
            with self.subTest(invoice=invoice.invoice_number, quantity=quantity):
                with self.assertRaisesMessage(ValidationError, "a return to the card is not possible yet"):
                    self.ret(invoice, quantity, f"SR-{invoice.pk}-{quantity}")
        self.assertFalse(SalesReturn.objects.exists())
        self.assertEqual(self.snapshot(card, walk_in), before_walk)
        self.assertEqual(self.snapshot(card, self.customer), before_named)
        from settings_core.ui_messages import translate

        self.assertIn("المرتجع على الكارت", translate("This invoice was paid partly by gift card, and a return to the card is not possible yet. Cancel the card payment or the whole invoice instead."))
        # A named customer's route: cancel the card payment, then the return goes through as for any credit sale.
        services.cancel_redemption(GiftCardEntry.objects.get(invoice=named_sale, kind=EntryKind.REDEEMED), self.owner, "هيرجّع")
        self.ret(named_sale, "1", "SR-NAMED-OK")
        self.assertEqual(services.balance(card), D("800.00"))
        self.assertEqual(customer_balance(self.customer), D("500.00") - D("250.00"))  # 500 due again, less the return's credit share
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    # ---- 1b: the redeemable due subtracts posted returns; the box hides when nothing is due
    def test_redeem_on_a_returned_invoice_is_capped_by_the_return(self):
        from sales.services import create_sales_return

        card = self.sell("500.00")
        invoice = self.invoice("300.00")  # named customer, all on credit
        create_sales_return("SR-RB-1", TODAY, invoice.pk, [{"source_line": invoice.lines.get(), "quantity": D("1")}], "رجّع", self.owner)
        self.assertEqual(customer_balance(self.customer), ZERO)
        self.assertEqual(services.redeemable_due(invoice), ZERO)
        with self.assertRaisesMessage(ValidationError, "more than what is left on the invoice (0.00)"):
            services.redeem(card, invoice, D("100.00"), self.owner, lang="en")
        self.assertEqual((services.balance(card), customer_balance(self.customer), box_balance(self.box())), (D("500.00"), ZERO, D("-500.00")))
        self.client.force_login(self.cashier)
        self.assertNotContains(self.client.get(reverse("sales:detail", args=[invoice.pk])), "data-gift-card-box")

    # ---- 1c and 3: cancel_redemption refused after a return, on a walk-in, and without the collection permission
    def test_cancel_redemption_refused_after_a_return(self):
        from sales.models import SalesReturn, SalesReturnStatus

        card = self.sell("500.00")
        invoice = self.invoice("300.00")
        entry = services.redeem(card, invoice, D("100.00"), self.owner)
        # a return that predates the D7 guard (or one a future return-to-card path posts) blocks the cancellation
        SalesReturn.objects.create(return_number="SR-OLD", return_date=TODAY, source_invoice=invoice, total_amount=D("50.00"), cash_amount=ZERO,
                                   due_amount=D("50.00"), status=SalesReturnStatus.POSTED, reason="قديم", created_by=self.owner)
        with self.assertRaisesMessage(ValidationError, "posted return"):
            services.cancel_redemption(entry, self.owner, "x", lang="en")
        self.assertEqual((services.balance(card), box_balance(self.box())), (D("400.00"), D("-400.00")))

    def test_the_reviewers_scenario_a_returned_walk_in_sale_cannot_be_drained_twice(self):
        card = self.sell("500.00")
        second = self.sell("500.00")
        walk_in = walk_in_customer()
        invoice, _ = self.till("300.00", "0", gift=(card, D("300.00")))
        with self.assertRaises(ValidationError):  # step 1: the return is refused (D7)
            self.ret(invoice)
        entry = GiftCardEntry.objects.get(invoice=invoice, kind=EntryKind.REDEEMED)
        with self.assertRaisesMessage(ValidationError, "walk-in sale"):  # step 2: the walk-in's card payment stays with its invoice
            services.cancel_redemption(entry, self.owner, "x", lang="en")
        with self.assertRaisesMessage(ValidationError, "walk-in sale"):  # nor from the collections screen
            cancel_customer_payment(entry.payment_id, self.owner, "x")
        with self.assertRaises(ValidationError):  # step 3: the second card finds nothing due
            services.redeem(second, invoice, D("300.00"), self.owner)
        self.assertEqual(self.snapshot(card, walk_in), {"cash": D("1000.00"), "box": D("-700.00"), "card": D("200.00"), "customer": ZERO})
        self.assertEqual(services.balance(second), D("500.00"))
        # The route is cancelling the whole invoice: the card gets its 300 back, the walk-in owes nothing.
        cancel_posted_sales_invoice(invoice.pk, self.owner, "رجّع كله")
        self.assertEqual(self.snapshot(card, walk_in), {"cash": D("1000.00"), "box": D("-1000.00"), "card": D("500.00"), "customer": ZERO})
        self.assertTrue(ledger_reports.reconciliation()["ok"])

    def test_cancel_redemption_needs_the_collection_permission_too(self):
        card = self.sell("500.00")
        entry = services.redeem(card, self.invoice("300.00"), D("100.00"), self.owner)
        role = make_role(code="CARD-ONLY", name_ar="كروت", name_en="Cards")
        grant(role, Permission.objects.get(code="giftcards.sell"))
        clerk = make_user(username="gc_cardonly")
        make_user_profile(user=clerk, role=role)
        with self.assertRaises(PermissionDenied):
            services.cancel_redemption(entry, clerk, "x")
        self.assertEqual((services.balance(card), customer_balance(self.customer)), (D("400.00"), D("200.00")))

    # ---- 2: the code is masked everywhere but once, right after the sale
    def test_the_full_code_shows_once_after_the_sale_and_then_only_audited_to_a_manager(self):
        self.client.force_login(self.cashier)
        page = self.client.post(reverse("giftcards:list"), {"lang": "ar", "face_value": "250", "cashbox": str(self.cash.pk)}, follow=True)
        card = GiftCard.objects.get()
        full = services.pretty(card.code)
        self.assertContains(page, full)
        for url in (reverse("giftcards:list"), reverse("giftcards:detail", args=[card.pk]), reverse("giftcards:receipt", args=[card.pk]),
                    reverse("giftcards:receipt", args=[card.pk]) + "?reveal=1"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, full)
                self.assertNotContains(response, card.code)
                self.assertContains(response, card.code[-4:])
        self.assertFalse(AuditLog.objects.filter(action="reveal_gift_card_code").exists())
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("giftcards:receipt", args=[card.pk])), full)
        revealed = self.client.get(reverse("giftcards:receipt", args=[card.pk]) + "?reveal=1")
        self.assertContains(revealed, full)
        self.assertEqual(AuditLog.objects.filter(action="reveal_gift_card_code", object_id=str(card.pk), actor=self.owner).count(), 1)
        self.assertEqual(box_balance(self.cash), D("250.00"))

    # ---- 4: the offline sync never posts into a held box
    def test_an_offline_sync_naming_the_gift_card_box_is_refused(self):
        import json

        from shifts.services import open_shift

        self.sell("100.00")
        open_shift(self.owner, self.cash, "0")
        self.client.force_login(self.owner)
        import uuid

        payload = {"key": str(uuid.uuid4()), "recorded_at": timezone.now().isoformat(), "customer": "", "location": str(self.location.pk),
                   "cashbox": str(self.box().pk), "discount": "0", "tendered": "300",
                   "lines": [{"id": self.item.pk, "qty": "1", "price": "300.00", "unit": None, "serial": None}]}
        response = self.client.post(reverse("sales:pos_sync"), data=json.dumps(payload), content_type="application/json")
        self.assertEqual(response.json().get("status"), "refused", response.content[:300])
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual((box_balance(self.box()), box_balance(self.cash)), (D("-100.00"), D("100.00")))
        control = self.client.post(reverse("sales:pos_sync"), data=json.dumps({**payload, "key": str(uuid.uuid4()), "cashbox": str(self.cash.pk)}), content_type="application/json")
        self.assertEqual(control.json().get("status"), "posted", control.content.decode()[:300])  # the same sale into the drawer goes through
        self.assertEqual((box_balance(self.box()), box_balance(self.cash)), (D("-100.00"), D("400.00")))

    # ---- 6, 7: one held reversal per entry; the permission is checked before a request is held
    def test_reversal_requests_are_held_per_entry_and_need_the_breakage_permission(self):
        card = self.sell("80.00")
        breakage_entry = services.breakage(card, self.owner, "منتهي")
        sold_entry = card.entries.get(kind=EntryKind.SOLD)
        set_held_actions(["giftcards.reverse", "giftcards.breakage"], self.owner)
        role = make_role(code="CLERK-REV", name_ar="مسؤول", name_en="Clerk")
        for code in ("giftcards.sell", "giftcards.breakage"):
            grant(role, Permission.objects.get(code=code))
        clerk = make_user(username="gc_rev")
        make_user_profile(user=clerk, role=role)
        self.client.force_login(clerk)
        for entry in (breakage_entry, sold_entry):
            self.client.post(reverse("giftcards:detail", args=[card.pk]), {"lang": "en", "action": "reverse", "entry": str(entry.pk),
                                                                           "reversal_date": TODAY.isoformat(), "reason": "غلط"})
        held_ids = set(ApprovalRequest.objects.filter(action="giftcards.reverse", status=ApprovalStatus.PENDING).values_list("object_id", flat=True))
        self.assertEqual(held_ids, {breakage_entry.pk, sold_entry.pk})
        approve(ApprovalRequest.objects.get(action="giftcards.reverse", object_id=breakage_entry.pk).pk, self.owner)
        self.assertEqual((services.balance(card), box_balance(self.box())), (D("80.00"), D("-80.00")))
        # A cashier (no giftcards.breakage) leaves no request behind.
        self.client.force_login(self.cashier)
        self.client.post(reverse("giftcards:detail", args=[card.pk]), {"lang": "en", "action": "breakage", "reason": "x"})
        self.assertFalse(ApprovalRequest.objects.filter(action="giftcards.breakage").exists())
        self.assertEqual(services.balance(card), D("80.00"))

    # ---- 8: the receipt follows the capability
    def test_two_sales_in_two_tabs_each_show_their_code_once_and_the_receipt_is_never_cached(self):
        self.client.force_login(self.cashier)
        for value in ("100", "200"):
            self.client.post(reverse("giftcards:list"), {"lang": "ar", "face_value": value, "cashbox": str(self.cash.pk)})
        first, second = GiftCard.objects.order_by("pk")
        for card in (first, second):
            response = self.client.get(reverse("giftcards:receipt", args=[card.pk]))
            self.assertContains(response, services.pretty(card.code))
            self.assertIn("no-store", response["Cache-Control"])
            self.assertNotContains(self.client.get(reverse("giftcards:receipt", args=[card.pk])), services.pretty(card.code))

    def test_the_receipt_is_closed_with_the_capability(self):
        card = self.sell("50.00")
        set_capability_enabled(self.profile, "gift_cards", False, user=self.owner)
        self.client.force_login(self.owner)
        self.assertNotEqual(self.client.get(reverse("giftcards:receipt", args=[card.pk])).status_code, 200)

    # ---- 10: the liability account's code comes from the chart
    def test_the_screen_names_the_liability_account_from_the_chart(self):
        Projector().rebuild()
        Account.objects.filter(control="gift_card_liability").update(code="2190")
        self.client.force_login(self.owner)
        page = self.client.get(reverse("giftcards:list") + "?lang=en")
        self.assertContains(page, "(2190)")
        self.assertNotContains(page, "(2110)")

    # ---- 11: an import file never changes a held box
    def test_an_import_row_cannot_change_a_gift_card_box(self):
        from types import SimpleNamespace

        from imports.apply_services import apply_cashbox_row, apply_opening_balance_row

        self.sell("100.00")
        box = self.box()
        batch = SimpleNamespace(batch_code="IMP-1", go_live_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "cannot be changed from an import file"):
            apply_cashbox_row({"cashbox_code": box.cashbox_code, "name_ar": "خزنة", "opening_balance": "500"}, batch)
        with self.assertRaisesMessage(ValidationError, "cannot be changed from an import file"):
            apply_opening_balance_row({"entity_type": "cashbox", "entity_code": box.cashbox_code, "opening_balance": "500"}, batch)
        box.refresh_from_db()
        self.assertEqual((box.opening_balance, box.name_ar, box_balance(box)), (ZERO, "كروت هدايا غير مصروفة", D("-100.00")))
