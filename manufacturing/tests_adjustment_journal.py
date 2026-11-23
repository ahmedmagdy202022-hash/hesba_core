"""A characterization of how the general ledger books a stock adjustment today, shape by shape.

Written before Manufacturing F10 (HG-141) touched ``ledger/projector.py`` and left unedited by it: it passed on the
commit before the projector change and passes after it, so every stock operation that is not a spare part taken for a
maintenance job books exactly as it did.

Each shape pins the journal lines of the documents it makes, per account code and to the piastre, and the control
balances the scenario ends with:

* a plain adjustment out (a stock-count loss) and its reversal on a later day;
* a plain adjustment in at a typed cost (a stock-count gain) and its reversal on a later day;
* materials issued to a project site, and the reversal of that issue;
* a plain production run (its materials and its product);
* a maintenance expense paid from a cashbox (the expenses screen's «صيانة وإصلاحات»), so the account a maintenance
  expense lands in today is pinned beside the rest.

Every shape runs twice: on the manufacturing chart with the projects module on (110501 raw materials, 110502 work in
progress, 110503 finished goods, 5103 project costs) and on a restaurant bakery's chart with the manufacturing module
on and no projects module (one inventory account, 1105; work in progress and a project issue both fall back to the
cost of sales, 5101).

The stock the fixtures start with comes in through the test factory's bare movements, which the ledger books as a stock
gain (and the cashbox's opening money, a bare movement too, sits in suspense); both are pinned with the rest so nothing
else can move unseen.

The plant: an oven heating element 350.00 a piece (10 in stock), flour 20.00 a kilo (50), and a cake made of 2 kg of
flour for 10 cakes.
"""

import re
from datetime import timedelta
from decimal import Decimal as D

from django.test import TestCase
from django.utils import timezone

from cashboxes.models import CashboxDirection
from expenses.models import ExpenseCategory
from expenses.services import record_expense
from hesba_testing.factories import make_cashbox, make_cashbox_movement, make_customer, make_item, make_location, stock_in
from inventory.models import StockAdjustmentDirection
from inventory.services import adjust_stock, cancel_stock_operation
from ledger import reports as gl
from ledger.models import JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from projects import services as project_services
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled

from . import services
from .tests import person

TODAY = timezone.localdate()
BEFORE = TODAY - timedelta(days=3)
CONTROLS = ("inventory", "raw_materials", "wip", "finished_goods", "production_cost", "cogs", "project_cost", "stock_loss", "stock_gain",
            "general_expense", "cash", "suspense")


def lines(source_type, source_id, day):
    """[(days after ``day``, account code, debit, credit)] of every journal line of one document, after a rebuild."""

    Projector().rebuild()
    rows = (JournalLine.objects.filter(entry__source_type=source_type, entry__source_id=source_id)
            .select_related("account", "entry").order_by("entry__entry_date", "account__code", "debit", "credit"))
    return [((line.entry.entry_date - day).days, line.account.code, str(line.debit), str(line.credit)) for line in rows]


def journal(*operations):
    """{reference: lines} for stock operations (the date inside a generated reference is left out)."""

    return {re.sub(r"-\d{8}-", "-", operation.reference_number): lines("stock_operation", operation.pk, operation.operation_date) for operation in operations}


def controls():
    """Every control account of the list that holds anything, after a rebuild."""

    Projector().rebuild()
    return {key: str(value) for key in CONTROLS if (value := gl.control_balance(key))}


def account_balances(*codes):
    """The balance of accounts that have no control key (5206 has none), by code."""

    Projector().rebuild()
    found = {}
    for code in codes:
        debit = sum((line.debit - line.credit for line in JournalLine.objects.filter(account__code=code)), D("0"))
        if debit:
            found[code] = str(debit)
    return found


class FactoryAdjustments(TestCase):
    ACTIVITY, SUB = "manufacturing", "food"
    MODULES = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports,expenses,manufacturing,projects"

    START = {"raw_materials": "4500.00", "stock_gain": "-4500.00"}                     # 10 x 350.00 + 50 x 20.00
    OUT = {"ADJ-OUT": [(0, "110501", "0.00", "700.00"), (0, "5211", "700.00", "0.00")]}
    OUT_CONTROLS = {"raw_materials": "3800.00", "stock_loss": "700.00", "stock_gain": "-4500.00"}
    OUT_REVERSED = {"ADJ-OUT": [(0, "110501", "0.00", "700.00"), (0, "5211", "700.00", "0.00"), (3, "110501", "700.00", "0.00"), (3, "4202", "0.00", "700.00")]}
    OUT_REVERSED_CONTROLS = {"raw_materials": "4500.00", "stock_loss": "700.00", "stock_gain": "-5200.00"}
    IN = {"ADJ-IN": [(0, "110501", "720.00", "0.00"), (0, "4202", "0.00", "720.00")]}
    IN_CONTROLS = {"raw_materials": "5220.00", "stock_gain": "-5220.00"}
    IN_REVERSED = {"ADJ-IN": [(0, "110501", "720.00", "0.00"), (0, "4202", "0.00", "720.00"), (3, "110501", "0.00", "720.00"), (3, "5211", "720.00", "0.00")]}
    IN_REVERSED_CONTROLS = {"raw_materials": "4500.00", "stock_loss": "720.00", "stock_gain": "-5220.00"}
    SITE = [(0, "110501", "0.00", "1050.00"), (0, "5103", "1050.00", "0.00")]
    SITE_CONTROLS = {"raw_materials": "3450.00", "project_cost": "1050.00", "stock_gain": "-4500.00"}
    SITE_REVERSED = SITE + [(3, "110501", "1050.00", "0.00"), (3, "5103", "0.00", "1050.00")]
    SITE_REVERSED_CONTROLS = START
    RUN = {
        "MO-001-C01": [(0, "110501", "0.00", "120.00"), (0, "110502", "120.00", "0.00")],
        "MO-001-OUT": [(0, "110502", "0.00", "120.00"), (0, "110503", "120.00", "0.00")],
    }
    RUN_CONTROLS = {"raw_materials": "4380.00", "finished_goods": "120.00", "stock_gain": "-4500.00"}
    EXPENSE = [(0, "1101", "0.00", "500.00"), (0, "5206", "500.00", "0.00")]
    # The cashbox's opening 5,000.00 is a bare movement of the test factory with no document behind it: suspense holds it.
    EXPENSE_CONTROLS = {"raw_materials": "4500.00", "stock_gain": "-4500.00", "cash": "4500.00", "suspense": "-5000.00"}

    def setUp(self):
        self.profile = prepared_client(activity=self.ACTIVITY, sub_activity=self.SUB, modules=self.MODULES)
        set_capability_enabled(self.profile, "vat", False)
        self.owner = person(RoleCode.OWNER, "adj_owner")
        self.plant = make_location(location_code="PLANT", is_default=True)
        self.element = make_item(item_code="SP-HEAT", item_name="سخان فرن", is_stock_tracked=True)
        self.flour = make_item(item_code="RM-FLOUR", item_name="دقيق (كيلو)", is_stock_tracked=True)
        self.cake = make_item(item_code="FG-CAKE", item_name="كيكة", is_stock_tracked=True, default_sale_price="120")
        stock_in(self.element, self.plant, 10, "350.00", movement_date=BEFORE)
        stock_in(self.flour, self.plant, 50, "20.00", movement_date=BEFORE)

    def pinned(self, found, expected, balances):
        self.assertEqual(found, expected)
        self.assertEqual(controls(), balances)
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_the_stock_the_plant_starts_with(self):
        self.assertEqual(controls(), self.START)

    def test_a_plain_adjustment_out_and_its_reversal_on_a_later_day(self):
        operation = adjust_stock("ADJ-OUT", BEFORE, self.element, self.plant, StockAdjustmentDirection.OUT, "2", "جرد: عجز", self.owner)
        self.assertEqual(operation.unit_cost, D("350.0000"))
        self.pinned(journal(operation), self.OUT, self.OUT_CONTROLS)
        cancel_stock_operation(operation.pk, TODAY, "غلطة في التسجيل", self.owner)
        operation.refresh_from_db()
        self.pinned(journal(operation), self.OUT_REVERSED, self.OUT_REVERSED_CONTROLS)

    def test_a_plain_adjustment_in_and_its_reversal_on_a_later_day(self):
        operation = adjust_stock("ADJ-IN", BEFORE, self.element, self.plant, StockAdjustmentDirection.IN, "2", "جرد: زيادة", self.owner, unit_cost="360.00")
        self.pinned(journal(operation), self.IN, self.IN_CONTROLS)
        cancel_stock_operation(operation.pk, TODAY, "غلطة في التسجيل", self.owner)
        operation.refresh_from_db()
        self.pinned(journal(operation), self.IN_REVERSED, self.IN_REVERSED_CONTROLS)

    def test_materials_issued_to_a_project_site_and_the_reversal_of_the_issue(self):
        customer = make_customer(customer_code="C-SITE", name="م. حسام")
        project = project_services.save_project({"name": "تجهيز فرن العميل", "customer": customer, "contract_value": "10000"}, self.owner)
        operation = project_services.issue_materials(project, self.owner, item=self.element, location=self.plant, quantity="3", operation_date=BEFORE)
        self.pinned(lines("stock_operation", operation.pk, BEFORE), self.SITE, self.SITE_CONTROLS)
        cancel_stock_operation(operation.pk, TODAY, "غلطة في التسجيل", self.owner)
        self.pinned(lines("stock_operation", operation.pk, BEFORE), self.SITE_REVERSED, self.SITE_REVERSED_CONTROLS)

    def test_a_plain_production_run(self):
        recipe = services.save_recipe({"product": self.cake, "output_quantity": "10"}, [(self.flour, "2")], self.owner)
        run = services.produce(recipe, self.owner, batches="3", location=self.plant, run_date=BEFORE)
        operations = [consumption.operation for consumption in run.consumptions.select_related("operation")] + [run.output_operation]
        self.pinned(journal(*operations), self.RUN, self.RUN_CONTROLS)

    def test_a_maintenance_expense_paid_from_a_cashbox(self):
        cashbox = make_cashbox(cashbox_code="MAIN", is_default=True)
        make_cashbox_movement(cashbox, CashboxDirection.IN, "5000.00", movement_date=BEFORE)
        expense = record_expense(category=ExpenseCategory.objects.get(code="maintenance"), cashbox=cashbox, amount="500", expense_date=BEFORE,
                                 description="فني صيانة الفرن", user=self.owner)
        self.pinned(lines("cashbox_operation", expense.cashbox_operation_id, BEFORE), self.EXPENSE, self.EXPENSE_CONTROLS)
        self.assertEqual(account_balances("5206", "5211", "5299"), {"5206": "500.00"})


class BakeryAdjustments(FactoryAdjustments):
    """The same shapes on a restaurant bakery's chart with the manufacturing module on and no projects module: stock is
    one account (1105), and work in progress and a project issue both fall back to the cost of sales (5101)."""

    ACTIVITY, SUB = "restaurants", "bakery"
    MODULES = "customers,items_services,sales_operations,cashboxes,reports,inventory,expenses,pdf_printing,tables_orders,manufacturing"

    START = {"inventory": "4500.00", "stock_gain": "-4500.00"}
    OUT = {"ADJ-OUT": [(0, "1105", "0.00", "700.00"), (0, "5211", "700.00", "0.00")]}
    OUT_CONTROLS = {"inventory": "3800.00", "stock_loss": "700.00", "stock_gain": "-4500.00"}
    OUT_REVERSED = {"ADJ-OUT": [(0, "1105", "0.00", "700.00"), (0, "5211", "700.00", "0.00"), (3, "1105", "700.00", "0.00"), (3, "4202", "0.00", "700.00")]}
    OUT_REVERSED_CONTROLS = {"inventory": "4500.00", "stock_loss": "700.00", "stock_gain": "-5200.00"}
    IN = {"ADJ-IN": [(0, "1105", "720.00", "0.00"), (0, "4202", "0.00", "720.00")]}
    IN_CONTROLS = {"inventory": "5220.00", "stock_gain": "-5220.00"}
    IN_REVERSED = {"ADJ-IN": [(0, "1105", "720.00", "0.00"), (0, "4202", "0.00", "720.00"), (3, "1105", "0.00", "720.00"), (3, "5211", "720.00", "0.00")]}
    IN_REVERSED_CONTROLS = {"inventory": "4500.00", "stock_loss": "720.00", "stock_gain": "-5220.00"}
    SITE = [(0, "1105", "0.00", "1050.00"), (0, "5101", "1050.00", "0.00")]
    SITE_CONTROLS = {"inventory": "3450.00", "cogs": "1050.00", "stock_gain": "-4500.00"}
    SITE_REVERSED = SITE + [(3, "1105", "1050.00", "0.00"), (3, "5101", "0.00", "1050.00")]
    SITE_REVERSED_CONTROLS = START
    RUN = {
        "MO-001-C01": [(0, "1105", "0.00", "120.00"), (0, "5101", "120.00", "0.00")],
        "MO-001-OUT": [(0, "1105", "120.00", "0.00"), (0, "5101", "0.00", "120.00")],
    }
    RUN_CONTROLS = {"inventory": "4500.00", "stock_gain": "-4500.00"}
    EXPENSE_CONTROLS = {"inventory": "4500.00", "stock_gain": "-4500.00", "cash": "4500.00", "suspense": "-5000.00"}
