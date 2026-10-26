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


class CodexFixesTests(ShippingSetup):
    """Codex on #272: each entity's parcels and couriers stay its own, a second shipment collects only what is left, a
    delivery is undone from the board, and fees above what was collected are paid in from the chosen box."""

    def branch(self):
        from entities.models import Entity
        from entities.services import main_entity

        main_entity()
        branch = Entity.objects.create(code="BR-SH", name_ar="الفرع")
        shop = make_location(location_code="SHOP-BR", is_selling_location=True, entity=branch)
        stock_in(self.dress, shop, 5, "200.00", movement_date=TODAY)
        return branch, shop

    def test_a_branch_invoice_ships_with_the_branchs_courier_and_its_collection_stays_there(self):
        from entities.current import working_in

        branch, shop = self.branch()
        invoice = create_sales_draft({"invoice_number": "SH-BR", "invoice_date": TODAY, "customer": self.customer, "selling_location": shop,
                                      "cashbox": self.cash, "paid_now": D("0"), "discount_amount": D("0")},
                                     [{"item": self.dress, "quantity": D("1"), "unit_sale_price": D("500.00"), "line_discount_amount": D("0")}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        with self.assertRaisesMessage(ValidationError, "لكيان تاني"):
            services.ship(invoice, self.courier, self.owner)
        with working_in(branch):
            local = services.save_courier({"name": "بوسطة الفرع", "fee_per_parcel": "20"}, self.owner)
        self.assertEqual(local.cashbox.entity, branch)
        shipment = services.deliver(services.ship(invoice, local, self.owner), self.owner)
        self.assertEqual(shipment.payment.cashbox.entity, branch)  # the customer's payment is the branch's, like its debt
        with self.assertRaisesMessage(ValidationError, "لكيان تاني"):
            services.settle([shipment], self.cash, self.owner)  # the main entity's cashbox

    def test_someone_kept_to_a_branch_never_sees_or_moves_the_main_entitys_parcels(self):
        from entities.models import EntityMembership

        branch, _shop = self.branch()
        main_parcel = services.ship(self.invoice(), self.courier, self.owner)
        clerk = person(RoleCode.MANAGER, "sh_branch_clerk")
        EntityMembership.objects.create(user=clerk, entity=branch, is_default=True)
        self.client.force_login(clerk)
        self.assertNotContains(self.client.get(reverse("shipping:board") + "?lang=ar"), f'data-shipment="{main_parcel.pk}"')
        self.assertEqual(self.client.post(reverse("shipping:board"), {"lang": "ar", "action": "deliver", "shipment": main_parcel.pk}).status_code, 404)
        self.assertEqual(Shipment.objects.get(pk=main_parcel.pk).status, ShipmentStatus.WITH_COURIER)

    def test_a_second_shipment_collects_only_what_is_left(self):
        invoice = self.invoice()
        services.deliver(services.ship(invoice, self.courier, self.owner), self.owner)
        with self.assertRaisesMessage(ValidationError, "أكتر من الباقي"):
            services.ship(invoice, self.courier, self.owner, {"cod_amount": "500"})
        again = services.ship(invoice, self.courier, self.owner)  # a replacement parcel, nothing more to collect
        self.assertEqual(again.cod_amount, D("0.00"))
        services.deliver(again, self.owner)
        self.assertEqual(balance("customer", self.customer), D("0.00"))  # not a spurious credit

    def test_a_delivery_is_undone_from_the_board(self):
        shipment = services.deliver(services.ship(self.invoice(), self.courier, self.owner), self.owner)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("shipping:board") + "?lang=ar")
        self.assertContains(page, f'id="undo-{shipment.pk}"')
        self.assertContains(page, f'form="undo-{shipment.pk}"')
        self.client.post(reverse("shipping:board"), {"lang": "ar", "action": "undo", "shipment": shipment.pk, "reason": "اتسجلت بالغلط"})
        self.assertEqual(Shipment.objects.get(pk=shipment.pk).status, ShipmentStatus.WITH_COURIER)
        self.assertEqual(balance("customer", self.customer), D("500.00"))

    def test_fees_above_what_was_collected_are_paid_from_the_chosen_box(self):
        shipment = services.deliver(services.ship(self.invoice(), self.courier, self.owner, {"cod_amount": "0", "fee": "30"}), self.owner)
        settlement = services.settle([shipment], self.cash, self.owner)
        self.assertEqual((settlement.collected, settlement.fees, settlement.net), (D("0.00"), D("30.00"), D("-30.00")))
        self.assertEqual(get_cashbox_balance(self.cash), D("970.00"))
        self.assertEqual(services.held(self.courier), D("0.00"))
        self.assertEqual(settlement.fee_expense.amount, D("30.00"))
        Projector().rebuild()
        self.assertTrue(reports.trial_balance()["balanced"])
        self.assertEqual(bal("1120"), D("0"))


class SecondReviewTests(ShippingSetup):
    """Codex on #272 (second look): a courier's box is in the company's currency, and only who sees money sees what it holds."""

    def test_a_new_courier_box_is_in_the_company_currency(self):
        from settings_core.models import ClientProfile

        ClientProfile.objects.filter(pk=ClientProfile.get_active().pk).update(default_currency="SAR")
        self.assertEqual(services.save_courier({"name": "سمسا"}, self.owner).cashbox.currency, "SAR")

    def test_a_cashier_handles_parcels_without_seeing_courier_money(self):
        shipment = services.deliver(services.ship(self.invoice(), self.courier, self.owner), self.owner)
        services.settle([shipment], self.cash, self.owner)
        cashier = person(RoleCode.CASHIER, "sh_cashier_view")
        self.client.force_login(cashier)
        page = self.client.get(reverse("shipping:board") + "?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-held")
        self.assertNotContains(page, "data-settlements")
        self.client.force_login(self.owner)
        page = self.client.get(reverse("shipping:board") + "?lang=ar")
        self.assertContains(page, "data-held")
        self.assertContains(page, "data-settlements")


class ThirdReviewTests(ShippingSetup):
    """Codex on #272 (third look): the reconciliation counts what couriers hold, and a posted return lowers what is collected."""

    def test_an_unsettled_collection_reconciles(self):
        services.deliver(services.ship(self.invoice(), self.courier, self.owner), self.owner)
        Projector().rebuild()
        result = reports.reconciliation()
        self.assertTrue(result["ok"], [row for row in result["rows"] if row["difference"]])

    def test_a_posted_return_is_not_collected_again(self):
        from sales.services import create_sales_return

        invoice = create_sales_draft({"invoice_number": "SH-RET", "invoice_date": TODAY, "customer": self.customer, "selling_location": self.location,
                                      "cashbox": self.cash, "paid_now": D("0"), "discount_amount": D("0")},
                                     [{"item": self.dress, "quantity": D("2"), "unit_sale_price": D("250.00"), "line_discount_amount": D("0")}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        create_sales_return(return_number="SR-SH", return_date=TODAY, source_invoice_id=invoice.pk,
                            lines=[{"source_line": invoice.lines.get(), "quantity": 1}], reason="رجّع واحد", user=self.owner)
        shipment = services.ship(invoice, self.courier, self.owner)
        self.assertEqual(shipment.cod_amount, D("250.00"))
        services.deliver(shipment, self.owner)
        self.assertEqual(balance("customer", self.customer), D("0.00"))


class FourthReviewTests(ShippingSetup):
    """Codex on #272 (fourth look): an invoice with a parcel out is not cancelled, and a cancelled one is never collected."""

    def test_a_parcel_out_blocks_cancelling_its_invoice(self):
        from sales.services import cancel_posted_sales_invoice

        invoice = self.invoice()
        shipment = services.ship(invoice, self.courier, self.owner)
        with self.assertRaisesMessage(ValidationError, "parcel with a courier"):
            cancel_posted_sales_invoice(invoice.pk, self.owner, "غلط")
        services.came_back(shipment, self.owner, "العميل رفض")
        cancel_posted_sales_invoice(invoice.pk, self.owner, "غلط")
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "cancelled")

    def test_a_cancelled_invoice_is_never_delivered_or_written_off(self):
        from sales.models import SalesInvoice

        invoice = self.invoice()
        shipment = services.ship(invoice, self.courier, self.owner)
        SalesInvoice.objects.filter(pk=invoice.pk).update(status="cancelled")  # as if cancelled before the guard existed
        with self.assertRaisesMessage(ValidationError, "مترحّلة"):
            services.deliver(shipment, self.owner)
        with self.assertRaisesMessage(ValidationError, "مترحّلة"):
            services.lose(shipment, self.owner, "ضاعت")
        self.assertEqual(services.held(self.courier), D("0.00"))


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
