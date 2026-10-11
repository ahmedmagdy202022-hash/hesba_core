"""SVC-001: work orders, end to end, with explicit stock, cash and ledger assertions (HG-041, design A)."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import CashboxDirection
from hesba_testing.factories import (make_cashbox, make_cashbox_movement, make_customer, make_item, make_location, make_role, make_seeded_role,
                                     make_user, make_user_profile, stock_in)
from inventory.services import get_item_stock_quantity
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core import setup_catalog as catalog

from . import services
from .models import Status, WorkOrder

TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class WorkshopSetup(TestCase):
    sub = "maintenance"

    def setUp(self):
        prepared_client("services", self.sub, ",".join(catalog.default_modules("services")))
        from staff.services import save_employee

        self.owner = person(RoleCode.OWNER, "wo_owner")
        self.location = make_location(location_code="SHOP", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="CASH-WO", is_default=True)
        make_cashbox_movement(self.cashbox, CashboxDirection.IN, "1000.00")
        self.part = make_item(item_code="PART-1", item_name="موتور غسالة", default_sale_price=D("1800.00"), is_stock_tracked=True)
        stock_in(self.part, self.location, "5", "1350.00")
        self.labour = make_item(item_code="LAB-1", item_name="مصنعية إصلاح", default_sale_price=D("300.00"), is_stock_tracked=False)
        self.customer = make_customer(customer_code="CUS-WO", name="محمد علي", phone="01001234567")
        self.tech = save_employee({"name": "أحمد الفني", "title": "فني"}, self.owner)

    def order(self, **extra):
        return services.receive({"customer": self.customer, "device": "غسالة توشيبا", "problem": "مش بتعصر", "technician": self.tech,
                                 "warranty_days": "30", **extra}, self.owner)

    def to_ready(self, order):
        for status in (Status.DIAGNOSED, Status.APPROVED, Status.IN_REPAIR, Status.READY):
            services.move(order, status, self.owner)
        order.refresh_from_db()
        return order


class WorkOrderCycleTests(WorkshopSetup):
    def test_delivery_is_one_ordinary_invoice_moving_the_exact_parts(self):
        from cashboxes.services import get_cashbox_balance
        from parties.services import balance

        order = self.order()
        self.assertTrue(order.number.startswith("WO-"))
        services.add_line(order, self.part, self.owner, quantity="1")
        services.add_line(order, self.labour, self.owner)
        self.assertEqual(get_item_stock_quantity(self.part), D("5"))  # an open job moves nothing
        self.assertEqual(services.reserved_parts(), {self.part.pk: D("1.000")})
        order = self.to_ready(order)
        services.deliver(order, self.owner, paid_now="1000", cashbox=self.cashbox)
        order.refresh_from_db()
        invoice = order.invoice
        self.assertEqual((order.status, invoice.status, invoice.total_amount, invoice.paid_now), (Status.DELIVERED, "posted", D("2100.00"), D("1000.00")))
        self.assertEqual(get_item_stock_quantity(self.part), D("4"))
        part_line = invoice.lines.get(item=self.part)
        self.assertEqual(part_line.line_cost_amount, D("1350.00"))  # at movement cost
        self.assertEqual(balance("customer", self.customer), D("1100.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("2000.00"))
        self.assertEqual(order.warranty_until, TODAY + timedelta(days=30))
        self.assertEqual(services.reserved_parts(), {})
        self.assertEqual(list(order.events.values_list("status", flat=True)), ["received", "diagnosed", "approved", "in_repair", "ready", "delivered"])

    def test_a_warranty_part_is_free_and_still_costed(self):
        from ledger import reports as gl
        from parties.services import balance

        first = self.to_ready(self.order())
        services.deliver(first, self.owner)  # nothing on it: delivered without an invoice
        first.refresh_from_db()
        self.assertIsNone(first.invoice)
        back = services.receive({"warranty_of": first, "device": first.device, "problem": "رجع نفس العطل"}, self.owner)
        self.assertEqual(back.customer, self.customer)
        line = services.add_line(back, self.part, self.owner)
        self.assertTrue(line.is_warranty)
        back = self.to_ready(back)
        services.deliver(back, self.owner)
        back.refresh_from_db()
        sold = back.invoice.lines.get()
        self.assertEqual((sold.unit_sale_price, sold.line_total_amount, sold.line_cost_amount), (D("0.00"), D("0.00"), D("1350.00")))
        self.assertEqual(back.invoice.total_amount, D("0.00"))
        self.assertEqual(balance("customer", self.customer), D("0.00"))
        self.assertEqual(get_item_stock_quantity(self.part), D("4"))
        check = gl.reconciliation()
        self.assertTrue(check["balanced"])
        # The only difference is the test's own opening cash (a bare movement the projector parks in suspense); the jobs add none.
        self.assertEqual({row["key"]: row["difference"] for row in check["rows"] if row["difference"]}, {"suspense": D("-1000.00")})

    def test_cancelling_the_invoice_brings_the_parts_back(self):
        from sales.services import cancel_posted_sales_invoice

        order = self.order()
        services.add_line(order, self.part, self.owner, quantity="2")
        services.deliver(self.to_ready(order), self.owner)
        order.refresh_from_db()
        self.assertEqual(get_item_stock_quantity(self.part), D("3"))
        cancel_posted_sales_invoice(order.invoice.pk, self.owner, "غلط")
        self.assertEqual(get_item_stock_quantity(self.part), D("5"))

    def test_the_order_only_moves_along_its_path(self):
        order = self.order()
        with self.assertRaisesMessage(ValidationError, "مينفعش تنقل"):
            services.move(order, Status.READY, self.owner)
        with self.assertRaisesMessage(ValidationError, "جاهز للتسليم"):
            services.deliver(order, self.owner)
        services.move(order, Status.CANCELLED, self.owner)
        with self.assertRaisesMessage(ValidationError, "اتقفل"):
            services.add_line(order, self.labour, self.owner)

    def test_paying_more_than_the_invoice_is_refused(self):
        order = self.order()
        services.add_line(order, self.labour, self.owner)
        with self.assertRaisesMessage(ValidationError, "المدفوع"):
            services.deliver(self.to_ready(order), self.owner, paid_now="301", cashbox=self.cashbox)
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice), (Status.READY, None))

    def test_a_technician_moves_their_own_orders_along_the_bench_only(self):
        technician = make_user(username="wo_tech")
        make_user_profile(user=technician, role=make_role("ROLE-TECH"))
        self.tech.user = technician
        self.tech.save()
        mine = self.order()
        services.move(mine, Status.DIAGNOSED, technician)
        services.update_details(mine, {"diagnosis": "الموتور محروق"}, technician)
        with self.assertRaises(PermissionDenied):
            services.move(mine, Status.CANCELLED, technician)  # cancelling is the counter's
        with self.assertRaises(PermissionDenied):
            services.add_line(mine, self.part, technician)  # so is what the customer pays
        from staff.services import save_employee

        other = self.order(technician=save_employee({"name": "فني تاني"}, self.owner))
        with self.assertRaises(PermissionDenied):
            services.move(other, Status.DIAGNOSED, technician)
        self.assertEqual(list(services.visible_to(technician)), [mine])


class WorkOrderScreenTests(WorkshopSetup):
    def test_the_counter_receives_prints_works_and_delivers(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("workorders:new"), {"customer_name": "عميل جديد", "customer_phone": "01112223334", "device": "تكييف",
                                                                "problem": "مش بيبرّد", "warranty_days": "30"})
        order = WorkOrder.objects.get()
        self.assertRedirects(response, f"{reverse('workorders:receipt', args=[order.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("workorders:receipt", args=[order.pk])), order.number)
        url = reverse("workorders:detail", args=[order.pk])
        self.client.post(url, {"action": "add_line", "item": self.labour.pk, "quantity": "1"})
        for status in ("diagnosed", "approved", "in_repair", "ready"):
            self.client.post(url, {"action": "move", "to": status})
        page = self.client.get(url)
        self.assertContains(page, "data-deliver")
        self.client.post(url, {"action": "deliver", "paid_now": "300", "cashbox": self.cashbox.pk})
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice.total_amount), (Status.DELIVERED, D("300.00")))
        board = self.client.get(reverse("workorders:list") + "?show=delivered")
        self.assertContains(board, f'data-order="{order.pk}"')
        self.assertContains(self.client.get(reverse("workorders:report")), f'data-tech="0"')

    def test_the_menu_dashboard_and_lead_action_follow_the_workshop(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("dashboard_snapshot"))
        self.assertContains(page, f'href="{reverse("workorders:list")}?lang=ar"')
        self.assertContains(page, "الورشة دلوقتي")
        self.assertContains(page, f'href="{reverse("workorders:new")}?lang=ar" data-action="workorders"')

    def test_who_may_open_the_screens(self):
        late = self.order(promised_on=(TODAY - timedelta(days=2)).isoformat())
        stock_keeper = person(RoleCode.STOCK_KEEPER, "wo_stock")
        self.client.force_login(stock_keeper)
        self.assertEqual(self.client.get(reverse("workorders:list")).status_code, 403)
        cashier = person(RoleCode.CASHIER, "wo_cashier")
        self.client.force_login(cashier)
        from permissions.services import user_has_permission

        if user_has_permission(cashier, "sales.view_sales_invoices") and not user_has_permission(cashier, "sales.create_sales_invoice"):
            self.assertEqual(self.client.get(reverse("workorders:new")).status_code, 403)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("workorders:list") + "?show=late"), f'data-order="{late.pk}"')


class NotAWorkshopTests(WorkshopSetup):
    sub = "beauty"

    def test_a_salon_has_no_work_orders(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("workorders:list")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("dashboard_snapshot")), reverse("workorders:list"))
