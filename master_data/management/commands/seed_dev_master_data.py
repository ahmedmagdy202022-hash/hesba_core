from decimal import Decimal

from django.core.management.base import BaseCommand

from cashboxes.models import Cashbox
from master_data.models import Category, Customer, Item, Location, Supplier


class Command(BaseCommand):
    help = "Create a minimal idempotent dev seed for first admin smoke tests."

    def handle(self, *args, **options):
        category, _ = Category.objects.update_or_create(
            category_code="DEV-CAT",
            defaults={
                "name_ar": "تصنيف تجريبي",
                "name_en": "Dev Category",
                "active": True,
            },
        )

        location, _ = Location.objects.update_or_create(
            location_code="MAIN",
            defaults={
                "name_ar": "المخزن الرئيسي",
                "name_en": "Main Location",
                "description": "Default dev receiving and selling location.",
                "is_default": True,
                "is_receiving_location": True,
                "is_selling_location": True,
                "active": True,
            },
        )

        item, _ = Item.objects.update_or_create(
            item_code="ITEM-001",
            defaults={
                "barcode": "DEV-ITEM-001",
                "item_name": "صنف تجريبي",
                "category": category,
                "size": "",
                "color": "",
                "unit": "unit",
                "default_sale_price": Decimal("120.00"),
                "default_purchase_price": Decimal("100.00"),
                "average_cost": Decimal("100.0000"),
                "min_stock": Decimal("1.000"),
                "is_stock_tracked": True,
                "active": True,
                "import_batch_id": "DEV-SEED",
            },
        )

        customer, _ = Customer.objects.update_or_create(
            customer_code="CUST-001",
            defaults={
                "name": "عميل تجريبي",
                "phone": "01000000000",
                "whatsapp": "01000000000",
                "opening_balance": Decimal("0.00"),
                "credit_limit": Decimal("0.00"),
                "active": True,
                "import_batch_id": "DEV-SEED",
            },
        )

        supplier, _ = Supplier.objects.update_or_create(
            supplier_code="SUP-001",
            defaults={
                "name": "مورد تجريبي",
                "phone": "01000000001",
                "whatsapp": "01000000001",
                "opening_balance": Decimal("0.00"),
                "active": True,
                "import_batch_id": "DEV-SEED",
            },
        )

        cashbox, _ = Cashbox.objects.update_or_create(
            cashbox_code="CASH-001",
            defaults={
                "name_ar": "خزنة تجريبية",
                "name_en": "Dev Cashbox",
                "opening_balance": Decimal("1000.00"),
                "currency": "EGP",
                "is_default": True,
                "active": True,
                "notes": "Created for local admin smoke tests.",
                "import_batch_id": "DEV-SEED",
            },
        )

        self.stdout.write(self.style.SUCCESS("Dev master data seed is ready."))
        self.stdout.write(f"Category: {category}")
        self.stdout.write(f"Location: {location}")
        self.stdout.write(f"Item: {item}")
        self.stdout.write(f"Customer: {customer}")
        self.stdout.write(f"Supplier: {supplier}")
        self.stdout.write(f"Cashbox: {cashbox}")
