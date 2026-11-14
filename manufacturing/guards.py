"""Manufacturing F11b (HG-102): a stock operation that belongs to a production run or to a production order is not
reversed from the stock operations screen.

A run is one document made of several stock operations: its product coming in and each material going out. An order
on the floor holds the materials issued to it. Their own pages cancel them whole, under their own rules
(``services.cancel_run``, which refuses once the goods have left, HG-100; ``orders.cancel``). The screen
«التحويلات والتسويات» reverses one operation through the engine alone, which knows nothing of the run: it left the
run «posted» with its product gone, or a product in stock at the cost of materials that came back.

So the two places that reverse an operation a person names (``inventory.views.operation_cancel`` and the approval
that runs it later, ``approvals.actions``) ask here first, as the cashbox operations screen asks before it reverses a
gift card's, a cheque's or a payroll's cash operation (HG-071, HG-056, HG-060). The engine is not changed, and a
run's and an order's own cancellation, which call it directly, do not pass through here.

The rule holds where the owning page can open. In a business with the manufacturing module switched off (a
restaurant whose kitchen makes its dishes through ``restaurant.kitchen``), the run's page is closed by the module
gate. Since HG-107 a kitchen preparation is cancelled whole from its dish's page («إلغاء التحضير»), so there the screen
refuses a preparation's operations too and sends the person to the dish's page. Only where neither page can open (both
modules off after use) does the screen keep reversing them as before, so nobody is sent to a closed page.
"""

from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.urls import reverse

# In English, as the engine's own refusals are; ``settings_core.ui_messages`` holds the Arabic.
RUN_MESSAGE = ("This operation belongs to production run {number} and cannot be reversed from the transfers and adjustments screen. "
               "Cancel the run from its own page: its product leaves stock and all of its materials come back, together.")
ORDER_MESSAGE = ("This operation is a material issue of production order {number} and cannot be reversed from the transfers and adjustments screen. "
                 "Cancel the order from its own page: all of its materials come back together.")
DISH_MESSAGE = ("This operation belongs to the kitchen preparation {number} of “{dish}” and cannot be reversed from the transfers and adjustments screen. "
                "Cancel the preparation from the dish's page: its plates leave stock and all of their ingredients come back, together.")


@dataclass(frozen=True)
class Owner:
    """The document a stock operation belongs to, and its own page."""

    kind: str  # "run", "order" or (HG-107) "dish": a kitchen preparation, cancelled from its dish's page
    number: str
    url: str
    dish: str = ""  # the dish's name, for a kitchen preparation

    @property
    def refusal(self):
        if self.kind == "dish":
            return DISH_MESSAGE.format(number=self.number, dish=self.dish)
        return (RUN_MESSAGE if self.kind == "run" else ORDER_MESSAGE).format(number=self.number)


def owners(operation_ids):
    """{operation pk: Owner} for those of ``operation_ids`` that belong to a run or to an order.

    A finished order's materials are its run's materials too (``produce`` uses the operations the order issued), so
    they belong to the run, where they are reversed from then on."""

    return _owners(operation_ids)[0]


def _owners(operation_ids):
    """(``owners``, {operation pk: run pk} for those that belong to a run)."""

    from .models import OrderMaterialIssue, ProductionConsumption, ProductionRun

    ids = [int(pk) for pk in operation_ids]
    found, runs = {}, {}
    if not ids:
        return found, runs
    for operation, order, number in OrderMaterialIssue.objects.filter(operation_id__in=ids).values_list("operation_id", "order_id", "order__number"):
        found[operation] = Owner("order", number, reverse("manufacturing:order", args=[order]))
    for operation, run, number in ProductionConsumption.objects.filter(operation_id__in=ids).values_list("operation_id", "run_id", "run__number"):
        found[operation], runs[operation] = Owner("run", number, reverse("manufacturing:run", args=[run])), run
    for operation, run, number in ProductionRun.objects.filter(output_operation_id__in=ids).values_list("output_operation_id", "pk", "number"):
        found[operation], runs[operation] = Owner("run", number, reverse("manufacturing:run", args=[run])), run
    return found, runs


def pages_open():
    """Whether a run's and an order's own pages can open here (the module gate, ``settings_core.module_gate``)."""

    from settings_core.module_gate import closed_module

    return closed_module(reverse("manufacturing:home")) is None


def _kitchen_dishes(run_ids):
    """HG-107: {run pk: dish} for the runs that are kitchen preparations whose dish's page can open here."""

    from restaurant import kitchen

    if not run_ids or not kitchen.dish_pages_open():
        return {}
    return kitchen.preparation_dishes(run_ids)


def guarded(operation_ids):
    """``owners`` of the operations the stock screen must leave to their own document: where the manufacturing pages
    are open, the run or the order; where they are closed, a kitchen preparation whose dish's page can open (HG-107),
    sent to that page; none where no page that cancels it can open."""

    found, runs = _owners(operation_ids)
    if not found:
        return {}
    if pages_open():
        return found
    dishes = _kitchen_dishes(set(runs.values()))
    from restaurant.kitchen import dish_page

    return {operation: Owner("dish", owner.number, dish_page(dishes[runs[operation]], runs[operation]), dishes[runs[operation]].item_name)
            for operation, owner in found.items() if runs.get(operation) in dishes}


def run_page(run_id):
    """The page that cancels a run (FB-27's link and module check for «إلغاء تشغيلة إنتاج»): the run's own page, or,
    where the manufacturing pages are closed, its dish's page for a kitchen preparation (HG-107)."""

    own = reverse("manufacturing:run", args=[run_id])
    if pages_open():
        return own
    dish = _kitchen_dishes([run_id]).get(int(run_id))
    if dish is None:
        return own
    from restaurant.kitchen import dish_page

    return dish_page(dish, run_id)


def before_stock_operation_reversal(operation_id):
    """Called before the engine reverses an operation a person named. Refuses one that belongs to a run or an order."""

    owner = guarded([operation_id]).get(int(operation_id))
    if owner is not None:
        raise ValidationError(owner.refusal)
