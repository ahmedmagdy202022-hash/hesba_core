"""GL-002 (HG-032): the general journal, projected from the posted sub-ledgers.

Hesba already records every posted effect in four authoritative sub-ledgers:
customer and supplier ledger entries, cashbox movements, and stock movements.
The projector reads those rows (and nothing else that moves money), groups
them by the document that caused them and the date, and turns each group into
one balanced journal entry:

* the sub-ledger rows give the receivable, payable, cash and inventory lines
  exactly, so those accounts reconcile with the existing reports by
  construction;
* the document type decides where the balancing side goes — revenue and VAT
  for a sale, COGS for the stock it took out, input VAT for a purchase, the
  expense account of an expense, gain or loss for a stock count, and so on.

Posting services are never called or changed. A cancelled document's reversal
rows carry their own date, so the cancellation appears as its own reversing
entry. Whatever cannot be placed lands in the suspense account, which the
reconciliation screen shows; it should always read zero.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from config.money import money_round

from . import services
from .models import Account, EntryKind, JournalEntry, JournalLine

ZERO = Decimal("0")
CENT = Decimal("0.01")

STOCK_IN = {"purchase_in", "sale_return_in", "transfer_in", "adjustment_in", "opening_stock"}

#: Sub-ledger foreign keys that name the document behind a row, in priority order.
CUSTOMER_KEYS = ("sales_invoice", "sales_return", "customer_payment", "opening_balance_adjustment")
SUPPLIER_KEYS = ("purchase_invoice", "purchase_return", "supplier_payment", "opening_balance_adjustment")
CASH_KEYS = ("sales_invoice", "sales_return", "customer_payment", "purchase_invoice", "purchase_return", "supplier_payment",
             "opening_balance_adjustment", "cashbox_operation")
STOCK_KEYS = ("sales_invoice", "sales_return", "purchase_invoice", "purchase_return", "stock_operation")


def _key(row, keys):
    for name in keys:
        value = getattr(row, f"{name}_id")
        if value:
            return name, value
    # A reversal row may carry only a pointer to the row it reverses.
    original = getattr(row, "reversal_of", None)
    if original is not None:
        return _key(original, keys)
    return None


class _Accounts:
    """Control key → Account, resolved once per rebuild."""

    def __init__(self):
        services.ensure_chart()
        self.by_control = {a.control: a for a in Account.objects.exclude(control="")}
        self.products = None

    def __getitem__(self, control):
        account = self.by_control.get(control)
        if account is None:
            account = self.by_control["suspense"]
        return account

    def has(self, control):
        return control in self.by_control

    def inventory(self, item_id):
        if self.has("inventory"):
            return self["inventory"]
        if self.products is None:
            from manufacturing.models import Recipe

            self.products = set(Recipe.objects.values_list("product_id", flat=True))
        return self["finished_goods" if item_id in self.products else "raw_materials"]


class _Group:
    """The rows of one document on one date, becoming one entry."""

    def __init__(self, source_type, source_id, day):
        self.source_type, self.source_id, self.day = source_type, source_id, day
        self.lines = []  # dicts: account, entity_id, amount (+debit / -credit), dims
        self.money = ZERO  # receivable + payable + cash lines, signed as debits
        self.stock = ZERO  # inventory lines, signed as debits
        self.entity_id = None

    def add(self, account, amount, entity_id, kind=None, **dims):
        amount = money_round(Decimal(amount))
        if not amount:
            return
        self.lines.append({"account": account, "amount": amount, "entity_id": entity_id, **dims})
        if kind == "money":
            self.money += amount
        elif kind == "stock":
            self.stock += amount
        if self.entity_id is None and entity_id:
            self.entity_id = entity_id

    def balance(self):
        return sum((line["amount"] for line in self.lines), ZERO)


class Projector:
    def __init__(self):
        self.acc = _Accounts()
        from entities.services import main_entity

        self.main_id = main_entity().pk
        self.groups = {}

    # ---- collecting sub-ledger rows ----

    def group(self, source_type, source_id, day):
        key = (source_type, source_id, day)
        if key not in self.groups:
            self.groups[key] = _Group(source_type, source_id, day)
        return self.groups[key]

    def collect(self):
        from cashboxes.models import CashboxMovement
        from inventory.models import StockMovement
        from purchases.models import SupplierLedgerEntry
        from sales.models import CustomerLedgerEntry

        self._doc_entity = {}
        for row in CustomerLedgerEntry.objects.select_related("sales_invoice__selling_location", "customer_payment__cashbox", "sales_return__source_invoice__selling_location"):
            source = _key(row, CUSTOMER_KEYS) or ("customer_entry", row.pk)
            amount = (row.due_increase or ZERO) - (row.due_decrease or ZERO)
            entity = self._entity_of_customer_row(row)
            self.group(*source, row.entry_date).add(self.acc["receivable"], amount, entity, "money", customer_id=row.customer_id)
        for row in SupplierLedgerEntry.objects.select_related("purchase_invoice__receiving_location", "supplier_payment__cashbox", "purchase_return__source_invoice__receiving_location"):
            source = _key(row, SUPPLIER_KEYS) or ("supplier_entry", row.pk)
            amount = (row.due_increase or ZERO) - (row.due_decrease or ZERO)
            entity = self._entity_of_supplier_row(row)
            self.group(*source, row.entry_date).add(self.acc["payable"], -amount, entity, "money", supplier_id=row.supplier_id)
        for row in CashboxMovement.objects.select_related("cashbox", "reversal_of"):
            source = _key(row, CASH_KEYS) or ("cash_movement", row.pk)
            amount = row.amount if row.direction == "in" else -row.amount
            self.group(*source, row.movement_date).add(self.acc["cash"], amount, row.cashbox.entity_id or self.main_id, "money", cashbox_id=row.cashbox_id)
        for row in StockMovement.objects.select_related("location", "reversal_of"):
            source = _key(row, STOCK_KEYS) or ("opening_stock" if row.movement_type == "opening_stock" else "stock_movement", row.pk)
            value = money_round(row.quantity * row.unit_cost)
            amount = value if row.movement_type in STOCK_IN else -value
            self.group(*source, row.movement_date).add(self.acc.inventory(row.item_id), amount, row.location.entity_id or self.main_id, "stock", location_id=row.location_id)
        self._openings()
        self._fixed_assets()

    def _entity_of_customer_row(self, row):
        location = None
        if row.sales_invoice_id:
            location = row.sales_invoice.selling_location
        elif row.sales_return_id:
            location = row.sales_return.source_invoice.selling_location
        elif row.customer_payment_id:
            return row.customer_payment.cashbox.entity_id or self.main_id
        return (location.entity_id if location else None) or self.main_id

    def _entity_of_supplier_row(self, row):
        location = None
        if row.purchase_invoice_id:
            location = row.purchase_invoice.receiving_location
        elif row.purchase_return_id:
            location = row.purchase_return.source_invoice.receiving_location
        elif row.supplier_payment_id:
            return row.supplier_payment.cashbox.entity_id or self.main_id
        return (location.entity_id if location else None) or self.main_id

    def _openings(self):
        """Opening balances typed on the customer, supplier and cashbox records."""

        from cashboxes.models import Cashbox
        from master_data.models import Customer, Supplier

        for customer in Customer.objects.exclude(opening_balance=0):
            g = self.group("opening_customer", customer.pk, timezone.localdate(customer.created_at))
            g.add(self.acc["receivable"], customer.opening_balance, self.main_id, "money", customer_id=customer.pk)
        for supplier in Supplier.objects.exclude(opening_balance=0):
            g = self.group("opening_supplier", supplier.pk, timezone.localdate(supplier.created_at))
            g.add(self.acc["payable"], -supplier.opening_balance, self.main_id, "money", supplier_id=supplier.pk)
        for cashbox in Cashbox.objects.exclude(opening_balance=0):
            g = self.group("opening_cashbox", cashbox.pk, timezone.localdate(cashbox.created_at))
            g.add(self.acc["cash"], cashbox.opening_balance, cashbox.entity_id or self.main_id, "money", cashbox_id=cashbox.pk)

    def _fixed_assets(self):
        from fixed_assets.models import AssetStatus, FixedAsset
        from fixed_assets.services import accumulated, charged_months

        today = timezone.localdate()
        self._asset_ops = {}
        for asset in FixedAsset.objects.exclude(status=AssetStatus.CANCELLED):
            if asset.payment_operation_id:
                self._asset_ops[asset.payment_operation_id] = ("purchase", asset)
            else:
                g = self.group("asset_opening", asset.pk, asset.in_service_on)
                g.add(self.acc["fixed_assets"], asset.cost, self.main_id)
                g.add(self.acc["opening_equity"], -Decimal(asset.cost), self.main_id)
            for month, amount in charged_months(asset):
                if month > today:
                    break
                g = self.group("depreciation", asset.pk, month)
                g.add(self.acc["depreciation"], amount, self.main_id)
                g.add(self.acc["accumulated_depreciation"], -amount, self.main_id)
            if asset.status == AssetStatus.DISPOSED and asset.disposed_on:
                if asset.disposal_operation_id:
                    self._asset_ops[asset.disposal_operation_id] = ("disposal", asset)
                g = self.group("asset_disposal", asset.pk, asset.disposed_on)
                g.add(self.acc["fixed_assets"], -Decimal(asset.cost), self.main_id)
                g.add(self.acc["accumulated_depreciation"], accumulated(asset, asset.disposed_on), self.main_id)
                g.add(self.acc["asset_disposal"], -g.balance(), self.main_id)

    # ---- placing the balancing side ----

    def place(self, g):
        handler = getattr(self, f"_place_{g.source_type}", None)
        if handler is not None:
            handler(g)
        remainder = g.balance()
        if remainder:
            g.add(self.acc["suspense"], -remainder, g.entity_id or self.main_id)

    @staticmethod
    def _split_tax(money, tax, total):
        if not total:
            return ZERO
        return money_round(money * Decimal(tax) / Decimal(total))

    def _place_sales_invoice(self, g):
        from sales.models import SalesInvoice

        inv = SalesInvoice.objects.get(pk=g.source_id)
        tax = self._split_tax(g.money, inv.tax_amount, inv.total_amount)
        g.add(self.acc["vat_out"], -tax, g.entity_id)
        g.add(self.acc["sales"], -(g.money - tax), g.entity_id)
        g.add(self.acc["cogs"], -g.stock, g.entity_id)

    def _place_sales_return(self, g):
        from sales.models import SalesReturn
        from taxes.models import SalesReturnLineTax

        ret = SalesReturn.objects.get(pk=g.source_id)
        returned_tax = SalesReturnLineTax.objects.filter(return_line__sales_return=ret).aggregate(t=Sum("tax_amount"))["t"] or ZERO
        tax = self._split_tax(g.money, returned_tax, ret.total_amount)
        g.add(self.acc["vat_out"], -tax, g.entity_id)
        g.add(self.acc["sales_returns"], -(g.money - tax), g.entity_id)
        g.add(self.acc["cogs"], -g.stock, g.entity_id)

    def _place_purchase_invoice(self, g):
        from purchases.models import PurchaseInvoice

        inv = PurchaseInvoice.objects.get(pk=g.source_id)
        tax = self._split_tax(g.money, inv.tax_amount, inv.total_amount)
        g.add(self.acc["vat_in"], -tax, g.entity_id)
        # Whatever the stock lines do not carry was bought as a service or expense.
        g.add(self.acc["general_expense"], -(g.money - tax) - g.stock, g.entity_id)

    def _place_purchase_return(self, g):
        from purchases.models import PurchaseReturn
        from taxes.models import PurchaseReturnLineTax

        ret = PurchaseReturn.objects.get(pk=g.source_id)
        returned_tax = PurchaseReturnLineTax.objects.filter(return_line__purchase_return=ret).aggregate(t=Sum("tax_amount"))["t"] or ZERO
        tax = self._split_tax(g.money, returned_tax, ret.total_amount)
        g.add(self.acc["vat_in"], -tax, g.entity_id)
        g.add(self.acc["general_expense"], -(g.money - tax) - g.stock, g.entity_id)

    def _place_opening_balance_adjustment(self, g):
        g.add(self.acc["opening_equity"], -g.balance(), g.entity_id)

    _place_opening_customer = _place_opening_supplier = _place_opening_cashbox = _place_opening_stock = _place_opening_balance_adjustment

    def _place_cashbox_operation(self, g):
        from cashboxes.models import CashboxOperation

        op = CashboxOperation.objects.get(pk=g.source_id)
        expense = getattr(op, "expense", None) if hasattr(op, "expense") else None
        asset = self._asset_ops.get(op.pk)
        remainder = -g.balance()
        if expense is not None:
            link = getattr(expense.category, "ledger_link", None)
            account = link.account if link else self.acc["general_expense"]
            g.add(account, remainder, g.entity_id)
        elif asset is not None and asset[0] == "purchase":
            g.add(self.acc["fixed_assets"], remainder, g.entity_id)
        elif asset is not None and asset[0] == "disposal":
            g.add(self.acc["asset_disposal"], remainder, g.entity_id)
        elif remainder:
            g.add(self.acc["owner_drawings"], remainder, g.entity_id)

    def _place_stock_operation(self, g):
        from inventory.models import StockOperation

        op = StockOperation.objects.get(pk=g.source_id)
        remainder = -g.balance()
        if not remainder:
            return
        if hasattr(op, "production_consumption") or hasattr(op, "production_output"):
            account = self.acc["wip"] if self.acc.has("wip") else self.acc["cogs"]
        elif hasattr(op, "project_issue"):
            account = self.acc["project_cost"] if self.acc.has("project_cost") else self.acc["cogs"]
        else:
            account = self.acc["stock_loss"] if remainder > 0 else self.acc["stock_gain"]
        g.add(account, remainder, g.entity_id)

    def _place_stock_movement(self, g):
        remainder = -g.balance()
        g.add(self.acc["stock_loss"] if remainder > 0 else self.acc["stock_gain"], remainder, g.entity_id)

    # ---- entities ----

    def balance_entities(self, g):
        """A document that moves value between entities leaves each one owing
        or owed: the difference goes to the due-between-entities account."""

        by_entity = defaultdict(Decimal)
        for line in g.lines:
            line["entity_id"] = line["entity_id"] or g.entity_id or self.main_id
            by_entity[line["entity_id"]] += line["amount"]
        if len([e for e, v in by_entity.items() if v]) > 1:
            for entity_id, value in by_entity.items():
                if value:
                    g.add(self.acc["intercompany"], -value, entity_id)

    # ---- writing ----

    def rebuild(self):
        self.collect()
        entries = []
        for key in sorted(self.groups, key=lambda k: (k[2], k[0], k[1])):
            g = self.groups[key]
            if not g.lines:
                continue
            g.entity_id = g.entity_id or self.main_id
            self.place(g)
            self.balance_entities(g)
            entries.append(g)
        with transaction.atomic():
            JournalEntry.objects.filter(kind=EntryKind.PROJECTED).delete()
            for g in entries:
                entry = JournalEntry.objects.create(entry_date=g.day, kind=EntryKind.PROJECTED, source_type=g.source_type,
                                                    source_id=g.source_id, reference=_reference(g))
                JournalLine.objects.bulk_create([
                    JournalLine(entry=entry, account=line["account"], entity_id=line["entity_id"],
                                debit=line["amount"] if line["amount"] > 0 else ZERO, credit=-line["amount"] if line["amount"] < 0 else ZERO,
                                customer_id=line.get("customer_id"), supplier_id=line.get("supplier_id"),
                                cashbox_id=line.get("cashbox_id"), location_id=line.get("location_id"))
                    for line in g.lines if line["amount"]
                ])
        return len(entries)


def _reference(g):
    from cashboxes.models import CashboxOperation, OpeningBalanceAdjustment
    from inventory.models import StockOperation
    from purchases.models import PurchaseInvoice, PurchaseReturn, SupplierPayment
    from sales.models import CustomerPayment, SalesInvoice, SalesReturn

    lookup = {
        "sales_invoice": (SalesInvoice, "invoice_number"), "sales_return": (SalesReturn, "return_number"),
        "customer_payment": (CustomerPayment, "payment_number"), "purchase_invoice": (PurchaseInvoice, "invoice_number"),
        "purchase_return": (PurchaseReturn, "return_number"), "supplier_payment": (SupplierPayment, "payment_number"),
        "cashbox_operation": (CashboxOperation, "reference_number"), "stock_operation": (StockOperation, "reference_number"),
        "opening_balance_adjustment": (OpeningBalanceAdjustment, "adjustment_number"),
    }
    if g.source_type in lookup:
        model, field = lookup[g.source_type]
        return model.objects.filter(pk=g.source_id).values_list(field, flat=True).first() or ""
    return f"{g.source_type}-{g.source_id}"


# ---- freshness ----

def fingerprint():
    """Changes whenever anything the projection reads changes."""

    from cashboxes.models import Cashbox, CashboxMovement
    from fixed_assets.models import FixedAsset
    from inventory.models import StockMovement
    from master_data.models import Customer, Supplier
    from purchases.models import SupplierLedgerEntry
    from sales.models import CustomerLedgerEntry

    parts = []
    for model in (CustomerLedgerEntry, SupplierLedgerEntry, CashboxMovement, StockMovement):
        agg = model.objects.aggregate(n=Max("pk"))
        parts.append(f"{model.objects.count()}:{agg['n']}")
    for model in (Customer, Supplier, Cashbox):
        parts.append(str(model.objects.aggregate(s=Sum("opening_balance"))["s"]))
    parts.append(";".join(f"{a.pk}{a.status}{a.disposed_on}" for a in FixedAsset.objects.order_by("pk")))
    parts.append(timezone.localdate().strftime("%Y-%m"))  # depreciation months roll over
    parts.append(str(Account.objects.count()))
    return "|".join(parts)


def ensure_fresh():
    """Rebuild the projected journal if any source changed since the last build."""

    from settings_core.models import SystemSetting

    from . import services

    services.ensure_chart()
    current = fingerprint()
    stored = SystemSetting.objects.filter(key="ledger.fingerprint").values_list("value", flat=True).first()
    if stored == current:
        return False
    Projector().rebuild()
    SystemSetting.objects.update_or_create(key="ledger.fingerprint", defaults={"value": current, "description": "GL-002 projection fingerprint", "active": True})
    return True
