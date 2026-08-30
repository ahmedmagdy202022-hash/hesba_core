from datetime import date
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase
from django.urls import reverse

from cashboxes.models import CashboxDirection, CashboxMovement
from hesba_testing.factories import (
    add_purchase_line,
    add_sales_line,
    make_cashbox,
    make_cashbox_movement,
    make_customer,
    make_draft_purchase_invoice,
    make_draft_sales_invoice,
    make_item,
    make_location,
    make_supplier,
)
from inventory.models import StockMovement
from inventory.services import get_item_location_stock_quantity
from purchases.models import (
    PurchaseInvoiceStatus,
    SupplierLedgerEntry,
    SupplierLedgerEntryType,
    SupplierPayment,
)
from sales.models import (
    CustomerLedgerEntry,
    CustomerLedgerEntryType,
    CustomerPayment,
    SalesInvoiceStatus,
)


class ProtectedAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin_user = get_user_model().objects.create_superuser(
            username="protected_admin_tester",
            email="",
            password="admin-tests-only",
        )

    def setUp(self):
        self.client.force_login(self.admin_user)
        self.request = RequestFactory().get("/admin/")
        self.request.user = self.admin_user

    def test_used_party_opening_balances_cannot_be_changed_through_admin(self):
        customer = make_customer(
            customer_code="ADMIN-CUSTOMER",
            name="Customer before",
            opening_balance=Decimal("25.00"),
        )
        supplier = make_supplier(
            supplier_code="ADMIN-SUPPLIER",
            name="Supplier before",
            opening_balance=Decimal("35.00"),
        )
        CustomerLedgerEntry.objects.create(
            customer=customer,
            entry_date=date(2026, 1, 15),
            entry_type=CustomerLedgerEntryType.SALES_DUE,
            due_increase=Decimal("1.00"),
        )
        SupplierLedgerEntry.objects.create(
            supplier=supplier,
            entry_date=date(2026, 1, 15),
            entry_type=SupplierLedgerEntryType.PURCHASE_DUE,
            due_increase=Decimal("1.00"),
        )

        customer_response = self.client.post(
            reverse("admin:master_data_customer_change", args=[customer.pk]),
            {
                "customer_code": customer.customer_code,
                "name": "Customer after",
                "phone": "",
                "whatsapp": "",
                "email": "",
                "address": "",
                "opening_balance": "999.00",
                "credit_limit": "0.00",
                "notes": "",
                "active": "on",
                "import_batch_id": "",
            },
        )
        supplier_response = self.client.post(
            reverse("admin:master_data_supplier_change", args=[supplier.pk]),
            {
                "supplier_code": supplier.supplier_code,
                "name": "Supplier after",
                "phone": "",
                "whatsapp": "",
                "email": "",
                "address": "",
                "opening_balance": "999.00",
                "notes": "",
                "active": "on",
                "import_batch_id": "",
            },
        )

        self.assertEqual(customer_response.status_code, 302)
        self.assertEqual(supplier_response.status_code, 302)
        customer.refresh_from_db()
        supplier.refresh_from_db()
        self.assertEqual(customer.name, "Customer after")
        self.assertEqual(supplier.name, "Supplier after")
        self.assertEqual(customer.opening_balance, Decimal("25.00"))
        self.assertEqual(supplier.opening_balance, Decimal("35.00"))

    def test_used_cashbox_opening_balance_and_currency_are_frozen_in_admin(self):
        cashbox = make_cashbox(
            cashbox_code="ADMIN-CASHBOX",
            name_ar="قبل",
            opening_balance=Decimal("50.00"),
            currency="EGP",
        )
        make_cashbox_movement(cashbox, CashboxDirection.IN, "1.00")

        response = self.client.post(
            reverse("admin:cashboxes_cashbox_change", args=[cashbox.pk]),
            {
                "cashbox_code": cashbox.cashbox_code,
                "name_ar": "بعد",
                "name_en": "After",
                "opening_balance": "999.00",
                "currency": "USD",
                "notes": "",
                "active": "on",
                "import_batch_id": "",
            },
        )

        self.assertEqual(response.status_code, 302)
        cashbox.refresh_from_db()
        self.assertEqual(cashbox.name_ar, "بعد")
        self.assertEqual(cashbox.opening_balance, Decimal("50.00"))
        self.assertEqual(cashbox.currency, "EGP")

    def test_movement_and_ledger_admins_are_strictly_view_only(self):
        item = make_item(item_code="ADMIN-STOCK")
        location = make_location(location_code="ADMIN-LOCATION")
        cashbox = make_cashbox(cashbox_code="ADMIN-MOVEMENT-CASHBOX")
        customer = make_customer(customer_code="ADMIN-LEDGER-CUSTOMER")
        supplier = make_supplier(supplier_code="ADMIN-LEDGER-SUPPLIER")
        stock_movement = item.stock_movements.create(
            movement_date=date(2026, 1, 15),
            movement_type="opening_stock",
            location=location,
            quantity=Decimal("1.000"),
            unit_cost=Decimal("1.0000"),
        )
        cash_movement = make_cashbox_movement(cashbox, CashboxDirection.IN, "1.00")
        supplier_entry = SupplierLedgerEntry.objects.create(
            supplier=supplier,
            entry_date=date(2026, 1, 15),
            entry_type=SupplierLedgerEntryType.OPENING_BALANCE,
            due_increase=Decimal("1.00"),
        )
        customer_entry = CustomerLedgerEntry.objects.create(
            customer=customer,
            entry_date=date(2026, 1, 15),
            entry_type=CustomerLedgerEntryType.OPENING_BALANCE,
            due_increase=Decimal("1.00"),
        )

        for obj in (stock_movement, cash_movement, supplier_entry, customer_entry):
            model_admin = admin.site._registry[type(obj)]
            with self.subTest(model=obj._meta.label):
                self.assertFalse(model_admin.has_add_permission(self.request))
                self.assertFalse(model_admin.has_change_permission(self.request, obj))
                self.assertFalse(model_admin.has_delete_permission(self.request, obj))
                self.assertNotIn("delete_selected", model_admin.get_actions(self.request))
                change_url = reverse(
                    f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                    args=[obj.pk],
                )
                self.assertEqual(self.client.get(change_url).status_code, 200)
                self.assertEqual(self.client.post(change_url, {}).status_code, 403)

        self.assertEqual(get_item_location_stock_quantity(item, location), Decimal("1.000"))
        self.assertEqual(CashboxMovement.objects.get(pk=cash_movement.pk).amount, Decimal("1.00"))

    def test_posted_and_cancelled_transactions_cannot_be_mutated_in_admin(self):
        item = make_item(item_code="ADMIN-TRANSACTION-ITEM")
        location = make_location(location_code="ADMIN-TRANSACTION-LOCATION")
        cashbox = make_cashbox(cashbox_code="ADMIN-TRANSACTION-CASHBOX")
        customer = make_customer(customer_code="ADMIN-TRANSACTION-CUSTOMER")
        supplier = make_supplier(supplier_code="ADMIN-TRANSACTION-SUPPLIER")

        purchase_objects = []
        sales_objects = []
        for suffix, status in (("POSTED", PurchaseInvoiceStatus.POSTED), ("CANCELLED", PurchaseInvoiceStatus.CANCELLED)):
            invoice = make_draft_purchase_invoice(
                supplier=supplier,
                location=location,
                cashbox=cashbox,
                invoice_number=f"ADMIN-PI-{suffix}",
            )
            line = add_purchase_line(invoice, item, 1, "1.00")
            invoice.status = status
            invoice.save(update_fields=["status"])
            purchase_objects.extend((invoice, line))

        for suffix, status in (("POSTED", SalesInvoiceStatus.POSTED), ("CANCELLED", SalesInvoiceStatus.CANCELLED)):
            invoice = make_draft_sales_invoice(
                customer=customer,
                location=location,
                cashbox=cashbox,
                invoice_number=f"ADMIN-SI-{suffix}",
            )
            line = add_sales_line(invoice, item, 1, "1.00")
            invoice.status = status
            invoice.save(update_fields=["status"])
            sales_objects.extend((invoice, line))

        supplier_payment = SupplierPayment.objects.create(
            payment_number="ADMIN-SP-POSTED",
            payment_date=date(2026, 1, 15),
            supplier=supplier,
            cashbox=cashbox,
            amount=Decimal("1.00"),
        )
        customer_payment = CustomerPayment.objects.create(
            payment_number="ADMIN-CP-CANCELLED",
            payment_date=date(2026, 1, 15),
            customer=customer,
            cashbox=cashbox,
            amount=Decimal("1.00"),
            status="cancelled",
        )

        for obj in (*purchase_objects, *sales_objects, supplier_payment, customer_payment):
            model_admin = admin.site._registry[type(obj)]
            with self.subTest(model=obj._meta.label, pk=obj.pk):
                self.assertFalse(model_admin.has_change_permission(self.request, obj))
                self.assertFalse(model_admin.has_delete_permission(self.request, obj))
                self.assertNotIn("delete_selected", model_admin.get_actions(self.request))
                change_url = reverse(
                    f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                    args=[obj.pk],
                )
                self.assertEqual(self.client.get(change_url).status_code, 200)
                self.assertEqual(self.client.post(change_url, {}).status_code, 403)

    def test_draft_invoices_and_lines_are_view_only_in_admin(self):
        item = make_item(item_code="ADMIN-DRAFT-ITEM")
        location = make_location(location_code="ADMIN-DRAFT-LOCATION")
        cashbox = make_cashbox(cashbox_code="ADMIN-DRAFT-CASHBOX")
        purchase_invoice = make_draft_purchase_invoice(
            supplier=make_supplier(supplier_code="ADMIN-DRAFT-SUPPLIER"),
            location=location,
            cashbox=cashbox,
            invoice_number="ADMIN-DRAFT-PI",
        )
        purchase_line = add_purchase_line(purchase_invoice, item, 2, "3.00")
        sales_invoice = make_draft_sales_invoice(
            customer=make_customer(customer_code="ADMIN-DRAFT-CUSTOMER"),
            location=location,
            cashbox=cashbox,
            invoice_number="ADMIN-DRAFT-SI",
        )
        sales_line = add_sales_line(sales_invoice, item, 2, "4.00")

        for obj in (purchase_invoice, purchase_line, sales_invoice, sales_line):
            model_admin = admin.site._registry[type(obj)]
            add_url = reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_add"
            )
            change_url = reverse(
                f"admin:{obj._meta.app_label}_{obj._meta.model_name}_change",
                args=[obj.pk],
            )
            with self.subTest(model=obj._meta.label):
                self.assertFalse(model_admin.has_add_permission(self.request))
                self.assertFalse(model_admin.has_change_permission(self.request, obj))
                self.assertFalse(model_admin.has_delete_permission(self.request, obj))
                self.assertNotIn("delete_selected", model_admin.get_actions(self.request))
                self.assertEqual(self.client.get(add_url).status_code, 403)
                self.assertEqual(self.client.post(add_url, {}).status_code, 403)
                self.assertEqual(self.client.get(change_url).status_code, 200)
                self.assertEqual(self.client.post(change_url, {}).status_code, 403)

        for invoice in (purchase_invoice, sales_invoice):
            invoice_admin = admin.site._registry[type(invoice)]
            inline = invoice_admin.get_inline_instances(self.request, invoice)[0]
            with self.subTest(inline=type(inline).__name__):
                self.assertFalse(inline.has_add_permission(self.request, invoice))
                self.assertFalse(inline.has_change_permission(self.request, invoice))
                self.assertFalse(inline.has_delete_permission(self.request, invoice))

        purchase_invoice.refresh_from_db()
        sales_invoice.refresh_from_db()
        purchase_line.refresh_from_db()
        sales_line.refresh_from_db()
        self.assertEqual(purchase_invoice.total_amount, Decimal("6.00"))
        self.assertEqual(sales_invoice.total_amount, Decimal("8.00"))
        self.assertEqual(purchase_line.quantity, Decimal("2.000"))
        self.assertEqual(sales_line.quantity, Decimal("2.000"))
