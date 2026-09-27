"""INSTAL-001: a schedule over a posted credit sale, collected as ordinary customer payments."""

from datetime import date, timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.services import get_cashbox_balance
from closing.models import Period
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from reports.dashboard_data import build_alerts
from reports.tests_dashboard import prepared_client
from sales.models import CustomerLedgerEntry, CustomerPayment
from sales.services import cancel_customer_payment, cancel_posted_sales_invoice, create_sales_return, post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile
from taxes.services import create_sales_draft_with_tax

from .models import InstalmentPlan
from .services import add_months, cancel_plan, collect, create_plan, overdue_summary, schedule, split


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class HelperTests(SimpleTestCase):
    def test_split_and_months(self):
        self.assertEqual(split(D("1000.00"), 3), [D("333.33"), D("333.33"), D("333.34")])
        self.assertEqual(sum(split(D("7999.99"), 12)), D("7999.99"))
        self.assertEqual(add_months(date(2026, 1, 31), 1), date(2026, 2, 28))


class PlanSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(sub_activity="electronics")  # instalments suggested -> on
        Period.objects.create(period_code="I", name="i", start_date=TODAY - timedelta(days=200), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "instal_owner")
        self.location = make_location(location_code="SHOP", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        self.tv = make_item(item_code="TV", item_name="TV 55", default_sale_price=D("12000.00"))
        stock_in(self.tv, self.location, 5, "9000.00", movement_date=TODAY - timedelta(days=120))
        recalculate_item_average_cost(self.tv)
        self.customer = make_customer(customer_code="MONA", name="Mona", phone="01001234567")

    def sale(self, paid="3000.00", days_ago=0, number="SI-I-1"):
        invoice = create_sales_draft_with_tax(
            {"invoice_number": number, "invoice_date": TODAY - timedelta(days=days_ago), "customer": self.customer, "selling_location": self.location, "cashbox": self.cashbox, "paid_now": D(paid)},
            [{"item": self.tv, "quantity": D("1"), "unit_sale_price": D("12000.00")}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        return invoice


class PlanTests(PlanSetup):
    def test_plan_splits_the_remaining_due_monthly(self):
        invoice = self.sale()
        plan = create_plan(invoice, 4, TODAY + timedelta(days=30), self.owner)
        self.assertEqual(plan.financed_amount, D("9000.00"))
        rows = list(plan.instalments.values_list("number", "due_date", "amount"))
        self.assertEqual(rows, [(n, add_months(TODAY + timedelta(days=30), n - 1), D("2250.00")) for n in range(1, 5)])
        self.assertTrue(AuditLog.objects.filter(module="installments", action="create_instalment_plan").exists())
        with self.assertRaises(ValidationError):
            create_plan(invoice, 3, TODAY + timedelta(days=30), self.owner)  # one plan per invoice

    def test_refusals(self):
        for kwargs, message in (({"paid": "12000.00", "number": "SI-X1"}, "مفيش مبلغ متبقي"),):
            with self.assertRaisesMessage(ValidationError, message):
                create_plan(self.sale(**kwargs), 3, TODAY + timedelta(days=30), self.owner)
        invoice = self.sale(number="SI-X2")
        with self.assertRaisesMessage(ValidationError, "من 1 لـ 60"):
            create_plan(invoice, 0, TODAY + timedelta(days=30), self.owner)
        with self.assertRaisesMessage(ValidationError, "بعد تاريخ الفاتورة"):
            create_plan(invoice, 3, invoice.invoice_date, self.owner)

    def test_collections_settle_the_oldest_first_and_move_cash_and_ledger(self):
        invoice = self.sale(days_ago=70)
        plan = create_plan(invoice, 3, TODAY - timedelta(days=40), self.owner)  # 3000 each; #1 and #2 already due
        info = schedule(plan, TODAY)
        self.assertEqual([row["state"] for row in info["rows"]], ["overdue", "overdue", "upcoming"])
        self.assertEqual(info["overdue"], D("6000.00"))
        before = get_cashbox_balance(self.cashbox)
        payment = collect(plan, D("4000.00"), self.cashbox, TODAY, self.owner)
        self.assertEqual(get_cashbox_balance(self.cashbox) - before, D("4000.00"))
        self.assertTrue(CustomerLedgerEntry.objects.filter(customer_payment=payment, due_decrease=D("4000.00")).exists())
        info = schedule(plan, TODAY)
        self.assertEqual([(row["state"], row["settled"], row["partial"]) for row in info["rows"]], [("paid", D("3000.00"), False), ("overdue", D("1000.00"), True), ("upcoming", D("0.00"), False)])
        self.assertEqual((info["paid"], info["remaining"], info["overdue"], info["next_open"]), (D("4000.00"), D("5000.00"), D("2000.00"), D("2000.00")))
        with self.assertRaisesMessage(ValidationError, "مش أكبر من الباقي"):
            collect(plan, D("5000.01"), self.cashbox, TODAY, self.owner)
        # A cancelled collection stops counting.
        cancel_customer_payment(payment.pk, self.owner, "typo")
        self.assertEqual(schedule(plan, TODAY)["paid"], D("0.00"))
        collect(plan, D("9000.00"), self.cashbox, TODAY, self.owner)
        self.assertTrue(schedule(plan, TODAY)["done"])

    def test_returns_are_credited_and_a_cancelled_invoice_stops_the_plan(self):
        invoice = self.sale()
        plan = create_plan(invoice, 3, TODAY + timedelta(days=30), self.owner)
        create_sales_return(return_number="SR-I-1", return_date=TODAY, source_invoice_id=invoice.pk, lines=[{"source_line": invoice.lines.get(), "quantity": D("1")}], reason="faulty", user=self.owner)
        info = schedule(plan, TODAY)
        self.assertEqual(info["credited"], D("9000.00"))
        self.assertTrue(info["done"])
        other = self.sale(number="SI-I-2")
        plan2 = create_plan(other, 2, TODAY + timedelta(days=30), self.owner)
        cancel_posted_sales_invoice(other.pk, self.owner, "mistake")
        with self.assertRaisesMessage(ValidationError, "الفاتورة اتلغت"):
            collect(plan2, D("100"), self.cashbox, TODAY, self.owner)

    def test_overdue_alert_and_cancelling_a_plan(self):
        plan = create_plan(self.sale(days_ago=70), 3, TODAY - timedelta(days=40), self.owner)
        self.assertEqual(overdue_summary(TODAY), {"count": 2, "amount": D("6000.00"), "plans": 1})
        alerts = {alert["key"]: alert for alert in build_alerts({"reports.view_customer_report"}, TODAY)}
        self.assertEqual(alerts["instalments_overdue"]["amount"], "6,000.00")
        collect(plan, D("10"), self.cashbox, TODAY, self.owner)
        with self.assertRaisesMessage(ValidationError, "عليها تحصيلات"):
            cancel_plan(plan, self.owner)
        cancel_customer_payment(CustomerPayment.objects.get().pk, self.owner, "typo")
        cancel_plan(plan, self.owner)
        self.assertEqual(overdue_summary(TODAY)["count"], 0)
        set_capability_enabled(self.profile, "installments", False)
        self.assertNotIn("instalments_overdue", {alert["key"] for alert in build_alerts({"reports.view_customer_report"}, TODAY)})


class ScreenTests(PlanSetup):
    def test_create_collect_and_remind_from_the_screens(self):
        invoice = self.sale()
        self.client.force_login(self.owner)
        detail = self.client.get(reverse("sales:detail", args=[invoice.pk]))
        self.assertContains(detail, reverse("installments:create", args=[invoice.pk]))
        self.assertContains(self.client.get(reverse("installments:create", args=[invoice.pk])), "data-new-plan")
        response = self.client.post(reverse("installments:create", args=[invoice.pk]), {"count": "6", "first_due_date": (TODAY + timedelta(days=30)).isoformat()})
        plan = InstalmentPlan.objects.get()
        self.assertRedirects(response, f"{reverse('installments:detail', args=[plan.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("sales:detail", args=[invoice.pk])), "data-instalment-link")
        page = self.client.get(reverse("installments:detail", args=[plan.pk]))
        self.assertContains(page, 'data-instalment="upcoming"', count=6)
        self.assertContains(page, "https://wa.me/201001234567")
        self.client.post(reverse("installments:detail", args=[plan.pk]), {"action": "collect", "amount": "1500", "cashbox": self.cashbox.pk, "payment_date": TODAY.isoformat()})
        self.assertEqual(schedule(plan)["paid"], D("1500.00"))
        self.assertContains(self.client.get(reverse("installments:detail", args=[plan.pk])), 'data-instalment="paid"', count=1)
        bad = self.client.post(reverse("installments:detail", args=[plan.pk]), {"action": "collect", "amount": "abc", "cashbox": self.cashbox.pk}, follow=True)
        self.assertContains(bad, "بيانات غير صحيحة")
        listing = self.client.get(reverse("installments:list"), {"lang": "en"})
        self.assertContains(listing, f'data-plan-row="{plan.pk}"')
        self.assertContains(self.client.get(reverse("sales:list")), reverse("installments:list"))

    def test_permissions_and_capability_gate(self):
        invoice = self.sale()
        plan = create_plan(invoice, 2, TODAY + timedelta(days=30), self.owner)
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "instal_store"))
        self.assertEqual(self.client.get(reverse("installments:detail", args=[plan.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("installments:detail", args=[plan.pk]), {"action": "cancel"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("installments:create", args=[invoice.pk]), {"count": "2", "first_due_date": "2099-01-01"}).status_code, 403)
        plan.refresh_from_db()
        self.assertEqual(plan.status, "active")
        set_capability_enabled(ClientProfile.get_active(), "installments", False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("installments:list")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("sales:detail", args=[invoice.pk])), "data-instalment-link")
