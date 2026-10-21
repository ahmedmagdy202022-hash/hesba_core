"""DEMO-001: turn an empty database into a showcase shop, once.

Only runs when DEMO_MODE is on. Creates the client and the demo logins
(owner, manager, cashier, stock keeper, accountant), finishes setup as a
retail shop with every module on, posts sample trade through the real
services (seed_demo_business), and adds what that kind of business also
uses: tables, appointments, patient files, projects, production orders or
expiry dates (R2-5). Names and prices follow the chosen activity
(reports.demo_catalogs).

Running it again on a filled database only tops up today's trade
(seed_demo_business does that idempotently), so the "today" cards are never
all zero the day after the demo was prepared.
"""

from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management import call_command
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Fill an empty demo database with a sample shop (DEMO_MODE only)."

    def handle(self, *args, **options):
        if not settings.DEMO_MODE:
            raise CommandError("prepare_demo only runs with DEMO_MODE=True; it writes sample data and known passwords.")
        from settings_core.models import ClientProfile

        if ClientProfile.get_active() is not None:
            call_command("seed_demo_business", username="owner", force=True, verbosity=0)
            from settings_core.setup_services import usable_modules

            self._fit_users(set(usable_modules()))  # Codex #233: a demo made before F26 loses its waiter too
            self.stdout.write("Demo data already present; today's trade topped up.")
            return
        password = settings.DEMO_PASSWORD
        call_command("bootstrap_client", display_name="محل حِسبة التجريبي", client_code="DEMO", password=password, verbosity=0)
        from settings_core import setup_catalog as catalog
        from settings_core.setup_services import complete_setup

        profile = ClientProfile.get_active()
        # The ready-made shop also shows employees and reps (FEEDBACK-R1 ties reps to that module).
        complete_setup(profile, "commercial", "retail", ",".join(catalog.default_modules("commercial") + ("employees_technicians",)))
        call_command("seed_demo_users", password=password, force=True, verbosity=0)
        self._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        self._spread_sale_times()
        self._extras()
        self.stdout.write(self.style.SUCCESS(f"Demo shop ready. Sign in as owner / {password}"))

    #: (days ago, customer index, ((item index, quantity), ...), share paid now)
    #: A month of uneven trade so the charts, weekdays and peak hours have a
    #: shape. Item 5 is never touched: it must stay out of stock for the alert.
    HISTORY_SALES = (
        (27, 0, ((0, 2),), 1), (26, 3, ((1, 1), (2, 2)), 1), (24, 1, ((3, 1),), 0.5),
        (23, 0, ((2, 3),), 1), (21, 3, ((0, 3), (1, 2)), 0.5), (20, 0, ((2, 2),), 1),
        (19, 1, ((0, 1), (3, 1)), 1), (17, 3, ((1, 3),), 0), (16, 0, ((2, 4),), 1),
        (14, 0, ((0, 2), (2, 1)), 1), (13, 3, ((3, 2),), 0.5), (12, 1, ((1, 1),), 1),
        (10, 0, ((0, 4),), 1), (9, 3, ((2, 5), (1, 1)), 0.5), (7, 0, ((3, 1),), 1),
        (6, 1, ((0, 2), (2, 2)), 1), (5, 3, ((1, 2),), 0), (3, 0, ((2, 3),), 1),
        (1, 3, ((0, 3), (3, 1)), 0.5),
    )

    def _history(self):
        """Sales across the last four weeks, through the real posting services."""

        from django.contrib.auth import get_user_model

        from reports.management.commands.seed_demo_business import Command as Seed

        seed = Seed()
        owner = get_user_model().objects.get(username="owner")
        master = seed._seed_master_data()
        self._kitchen(master, owner)
        today = timezone.localdate()
        self._date_openings(today - timedelta(days=30))
        need = {}
        for _, _, lines, _ in self.HISTORY_SALES:
            for index, quantity in lines:
                need[index] = need.get(index, 0) + quantity
        seed.stock_in(master, "DEMO-PI-HIST", today - timedelta(days=29), master["suppliers"][0], sorted(need.items()), Decimal("0.00"), owner)
        for n, (days_ago, customer, lines, share) in enumerate(self.HISTORY_SALES, start=1):
            seed.sell(master, f"DEMO-SH-{n:03d}", today - timedelta(days=days_ago), customer, lines, owner, share=share)

    @staticmethod
    def _dishes(master):
        """The menu's dishes (made to order, not bought), in catalog order."""

        return [item for item, product in zip(master["items"], master["catalog"].items) if not product.stock][:3]

    def _kitchen(self, master, owner):
        """W16: a restaurant demo's dishes are made in the kitchen (RESTO-004, HG-044 C1).

        Before anything is sold, each dish gets its ingredients and is switched to "made in the kitchen", so every
        plate sold in the sample month is cooked from stock through the manufacturing engine. The dashboard's margin
        is then the dish-costing screen's, not 98% from dishes that cost nothing."""

        from restaurant import insights, kitchen

        raw = master["raw"]
        if not (master["catalog"].key.startswith("restaurants.") and raw):
            return
        for n, dish in enumerate(self._dishes(master)):
            if kitchen.is_kitchen_made(dish):
                continue
            lines = [(raw[n % len(raw)], "0.250")]
            if raw[-1].pk != raw[n % len(raw)].pk:
                lines.append((raw[-1], "0.100"))
            insights.save_ingredients(dish, lines, owner)
            kitchen.make_in_kitchen(dish, owner)
            dish.refresh_from_db(fields=["is_stock_tracked"])  # switched in place: nothing has sold it yet

    @staticmethod
    def _date_openings(day):
        """UI-TRACK F28: the opening balances count from before the month of history, not from today.

        The general ledger dates an opening balance on the day its record was made; the demo makes
        its records today and then backfills four weeks of trade, so the cash ledger used to start
        in the red. The sample records are dated a day before the first sample document instead.
        """

        from datetime import datetime, time

        from cashboxes.models import Cashbox
        from master_data.models import Customer, Supplier

        moment = timezone.make_aware(datetime.combine(day, time(9, 0)))
        for model in (Cashbox, Customer, Supplier):
            model.objects.filter(created_at__gt=moment).update(created_at=moment)

    def _spread_sale_times(self):
        """Past invoices were all created this second; give them shop hours so
        the peak-hours chart shows a working day instead of one bar at "now"."""

        from sales.models import SalesInvoice

        today = timezone.localdate()
        hours = (10, 12, 13, 19, 20, 18, 11, 21, 17, 14, 19, 20)
        for n, invoice in enumerate(SalesInvoice.objects.filter(invoice_date__lt=today).order_by("pk")):
            stamp = timezone.make_aware(datetime.combine(invoice.invoice_date, time(hours[n % len(hours)], (n * 17) % 60)))
            SalesInvoice.objects.filter(pk=invoice.pk).update(created_at=stamp)

    def _extras(self):
        """What this kind of business also shows, beyond buying and selling (R2-5):
        tables for a restaurant, appointments and patient files for a clinic,
        projects for a builder, production orders for a factory, expiry dates
        for a pharmacy."""

        from django.contrib.auth import get_user_model

        from reports.demo_catalogs import active_catalog
        from reports.management.commands.seed_demo_business import Command as Seed
        from staff.services import save_employee

        from batches.services import batches_enabled
        from settings_core.setup_services import usable_modules

        owner = get_user_model().objects.get(username="owner")
        catalog = active_catalog()
        master = Seed()._seed_master_data(catalog)
        # DEMO-FIT: an extra whose module (or option) was switched off in setup is left out.
        modules = set(usable_modules())
        extras = {extra for extra in catalog.extras if self.EXTRA_MODULES.get(extra, "") in modules | {""}}
        if not (batches_enabled() and "inventory" in modules):
            extras.discard("batches")
        if "tables" in extras:
            from restaurant import services as tables

            for name in ("1", "2", "3", "4", "5", "6"):
                tables.save_table({"name": name, "seats": 4}, owner)
            self._floor(master, catalog, owner)
        if extras & {"appointments", "patients", "orders", "projects", "workorders"}:
            staff = save_employee({"name": catalog.staff[0], "title": catalog.staff[1], "commission_percent": "10"}, owner)
        if "appointments" in extras:
            self._appointments(master, staff, owner)
        if "patients" in extras:
            self._patients(master, staff, owner)
        if "students" in extras:
            self._students(master, staff, owner)
        if "animals" in extras:
            self._vet(master, staff, owner, timezone.localdate())
        if "projects" in extras:
            self._projects(master, owner)
        if "contracts" in extras:
            self._contracts(master, owner)
        if "packages" in extras:
            self._packages(master, owner)
        if "hours" in extras:
            self._hours(master, owner)
        if "orders" in extras:
            self._orders(master, owner)
        if "batches" in extras:
            self._batches(master, owner)
        if "workorders" in extras:
            self._workorders(master, staff, owner)
        self._options(master, owner, modules)
        if catalog.shelf:
            self._shelf(master, catalog, owner, modules, "batches" in extras)
        if "expenses" in modules:
            self._expenses(master, owner)  # last: a share of the whole month's sales, the shelf's included
        self._fit_users(modules)

    #: DM2 (W06): the month's running costs, as a share of the month's sales, and how many days ago each was paid.
    MONTH_EXPENSES = (("rent", "0.08", 17, "إيجار المحل — الشهر ده"), ("utilities", "0.03", 9, "فاتورة الكهربا والمية"),
                      ("internet_phone", "0.015", 12, "باقة الإنترنت والتليفون"), ("supplies", "0.02", 5, "أدوات نظافة وتغليف"))

    def _expenses(self, master, owner):
        """DM2 (W06): a month without rent or electricity made net profit the same as gross profit. Paid from the
        main cashbox, never more than it holds, inside this month."""

        from django.db.models import Sum

        from cashboxes.services import get_cashbox_balance
        from expenses.models import Expense, ExpenseCategory
        from expenses.services import record_expense
        from sales.models import SalesInvoice

        if Expense.objects.exists():
            return
        today = timezone.localdate()
        first = today.replace(day=1)
        sales = SalesInvoice.objects.filter(status="posted", invoice_date__gte=first).aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
        cashbox = master["cashboxes"][0]
        for code, share, days_ago, description in self.MONTH_EXPENSES:
            category = ExpenseCategory.objects.filter(code=code, active=True).first()
            amount = (sales * Decimal(share)).quantize(Decimal("1"))
            if category is None or amount <= 0 or amount > get_cashbox_balance(cashbox):
                continue
            record_expense(category=category, cashbox=cashbox, amount=amount, expense_date=max(first, today - timedelta(days=days_ago)),
                           description=description, user=owner)

    #: AN7 (G06): (days ago, ((shelf item number, quantity), ...)) of walk-in sales from the shelf, so the
    #: till's buttons and the reports have the weighed goods and the everyday packs selling.
    SHELF_SALES = (
        (13, ((7, 2), (30, Decimal("1.5")), (14, 6), (26, 1))), (11, ((32, 2), (35, Decimal("0.5")), (8, 2), (21, 3))),
        (10, ((7, 1), (31, 2), (33, 1), (15, 4))), (8, ((1, 3), (34, Decimal("1.5")), (8, 1), (13, 1))),
        (7, ((7, 2), (36, 2), (22, 2), (12, 1))), (5, ((30, Decimal("2.5")), (32, 1), (9, 4), (14, 6))),
        (4, ((7, 3), (33, Decimal("1.5")), (3, 2), (28, 1))), (3, ((35, 1), (31, 1), (16, 1), (23, 2))),
        (2, ((7, 2), (32, 2), (8, 2), (21, 2))), (1, ((30, 2), (34, 1), (36, Decimal("0.5")), (14, 6))),
    )

    @staticmethod
    def _shelf_sales(count):
        """W05: ten walk-in sales over two weeks, three shelf items each, one of a kind; every item sells at
        most twice, well inside what was bought."""

        return tuple((13 - n, tuple(((n * 3 + k * 7) % count + 1, 1) for k in range(3))) for n in range(10))

    def _shelf(self, master, catalog, owner, modules, batches):
        """AN7 (G06, Ahmed approved): the rest of a supermarket's shelf, in sections; packs scan, weighed goods
        do not; some are bought by the carton and the fresh ones are kept by batch. Bought once on credit from
        the wholesaler (so the cashbox never goes below zero) and sold to walk-in customers over two weeks."""

        from barcode.symbology import internal_ean13
        from master_data.models import Category, Item
        from reports.management.commands.seed_demo_business import Command as Seed
        from sales.pos import walk_in_customer
        from settings_core.capabilities import enabled_capabilities
        from units.models import ItemUnit

        if Item.objects.filter(item_code="DEMO-SH-01").exists():
            return
        on = set(enabled_capabilities())
        today = timezone.localdate()
        base_category = master["items"][0].category
        shelf, number = [], 0
        for code, name_ar, name_en, products in catalog.shelf:
            category = base_category if code is None else Category.objects.update_or_create(
                category_code=code, defaults={"name_ar": name_ar, "name_en": name_en, "active": True})[0]
            for product in products:
                number += 1
                item = Item.objects.create(item_code=f"DEMO-SH-{number:02d}", item_name=product.name, category=category, unit=product.unit,
                                           default_sale_price=product.sale, default_purchase_price=product.purchase, min_stock=Decimal("0"),
                                           is_stock_tracked=True, active=True, import_batch_id="DEMO-BUSINESS")
                if product.barcode and "barcode" in on:
                    Item.objects.filter(pk=item.pk).update(barcode=internal_ean13(item.pk))
                if product.carton and "units" in on:
                    name, name_en_unit, factor = product.carton
                    ItemUnit.objects.create(item=item, name_ar=name, name_en=name_en_unit, factor=Decimal(factor),
                                            sale_price=(product.sale * factor).quantize(Decimal("0.01")),
                                            purchase_price=(product.purchase * factor).quantize(Decimal("0.01")))
                shelf.append((item, product))
        if "purchases" not in modules:
            return
        seed = Seed()
        received = today - timedelta(days=16)
        seed._post_purchase("DEMO-PI-SHELF", received, master["suppliers"][1], master["location"], master["cashboxes"][0],
                            [(item, product.quantity) for item, product in shelf], Decimal("0.00"), owner)
        if batches:
            from batches.services import register_batch

            for item, product in shelf:
                if product.expires:
                    register_batch(item=item, location=master["location"], batch_no=f"LOT-{item.item_code[-2:]}{received:%y%m%d}",
                                   expiry_date=today + timedelta(days=product.expires), quantity=product.quantity, received_on=received, user=owner)
        walk_in = walk_in_customer()
        sales = self.SHELF_SALES if catalog.key == "commercial.grocery" else self._shelf_sales(len(shelf))
        for n, (days, lines) in enumerate(sales, start=1):
            sold = [(shelf[index - 1][0], Decimal(quantity)) for index, quantity in lines]
            total = sum(((item.default_sale_price * quantity).quantize(Decimal("0.01")) for item, quantity in sold), Decimal("0.00"))
            seed._post_sale(f"DEMO-SI-SH-{n:02d}", today - timedelta(days=days), walk_in, master["location"], master["cashboxes"][0],
                            sold, total, owner)
        self._spread_sale_times()

    #: A demo login whose role only makes sense with one module (HG-048): a clothes shop has no waiter.
    ROLE_MODULES = {"waiter": "tables_orders", "engineer": "projects"}

    def _fit_users(self, modules):
        """UI-TRACK F26: the users screen of a shop lists only the people a shop has."""

        from django.contrib.auth import get_user_model
        from django.db.models import ProtectedError

        for username, module in self.ROLE_MODULES.items():
            if module in modules:
                continue
            user = get_user_model().objects.filter(username=username).first()
            if user is None:
                continue
            try:
                user.delete()
            except ProtectedError:  # already used for something: keep it, switched off
                user.is_active = False
                user.save(update_fields=["is_active"])

    #: The module a catalog extra belongs to; an extra not listed here belongs to the activity itself.
    EXTRA_MODULES = {"tables": "tables_orders", "appointments": "appointments_visits", "projects": "projects", "orders": "manufacturing"}

    def _options(self, master, owner, modules):
        """DEMO-FIT: each option switched on in setup gets something of its own to show, unless the
        activity's extras already gave it some: a credit sale on instalments with one overdue, a
        wholesale price list, a carton unit, sizes and colours in stock, serial numbers on the shelf,
        expiry dates, two sales with VAT, an asset depreciating, barcodes on the goods and an open shift."""

        from settings_core.capabilities import enabled_capabilities

        on = set(enabled_capabilities())
        goods = [item for item in master["items"] if item.is_stock_tracked]
        steps = (
            ("barcode", self._barcodes), ("installments", self._instalments), ("price_lists", self._price_list),
            ("units", self._units), ("variants", self._variants), ("serials", self._serials),
            ("batches_expiry", self._batches_option), ("vat", self._vat), ("fixed_assets", self._asset), ("pos", self._shift),
            ("quotations", self._quotations),
        )
        for slug, step in steps:
            if slug in on:
                step(master, goods, owner, modules)
        if "purchases" in modules:
            self._purchase_orders(master, goods, owner, modules)

    def _purchase_orders(self, master, goods, owner, modules):
        """T2: an order sent to a supplier, and one confirmed and partly received on a purchase invoice."""

        from orders import purchasing as po
        from orders.models import PurchaseOrder, PurchaseStatus
        from purchases.services import post_purchase_invoice
        from taxes.services import create_purchase_draft_with_tax

        suppliers = master.get("suppliers") or []
        if PurchaseOrder.objects.exists() or not suppliers or len(goods) < 2:
            return
        today = timezone.localdate()
        sent = po.create_order({"supplier": suppliers[0], "order_date": today - timedelta(days=1), "terms": "التوريد في المخزن الرئيسي."}, owner)
        for item, quantity in ((goods[0], "24"), (goods[1], "12")):
            po.add_line(sent, item, owner, quantity=quantity)
        po.move(sent, PurchaseStatus.SENT, owner)
        order = po.create_order({"supplier": suppliers[-1], "order_date": today - timedelta(days=8), "expected_on": today - timedelta(days=2),
                                 "terms": "الدفع بعد 30 يوم من الاستلام."}, owner)
        for item, quantity in ((goods[1], "20"), (goods[0], "10")):
            po.add_line(order, item, owner, quantity=quantity, discount_percent="2")
        order = po.move(order, PurchaseStatus.CONFIRMED, owner)
        first = order.lines.order_by("pk").first()
        half = first.quantity / 2
        invoice = create_purchase_draft_with_tax({"invoice_number": "DEMO-PO-001", "invoice_date": today - timedelta(days=3), "supplier": order.supplier,
                                                  "receiving_location": master["location"], "paid_now": Decimal("0"), "notes": f"أمر شراء {order.number}"},
                                                 [{"item": first.item, "quantity": half, "unit_purchase_price": first.unit_price,
                                                   "line_discount_amount": first.discount_for(half)}], owner)
        po.link_receipt(order, invoice, owner)
        post_purchase_invoice(invoice.pk, user=owner)

    def _barcodes(self, master, goods, owner, modules):
        from barcode.symbology import internal_ean13
        from master_data.models import Item

        for item in goods:
            if not item.barcode:
                Item.objects.filter(pk=item.pk).update(barcode=internal_ean13(item.pk))

    def _instalments(self, master, goods, owner, modules):
        """The oldest credit sale is put on four monthly instalments starting a week after it, so one is already late."""

        from installments.models import InstalmentPlan
        from installments.services import create_plan
        from sales.models import SalesInvoice

        if InstalmentPlan.objects.exists() or "customers" not in modules:
            return
        invoice = (SalesInvoice.objects.filter(status="posted", remaining_due__gt=0, customer__isnull=False, project_certificate__isnull=True)
                   .order_by("invoice_date", "pk").first())
        if invoice is not None:
            create_plan(invoice, 4, invoice.invoice_date + timedelta(days=7), owner, notes="تقسيط على 4 شهور")

    def _price_list(self, master, goods, owner, modules):
        from pricing.models import PriceList
        from pricing.services import assign_customers, save_price_list

        if PriceList.objects.exists():
            return
        wholesale = save_price_list(user=owner, code="WHOLESALE", name_ar="سعر الجملة", name_en="Wholesale", adjust_percent=Decimal("-10"))
        assign_customers(wholesale, [master["customers"][3]], owner)  # the big account buys at wholesale

    def _units(self, master, goods, owner, modules):
        from units.models import ItemUnit

        if ItemUnit.objects.exists() or not goods:
            return
        ItemUnit.objects.create(item=goods[0], name_ar="كرتونة", name_en="Carton", factor=Decimal("12"))

    #: sub-activity -> (code, name, sizes, colours, unit) of the one demo model
    VARIANT_MODELS = {
        "": ("TSHIRT", "تيشيرت قطن بياقة", ["S", "M", "L"], ["أسود", "كحلي"], "unit"),
        "cafe": ("LATTE", "لاتيه", ["صغير", "وسط", "كبير"], ["سخن", "ساقع"], "كوباية"),
    }

    def _variants(self, master, goods, owner, modules):
        """A model in three sizes and two colours, some of it bought in (when purchases are on)."""

        from reports.management.commands.seed_demo_business import Command as Seed
        from settings_core.models import ClientProfile
        from variants.models import VariantGroup
        from variants.services import create_group

        if VariantGroup.objects.exists() or "inventory" not in modules:
            return
        sale = Decimal(goods[0].default_sale_price if goods else "300")
        # UI-TRACK F27: a real model of this trade, so the first thing seen under sizes & colours is not "sample model".
        profile = ClientProfile.get_active()
        code, name, sizes, colors, unit = self.VARIANT_MODELS.get(getattr(profile, "sub_activity_slug", ""), self.VARIANT_MODELS[""])
        _, items = create_group(code=code, name=name, sizes=sizes, colors=colors, unit=unit,
                                sale_price=sale, purchase_price=(sale * Decimal("0.6")).quantize(Decimal("0.01")), user=owner,
                                category=master["items"][0].category)
        if "purchases" in modules:
            Seed()._post_purchase("DEMO-PI-VAR", timezone.localdate() - timedelta(days=3), master["suppliers"][0], master["location"],
                                  master["cashboxes"][0], [(item, Decimal(n % 3 + 2)) for n, item in enumerate(items)], Decimal("0.00"), owner)

    def _serials(self, master, goods, owner, modules):
        """A good the daily trade never sells (items 2 and 4) is tracked by serial, one serial for each unit on
        the shelf; the fewer units the better (a phone shop, not a warehouse of screws)."""

        from batches.services import stock_on_hand
        from serials.models import SerialNumber, SerialSetting
        from serials.services import register_serials

        candidates = [item for item in (master["items"][3], master["items"][1]) if item.is_stock_tracked]
        if SerialNumber.objects.exists() or not candidates:
            return
        on_hand = stock_on_hand([item.pk for item in candidates])
        shelf = sorted((quantity, item.pk, item) for item in candidates if 0 < (quantity := on_hand.get(item.pk) or 0) <= 30)
        if not shelf:
            return
        quantity, _, item = shelf[0]
        SerialSetting.objects.update_or_create(item=item, defaults={"tracked": True, "warranty_months": 12})
        register_serials(item, [f"SN{item.pk:03d}{n:04d}" for n in range(1, int(quantity) + 1)], timezone.localdate() - timedelta(days=20), owner,
                         note="Demo")

    def _batches_option(self, master, goods, owner, modules):
        from batches.models import Batch

        if not Batch.objects.exists() and "inventory" in modules:
            self._batches(master, owner)

    def _vat(self, master, goods, owner, modules):
        """Two paid sales with VAT on them (three days ago and today), so the month's tax report has figures."""

        from sales.models import SalesInvoice
        from sales.services import post_sales_invoice
        from taxes.services import compute_lines, create_sales_draft_with_tax

        if SalesInvoice.objects.filter(status="posted", tax_amount__gt=0).exists():
            return
        item = master["items"][0]  # never the good planted low or sold out
        today = timezone.localdate()
        for n, day in enumerate((today - timedelta(days=3), today), start=1):
            line = {"item": item, "quantity": Decimal("1"), "unit_sale_price": Decimal(item.default_sale_price)}
            paid = sum((taxable + tax for *_, taxable, tax in compute_lines([line])), Decimal("0.00"))  # paid in full, tax included
            draft = create_sales_draft_with_tax({"invoice_number": f"DEMO-TAX-{n:03d}", "invoice_date": day, "customer": master["customers"][0],
                                                 "selling_location": master["location"], "cashbox": master["cashboxes"][0], "paid_now": paid},
                                                [line], owner)
            post_sales_invoice(draft.id, user=owner)

    def _asset(self, master, goods, owner, modules):
        from fixed_assets.models import FixedAsset
        from fixed_assets.services import register_asset

        if FixedAsset.objects.exists():
            return
        started = (timezone.localdate().replace(day=1) - timedelta(days=100)).replace(day=1)
        register_asset(name="أجهزة كمبيوتر وطابعة", category="computers", in_service_on=started, cost=Decimal("36000"), salvage_value=Decimal("0"),
                       useful_months=36, user=owner, notes="Demo")

    def _shift(self, master, goods, owner, modules):
        """The cashier's shift is open this morning with a float in the drawer."""

        from django.contrib.auth import get_user_model

        from shifts.models import Shift
        from shifts.services import open_shift

        if Shift.objects.exists() or "cashboxes" not in modules:
            return
        cashier = get_user_model().objects.filter(username="cashier").first() or owner
        open_shift(cashier, master["cashboxes"][0], Decimal("500"))
        # AN8: the till takes cards too; what is paid by card lands in the card machine's account.
        from cashboxes.models import Cashbox
        from sales.pos_buttons import card_cashbox, save_card_cashbox

        if card_cashbox() is None:
            machine, _ = Cashbox.objects.get_or_create(cashbox_code="DEMO-CASH-CARD", defaults={
                "name_ar": "ماكينة الفيزا", "name_en": "Card machine", "currency": "EGP", "opening_balance": Decimal("0.00"),
                "import_batch_id": "DEMO-BUSINESS"})
            save_card_cashbox(machine)

    def _quotations(self, master, goods, owner, modules):
        """T1: a draft quotation, one sent and waiting, and an accepted order already half invoiced and delivered."""

        from batches.services import stock_on_hand
        from orders import services as quotes
        from orders.models import SalesOrder, Status
        from sales.services import post_sales_invoice
        from taxes.services import create_sales_draft_with_tax

        customers = [customer for customer in master["customers"] if customer.customer_code != "WALK-IN"]
        on_hand = stock_on_hand([item.pk for item in goods])
        stocked = [item for item in goods if (on_hand.get(item.pk) or 0) >= 6]
        if SalesOrder.objects.exists() or len(customers) < 3 or len(stocked) < 2:
            return
        today = timezone.localdate()
        draft = quotes.create_quote({"customer": customers[1], "quote_date": today, "terms": "الأسعار شاملة النقل داخل المحافظة."}, owner)
        for item, quantity in ((stocked[0], "10"), (stocked[1], "5")):
            quotes.add_line(draft, item, owner, quantity=quantity)
        sent = quotes.create_quote({"customer": customers[2], "quote_date": today - timedelta(days=4), "terms": "التسليم خلال 7 أيام من الموافقة."}, owner)
        for item, quantity in ((stocked[1], "8"), (stocked[0], "12")):
            quotes.add_line(sent, item, owner, quantity=quantity, discount_percent="5")
        quotes.move(sent, Status.SENT, owner)
        order = quotes.create_quote({"customer": customers[0], "quote_date": today - timedelta(days=9), "terms": "50% مقدم والباقي عند التسليم."}, owner)
        for item, quantity in ((stocked[0], "6"), (stocked[1], "6")):
            quotes.add_line(order, item, owner, quantity=quantity, discount_percent="3")
        order = quotes.move(quotes.move(order, Status.SENT, owner), Status.CONFIRMED, owner)
        first = order.lines.order_by("pk").first()
        half = first.quantity / 2
        invoice = create_sales_draft_with_tax({"invoice_number": "DEMO-SO-001", "invoice_date": today - timedelta(days=2), "customer": order.customer,
                                               "selling_location": master["location"], "paid_now": Decimal("0"), "notes": f"أمر بيع {order.order_number}"},
                                              [{"item": first.item, "quantity": half, "unit_sale_price": first.unit_price,
                                                "line_discount_amount": first.discount_for(half)}], owner)
        quotes.link_invoice(order, invoice, owner)
        post_sales_invoice(invoice.pk, user=owner)

    def _workorders(self, master, staff, owner):
        """SVC-001: a workshop's week: devices in, on the bench, waiting for approval, ready, delivered, and one back under warranty."""

        from datetime import timedelta

        from django.utils import timezone

        from workorders import services as wo
        from workorders.models import Status, WorkOrder

        if not wo.is_workshop():
            return
        parts = [item for item in master["items"] if item.is_stock_tracked]
        labour = [item for item in master["items"] if not item.is_stock_tracked]
        customers = [customer for customer in master["customers"] if customer.customer_code != "WALK-IN"]
        if not (parts and labour and customers):
            return
        cashbox = next((box for box in master["cashboxes"] if box.is_default), master["cashboxes"][0]) if master["cashboxes"] else None
        today, now = timezone.localdate(), timezone.now()
        plan = (
            ("غسالة توشيبا 7 كيلو", "مش بتعصر وبتعمل صوت عالي", None, 0, ()),
            ("تكييف شارب 1.5 حصان", "مش بيبرّد", (Status.DIAGNOSED, Status.QUOTED), 3, ((labour[0], 1), (parts[0], 1))),
            ("ثلاجة كريازي", "الفريزر بيعمل تلج زيادة", (Status.IN_REPAIR,), 7, ((labour[-1], 1),)),
            ("غسالة أطباق بوش", "مش بتسحب مياه", (Status.DIAGNOSED, Status.APPROVED, Status.IN_REPAIR, Status.READY), 2, ((labour[-1], 1), (parts[-1], 1))),
            ("ميكروويف شارب", "مش بيسخّن", (Status.IN_REPAIR, Status.READY), 10, ((labour[0], 1), (parts[0], 1))),
        )
        orders = []
        for n, (device, problem, path, days_ago, lines) in enumerate(plan):
            order = wo.receive({"customer": customers[n % len(customers)], "device": device, "problem": problem, "technician": staff if path else None,
                                "promised_on": today + timedelta(days=2 - days_ago // 2), "warranty_days": "30", "serial": f"SN-{4710 + n}"}, owner)
            WorkOrder.objects.filter(pk=order.pk).update(received_at=now - timedelta(days=days_ago))
            for item, quantity in lines:
                wo.add_line(order, item, owner, quantity=str(quantity))
            for status in path or ():
                wo.move(order, status, owner)
            orders.append(order)
        # The microwave went home ten days ago, paid in cash; the dishwasher is waiting at the counter.
        delivered = orders[-1]
        try:
            paid = wo.totals(delivered)["total"]
            wo.deliver(delivered, owner, paid_now=str(paid) if cashbox else "0", cashbox=cashbox)
            WorkOrder.objects.filter(pk=delivered.pk).update(delivered_at=now - timedelta(days=8), warranty_until=today + timedelta(days=22))
            delivered.refresh_from_db()
            back = wo.receive({"warranty_of": delivered, "device": delivered.device, "serial": delivered.serial, "problem": "رجع نفس العطل",
                               "technician": staff}, owner)
            wo.add_line(back, parts[0], owner, quantity="1", is_warranty=True)
        except ValidationError:
            pass  # a part out of stock: the demo keeps it ready instead

    def _floor(self, master, catalog, owner):
        """RESTO-002: a week of paid orders at different hours, and one waste adjustment. The dishes' ingredients come from _kitchen."""

        from datetime import datetime as _dt

        from inventory.services import adjust_stock
        from restaurant import services as floor
        from restaurant.models import DiningTable, Order
        from staff.services import save_employee

        dishes = self._dishes(master)
        raw = master["raw"]
        if not dishes or not raw:
            return
        self._kitchen(master, owner)  # already done before the month's sales; here for a demo filled without them
        waiter = save_employee({"name": catalog.staff[0], "title": catalog.staff[1]}, owner)
        cashbox = master["cashboxes"][0]
        tables = list(DiningTable.objects.order_by("sort_order", "name"))
        today = timezone.localdate()
        plan = ((1, 13, 0, 2, (0, 1)), (1, 14, 1, 4, (0, 2)), (2, 20, 2, 3, (1,)), (3, 21, 0, 2, (0, 0, 2)), (4, 13, None, 1, (2,)),
                (5, 20, 3, 5, (0, 1, 1)), (6, 21, None, 1, (1,)), (0, 13, 1, 2, (0,)))
        for days, hour, table, guests, picks in plan:
            if table is None:
                order = floor.open_order(owner, kind="takeaway", waiter=waiter)
            else:
                order = floor.open_order(owner, table=tables[table], guests=guests, waiter=waiter)
            for pick in picks:
                floor.add_item(order, dishes[pick % len(dishes)], owner)
            floor.pay(order, owner, cashbox=cashbox, tendered=Decimal("100000"))  # paid in cash
            closed = timezone.make_aware(_dt.combine(today - timedelta(days=days), time(hour, 15)))
            Order.objects.filter(pk=order.pk).update(opened_at=closed - timedelta(minutes=40 + 5 * guests), closed_at=closed)
        adjust_stock(f"DEMO-WASTE-{today:%m%d}", today - timedelta(days=2), raw[0], master["location"], "out", "1", "هالك — باظت في التلاجة", owner)
        driver = save_employee({"name": "محمود الطيار", "title": "طيار دليفري"}, owner)  # RESTO-003: a delivery out with its driver and fee
        order = floor.open_order(owner, kind="delivery", customer=master["customers"][0], address="شارع النصر 12، مدينة نصر", delivery_fee="20")
        floor.add_item(order, dishes[0], owner, 2)
        floor.dispatch(order, driver, owner)
        floor.pay(order, owner, cashbox=cashbox, tendered=Decimal("100000"))
        closed = timezone.make_aware(_dt.combine(today - timedelta(days=1), time(21, 40)))
        Order.objects.filter(pk=order.pk).update(opened_at=closed - timedelta(minutes=55), dispatched_at=closed - timedelta(minutes=30), closed_at=closed)

    def _appointments(self, master, staff, owner):
        from appointments.services import book
        from master_data.models import Item

        service = next((item for item in master["items"] if not item.is_stock_tracked), None)
        if service is None:
            service, _ = Item.objects.get_or_create(item_code="SRV-01", defaults={"item_name": "خدمة تركيب", "default_sale_price": 150, "is_stock_tracked": False})
        today = timezone.localdate()
        for days, hour, customer in ((0, 17, 1), (1, 10, 0), (1, 13, 2), (2, 11, 3)):
            book({"customer": master["customers"][customer], "employee": staff, "service": service,
                  "starts_at": timezone.make_aware(datetime.combine(today + timedelta(days=days), time(hour))), "duration_minutes": 60}, owner)

    def _patients(self, master, doctor, owner):
        from medical.services import file_for, save_profile, save_visit

        today = timezone.localdate()
        cards = (
            ({"date_of_birth": "1978-03-14", "gender": "male", "blood_type": "A+", "chronic_conditions": "ضغط مرتفع", "current_medications": "كونكور 5 مجم"},
             {"complaint": "صداع ودوخة", "blood_pressure": "150/95", "pulse": "84", "diagnosis": "ارتفاع ضغط الدم", "treatment": "متابعة الضغط يوميًا وتقليل الملح"}),
            ({"date_of_birth": "1990-11-02", "gender": "female", "blood_type": "O+", "allergies": "بنسلين"},
             {"complaint": "كحة وسخونية", "temperature": "38.2", "diagnosis": "التهاب حلق", "treatment": "مضاد حيوي بديل للبنسلين وراحة"}),
            ({"date_of_birth": "2015-06-20", "gender": "male", "blood_type": "B+"},
             {"complaint": "متابعة دورية", "weight": "32", "height": "135", "diagnosis": "حالة عامة جيدة", "treatment": "لا يوجد"}),
        )
        first = master["customers"][0]
        save_visit(first, {"complaint": "كشف أول مرة", "blood_pressure": "160/100", "diagnosis": "ارتفاع ضغط الدم", "treatment": "كونكور 5 مجم",
                           "visit_date": (today - timedelta(days=45)).isoformat(), "doctor": doctor}, owner)  # MED-002: a returning patient
        for customer, (profile, visit) in zip(master["customers"], cards):
            save_profile(file_for(customer), profile, owner)
            save_visit(customer, {**visit, "visit_date": (today - timedelta(days=7)).isoformat(),
                                  "follow_up_date": (today + timedelta(days=7)).isoformat(), "doctor": doctor}, owner)
        self._clinic_month(master, doctor, owner, today)
        self._dental(master, doctor, owner, today)
        self._lab(master, owner, today)
        self._physio(master, doctor, owner, today)
        self._queue(master, doctor, owner, today)

    def _queue(self, master, doctor, owner, today):
        """MED-007: a clinic's waiting list has today's booked patient in, one called and one waiting."""

        from appointments.models import Appointment
        from medical import queue

        if not queue.has_queue():
            return
        booked = Appointment.objects.filter(starts_at__date=today, customer=master["customers"][1]).first()
        first = queue.check_in(master["customers"][2], owner, doctor=doctor)
        queue.check_in(master["customers"][1], owner, appointment=booked)
        queue.move(first, "called", owner)

    def _vet(self, master, doctor, owner, today):
        """MED-006: a vet's first customer owns a dog and a cat; the dog came twice."""

        from medical import vet
        from medical.services import save_visit

        if not vet.is_vet():
            return
        owner_customer = master["customers"][0]
        dog = vet.save_animal(owner_customer, {"name": "روكي", "species": "dog", "breed": "جيرمن شيبرد", "sex": "male",
                                               "date_of_birth": (today - timedelta(days=3 * 365)).isoformat(), "colour": "أسود وبني",
                                               "microchip": "956000012345678", "neutered": True}, owner)
        vet.save_animal(owner_customer, {"name": "مشمش", "species": "cat", "breed": "شيرازي", "sex": "female",
                                         "date_of_birth": (today - timedelta(days=300)).isoformat(), "allergies": "بنسلين"}, owner)
        save_visit(owner_customer, {"animal": dog, "doctor": doctor, "visit_date": (today - timedelta(days=60)).isoformat(), "complaint": "تطعيم سنوي",
                                    "weight": "31", "diagnosis": "حالة عامة جيدة", "treatment": "تطعيم سعار + تطعيم خماسي"}, owner)
        save_visit(owner_customer, {"animal": dog, "doctor": doctor, "visit_date": (today - timedelta(days=5)).isoformat(), "complaint": "عرج في الرجل الشمال",
                                    "weight": "32", "diagnosis": "التواء بسيط", "treatment": "راحة 5 أيام ومسكن",
                                    "follow_up_date": (today + timedelta(days=5)).isoformat()}, owner)

    def _physio(self, master, therapist, owner, today):
        """MED-005: a physiotherapy centre's first patient bought ten sessions and used three."""

        from medical import physio
        from sales.services import post_sales_invoice

        if not physio.is_physio():
            return
        session = next((item for item in master["items"] if not item.is_stock_tracked), None)
        if session is None:
            return
        package = physio.sell(master["customers"][0], {"service": session, "sessions": 10, "price": "3000", "notes": "خشونة ركبة"}, owner)
        post_sales_invoice(package.invoice.pk, owner)
        for days in (14, 10, 7):
            physio.use(package, owner, used_on=today - timedelta(days=days), therapist=therapist)

    def _lab(self, master, owner, today):
        """MED-004: a lab has its tests with normal ranges, and two days of results for its first patient."""

        from medical import lab

        if not lab.is_lab():
            return
        services = {item.item_name: item for item in master["items"] if not item.is_stock_tracked}
        tests = {}
        for position, (name, unit, low, high, service) in enumerate((
                ("هيموجلوبين", "g/dL", "12", "16", "صورة دم كاملة"), ("كرات دم بيضاء", "×10³/µL", "4", "11", "صورة دم كاملة"),
                ("سكر صايم", "mg/dL", "70", "100", ""), ("إنزيم ALT", "U/L", "7", "56", "وظائف كبد"), ("فصيلة الدم", "", "", "", ""))):
            tests[name] = lab.save_test({"name": name, "unit": unit, "low": low, "high": high, "service": services.get(service), "position": position}, owner)
        patient = master["customers"][0]
        lab.record_results(patient, today - timedelta(days=30), {tests["هيموجلوبين"]: ("11.2", ""), tests["سكر صايم"]: ("132", "")}, owner)
        lab.record_results(patient, today - timedelta(days=1), {tests["هيموجلوبين"]: ("12.8", ""), tests["كرات دم بيضاء"]: ("7.5", ""),
                                                                tests["سكر صايم"]: ("118", ""), tests["إنزيم ALT"]: ("31", ""),
                                                                tests["فصيلة الدم"]: ("", "A+")}, owner)

    def _dental(self, master, doctor, owner, today):
        """MED-003: a dental clinic's first patient has a chart and a plan in two stages, one step done."""

        from medical import dental

        if not dental.is_dental():
            return
        services = [item for item in master["items"] if not item.is_stock_tracked]
        if len(services) < 3:
            return
        checkup, root_canal, cleaning = services[:3]
        patient = master["customers"][0]
        for tooth, condition, notes in ((16, "caries", "تسوّس عميق"), (26, "filled", ""), (36, "root_canal", ""), (46, "missing", "اتخلعت 2024"),
                                        (11, "crown", "")):
            dental.set_tooth(patient, tooth, condition, owner, notes)
        first = dental.plan_step(patient, {"service": cleaning, "stage": 1, "doctor": doctor}, owner)
        dental.plan_step(patient, {"service": root_canal, "tooth": 16, "stage": 2, "doctor": doctor}, owner)
        dental.plan_step(patient, {"service": checkup, "stage": 3, "doctor": doctor, "notes": "متابعة بعد العلاج"}, owner)
        dental.complete_step(first, owner, done_on=today - timedelta(days=7))

    def _clinic_month(self, master, doctor, owner, today):
        """MED-002: billed visits for the clinic report, and a follow-up that was missed."""

        from appointments.services import bill, book, set_status
        from medical.services import file_for, save_profile, save_visit
        from sales.pos_customers import quick_add_customer
        from sales.services import post_sales_invoice

        service = next((item for item in master["items"] if not item.is_stock_tracked), None)
        if service is not None:
            for days, customer in ((7, 0), (7, 1), (7, 2)):
                appointment = book({"customer": master["customers"][customer], "employee": doctor, "service": service,
                                    "starts_at": timezone.make_aware(datetime.combine(today - timedelta(days=days), time(10 + customer))),
                                    "duration_minutes": 30}, owner)
                set_status(appointment, "arrived", owner)
                post_sales_invoice(bill(appointment, owner).pk, owner)
            self._insured_visit(service, doctor, owner, today)
        missed, _ = quick_add_customer("سلمى حسن", "01000000105", owner)
        save_profile(file_for(missed), {"gender": "female", "date_of_birth": "1985-01-09", "chronic_conditions": "سكر"}, owner)
        save_visit(missed, {"complaint": "متابعة السكر", "diagnosis": "سكر من النوع الثاني", "treatment": "جلوكوفاج 500 مجم",
                            "visit_date": (today - timedelta(days=21)).isoformat(), "follow_up_date": (today - timedelta(days=6)).isoformat(),
                            "doctor": doctor}, owner)

    def _insured_visit(self, service, doctor, owner, today):
        """MED-008: an insurance company, an insured patient, and one visit billed as two invoices (posted, so it shows on the claim)."""

        from appointments.services import book, set_status
        from medical import insurance
        from sales.pos_customers import quick_add_customer
        from sales.services import post_sales_invoice

        if not insurance.is_insurance_install():
            return
        contract = insurance.save_contract({"name": "مصر للتأمين الصحي", "phone": "0223456789", "coverage_percent": "80", "claim_day": "5"}, owner)
        insured, _ = quick_add_customer("كريم فؤاد", "01000000106", owner)
        insurance.set_cover(insured, {"contract": contract, "member_number": "MI-204518"}, owner)
        appointment = book({"customer": insured, "employee": doctor, "service": service,
                            "starts_at": timezone.make_aware(datetime.combine(today - timedelta(days=3), time(12))), "duration_minutes": 30}, owner)
        set_status(appointment, "arrived", owner)
        bill = insurance.bill_insured(appointment, owner)
        for invoice in (bill.patient_invoice, bill.insurer_invoice):
            if invoice is not None:
                post_sales_invoice(invoice.pk, owner)

    #: CONTRACT-002: a bill of quantities per contracting sub-activity (code, item, unit, quantity, rate).
    BOQ = {
        "contracting.general": (("1", "خرسانة عادية للأساسات", "م³", "120", "1800"), ("2", "خرسانة مسلحة للهيكل", "م³", "450", "3200"),
                                ("3", "مباني طوب أحمر", "م²", "2200", "260"), ("4", "لياسة داخلية وخارجية", "م²", "5200", "95"),
                                ("5", "عزل أسطح", "م²", "600", "180")),
        "contracting.finishing": (("1", "معجون ودهانات بلاستيك", "م²", "3800", "85"), ("2", "أرضيات بورسلين", "م²", "900", "620"),
                                  ("3", "أسقف جبس بورد", "م²", "700", "380"), ("4", "سيراميك حوائط حمامات ومطابخ", "م²", "450", "420")),
        "contracting.electromechanical": (("1", "تأسيس كهرباء (نقطة)", "نقطة", "650", "450"), ("2", "تأسيس سباكة (نقطة)", "نقطة", "180", "900"),
                                          ("3", "توريد وتركيب تكييفات", "جهاز", "24", "24500"), ("4", "لوحات توزيع كهرباء", "لوحة", "12", "8500")),
        "contracting.infrastructure": (("1", "حفر وردم", "م³", "6000", "45"), ("2", "طبقة أساس سن", "م³", "2400", "420"),
                                       ("3", "رصف أسفلت", "م²", "9000", "140"), ("4", "توريد وتركيب بلدورة", "م.ط", "1800", "190")),
    }
    #: TRACK-ACTS W19: the children's stage fits the place (a nursery has no prep-school pupils).
    STAGES = {"nursery": "كي جي 1", "languages": "مستوى مبتدئ", "training": "خريجين", "tutoring_center": "تالتة إعدادي", "private_tutor": "تالتة إعدادي"}

    def _students(self, master, teacher, owner):
        """EDU-001: courses from the catalog's services, three groups, parents with their children enrolled."""

        from education import services as edu
        from master_data.models import Customer
        from staff.services import save_employee

        if not edu.is_education_install():
            return  # the same catalog also serves a services business, which has no student files
        today = timezone.localdate()
        courses = [edu.save_course({"name": item.item_name, "fee": item.default_sale_price, "basis": "monthly", "item": item}, owner)
                   for item in master["items"] if not item.is_stock_tracked][:2]
        if not courses:
            return
        second = save_employee({"name": "أ. نادية", "title": teacher.title or "مدرسة"}, owner)
        # EDU-004: each group says how its teacher is paid: per class, a share of the fees, or a fixed month.
        plan = (
            (courses[0], "مجموعة السبت والتلات", teacher, "قاعة 1", "sat,tue", time(16), 12, "percent", "40"),
            (courses[0], "مجموعة الأحد والأربع", second, "قاعة 2", "sun,wed", time(18), 12, "session", "120"),
            (courses[-1], "مجموعة الاتنين والخميس", teacher, "قاعة 1", "mon,thu", time(17), 10, "monthly", "800"),
        )
        groups = [edu.save_group({"course": course, "name": name, "teacher": who, "room": room, "days": days, "start_time": start,
                                  "duration_minutes": "90", "capacity": str(capacity), "starts_on": today - timedelta(days=40),
                                  "teacher_pay": pay_basis, "teacher_rate": rate}, owner)
                  for course, name, who, room, days, start, capacity, pay_basis, rate in plan]
        families = (
            ("محمد السيد (ولي أمر)", "01001234501", "الأب", ("يوسف محمد", "مريم محمد")),
            ("هالة عبد الرحمن (ولية أمر)", "01101234502", "الأم", ("عمر خالد",)),
            ("طارق فهمي (ولي أمر)", "01201234503", "الأب", ("سلمى طارق", "آدم طارق")),
            ("نجلاء حسن (ولية أمر)", "01501234504", "الأم", ("ليلى أحمد",)),
            ("إبراهيم سالم (ولي أمر)", "01001234505", "الأب", ("حمزة إبراهيم",)),
        )
        from settings_core.models import ClientProfile

        stage = self.STAGES.get(getattr(ClientProfile.get_active(), "sub_activity_slug", ""), "تالتة إعدادي")
        students = []
        for parent, phone, relation, children in families:
            payer = Customer.objects.filter(phone=phone).first()
            for child in children:
                data = {"name": child, "stage": stage, "relation": relation}
                data.update({"payer": payer} if payer else {"payer_name": parent, "payer_phone": phone})
                student = edu.save_student(data, owner)
                payer = student.payer
                students.append(student)
        for n, student in enumerate(students):
            # Brothers and sisters get 10% off; the first two groups share the students, the third takes a few.
            sibling = sum(1 for other in students if other.payer_id == student.payer_id) > 1
            edu.enroll(student, groups[n % 2], owner, start_date=today - timedelta(days=35), discount_percent="10" if sibling else "0")
            if n < 4:
                edu.enroll(student, groups[2], owner, start_date=today - timedelta(days=20))
        # EDU-002: four weeks of attendance, with a few absences and late arrivals.
        from education import attendance

        for group in groups:
            for days_ago in range(28, -1, -1):
                day = today - timedelta(days=days_ago)
                if attendance.weekday(day) not in group.weekdays:
                    continue
                roster = list(attendance.roster_on(group, day))
                marks = {}
                for k, row in enumerate(roster):
                    if (k + days_ago) % 9 == 0:
                        marks[str(row.student_id)] = "absent"
                    elif (k + days_ago) % 11 == 0:
                        marks[str(row.student_id)] = "late"
                if roster:
                    attendance.take(group, owner, day, marks)
        # EDU-003: last month billed and mostly paid, this month billed; two parents still owe.
        from config.numbering import next_in_series
        from education import billing
        from sales.models import CustomerPayment
        from sales.services import record_customer_payment

        last_month = (today.replace(day=1) - timedelta(days=1)).replace(day=1)
        cashbox = next((box for box in master["cashboxes"] if box.is_default), master["cashboxes"][0]) if master["cashboxes"] else None
        for month, day in ((last_month, min(last_month + timedelta(days=2), today)), (today.replace(day=1), today)):
            try:
                invoices = billing.bill_month(month, owner, post=True, invoice_date=day)
            except ValidationError:
                continue  # nothing running that month
            if month == last_month and cashbox is not None:
                for invoice in invoices[:-2]:
                    record_customer_payment(next_in_series(CustomerPayment, "payment_number", "CP-"), day + timedelta(days=3), invoice.customer, cashbox,
                                            invoice.total_amount, owner, notes="مصروفات الشهر")
        # EDU-004: last month's teachers paid out of the cashbox; this month still owed.
        from education import pay

        if cashbox is not None:
            for row in pay.teacher_rows(last_month):
                if row["remaining"] > 0:
                    try:
                        pay.pay(row["teacher"], last_month, row["remaining"], cashbox, owner)
                    except ValidationError:
                        pass  # the cashbox cannot cover it: the demo shows it owed
        self._nursery_day(students, owner)
        self._levels(groups, students, owner)

    def _levels(self, groups, students, owner):
        """EDU-006: a language centre's groups teach levels 1 and 2 of the first course, and two students sat a placement test."""

        from education import levels
        from education.models import StudyGroup

        if not levels.is_levels_install():
            return
        StudyGroup.objects.filter(pk=groups[0].pk).update(level=1)
        StudyGroup.objects.filter(pk=groups[1].pk).update(level=2)
        StudyGroup.objects.filter(pk=groups[2].pk).update(level=1)
        course = groups[0].course
        levels.record_placement(students[0], {"course": course, "level": "1", "score": "18", "out_of": "50", "notes": "مبتدئ"}, owner)
        levels.record_placement(students[1], {"course": course, "level": "2", "score": "36", "out_of": "50", "notes": "محادثة كويسة"}, owner)

    def _nursery_day(self, students, owner):
        """EDU-005: a nursery's door this morning — who may pick each child up, most children in, one already gone home."""

        from education import nursery

        if not nursery.is_nursery():
            return
        relations = {"الأب": "الأم", "الأم": "الأب"}
        now = timezone.now()
        for n, student in enumerate(students):
            parent = student.payer
            nursery.save_person(student, {"name": parent.name.split(" (")[0], "relation": student.relation or "ولي الأمر", "phone": parent.phone}, owner)
            nursery.save_person(student, {"name": f"{relations.get(student.relation, 'الجدة')} — {student.name.split()[-1]}",
                                          "relation": relations.get(student.relation, "الجدة"), "phone": f"0122{n:07d}"}, owner)
            if n == len(students) - 1:
                continue  # one child is not here yet
            arrived = timezone.localtime(now).replace(hour=8, minute=n * 5 % 60, second=0, microsecond=0)
            if arrived > now:
                # Before 8 a.m.: an hour ago, but never before midnight, or "this morning" would be yesterday.
                midnight = timezone.localtime(now).replace(hour=0, minute=0, second=0, microsecond=0)
                arrived = max(now - timedelta(hours=1), midnight)
            nursery.check_in(student, owner, brought_by=student.relation, at=arrived)
            if n == 0 and now - arrived > timedelta(hours=2):
                nursery.check_out(student, nursery.allowed(student).first(), owner, at=arrived + timedelta(hours=2))

    def _projects(self, master, owner):
        """CONTRACT-002: one project with a bill of quantities, terms, an advance,
        two certificates (one posted and collected, one draft), a subcontractor
        and a budget; one lump-sum project with a draft bill."""

        from projects import contract, costs
        from projects.models import ProjectPaymentKind
        from projects.services import bill_progress, issue_materials, save_project
        from purchases.services import post_purchase_invoice
        from sales.services import post_sales_invoice

        today = timezone.localdate()
        # Site materials: a well-stocked item (not the two kept short for the alerts), else a supply.
        stocked = [item for item in master["items"][:3] if item.is_stock_tracked] + master["raw"]
        boq = self.BOQ.get(master["catalog"].key)
        rows = (
            (3, "عمارة سكنية - التجمع", "التجمع الخامس، القاهرة الجديدة", "2500000", 60),
            (2, "مبنى مدرسة - المرحلة الأولى", "مدينة نصر", "1200000", 25),
        )
        for n, (customer, name, site, value, started) in enumerate(rows):
            project = save_project({"name": name, "customer": master["customers"][customer], "site": site, "contract_value": value,
                                    "start_date": today - timedelta(days=started), "end_date": today + timedelta(days=180)}, owner)
            if stocked:
                issue_materials(project, owner, item=stocked[0], location=master["location"], quantity=5 - 2 * n,
                                operation_date=today - timedelta(days=3))
            if n == 0 and boq:
                lines = [contract.save_boq_line(project, owner, {"code": code, "description": text, "unit": unit, "quantity": qty, "rate": rate})
                         for code, text, unit, qty, rate in boq]
                contract.save_terms(project, owner, retention_rate="5", advance_recovery_rate="10")
                total = contract.boq_total(project)
                contract.receive_payment(project, owner, kind=ProjectPaymentKind.ADVANCE, cashbox=master["cashboxes"][0],
                                         amount=(total * Decimal("0.15")).quantize(Decimal("1")), payment_date=today - timedelta(days=started))
                first = contract.create_certificate(project, owner, certificate_date=today - timedelta(days=started), description="أعمال الشهر الأول",
                                                    quantities={line.pk: (line.quantity * Decimal("0.25")).quantize(Decimal("1")) for line in lines[:2]})
                post_sales_invoice(first.invoice_id, user=owner)
                contract.receive_payment(project, owner, kind=ProjectPaymentKind.COLLECTION, cashbox=master["cashboxes"][0],
                                         amount=contract.net_payable(first), payment_date=today - timedelta(days=started - 10))
                # CONTRACT-003: the second certificate is posted and still unpaid past the credit days, so it shows as overdue.
                second = contract.create_certificate(project, owner, certificate_date=today - timedelta(days=40), description="أعمال الشهر التاني",
                                                     quantities={line.pk: (line.quantity * Decimal("0.45")).quantize(Decimal("1")) for line in lines[:3]})
                post_sales_invoice(second.invoice_id, user=owner)
                contract.create_certificate(project, owner, certificate_date=today, description="أعمال الشهر التالت",
                                            quantities={line.pk: (line.quantity * Decimal("0.60")).quantize(Decimal("1")) for line in lines[:3]})
                if master["suppliers"]:
                    subcontract = costs.save_subcontract(project, owner, {"supplier": master["suppliers"][-1], "scope": f"مقاولة باطن: {lines[-1].description}",
                                                                          "value": contract.line_amount(lines[-1]) * Decimal("0.8"), "retention_rate": "5"})
                    bill = costs.bill_subcontract(subcontract, owner, amount=(contract.line_amount(lines[-1]) * Decimal("0.2")).quantize(Decimal("1")),
                                                  description="مستخلص 1", bill_date=today - timedelta(days=10))
                    post_purchase_invoice(bill.invoice_id, owner)
                costs.save_budget(project, owner, {"materials": (total * Decimal("0.35")).quantize(Decimal("1")),
                                                   "subcontract": (total * Decimal("0.15")).quantize(Decimal("1")),
                                                   "labour": (total * Decimal("0.2")).quantize(Decimal("1")),
                                                   "equipment": (total * Decimal("0.05")).quantize(Decimal("1"))})
                continue
            invoice = bill_progress(project, owner, amount=Decimal(value) * Decimal("0.2"), description="مستخلص رقم 1")
            if n == 0:
                post_sales_invoice(invoice.id, user=owner)

    def _contracts(self, master, owner):
        """SVC-002: a monthly contract billed for its past months (two posted, one still a draft) and a
        quarterly one with a visit a period that is due, so "bill what is due" has work to do."""

        from agreements.services import add_months, bill_due, save_contract
        from sales.services import post_sales_invoice

        today = timezone.localdate()
        services = [item for item in master["items"] if not item.is_stock_tracked]
        if not services:
            return
        monthly = next((item for item in services if "شهر" in item.item_name), services[-1])
        first = save_contract({"customer": master["customers"][3], "service": monthly, "amount": monthly.default_sale_price, "every": "month",
                               "starts_on": add_months(today, -2).replace(day=1)}, owner)
        for bill in bill_due(owner, contracts=[first])[:2]:
            post_sales_invoice(bill.invoice.id, user=owner)
        visited = master["customers"][2]
        if not visited.address:  # SVC-004: its visits are booked at the customer's address
            visited.address = "١٤ شارع مصدق، الدقي، الجيزة"
            visited.save(update_fields=["address"])
        save_contract({"customer": visited, "service": services[0], "amount": Decimal(services[0].default_sale_price) * 2,
                       "every": "quarter", "visits_per_period": 1, "description": "عقد زيارات دورية",
                       "starts_on": today - timedelta(days=10)}, owner)

    def _packages(self, master, owner):
        """SVC-003: a posted package with two sessions used, and one still on a draft invoice."""

        from agreements.packages import sell_package, use_session
        from sales.services import post_sales_invoice
        from staff.models import Employee

        today = timezone.localdate()
        services = [item for item in master["items"] if not item.is_stock_tracked]
        if not services:
            return
        staff = Employee.objects.filter(active=True).order_by("pk").first()
        first = sell_package({"customer": master["customers"][0], "service": services[0], "sessions": 6,
                              "price": (Decimal(services[0].default_sale_price) * 6 * Decimal("0.85")).quantize(Decimal("0.01")),
                              "expires_on": today + timedelta(days=90), "notes": "خصم 15% على الباكدج"}, owner)
        post_sales_invoice(first.invoice.id, user=owner)
        for days in (9, 2):
            use_session(first, owner, used_on=today - timedelta(days=days), employee=staff)
        sell_package({"customer": master["customers"][1], "service": services[-1], "sessions": 4}, owner)

    def _hours(self, master, owner):
        """SVC-003: a week of hours for two customers; one customer's are billed on a draft invoice, one more hour after it."""

        from agreements.hours import bill_hours, log_time
        from staff.models import Employee

        today = timezone.localdate()
        services = [item for item in master["items"] if not item.is_stock_tracked]
        if not services:
            return
        staff = Employee.objects.filter(active=True).order_by("pk").first()
        work = ((6, 0, "2", "مراجعة المستندات"), (5, 1, "1.5", "اجتماع مع العميل"), (4, 0, "3", "إعداد التقرير"),
                (3, 1, "2.5", "تعديلات مطلوبة"), (2, 0, "1", "متابعة"))
        for days, customer, hours, description in work:
            log_time({"customer": master["customers"][customer], "service": services[0], "employee": staff, "hours": hours,
                      "worked_on": today - timedelta(days=days), "description": description}, owner)
        bill_hours(master["customers"][0], owner)
        log_time({"customer": master["customers"][0], "service": services[0], "employee": staff, "hours": "1", "worked_on": today,
                  "description": "مكالمة متابعة"}, owner)

    def _orders(self, master, owner):
        from manufacturing import orders
        from manufacturing.models import Recipe

        today = timezone.localdate()
        recipes = list(Recipe.objects.filter(product__in=master["items"], active=True).order_by("product__item_code"))
        for n, recipe in enumerate(recipes[:3]):
            order = orders.create_order({"recipe": recipe, "location": master["location"], "quantity": str(10 + 5 * n),
                                         "customer": master["customers"][3] if n == 0 else None, "due_date": today + timedelta(days=5 + n * 3),
                                         "labor_cost": "500", "overhead_cost": "200"}, owner)
            for _ in range(2 - n):  # two stages in, one stage in, not started
                try:  # HG-036: starting issues the materials; a short one stays planned (a real shortage on the board)
                    orders.advance(order, owner)
                except ValidationError:
                    break

    def _batches(self, master, owner):
        from batches.services import register_batch, stock_on_hand

        today = timezone.localdate()
        on_hand = stock_on_hand([item.pk for item in master["items"]])
        # Two run out soon, so the expiry alerts have something true to say.
        for item, days in zip(master["items"], (25, 45, 300, 200, 400)):
            quantity = on_hand.get(item.pk) or Decimal("0")
            if quantity > 0:
                register_batch(item=item, location=master["location"], batch_no=f"LOT-{item.item_code[-2:]}{today:%y%m}",
                               expiry_date=today + timedelta(days=days), quantity=quantity, received_on=today - timedelta(days=20), user=owner)
