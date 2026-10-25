"""T14 (MARKET-PARITY): couriers and cash on delivery — collected, settled less the fee, came back or lost (HG-061)."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox, CashboxKind
from cashboxes.services import get_cashbox_balance
from entities import scope as entity_scope
from hesba_testing.factories import make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from ledger import reports
from ledger.models import JournalLine
from ledger.projector import Projector
from parties.services import balance
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.services import cancel_customer_payment, create_sales_draft, post_sales_invoice

from . import services
from .models import Shipment, ShipmentStatus

TODAY = timezone.localdate()


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def bal(code):
    agg = JournalLine.objects.filter(account__code=code).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or D("0")) - (agg["c"] or D("0"))


class ShippingSetup(TestCase):
    def setUp(self):
        from settings_core import capabilities as caps
        from settings_core.models import ClientProfile

        prepared_client("commercial", "fashion", modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports")
        caps.set_capability_enabled(ClientProfile.get_active(), "shipping", True)
        self.owner = person(RoleCode.OWNER, "sh_owner")
        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cash = Cashbox.objects.create(cashbox_code="CASH-SH", name_ar="الخزنة", opening_balance=D("1000.00"), is_default=True)
        self.dress = make_item(item_code="DR", item_name="فستان", default_sale_price="500.00")
        stock_in(self.dress, self.location, 10, "200.00", movement_date=TODAY)
        recalculate_item_average_cost(self.dress)
        self.customer = make_customer(customer_code="SH-C", name="سارة")
        self.courier = services.save_courier({"name": "بوسطة", "phone": "0222222", "fee_per_parcel": "30"}, self.owner)

    def invoice(self, number="SH-1"):
        invoice = create_sales_draft({"invoice_number": number, "invoice_date": TODAY, "customer": self.customer, "selling_location": self.location,
                                      "cashbox": self.cash, "paid_now": D("0"), "discount_amount": D("0")},
                                     [{"item": self.dress, "quantity": D("1"), "unit_sale_price": D("500.00"), "line_discount_amount": D("0")}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        return invoice


class CodTests(ShippingSetup):
    def test_collected_then_settled_less_the_fee(self):
        shipment = services.ship(self.invoice(), self.courier, self.owner)
        self.assertEqual((shipment.cod_amount, shipment.fee), (D("500.00"), D("30.00")))
        services.deliver(shipment, self.owner)
        self.assertEqual(balance("customer", self.customer), D("0.00"))  # the customer owes nothing
        self.assertEqual(services.held(self.courier), D("500.00"))  # the courier owes it
        self.assertNotIn(self.courier.cashbox, entity_scope.cashboxes(Cashbox.objects))  # never offered as a cashbox
        Projector().rebuild()
        self.assertEqual(bal("1120"), D("500.00"))  # courier collections receivable, not cash on hand
        settlement = services.settle([shipment], self.cash, self.owner)
        self.assertEqual((settlement.collected, settlement.fees, settlement.net), (D("500.00"), D("30.00"), D("470.00")))
        self.assertEqual(services.held(self.courier), D("0.00"))
        self.assertEqual(get_cashbox_balance(self.cash), D("1470.00"))
        Projector().rebuild()
        self.assertEqual(bal("1120"), D("0"))
        self.assertEqual(bal("5205"), D("30.00"))  # the fee is a transport expense
        self.assertEqual(bal("1199"), D("0"))
        self.assertTrue(reports.trial_balance()["balanced"])
        self.assertTrue(reports.reconciliation()["ok"])
        with self.assertRaisesMessage(ValidationError, "اتسلّمت ولسه ماتسوّتش"):
            services.settle([shipment], self.cash, self.owner)

    def test_a_lost_parcel_is_written_off_and_one_that_came_back_still_owes(self):
        lost = services.ship(self.invoice(), self.courier, self.owner)
        with self.assertRaisesMessage(ValidationError, "اكتب السبب"):
            services.lose(lost, self.owner, "")
        services.lose(lost, self.owner, "المندوب ضيّعها")
        self.assertEqual(balance("customer", self.customer), D("0.00"))
        self.assertEqual(services.held(self.courier), D("0.00"))
        Projector().rebuild()
        self.assertEqual(bal("5205"), D("500.00"))  # the loss shows in the expenses
        back = services.ship(self.invoice("SH-2"), self.courier, self.owner)
        services.came_back(back, self.owner, "العميل رفض")
        self.assertEqual(Shipment.objects.get(pk=back.pk).status, ShipmentStatus.RETURNED)
        self.assertEqual(balance("customer", self.customer), D("500.00"))  # still owed until the return is made
        self.assertTrue(reports.trial_balance()["balanced"])

    def test_the_rules_around_a_shipment(self):
        invoice = self.invoice()
        with self.assertRaisesMessage(ValidationError, "أكتر من الباقي"):
            services.ship(invoice, self.courier, self.owner, {"cod_amount": "600"})
        shipment = services.ship(invoice, self.courier, self.owner, {"cod_amount": "200", "fee": "25"})
        with self.assertRaisesMessage(ValidationError, "شحنة لسه مفتوحة"):
            services.ship(invoice, self.courier, self.owner)
        shipment = services.deliver(shipment, self.owner)
        with self.assertRaisesMessage(ValidationError, "undo the delivery"):
            cancel_customer_payment(shipment.payment_id, self.owner, "غلط")
        with self.assertRaisesMessage(ValidationError, "مصاريف الشحن أكتر"):
            services.settle([shipment], self.cash, self.owner, {"fees": "250"})
        services.undo_delivery(shipment, self.owner, "اتسجلت بالغلط")
        shipment.refresh_from_db()
        self.assertEqual((shipment.status, shipment.payment), (ShipmentStatus.WITH_COURIER, None))
        self.assertEqual(services.held(self.courier), D("0.00"))
        self.assertEqual(balance("customer", self.customer), D("500.00"))
        self.assertEqual(self.courier.cashbox.kind, CashboxKind.COURIER)

    def test_cashier_cannot_add_couriers_or_settle(self):
        from django.core.exceptions import PermissionDenied

        cashier = person(RoleCode.CASHIER, "sh_cashier")
        with self.assertRaises(PermissionDenied):
            services.save_courier({"name": "x"}, cashier)


class ScreenTests(ShippingSetup):
    def test_the_board_and_the_invoice_link(self):
        invoice = self.invoice()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("sales:detail", args=[invoice.pk]) + "?lang=ar"), "data-ship-invoice")
        for lang in ("ar", "en"):
            self.assertContains(self.client.get(reverse("shipping:board") + f"?lang={lang}&invoice={invoice.pk}"), "data-ship-form")
        self.client.post(reverse("shipping:board"), {"lang": "ar", "action": "ship", "invoice": invoice.pk, "courier": self.courier.pk, "tracking_number": "BO-1"})
        shipment = Shipment.objects.get()
        self.assertContains(self.client.get(reverse("shipping:board") + "?lang=ar"), f'data-shipment="{shipment.pk}"')
        self.client.post(reverse("shipping:board"), {"lang": "ar", "action": "deliver", "shipment": shipment.pk})
        self.assertContains(self.client.get(reverse("shipping:board") + "?lang=ar"), f'data-delivered="{shipment.pk}"')
        self.client.post(reverse("shipping:board"), {"lang": "ar", "action": "settle", "shipments": [shipment.pk], "destination": self.cash.pk})
        self.assertEqual(Shipment.objects.get().status, ShipmentStatus.SETTLED)
        self.assertContains(self.client.get(reverse("shipping:board") + "?lang=ar"), "data-settlements")
