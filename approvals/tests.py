"""FB-27: what the owner holds for a second person's approval waits, and runs only when someone else approves it."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import (
    grant,
    make_location,
    make_item,
    make_role,
    make_seeded_role,
    make_user,
    make_user_profile,
    posted_invoice_ready,
    stock_in,
)
from inventory.models import StockOperation
from inventory.services import adjust_stock, get_item_stock_quantity, recalculate_item_average_cost
from permissions.models import Permission, RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from sales.services import cancel_posted_sales_invoice, post_sales_invoice

from . import services
from .models import ApprovalRequest


def clerk_with(*codes, username="clerk"):
    role = make_role(code=f"CLERK-{username}", name_ar="موظف", name_en="Clerk")
    for code in codes:
        grant(role, Permission.objects.get(code=code))
    user = make_user(username)
    make_user_profile(user=user, role=role)
    return user


class ApprovalCase(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = make_user("owner_fb27", is_superuser=True)
        self.manager = make_user("manager_fb27")
        make_user_profile(user=self.manager, role=make_seeded_role(RoleCode.MANAGER))
        self.clerk = clerk_with("sales.return_sale", "sales.view_sales_invoices", "inventory.adjust_stock", "inventory.view_stock")
        invoice, *_ = posted_invoice_ready()
        post_sales_invoice(invoice.pk, self.owner)
        self.invoice = SalesInvoice.objects.get(pk=invoice.pk)

    def hold_cancellations(self):
        services.set_held_actions(["sales.cancel_invoice", "inventory.reverse_operation"], self.owner)

    def ask_to_cancel(self, user=None):
        self.client.force_login(user or self.clerk)
        return self.client.post(f"/sales/{self.invoice.pk}/cancel/", {"reason": "wrong customer", "lang": "ar"}, follow=True)

    def status(self):
        return SalesInvoice.objects.get(pk=self.invoice.pk).status


class HoldingTests(ApprovalCase):
    def test_nothing_is_held_until_the_owner_says_so(self):
        self.assertEqual(services.held_actions(), set())
        self.ask_to_cancel()
        self.assertEqual(self.status(), "cancelled")
        self.assertFalse(ApprovalRequest.objects.exists())

    def test_a_held_action_waits_and_nothing_moves(self):
        self.hold_cancellations()
        page = self.ask_to_cancel()
        self.assertContains(page, "محتاجة موافقة")
        self.assertEqual(self.status(), "posted")
        request = ApprovalRequest.objects.get()
        self.assertEqual((request.action, request.object_id, request.object_label, request.data, request.requested_by),
                         ("sales.cancel_invoice", self.invoice.pk, self.invoice.invoice_number, {"reason": "wrong customer"}, self.clerk))
        self.ask_to_cancel()  # asking again does not ask twice
        self.assertEqual(ApprovalRequest.objects.count(), 1)

    def test_those_who_may_approve_act_directly(self):
        self.hold_cancellations()
        self.ask_to_cancel(self.manager)
        self.assertEqual(self.status(), "cancelled")
        self.assertFalse(ApprovalRequest.objects.exists())

    def test_only_the_owner_chooses_what_is_held(self):
        with self.assertRaises(PermissionDenied):
            services.set_held_actions(["sales.cancel_invoice"], self.manager)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post("/approvals/", {"do": "settings", "actions": ["sales.cancel_invoice"]}).status_code, 403)
        self.assertEqual(services.held_actions(), set())
        self.client.force_login(self.owner)
        self.assertContains(self.client.get("/approvals/"), "data-approvals-settings")
        self.client.post("/approvals/", {"do": "settings", "actions": ["sales.cancel_invoice", "not.an_action"]})
        self.assertEqual(services.held_actions(), {"sales.cancel_invoice"})
        self.assertTrue(AuditLog.objects.filter(action="set_held_actions", actor=self.owner).exists())


class DecidingTests(ApprovalCase):
    def setUp(self):
        super().setUp()
        self.hold_cancellations()
        self.ask_to_cancel()
        self.request = ApprovalRequest.objects.get()

    def test_approving_runs_it_in_the_requesters_name(self):
        self.client.force_login(self.manager)
        page = self.client.get("/approvals/")
        self.assertContains(page, f'data-approval="{self.request.pk}"')
        dashboard = self.client.get("/dashboard/")
        self.assertIn("approvals", [alert["key"] for alert in dashboard.context["alerts"]])
        self.client.post("/approvals/", {"do": "approve", "request": self.request.pk})
        self.assertEqual(self.status(), "cancelled")
        self.request.refresh_from_db()
        self.assertEqual((self.request.status, self.request.decided_by), ("done", self.manager))
        self.assertEqual(AuditLog.objects.get(action="cancel_posted_sales_invoice").actor, self.clerk)
        self.assertTrue(AuditLog.objects.filter(action="approval_done", actor=self.manager).exists())

    def test_nobody_approves_their_own_request(self):
        grant(self.clerk.hesba_profile.role, Permission.objects.get(code="approvals.approve"))
        with self.assertRaisesMessage(ValidationError, "approval_own_request"):
            services.approve(self.request.pk, self.clerk)
        self.assertFalse(services.waiting_for(self.clerk).exists())
        self.assertEqual(self.status(), "posted")

    def test_those_who_may_not_approve_cannot(self):
        other = clerk_with("sales.return_sale", username="other")
        with self.assertRaises(PermissionDenied):
            services.approve(self.request.pk, other)
        self.client.force_login(other)
        self.assertNotContains(self.client.get("/approvals/"), "data-approvals-waiting")

    def test_rejecting_or_withdrawing_leaves_it_as_it_was(self):
        services.reject(self.request.pk, self.manager, "keep it")
        self.assertEqual(self.status(), "posted")
        self.request.refresh_from_db()
        self.assertEqual((self.request.status, self.request.note), ("rejected", "keep it"))
        with self.assertRaisesMessage(ValidationError, "approval_not_waiting"):
            services.approve(self.request.pk, self.manager)
        self.ask_to_cancel()
        again = ApprovalRequest.objects.get(status="pending")
        with self.assertRaises(PermissionDenied):
            services.withdraw(again.pk, self.manager)
        self.client.force_login(self.clerk)
        self.client.post("/approvals/", {"do": "withdraw", "request": again.pk})
        again.refresh_from_db()
        self.assertEqual(again.status, "withdrawn")
        self.assertEqual(self.status(), "posted")

    def test_an_approval_that_can_no_longer_be_done_says_why(self):
        cancel_posted_sales_invoice(self.invoice.pk, self.manager, "done by hand")
        request = services.approve(self.request.pk, self.manager)
        self.assertEqual(request.status, "failed")
        self.assertIn("Only posted sales invoices can be cancelled", request.note)


class DatedReversalTests(ApprovalCase):
    def test_a_reversal_keeps_the_date_it_was_asked_for(self):
        self.hold_cancellations()
        location, item = make_location("ADJ"), make_item("ADJ-1")
        stock_in(item, location, 5, "10.00", movement_date=timezone.localdate() - timedelta(days=3))
        recalculate_item_average_cost(item)
        operation = adjust_stock("ADJ-OP-1", timezone.localdate(), item, location, "in", D("2"), "found", self.owner)
        asked_for = timezone.localdate() - timedelta(days=1)
        self.client.force_login(self.clerk)
        self.client.post(f"/inventory/operations/{operation.pk}/reverse/", {"reversal_date": asked_for.isoformat(), "reason": "counted twice"})
        request = ApprovalRequest.objects.get()
        self.assertEqual(request.data, {"reversal_date": asked_for.isoformat(), "reason": "counted twice"})
        self.assertEqual(get_item_stock_quantity(item), D("7"))
        self.assertEqual(services.approve(request.pk, self.manager).status, "done")
        operation = StockOperation.objects.get(pk=operation.pk)
        self.assertEqual((operation.status, operation.reversal_date, operation.cancelled_by), ("cancelled", asked_for, self.clerk))
        self.assertEqual(get_item_stock_quantity(item), D("5"))


class StillAllowedTests(ApprovalCase):
    """Codex #280: an approval runs only what may still be done, and an approver sees every request of their entity."""

    def setUp(self):
        super().setUp()
        self.hold_cancellations()
        self.ask_to_cancel()
        self.request = ApprovalRequest.objects.get()

    def test_a_requester_who_lost_the_permission_is_not_acted_for(self):
        from permissions.models import RolePermission

        RolePermission.objects.filter(role=self.clerk.hesba_profile.role, permission__code="sales.return_sale").delete()
        request = services.approve(self.request.pk, self.manager)
        self.assertEqual((request.status, request.note), ("failed", "approval_requester_not_allowed"))
        self.assertEqual(self.status(), "posted")
        self.client.force_login(self.clerk)
        self.assertContains(self.client.get("/approvals/"), "مبقاش مسموح له")

    def test_a_switched_off_account_is_not_acted_for(self):
        self.clerk.is_active = False
        self.clerk.save(update_fields=["is_active"])
        self.assertEqual(services.approve(self.request.pk, self.manager).note, "approval_requester_not_allowed")
        self.assertEqual(self.status(), "posted")

    def test_a_module_switched_off_since_the_request_stops_it(self):
        from unittest import mock

        with mock.patch("settings_core.module_gate.closed_module", return_value="sales_operations"):
            request = services.approve(self.request.pk, self.manager)
        self.assertEqual((request.status, request.note), ("failed", "approval_module_closed"))
        self.assertEqual(self.status(), "posted")

    def test_the_entity_filter_is_part_of_the_query(self):
        from unittest import mock

        self.assertEqual(services.waiting_for(self.manager).count(), 1)
        with mock.patch("entities.scope.scope", side_effect=lambda queryset, path: queryset.none()):
            self.assertEqual(services.waiting_for(self.manager).count(), 0)  # another entity's document: not counted
            self.client.force_login(self.manager)
            self.assertNotContains(self.client.get("/approvals/"), f'data-approval="{self.request.pk}"')

    def test_a_requester_moved_out_of_the_documents_entity_is_not_acted_for(self):
        from unittest import mock

        with mock.patch("entities.scope.for_user", side_effect=lambda queryset, path, user: queryset.none()):
            request = services.approve(self.request.pk, self.manager)
        self.assertEqual((request.status, request.note), ("failed", "approval_requester_not_allowed"))
        self.assertEqual(self.status(), "posted")

    def test_every_waiting_request_stays_on_the_requesters_list(self):
        for number in range(40):  # many decided requests on top of the waiting one
            ApprovalRequest.objects.create(action="sales.cancel_invoice", object_id=10_000 + number, status="rejected", requested_by=self.clerk)
        self.client.force_login(self.clerk)
        page = self.client.get("/approvals/")
        self.assertContains(page, f'data-my-approval="{self.request.pk}"')
        self.assertContains(page, "data-withdraw")

