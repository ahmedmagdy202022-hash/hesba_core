"""T6 (MARKET-PARITY): loyalty points earn on posting, spend as a discount, and come back on a cancel or a return (HG-058)."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, open_test_shift, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.pos import checkout, walk_in_customer
from sales.services import cancel_posted_sales_invoice, cancel_sales_return, create_sales_return

from . import services
from .models import EntryKind, InvoiceRedemption, LoyaltySettings, PointsEntry

TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def line(item, quantity, price):
    return {"item": item, "quantity": D(str(quantity)), "unit_sale_price": D(price), "line_discount_amount": D("0"), "description": ""}


class LoyaltySetup(TestCase):
    def setUp(self):
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        prepared_client("commercial", "fashion")
        caps.set_capability_enabled(ClientProfile.get_active(), "loyalty", True)
        LoyaltySettings.objects.update_or_create(pk=1, defaults={"amount_per_point": D("10.00"), "point_value": D("0.50"), "min_points": 20, "expiry_months": 0})
        self.owner = person(RoleCode.OWNER, "ly_owner")
        self.cashier = person(RoleCode.CASHIER, "ly_cashier")
        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        self.shirt = make_item(item_code="SH", item_name="قميص", default_sale_price="250.00")
        stock_in(self.shirt, self.location, 50, "100.00", movement_date=TODAY)
        recalculate_item_average_cost(self.shirt)
        self.customer = make_customer(customer_code="LY-1", name="منى", phone="01001234567")

    def sell(self, quantity=2, points=None, customer=None, discount="0"):
        invoice, _change = checkout(lines=[line(self.shirt, quantity, "250.00")], customer=customer or self.customer, location=self.location,
                                    cashbox=self.cashbox, discount=D(discount), tendered=D("100000"), user=self.owner, points=points)
        return invoice

    def points(self, customer=None):
        return services.balance(customer or self.customer)["points"]


class EarnAndSpendTests(LoyaltySetup):
    def test_a_posted_invoice_earns_points_and_the_walk_in_customer_none(self):
        invoice = self.sell(quantity=2)  # 500.00 → 50 points
        self.assertEqual(self.points(), 50)
        self.assertEqual(PointsEntry.objects.get(invoice=invoice).kind, EntryKind.EARN)
        self.sell(customer=walk_in_customer())
        self.assertFalse(PointsEntry.objects.filter(customer=walk_in_customer()).exists())
        self.assertEqual(services.balance(self.customer)["value"], D("25.00"))  # 50 × 0.50

    def test_spending_takes_the_discount_and_is_booked_on_posting(self):
        self.sell(quantity=4)  # 1000.00 → 100 points
        points, off = services.redeem(self.customer, "40", D("250.00"))
        self.assertEqual((points, off), (40, D("20.00")))
        invoice = self.sell(quantity=1, points=(points, off), discount=str(off))
        self.assertEqual(invoice.total_amount, D("230.00"))  # 250 − 20; posting used the discount it was given
        self.assertEqual(PointsEntry.objects.get(invoice=invoice, kind=EntryKind.SPEND).points, -40)
        self.assertEqual(self.points(), 100 - 40 + 23)  # spent 40, earned 23 on 230.00

    def test_what_cannot_be_spent(self):
        self.sell(quantity=4)
        for raw, text in (("1.5", "رقم صحيح"), ("10", "أقل عدد نقاط"), ("500", "100 نقطة بس"), ("abc", "رقم صحيح")):
            with self.subTest(raw=raw), self.assertRaisesMessage(ValidationError, text):
                services.redeem(self.customer, raw, D("1000.00"))
        with self.assertRaisesMessage(ValidationError, "أكتر من قيمة الفاتورة"):
            services.redeem(self.customer, "100", D("30.00"))  # 50.00 off a 30.00 invoice
        with self.assertRaisesMessage(ValidationError, "مش للعميل النقدي"):
            services.redeem(walk_in_customer(), "20", D("1000.00"))
        self.assertEqual(services.redeem(self.customer, "", D("10.00")), (0, D("0.00")))

    def test_a_draft_sets_its_points_aside(self):
        self.sell(quantity=4)  # 100 points
        draft = SalesInvoice.objects.create(invoice_number="LY-D1", invoice_date=TODAY, customer=self.customer, selling_location=self.location, cashbox=self.cashbox)
        services.remember(draft, 80, D("40.00"))
        self.assertEqual(self.points(), 20)  # 80 set aside for the draft
        with self.assertRaisesMessage(ValidationError, "20 نقطة بس"):
            services.redeem(self.customer, "30", D("1000.00"))
        draft.status = "cancelled"
        draft.save(update_fields=["status"])
        self.assertEqual(self.points(), 100)


class TakingBackTests(LoyaltySetup):
    def test_cancelling_gives_back_what_was_spent_and_takes_back_what_was_earned(self):
        self.sell(quantity=4)  # 100
        invoice = self.sell(quantity=1, points=(40, D("20.00")), discount="20.00")  # −40, +23
        cancel_posted_sales_invoice(invoice.pk, self.owner, "غلط")
        self.assertEqual(self.points(), 100)
        backs = PointsEntry.objects.filter(invoice=invoice, kind=EntryKind.BACK)
        self.assertEqual(sorted(row.points for row in backs), [-23, 40])
        self.assertEqual(PointsEntry.objects.filter(invoice=invoice).count(), 4)  # append-only: the first two stay

    def test_a_return_takes_back_its_share_and_cancelling_it_gives_it_back(self):
        invoice = self.sell(quantity=4)  # 1000.00 → 100
        source = invoice.lines.get()
        sales_return = create_sales_return("LY-R1", TODAY, invoice.pk, [{"source_line": source, "quantity": D("1")}], "مقاس", self.owner)
        self.assertEqual(self.points(), 75)  # a quarter came back
        cancel_sales_return(sales_return.pk, TODAY, "رجع في كلامه", self.owner)
        self.assertEqual(self.points(), 100)
        create_sales_return("LY-R2", TODAY, invoice.pk, [{"source_line": source, "quantity": D("4")}], "كله", self.owner)
        self.assertEqual(self.points(), 0)

    def test_points_run_out_oldest_first(self):
        LoyaltySettings.objects.filter(pk=1).update(expiry_months=6)
        old = self.sell(quantity=4)  # 100, expire in 6 months
        PointsEntry.objects.filter(invoice=old).update(entry_date=TODAY - timedelta(days=200), expires_on=TODAY - timedelta(days=20))
        self.sell(quantity=2)  # 50, still good
        self.assertEqual(services.balance(self.customer)["expired"], 100)
        self.assertEqual(self.points(), 50)
        PointsEntry.objects.create(customer=self.customer, kind=EntryKind.SPEND, points=-60, entry_date=TODAY - timedelta(days=100))
        later = services.balance(self.customer)
        self.assertEqual((later["expired"], later["points"]), (40, 50))  # the 60 spent came out of the old 100 first


class ScreenTests(LoyaltySetup):
    def test_the_till_spends_points(self):
        self.sell(quantity=4)  # 100
        open_test_shift(self.cashier, self.cashbox)
        self.client.force_login(self.cashier)
        page = self.client.get(reverse("sales:pos") + "?lang=ar")
        self.assertContains(page, "data-pos-points")
        response = self.client.post(reverse("sales:pos"), {"line_count": "1", "customer": str(self.customer.pk), "location": str(self.location.pk),
                                                           "cashbox": str(self.cashbox.pk), "discount": "0", "tendered": "300", "print": "0", "redeem_points": "60",
                                                           "item_0": str(self.shirt.pk), "qty_0": "1", "price_0": "250.00"})
        self.assertEqual(response.status_code, 302)
        invoice = SalesInvoice.objects.filter(customer=self.customer).latest("pk")
        self.assertEqual((invoice.discount_amount, invoice.total_amount), (D("30.00"), D("220.00")))
        self.assertEqual(InvoiceRedemption.objects.get(invoice=invoice).points, 60)
        self.assertEqual(self.points(), 100 - 60 + 22)

    def test_the_invoice_form_spends_points_and_the_card_shows_them(self):
        self.sell(quantity=4)
        self.client.force_login(self.owner)
        open_test_shift(self.owner, self.cashbox)
        self.assertContains(self.client.get(reverse("sales:create") + "?lang=ar"), 'name="redeem_points"')
        data = {"lang": "ar", "invoice_number": "INV-LY-1", "invoice_date": TODAY.isoformat(), "customer": self.customer.pk, "selling_location": self.location.pk,
                "cashbox": self.cashbox.pk, "paid_now": "0", "discount_amount": "10", "tax_amount": "0", "redeem_points": "20",
                "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "1000",
                "lines-0-item": self.shirt.pk, "lines-0-quantity": "1", "lines-0-unit_sale_price": "250.00", "lines-0-line_discount_amount": "0", "then": "post"}
        self.assertEqual(self.client.post(reverse("sales:create"), data).status_code, 302)
        invoice = SalesInvoice.objects.get(invoice_number="INV-LY-1")
        self.assertEqual((invoice.status, invoice.discount_amount, invoice.total_amount), ("posted", D("20.00"), D("230.00")))  # 10 typed + 20 points × 0.50
        self.assertContains(self.client.post(reverse("sales:create"), dict(data, invoice_number="INV-LY-2", redeem_points="5000")), "نقطة بس")
        card = self.client.get(reverse("parties:card", args=["customer", self.customer.pk]) + "?lang=ar")
        self.assertContains(card, "data-party-points")
        self.assertContains(card, "data-points-rows")
        balance = self.client.get(reverse("parties:balance", args=["customer", self.customer.pk])).json()
        self.assertEqual(balance["points"], 100 - 20 + 23)

    def test_the_rules_screen(self):
        self.client.force_login(self.owner)
        url = reverse("loyalty:home")
        for lang in ("ar", "en"):
            self.assertContains(self.client.get(url + f"?lang={lang}"), "data-loyalty-rules")
        self.sell(quantity=4)
        self.assertContains(self.client.get(url + "?lang=ar"), f'data-loyalty-customer="{self.customer.pk}"')
        response = self.client.post(url, {"lang": "ar", "amount_per_point": "20", "point_value": "1", "min_points": "50", "expiry_months": "12"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(LoyaltySettings.get().amount_per_point, D("20.00"))
        self.assertContains(self.client.post(url, {"lang": "ar", "amount_per_point": "0", "point_value": "1", "min_points": "1"}), "أكبر من صفر")
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.post(url, {"lang": "ar", "amount_per_point": "5", "point_value": "1", "min_points": "1"}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            services.save_rules({"amount_per_point": "5", "point_value": "1", "min_points": "1"}, self.cashier)

    def test_switched_off_nothing_is_earned_or_offered(self):
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        caps.set_capability_enabled(ClientProfile.get_active(), "loyalty", False)
        self.sell(quantity=4)
        self.assertFalse(PointsEntry.objects.exists())
        with self.assertRaisesMessage(ValidationError, "مقفولة"):
            services.redeem(self.customer, "20", D("100.00"))
        self.client.force_login(self.owner)
        open_test_shift(self.owner, self.cashbox)
        self.assertNotContains(self.client.get(reverse("sales:pos") + "?lang=ar"), "data-pos-points")
        self.assertNotIn("points", self.client.get(reverse("parties:balance", args=["customer", self.customer.pk])).json())


class OfflineTillTests(TestCase):
    def test_an_offline_sale_with_points_is_not_kept_on_the_device(self):
        from django.contrib.staticfiles import finders

        with open(finders.find("hesba/js/pos_offline.js"), encoding="utf-8") as handle:
            js = handle.read()
        self.assertIn('[name="redeem_points"]', js)  # the sync spends no points, so the till refuses to keep such a sale offline
        self.assertIn("render(T.points); return;", js)


class CodexReviewTests(LoyaltySetup):
    """Codex on #274: spending never uses points that had run out, split returns take back the full share, and a
    second sale on the same points is refused under the customer's lock."""

    def test_a_spend_never_uses_points_that_had_run_out(self):
        LoyaltySettings.objects.filter(pk=1).update(expiry_months=6)
        old = self.sell(quantity=4)  # 100
        PointsEntry.objects.filter(invoice=old).update(entry_date=TODAY - timedelta(days=200), expires_on=TODAY - timedelta(days=20))
        self.sell(quantity=2)  # 50, still good
        PointsEntry.objects.create(customer=self.customer, kind=EntryKind.SPEND, points=-20, entry_date=TODAY)
        now = services.balance(self.customer)
        self.assertEqual((now["expired"], now["points"]), (100, 30))  # the 20 came out of the live 50

    def test_split_returns_take_back_the_whole_share(self):
        cheap = make_item(item_code="CH", item_name="جورب", default_sale_price="15.00")
        stock_in(cheap, self.location, 10, "5.00", movement_date=TODAY)
        recalculate_item_average_cost(cheap)
        invoice, _change = checkout(lines=[line(cheap, 2, "15.00")], customer=self.customer, location=self.location, cashbox=self.cashbox,
                                    discount=D("0"), tendered=D("100"), user=self.owner)  # 30.00 → 3 points
        source = invoice.lines.get()
        first = create_sales_return("LY-S1", TODAY, invoice.pk, [{"source_line": source, "quantity": D("1")}], "مقاس", self.owner)
        self.assertEqual(self.points(), 2)  # 1.5 rounded down
        create_sales_return("LY-S2", TODAY, invoice.pk, [{"source_line": source, "quantity": D("1")}], "مقاس", self.owner)
        self.assertEqual(self.points(), 0)  # all of it came back: nothing kept
        cancel_sales_return(first.pk, TODAY, "رجع في كلامه", self.owner)
        self.assertEqual(self.points(), 2)  # one of the two kept again: 3 − floor(1.5)

    def test_a_second_draft_cannot_set_aside_the_same_points(self):
        from django.db import transaction

        from sales.services import create_sales_draft

        self.sell(quantity=4)  # 100

        def draft(number):
            return create_sales_draft({"invoice_number": number, "invoice_date": TODAY, "customer": self.customer, "selling_location": self.location,
                                       "cashbox": self.cashbox, "paid_now": D("0"), "discount_amount": D("0")}, [line(self.shirt, 1, "250.00")], self.owner)

        with transaction.atomic():
            services.remember(draft("LY-D1"), 80, D("40.00"))
        with self.assertRaisesMessage(ValidationError, "رصيد النقاط"):
            with transaction.atomic():
                services.remember(draft("LY-D2"), 40, D("20.00"))

    def test_points_owed_are_paid_from_the_next_earnings_once(self):
        LoyaltySettings.objects.filter(pk=1).update(min_points=1, expiry_months=6)
        first = self.sell(quantity=4)  # 100
        points, off = services.redeem(self.customer, "100", D("1000.00"))
        self.sell(quantity=4, points=(points, off), discount=str(off))  # spends the 100, earns 95 on 950.00
        create_sales_return("LY-O1", TODAY, first.pk, [{"source_line": first.lines.get(), "quantity": D("4")}], "كله", self.owner)  # the 100 is clawed back
        self.assertEqual(self.points(), -5)  # 95 earned against 100 owed
        PointsEntry.objects.filter(kind=EntryKind.EARN).update(expires_on=TODAY - timedelta(days=1))  # everything runs out
        self.assertEqual(self.points(), -5)  # the debt is not counted again once the later points expire

    def test_an_audit_row_without_its_document_leaves_points_alone(self):
        from audit.models import AuditEventType, AuditLog

        AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=self.owner, module="sales", action="cancel_posted_sales_invoice",
                                object_type="sales.SalesInvoice", object_id="999999", before_data={}, after_data={})
        AuditLog.objects.create(event_type=AuditEventType.CREATE, actor=self.owner, module="sales", action="create_sales_return",
                                object_type="sales.SalesReturn", object_id="", before_data={}, after_data={})
        self.assertFalse(PointsEntry.objects.exists())
