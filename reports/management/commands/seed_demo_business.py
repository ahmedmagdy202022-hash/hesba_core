"""Fill a local database with enough trade to make the dashboard worth looking at.

The dashboard reads real figures now, so an empty database shows an empty
dashboard. Rather than keeping fake numbers in the view, this builds real
business: suppliers stocked, items sold, customers part-paid, cash moved. Every
row goes through the posting services, so what the screen shows is genuinely
derived from movements and ledger entries — the same path a real day takes.

It also plants three deliberate problems so the alerts and the health score have
something true to report: an item sold out, an item under its minimum, and a
customer past their credit limit.
"""

from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from cashboxes.models import Cashbox
from master_data.models import Category, Customer, Item, Location, Supplier
from purchases.models import PurchaseInvoice, PurchaseLine, PurchasePaymentStatus
from purchases.services import post_purchase_invoice, record_supplier_payment
from sales.models import SalesInvoice, SalesLine, SalesPaymentStatus
from sales.services import post_sales_invoice, record_customer_payment


D = Decimal

# item_code, name, sale price, purchase price, min_stock
ITEMS = (
    ("DEMO-ITEM-01", "قميص قطن", D("240.00"), D("150.00"), D("10")),
    ("DEMO-ITEM-02", "بنطلون جينز", D("380.00"), D("250.00"), D("8")),
    ("DEMO-ITEM-03", "حزام جلد", D("120.00"), D("70.00"), D("15")),
    ("DEMO-ITEM-04", "حقيبة ظهر", D("450.00"), D("300.00"), D("5")),
    ("DEMO-ITEM-05", "جراب موبايل", D("90.00"), D("45.00"), D("20")),
)

# code, name, phone, credit limit
CUSTOMERS = (
    ("DEMO-CUST-01", "أحمد عبد الله", "01000000101", D("5000.00")),
    ("DEMO-CUST-02", "سارة محمود", "01000000102", D("3000.00")),
    ("DEMO-CUST-03", "محمد إبراهيم", "01000000103", D("1500.00")),
    ("DEMO-CUST-04", "شركة النور للتجارة", "01000000104", D("20000.00")),
)

SUPPLIERS = (
    ("DEMO-SUP-01", "مصنع الدلتا للملابس", "01000000201"),
    ("DEMO-SUP-02", "موردون متحدون", "01000000202"),
)

CASHBOXES = (
    ("DEMO-CASH-01", "الخزنة الرئيسية", "Main cashbox", D("8000.00"), True),
    ("DEMO-CASH-02", "خزنة الفرع", "Branch cashbox", D("300.00"), False),
)


class Command(BaseCommand):
    help = (
        "Create a realistic demo business — items, parties, stocked purchases, "
        "sales and payments — so the dashboard shows live figures locally."
    )

    def add_arguments(self, parser):
        parser.add_argument("--username", default=None, help="Actor for the postings. Defaults to the first superuser.")
        parser.add_argument(
            "--cashier-username",
            default="cashier",
            help="Owner of the self-scoped sales, so a cashier's dashboard is not empty.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Allow seeding with DEBUG off. Never use this on a client's real database.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "Refusing to write demo transactions while DEBUG is off. This seed "
                "posts invoices and moves cashboxes, which must never happen on a "
                "client's real database. Pass --force only if this one is disposable."
            )

        self.verbosity = options["verbosity"]
        actor = self._resolve_user(options["username"])
        cashier = self._optional_user(options["cashier_username"])

        if SalesInvoice.objects.filter(invoice_number__startswith="DEMO-SI-").exists():
            # Posted invoices are protected on purpose — every transaction has to
            # stay traceable, so nothing here deletes one. Start over by removing
            # the database file and running migrate again.
            self._say("Demo business already seeded. Delete the database file to start over.")
            self._summarise()
            return

        today = timezone.localdate()
        master = self._seed_master_data()
        self._seed_purchases(master, actor, today)
        self._seed_sales(master, actor, cashier, today)
        self._seed_payments(master, actor, today)

        self._say(self.style.SUCCESS("Demo business ready."))
        self._summarise()

    # ---- helpers ----

    def _say(self, message):
        if self.verbosity:
            self.stdout.write(message)

    def _resolve_user(self, username):
        users = get_user_model().objects
        if username:
            user = users.filter(username=username).first()
            if user is None:
                raise CommandError(f"No user named {username!r}. Run bootstrap_client first.")
            return user

        user = users.filter(is_superuser=True).order_by("pk").first()
        if user is None:
            raise CommandError("No superuser exists. Run bootstrap_client first, or pass --username.")
        return user

    def _optional_user(self, username):
        return get_user_model().objects.filter(username=username).first()

    # ---- master data ----

    def _seed_master_data(self):
        category, _ = Category.objects.update_or_create(
            category_code="DEMO-CAT-01",
            defaults={"name_ar": "ملابس وأحذية", "name_en": "Clothing", "active": True},
        )
        location, _ = Location.objects.update_or_create(
            location_code="DEMO-LOC-01",
            defaults={
                "name_ar": "المخزن الرئيسي",
                "name_en": "Main store",
                "is_default": True,
                "is_receiving_location": True,
                "is_selling_location": True,
                "active": True,
            },
        )

        items = []
        for code, name, sale, purchase, min_stock in ITEMS:
            item, _ = Item.objects.update_or_create(
                item_code=code,
                defaults={
                    "item_name": name,
                    "category": category,
                    "unit": "قطعة",
                    "default_sale_price": sale,
                    "default_purchase_price": purchase,
                    "min_stock": min_stock,
                    "is_stock_tracked": True,
                    "active": True,
                    "import_batch_id": "DEMO-BUSINESS",
                },
            )
            items.append(item)

        customers = []
        for code, name, phone, limit in CUSTOMERS:
            customer, _ = Customer.objects.update_or_create(
                customer_code=code,
                defaults={
                    "name": name,
                    "phone": phone,
                    "whatsapp": phone,
                    "credit_limit": limit,
                    "active": True,
                    "import_batch_id": "DEMO-BUSINESS",
                },
            )
            customers.append(customer)

        suppliers = []
        for code, name, phone in SUPPLIERS:
            supplier, _ = Supplier.objects.update_or_create(
                supplier_code=code,
                defaults={"name": name, "phone": phone, "active": True, "import_batch_id": "DEMO-BUSINESS"},
            )
            suppliers.append(supplier)

        cashboxes = []
        for code, name_ar, name_en, opening, is_default in CASHBOXES:
            cashbox, _ = Cashbox.objects.update_or_create(
                cashbox_code=code,
                defaults={
                    "name_ar": name_ar,
                    "name_en": name_en,
                    "opening_balance": opening,
                    "currency": "EGP",
                    "is_default": is_default,
                    "active": True,
                    "import_batch_id": "DEMO-BUSINESS",
                },
            )
            cashboxes.append(cashbox)

        return {
            "location": location,
            "items": items,
            "customers": customers,
            "suppliers": suppliers,
            "cashboxes": cashboxes,
        }

    # ---- purchases ----

    def _seed_purchases(self, master, actor, today):
        """Stock the shelves, deliberately unevenly.

        Item 5 is bought in a quantity that later sells out completely, and item
        4 lands just above its minimum, so the shortage alerts have real causes.
        """

        plan = (
            ("DEMO-PI-001", today - timedelta(days=20), 0, ((0, 40), (1, 25), (2, 60)), D("6000.00")),
            ("DEMO-PI-002", today - timedelta(days=12), 1, ((3, 6), (4, 12)), D("0.00")),
            ("DEMO-PI-003", today, 0, ((0, 10), (2, 20)), D("1500.00")),
        )

        for number, invoice_date, supplier_index, lines, paid_now in plan:
            self._post_purchase(
                number=number,
                invoice_date=invoice_date,
                supplier=master["suppliers"][supplier_index],
                location=master["location"],
                cashbox=master["cashboxes"][0],
                lines=[(master["items"][i], D(qty)) for i, qty in lines],
                paid_now=paid_now,
                actor=actor,
            )

    def _post_purchase(self, number, invoice_date, supplier, location, cashbox, lines, paid_now, actor):
        subtotal = sum((item.default_purchase_price * qty for item, qty in lines), D("0.00"))
        invoice = PurchaseInvoice.objects.create(
            invoice_number=number,
            invoice_date=invoice_date,
            supplier=supplier,
            receiving_location=location,
            cashbox=cashbox,
            subtotal=subtotal,
            discount_amount=D("0.00"),
            tax_amount=D("0.00"),
            total_amount=subtotal,
            paid_now=paid_now,
            remaining_due=subtotal - paid_now,
            payment_status=self._purchase_payment_status(subtotal, paid_now),
            created_by=actor,
            notes="Demo business seed",
        )
        for number_in_invoice, (item, qty) in enumerate(lines, start=1):
            line_total = item.default_purchase_price * qty
            PurchaseLine.objects.create(
                invoice=invoice,
                line_number=number_in_invoice,
                item=item,
                quantity=qty,
                unit_purchase_price=item.default_purchase_price,
                line_discount_amount=D("0.00"),
                line_total_amount=line_total,
            )
        post_purchase_invoice(invoice.id, user=actor)
        return invoice

    def _purchase_payment_status(self, total, paid):
        if paid >= total:
            return PurchasePaymentStatus.PAID
        if paid > 0:
            return PurchasePaymentStatus.PARTIAL
        return PurchasePaymentStatus.CREDIT

    # ---- sales ----

    def _seed_sales(self, master, actor, cashier, today):
        """Sell across several days, and put some of today's sales on the cashier.

        A cashier's dashboard is scoped to their own invoices, so without an
        invoice they created their cards would read zero and look broken.
        """

        plan = (
            ("DEMO-SI-001", today - timedelta(days=15), 0, ((0, 4), (2, 6)), D("1000.00"), actor),
            ("DEMO-SI-002", today - timedelta(days=8), 3, ((1, 5), (0, 3)), D("500.00"), actor),
            # Customer 3 has a 1,500 limit; this leaves them well past it so the
            # over-limit alert has a real subject.
            ("DEMO-SI-003", today - timedelta(days=4), 2, ((3, 2), (1, 2)), D("0.00"), actor),
            ("DEMO-SI-004", today, 1, ((0, 3), (4, 12)), D("900.00"), cashier or actor),
            ("DEMO-SI-005", today, 0, ((2, 8),), D("960.00"), actor),
        )

        for number, invoice_date, customer_index, lines, paid_now, creator in plan:
            self._post_sale(
                number=number,
                invoice_date=invoice_date,
                customer=master["customers"][customer_index],
                location=master["location"],
                cashbox=master["cashboxes"][0],
                lines=[(master["items"][i], D(qty)) for i, qty in lines],
                paid_now=paid_now,
                actor=creator,
            )

    def _post_sale(self, number, invoice_date, customer, location, cashbox, lines, paid_now, actor):
        subtotal = sum((item.default_sale_price * qty for item, qty in lines), D("0.00"))
        invoice = SalesInvoice.objects.create(
            invoice_number=number,
            invoice_date=invoice_date,
            customer=customer,
            selling_location=location,
            cashbox=cashbox,
            subtotal=subtotal,
            discount_amount=D("0.00"),
            tax_amount=D("0.00"),
            total_amount=subtotal,
            paid_now=paid_now,
            remaining_due=subtotal - paid_now,
            payment_status=self._sales_payment_status(subtotal, paid_now),
            created_by=actor,
            notes="Demo business seed",
        )
        for number_in_invoice, (item, qty) in enumerate(lines, start=1):
            line_total = item.default_sale_price * qty
            SalesLine.objects.create(
                invoice=invoice,
                line_number=number_in_invoice,
                item=item,
                quantity=qty,
                unit_sale_price=item.default_sale_price,
                line_discount_amount=D("0.00"),
                line_total_amount=line_total,
            )
        post_sales_invoice(invoice.id, user=actor)
        return invoice

    def _sales_payment_status(self, total, paid):
        if paid >= total:
            return SalesPaymentStatus.PAID
        if paid > 0:
            return SalesPaymentStatus.PARTIAL
        return SalesPaymentStatus.CREDIT

    # ---- payments ----

    def _seed_payments(self, master, actor, today):
        cashbox = master["cashboxes"][0]

        for number, customer, amount in (
            ("DEMO-CR-001", master["customers"][0], D("600.00")),
            ("DEMO-CR-002", master["customers"][3], D("1200.00")),
        ):
            record_customer_payment(
                payment_number=number,
                payment_date=today,
                customer=customer,
                cashbox=cashbox,
                amount=amount,
                user=actor,
                notes="Demo business seed",
            )

        record_supplier_payment(
            payment_number="DEMO-SP-001",
            payment_date=today,
            supplier=master["suppliers"][0],
            cashbox=cashbox,
            amount=D("2000.00"),
            user=actor,
            notes="Demo business seed",
        )

    # ---- summary ----

    def _summarise(self):
        if not self.verbosity:
            return

        from reports import selectors
        from reports.dashboard_data import HEALTH_INPUT_PERMISSIONS, health_score

        today = timezone.localdate()
        totals = selectors.profit_totals(date_from=today, date_to=today)
        stock = selectors.stock_alert_counts()
        cash = sum(row["balance"] for row in selectors.cashbox_report())
        customer_dues = sum(r["balance"] for r in selectors.customer_report() if r["balance"] > 0)
        supplier_dues = sum(r["balance"] for r in selectors.supplier_report() if r["balance"] > 0)

        self.stdout.write("")
        self.stdout.write("What the dashboard will show:")
        self.stdout.write(f"  Sales today        {totals['sales']:>12,.2f}")
        self.stdout.write(f"  Profit today       {totals['profit']:>12,.2f}")
        self.stdout.write(f"  Cashbox balance    {cash:>12,.2f}")
        self.stdout.write(f"  Customer dues      {customer_dues:>12,.2f}")
        self.stdout.write(f"  Supplier dues      {supplier_dues:>12,.2f}")
        self.stdout.write(f"  Out of stock       {stock['out_of_stock']:>12}")
        self.stdout.write(f"  Below minimum      {stock['low_stock']:>12}")
        # The score is permission-scoped on screen. Here it is shown in full,
        # because whoever ran this wants to know what the owner will see.
        owner_view = frozenset(HEALTH_INPUT_PERMISSIONS.values())
        self.stdout.write(f"  Health score       {health_score(today, owner_view)['score']:>12}")
