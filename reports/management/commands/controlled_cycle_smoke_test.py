from decimal import Decimal
from datetime import date

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from cashboxes.models import Cashbox, CashboxMovement
from inventory.models import StockMovement
from inventory.services import get_item_location_stock_quantity
from master_data.models import Customer, Item, Location, Supplier
from purchases.models import PurchaseInvoice, PurchaseLine, PurchasePaymentStatus, SupplierLedgerEntry
from purchases.services import post_purchase_invoice
from sales.models import CustomerLedgerEntry, SalesInvoice, SalesLine, SalesPaymentStatus
from sales.services import post_sales_invoice


class Command(BaseCommand):
    help = "Run the controlled dev business cycle smoke test."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=None,
            help="Actor for the posted invoices. Defaults to the first superuser.",
        )

    def _resolve_user(self, username):
        users = get_user_model().objects
        if username:
            try:
                return users.get(username=username)
            except users.model.DoesNotExist:
                raise CommandError(f"No user named {username!r}. Run bootstrap_client first.")

        user = users.filter(is_superuser=True).order_by("pk").first()
        if user is None:
            raise CommandError("No superuser exists. Run bootstrap_client first, or pass --username.")
        return user

    def handle(self, *args, **options):
        user = self._resolve_user(options["username"])
        supplier = Supplier.objects.get(supplier_code="SUP-001")
        customer = Customer.objects.get(customer_code="CUST-001")
        item = Item.objects.get(item_code="ITEM-001")
        location = Location.objects.get(location_code="MAIN")
        cashbox = Cashbox.objects.get(cashbox_code="CASH-001")

        purchase_number = "DEV-PI-TEST-001"
        sales_number = "DEV-SI-TEST-001"

        if PurchaseInvoice.objects.filter(invoice_number=purchase_number).exists() or SalesInvoice.objects.filter(
            invoice_number=sales_number
        ).exists():
            self.stdout.write("TEST_ALREADY_EXISTS")
        else:
            purchase_invoice = PurchaseInvoice.objects.create(
                invoice_number=purchase_number,
                invoice_date=date.today(),
                supplier=supplier,
                receiving_location=location,
                cashbox=cashbox,
                subtotal=Decimal("1000.00"),
                discount_amount=Decimal("0.00"),
                tax_amount=Decimal("0.00"),
                total_amount=Decimal("1000.00"),
                paid_now=Decimal("400.00"),
                remaining_due=Decimal("600.00"),
                payment_status=PurchasePaymentStatus.PARTIAL,
                created_by=user,
                notes="Controlled dev purchase test",
            )
            PurchaseLine.objects.create(
                invoice=purchase_invoice,
                line_number=1,
                item=item,
                quantity=Decimal("10.000"),
                unit_purchase_price=Decimal("100.00"),
                line_discount_amount=Decimal("0.00"),
                line_total_amount=Decimal("1000.00"),
            )
            post_purchase_invoice(purchase_invoice.id, user=user)

            sales_invoice = SalesInvoice.objects.create(
                invoice_number=sales_number,
                invoice_date=date.today(),
                customer=customer,
                selling_location=location,
                cashbox=cashbox,
                subtotal=Decimal("360.00"),
                discount_amount=Decimal("0.00"),
                tax_amount=Decimal("0.00"),
                total_amount=Decimal("360.00"),
                paid_now=Decimal("100.00"),
                remaining_due=Decimal("260.00"),
                payment_status=SalesPaymentStatus.PARTIAL,
                created_by=user,
                notes="Controlled dev sales test",
            )
            SalesLine.objects.create(
                invoice=sales_invoice,
                line_number=1,
                item=item,
                quantity=Decimal("3.000"),
                unit_sale_price=Decimal("120.00"),
                line_discount_amount=Decimal("0.00"),
                line_total_amount=Decimal("360.00"),
            )
            post_sales_invoice(sales_invoice.id, user=user)

            self.stdout.write("CONTROLLED_CYCLE_OK")

        self.stdout.write(f"Stock now: {get_item_location_stock_quantity(item, location)}")
        self.stdout.write(f"Supplier ledger entries: {SupplierLedgerEntry.objects.count()}")
        self.stdout.write(f"Customer ledger entries: {CustomerLedgerEntry.objects.count()}")
        self.stdout.write(f"Cashbox movements: {CashboxMovement.objects.count()}")
        self.stdout.write(f"Stock movements: {StockMovement.objects.count()}")
