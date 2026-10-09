"""Fill a local database with enough trade to make the dashboard worth looking at.

The dashboard reads real figures now, so an empty database shows an empty
dashboard. Rather than keeping fake numbers in the view, this builds real
business: suppliers stocked, items sold, customers part-paid, cash moved. Every
row goes through the posting services, so what the screen shows is genuinely
derived from movements and ledger entries — the same path a real day takes.

It also plants three deliberate problems so the alerts have
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
from reports.demo_catalogs import BASE, active_catalog
from sales.models import SalesInvoice, SalesLine, SalesPaymentStatus
from sales.services import post_sales_invoice, record_customer_payment
from staff.services import reps_in_use


D = Decimal
ZERO = D("0.00")

#: R2-5: what the items, customers and suppliers are called, their units and
#: prices, come from the activity's catalog (reports.demo_catalogs). Their
#: codes and roles are the same in every business.
ITEM_CODES = tuple(f"DEMO-ITEM-{n:02d}" for n in range(1, 6))
MIN_STOCK = (D("10"), D("8"), D("15"), D("5"), D("20"))

# code, phone, credit limit (for the base catalog's prices)
CUSTOMERS = (
    ("DEMO-CUST-01", "01000000101", D("5000.00")),
    ("DEMO-CUST-02", "01000000102", D("3000.00")),
    ("DEMO-CUST-03", "01000000103", D("1500.00")),
    ("DEMO-CUST-04", "01000000104", D("20000.00")),
)

SUPPLIERS = (
    ("DEMO-SUP-01", "01000000201"),
    ("DEMO-SUP-02", "01000000202"),
)

#: The lines whose cost sets how big this business's purchases and cash are,
#: next to the base catalog's: the history's two purchases.
STOCKING = ((0, 40), (1, 25), (2, 60), (3, 6), (4, 12))
#: The over-limit customer's only invoice (see _seed_history).
OVER_LIMIT_SALE = ((3, 2), (1, 2))

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

        today = timezone.localdate()
        master = self._seed_master_data()

        # The history is written once. Posted invoices are protected on purpose —
        # every transaction has to stay traceable — so nothing here rewrites or
        # deletes one.
        first_run = not SalesInvoice.objects.filter(invoice_number__startswith="DEMO-SI-").exists()
        if first_run:
            self._seed_history(master, actor, today)

        # Today's trade is topped up on every run. Without this the demo goes
        # flat the day after seeding: the balances stay but every "today" card
        # reads zero, which is exactly the dead-looking dashboard this is meant
        # to avoid. Each day's batch buys back what it sells, so stock and the
        # shortage alerts hold steady however many days it runs.
        added_today = self._seed_today(master, actor, cashier, today)

        if first_run:
            self._say(self.style.SUCCESS("Demo business ready."))
        elif added_today:
            self._say(self.style.SUCCESS(f"Topped up today's trade ({today})."))
        else:
            self._say(f"Today ({today}) already has demo trade. Nothing to add.")
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

    def _seed_master_data(self, catalog=None):
        catalog = catalog or active_catalog()
        category, _ = Category.objects.update_or_create(
            category_code="DEMO-CAT-01",
            defaults={"name_ar": catalog.category[0], "name_en": catalog.category[1], "active": True},
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
        for index, (code, product) in enumerate(zip(ITEM_CODES, catalog.items)):
            values = {
                "item_name": product.name,
                "category": category,
                "unit": product.unit,
                "default_sale_price": product.sale,
                "default_purchase_price": self._stocking_cost(catalog, index),
                "min_stock": MIN_STOCK[index] if product.stock else D("0"),
                "active": True,
                "import_batch_id": "DEMO-BUSINESS",
            }
            # Whether an item is kept in stock is fixed once it has moved.
            item, _ = Item.objects.update_or_create(item_code=code, defaults=values, create_defaults={**values, "is_stock_tracked": product.stock})
            items.append(item)

        raw = []
        if catalog.raw:
            supplies, _ = Category.objects.update_or_create(
                category_code="DEMO-CAT-02", defaults={"name_ar": "خامات ومستلزمات", "name_en": "Materials & supplies", "active": True},
            )
            for supply in catalog.raw:
                values = {"item_name": supply.name, "category": supplies, "unit": supply.unit, "default_sale_price": supply.price,
                          "default_purchase_price": supply.price, "min_stock": D("0"), "active": True, "import_batch_id": "DEMO-BUSINESS"}
                item, _ = Item.objects.update_or_create(item_code=supply.code, defaults=values, create_defaults={**values, "is_stock_tracked": True})
                raw.append(item)

        from staff.models import Employee

        # PERF-001: two sales reps share the customers, so the rep report has something to show.
        reps = [
            Employee.objects.update_or_create(code=code, defaults={"name": name, "title": "مندوب مبيعات", "commission_percent": rate, "active": True})[0]
            for code, name, rate in (("DEMO-REP-1", "سمير المندوب", D("3.00")), ("DEMO-REP-2", "هالة المندوبة", D("2.50")))
        ]
        sales_scale = self._ratio(catalog, STOCKING, "sale")
        customers = []
        for index, ((code, phone, limit), name) in enumerate(zip(CUSTOMERS, catalog.customers)):
            if index == 2:
                limit = self._over_limit(catalog)
            else:
                limit = max(self._round(limit * sales_scale, 500), D("500.00"))
            customer, _ = Customer.objects.update_or_create(
                customer_code=code,
                defaults={
                    "name": name,
                    "phone": phone,
                    "whatsapp": phone,
                    "credit_limit": limit,
                    "sales_rep": reps[index % len(reps)],
                    "active": True,
                    "import_batch_id": "DEMO-BUSINESS",
                },
            )
            customers.append(customer)

        suppliers = []
        for (code, phone), name in zip(SUPPLIERS, catalog.suppliers):
            supplier, _ = Supplier.objects.update_or_create(
                supplier_code=code,
                defaults={"name": name, "phone": phone, "active": True, "import_batch_id": "DEMO-BUSINESS"},
            )
            suppliers.append(supplier)

        cash_scale = max(self._ratio(catalog, STOCKING, "buy"), sales_scale)
        cashboxes = []
        for code, name_ar, name_en, opening, is_default in CASHBOXES:
            values = {
                "name_ar": name_ar,
                "name_en": name_en,
                "currency": "EGP",
                "is_default": is_default,
                "active": True,
                "import_batch_id": "DEMO-BUSINESS",
            }
            # The opening balance is set once: rewriting it after cash has moved
            # would change the balance without a movement (HG-002).
            cashbox, _ = Cashbox.objects.update_or_create(
                cashbox_code=code,
                defaults=values,
                create_defaults={**values, "opening_balance": max(self._round(opening * cash_scale, 100), D("300.00"))},
            )
            cashboxes.append(cashbox)

        return {
            "catalog": catalog,
            "location": location,
            "items": items,
            "raw": raw,
            "customers": customers,
            "suppliers": suppliers,
            "cashboxes": cashboxes,
        }

    # ---- what the catalog's prices make of the base amounts ----

    @staticmethod
    def _round(amount, step):
        return (D(amount) / step).to_integral_value(rounding="ROUND_FLOOR") * step

    @staticmethod
    def _stocking_cost(catalog, index):
        """What one unit costs to put on the shelf: bought, or made from its recipe."""

        product = catalog.items[index]
        if index in catalog.recipes:
            output, parts = catalog.recipes[index]
            return (sum((catalog.raw[r].price * q for r, q in parts), ZERO) / output).quantize(D("0.01"))
        return product.purchase if product.stock else ZERO

    def _value(self, catalog, lines, kind):
        """What index lines are worth in a catalog: at sale prices, or what stocking them costs."""

        if kind == "sale":
            return sum((catalog.items[i].sale * D(q) for i, q in lines), ZERO)
        return sum((self._stocking_cost(catalog, i) * D(q) for i, q in lines), ZERO)

    def _ratio(self, catalog, lines, kind):
        base = self._value(BASE, lines, kind)
        return self._value(catalog, lines, kind) / base if base else D("0")

    def _scaled(self, master, amount, lines, kind):
        """``amount`` was chosen for the base catalog's prices; the same share of these."""

        catalog = master["catalog"]
        if catalog is BASE:
            return amount
        return (amount * self._ratio(catalog, lines, kind)).quantize(D("0.01"))

    def _over_limit(self, catalog):
        """Customer 3's limit: below what their one credit invoice leaves them owing."""

        owed = self._value(catalog, OVER_LIMIT_SALE, "sale")
        if owed > D("1600"):
            return D("1500.00")
        return max(self._round(owed * D("0.6"), 50), D("50.00"))

    # ---- stocking and selling, by item position ----

    def stock_in(self, master, number, invoice_date, supplier, lines, paid_now, actor, base_amount=True):
        """Put ``lines`` [(item index, quantity)] on the shelf: bought from the
        supplier, or, for goods with a recipe, made from bought materials.
        Services are never stocked. ``paid_now`` is for the base catalog's
        prices unless ``base_amount`` is False."""

        catalog = master["catalog"]
        bought, made, materials = [], [], {}
        for index, quantity in lines:
            quantity = D(quantity)
            if index in catalog.recipes:
                output, parts = catalog.recipes[index]
                made.append((index, quantity))
                for raw_index, per_batch in parts:
                    materials[raw_index] = materials.get(raw_index, D("0")) + per_batch * quantity / output
            elif master["items"][index].is_stock_tracked:
                bought.append((master["items"][index], quantity))
        bought += [(master["raw"][i], q.quantize(D("0.001"))) for i, q in sorted(materials.items())]
        invoice = None
        if bought:
            total = sum((self._line_total(item.default_purchase_price, qty) for item, qty in bought), ZERO)
            paid = self._scaled(master, paid_now, lines, "buy") if base_amount else paid_now
            invoice = self._post_purchase(number=number, invoice_date=invoice_date, supplier=supplier, location=master["location"],
                                          cashbox=master["cashboxes"][0], lines=bought, paid_now=min(paid, total), actor=actor)
        for index, quantity in made:
            self._produce(master, index, quantity, invoice_date, actor)
        return invoice

    def sell(self, master, number, invoice_date, customer_index, lines, actor, paid_now=None, share=None):
        """Sell ``lines`` [(item index, quantity)]; paid ``paid_now`` (base prices) or ``share`` of the total."""

        items = [(master["items"][i], D(q)) for i, q in lines]
        if share is not None:
            total = sum((self._line_total(item.default_sale_price, qty) for item, qty in items), ZERO)
            paid = (total * D(str(share))).quantize(D("0.01"))
        else:
            paid = self._scaled(master, paid_now, lines, "sale")
        return self._post_sale(number=number, invoice_date=invoice_date, customer=master["customers"][customer_index],
                               location=master["location"], cashbox=master["cashboxes"][0], lines=items, paid_now=paid, actor=actor)

    def _produce(self, master, index, quantity, run_date, actor):
        from manufacturing.models import Recipe
        from manufacturing.services import produce, save_recipe

        product = master["items"][index]
        recipe = Recipe.objects.filter(product=product, active=True).first()
        output, parts = master["catalog"].recipes[index]
        if recipe is None:
            recipe = save_recipe({"product": product, "name": product.item_name, "output_quantity": output},
                                 [(master["raw"][r], q) for r, q in parts], actor)
        return produce(recipe, actor, batches=(quantity / recipe.output_quantity).quantize(D("0.001")), location=master["location"],
                       run_date=run_date, notes="Demo business seed")

    @staticmethod
    def _line_total(price, quantity):
        return (price * quantity).quantize(D("0.01"))

    # ---- history, written once ----

    #: What each day's batch sells, and therefore what it must buy back.
    #: (suffix, customer index, ((item index, quantity), ...), paid now).
    #: Item 5 is deliberately absent: the history sells it to nothing and the
    #: daily batch never restocks it, so the out-of-stock alert has a standing
    #: cause however many days this runs.
    DAILY_SALES = (
        ("A", 1, ((0, 3),), D("500.00")),
        ("B", 0, ((2, 8),), D("960.00")),
    )

    def _seed_history(self, master, actor, today):
        """Stock the shelves and trade for a few weeks, deliberately unevenly.

        Item 4 lands just above its minimum so the low-stock alert has a real
        cause, and one customer is pushed past their credit limit.
        """

        purchases = (
            ("DEMO-PI-001", today - timedelta(days=20), 0, ((0, 40), (1, 25), (2, 60)), D("6000.00")),
            ("DEMO-PI-002", today - timedelta(days=12), 1, ((3, 6), (4, 12)), D("0.00")),
        )
        for number, invoice_date, supplier_index, lines, paid_now in purchases:
            self.stock_in(master, number, invoice_date, master["suppliers"][supplier_index], lines, paid_now, actor)

        # Ingredients, spare parts, materials: bought once, on the shelf for
        # the screens that use them (recipes, production orders, projects).
        if master["raw"]:
            lines = [(item, supply.quantity) for item, supply in zip(master["raw"], master["catalog"].raw)]
            total = sum((self._line_total(item.default_purchase_price, qty) for item, qty in lines), ZERO)
            self._post_purchase(number="DEMO-PI-003", invoice_date=today - timedelta(days=18), supplier=master["suppliers"][1],
                                location=master["location"], cashbox=master["cashboxes"][0], lines=lines,
                                paid_now=(total / 2).quantize(D("0.01")), actor=actor)

        sales = (
            ("DEMO-SI-001", today - timedelta(days=15), 0, ((0, 4), (2, 6)), D("1000.00")),
            ("DEMO-SI-002", today - timedelta(days=8), 3, ((1, 5), (0, 3)), D("500.00")),
            # Customer 3 has a 1,500 limit; this leaves them well past it so the
            # over-limit alert has a real subject.
            ("DEMO-SI-003", today - timedelta(days=4), 2, OVER_LIMIT_SALE, D("0.00")),
            # Clears item 5 off the shelf for good.
            ("DEMO-SI-004", today - timedelta(days=2), 1, ((4, 12),), D("500.00")),
        )
        for number, invoice_date, customer_index, lines, paid_now in sales:
            self.sell(master, number, invoice_date, customer_index, lines, actor, paid_now=paid_now)

    # ---- today, topped up on every run ----

    def _seed_today(self, master, actor, cashier, today):
        """Give today its own trade, so the dashboard is never all zeros.

        Returns False when today already has some, which makes a same-day re-run
        a no-op rather than a duplicate.
        """

        stamp = today.strftime("%Y%m%d")
        if SalesInvoice.objects.filter(invoice_number__startswith=f"DEMO-SI-{stamp}-").exists():
            return False

        # Buy back exactly what today will sell, so running this on many days in
        # a row neither drains the shelves nor quietly inflates them.
        restock = {}
        for _, _, lines, _ in self.DAILY_SALES:
            for index, quantity in lines:
                restock[index] = restock.get(index, 0) + quantity

        restock_lines = sorted(restock.items())
        # Pay most of it, leaving a little on account. Derived from the lines
        # rather than fixed, so changing what the day sells cannot push the
        # remaining balance negative.
        restock_total = self._value(master["catalog"], restock_lines, "buy")

        self.stock_in(
            master,
            f"DEMO-PI-{stamp}",
            today,
            master["suppliers"][0],
            restock_lines,
            (restock_total * D("0.6")).quantize(D("0.01")),
            actor,
            base_amount=False,
        )

        # One invoice on the cashier and one on the owner, so a cashier's
        # self-scoped cards differ visibly from the whole day's total.
        for suffix, customer_index, lines, paid_now in self.DAILY_SALES:
            self.sell(master, f"DEMO-SI-{stamp}-{suffix}", today, customer_index, lines,
                      (cashier or actor) if suffix == "A" else actor, paid_now=paid_now)

        self._seed_payments(master, actor, today, stamp)
        return True

    def _post_purchase(self, number, invoice_date, supplier, location, cashbox, lines, paid_now, actor):
        subtotal = sum((self._line_total(item.default_purchase_price, qty) for item, qty in lines), ZERO)
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
            line_total = self._line_total(item.default_purchase_price, qty)
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

    def _post_sale(self, number, invoice_date, customer, location, cashbox, lines, paid_now, actor):
        subtotal = sum((self._line_total(item.default_sale_price, qty) for item, qty in lines), ZERO)
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
            salesperson=customer.sales_rep if reps_in_use() else None,  # FEEDBACK-R1
            created_by=actor,
            notes="Demo business seed",
        )
        for number_in_invoice, (item, qty) in enumerate(lines, start=1):
            line_total = self._line_total(item.default_sale_price, qty)
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

    def _seed_payments(self, master, actor, today, stamp):
        cashbox = master["cashboxes"][0]
        daily = [line for _, _, lines, _ in self.DAILY_SALES for line in lines]

        for number, customer, amount in (
            (f"DEMO-CR-{stamp}-A", master["customers"][0], D("600.00")),
            (f"DEMO-CR-{stamp}-B", master["customers"][3], D("1200.00")),
        ):
            record_customer_payment(
                payment_number=number,
                payment_date=today,
                customer=customer,
                cashbox=cashbox,
                amount=self._scaled(master, amount, daily, "sale"),
                user=actor,
                notes="Demo business seed",
            )

        supplier_amount = self._scaled(master, D("2000.00"), STOCKING, "buy")
        if supplier_amount > 0:
            record_supplier_payment(
                payment_number=f"DEMO-SP-{stamp}",
                payment_date=today,
                supplier=master["suppliers"][0],
                cashbox=cashbox,
                amount=supplier_amount,
                user=actor,
                notes="Demo business seed",
            )

    # ---- summary ----

    def _summarise(self):
        if not self.verbosity:
            return

        from reports import selectors

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
