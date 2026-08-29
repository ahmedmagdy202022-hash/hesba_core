from datetime import date

from django.test import TestCase
from django.urls import reverse

from closing.models import Period
from hesba_testing.factories import (
    make_item,
    make_location,
    make_seeded_role,
    make_user,
    make_user_profile,
    stock_in,
)
from permissions.models import RoleCode

from .models import StockOperation, StockOperationStatus


class StockOperationUiTests(TestCase):
    def setUp(self):
        Period.objects.create(
            period_code="2026-UI-STOCK",
            name="2026 UI stock",
            start_date=date(2026, 1, 1),
            end_date=date(2026, 12, 31),
        )
        self.item = make_item()
        self.source = make_location()
        self.destination = make_location(location_code="UI-DEST", name_ar="فرع")
        stock_in(self.item, self.source, 10, "4.00")

    def login_as(self, role_code, username):
        user = make_user(username)
        make_user_profile(user, make_seeded_role(role_code))
        self.client.force_login(user)
        return user

    def test_stock_keeper_posts_transfer_through_service_route(self):
        self.login_as(RoleCode.STOCK_KEEPER, "stock_transfer_ui")
        response = self.client.post(
            reverse("inventory:transfer"),
            {
                "lang": "en",
                "reference_number": "UI-TR-1",
                "operation_date": "2026-03-01",
                "item": self.item.pk,
                "source_location": self.source.pk,
                "destination_location": self.destination.pk,
                "quantity": "2.000",
                "reason": "Replenish branch",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(StockOperation.objects.get().movements.count(), 2)

    def test_stock_keeper_adjustment_form_hides_cost(self):
        self.login_as(RoleCode.STOCK_KEEPER, "stock_adjust_ui")
        response = self.client.get(reverse("inventory:adjustment"), {"lang": "en"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("unit_cost", response.context["form"].fields)

    def test_owner_adjustment_form_exposes_cost(self):
        self.login_as(RoleCode.OWNER, "stock_adjust_owner")
        response = self.client.get(reverse("inventory:adjustment"), {"lang": "en"})
        self.assertIn("unit_cost", response.context["form"].fields)

    def test_cashier_cannot_open_stock_operations(self):
        self.login_as(RoleCode.CASHIER, "stock_operation_denied")
        self.assertEqual(self.client.get(reverse("inventory:operations")).status_code, 403)

    def test_reversal_route_requires_reason_and_appends_history(self):
        self.login_as(RoleCode.STOCK_KEEPER, "stock_reverse_ui")
        self.client.post(
            reverse("inventory:transfer"),
            {
                "reference_number": "UI-TR-REV",
                "operation_date": "2026-03-01",
                "item": self.item.pk,
                "source_location": self.source.pk,
                "destination_location": self.destination.pk,
                "quantity": "2.000",
                "reason": "Move",
            },
        )
        operation = StockOperation.objects.get()
        response = self.client.post(
            reverse("inventory:operation_cancel", args=[operation.pk]),
            {"reversal_date": "2026-03-02", "reason": "Wrong destination"},
        )
        self.assertEqual(response.status_code, 302)
        operation.refresh_from_db()
        self.assertEqual(operation.status, StockOperationStatus.CANCELLED)
        self.assertEqual(operation.movements.count(), 4)
