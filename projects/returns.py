"""Contracting C12 (HG-127): materials back from the site, and a multi-line issue voucher.

Rules, stated once:

* an issue and a return are both ordinary stock adjustments made through the inventory engine's own ``adjust_stock``,
  under the site engineer's stand-in ``projects.issue_to_site`` (HG-048), and both are linked to the project as a
  ``ProjectIssue``. The direction is the operation's own ``adjustment_direction`` (out = issued to the site, in = back
  from the site), so no column is added;
* **a return comes back at the project's own net issued cost of that item**, never today's average:
  (Σ issued value − Σ returned value) ÷ (Σ issued quantity − Σ returned quantity), over the project's posted
  operations of that item, whatever their location. Stock value and project cost then move by the same amount
  (trap 6). A return may not exceed the net issued quantity;
* the project's materials cost is issues less returns (``materials_cost``), each operation valued as the ledger
  values its movement (quantity × unit cost, rounded to the piaster), so the project's figures and account 5103 agree;
* a voucher writes one operation per line, all in one transaction; its lines share a reference
  ``PI-<code>-NNN/1..n`` (a return ``MR-<code>-NNN/1..n``). A voucher of one line keeps the plain ``PI-<code>-NNN``
  that issues have always had.

Nothing here moves money: no cashbox, customer or supplier ledger or sales row is touched.
"""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from config.money import cost_round, money_round

from .models import Project, ProjectIssue

ISSUE, RETURN = "PI", "MR"
MAX_LINES = 30
WORDS = {
    "ar": {
        "line": "السطر {n} ({item}): ", "no_lines": "اكتب صنف وكمية واحدة على الأقل.", "too_many": "الإذن الواحد لحد {n} سطر.",
        "not_issued": "الصنف ده ما اتصرفش للمشروع ده، فمينفعش يرجع منه.",
        "over": "المرتجع أكتر من الصافي المصروف للمشروع من الصنف ده: المتاح للارتجاع {available}.",
        "issue_reason": "صرف خامات لمشروع {code} / Materials for project {code}",
        "return_reason": "ارتجاع خامات من موقع مشروع {code} / Materials back from the site of project {code}",
    },
    "en": {
        "line": "Line {n} ({item}): ", "no_lines": "Enter at least one item and quantity.", "too_many": "One voucher takes up to {n} lines.",
        "not_issued": "This item was not issued to this project, so nothing of it can come back.",
        "over": "The return is more than the net quantity issued to this project: {available} can come back.",
        "issue_reason": "صرف خامات لمشروع {code} / Materials for project {code}",
        "return_reason": "ارتجاع خامات من موقع مشروع {code} / Materials back from the site of project {code}",
    },
}


def is_return(operation):
    from inventory.models import StockAdjustmentDirection

    return operation.adjustment_direction == StockAdjustmentDirection.IN


def signed_value(operation):
    """What the operation adds to the project's cost: its value as the ledger books it, less for a return."""

    value = money_round(operation.quantity * operation.unit_cost)
    return -value if is_return(operation) else value


def signed_quantity(operation):
    return -operation.quantity if is_return(operation) else operation.quantity


def posted_operations(project, scoped=True):
    """The project's posted issues and returns. ``scoped``: as the entity being worked in sees them (HG-034); an
    operation is the entity's when its source (an issue) or its destination (a return) is."""

    from inventory.models import StockOperationStatus

    from entities import scope as entity_scope

    links = project.issues.select_related("operation", "operation__item").filter(operation__status=StockOperationStatus.POSTED)
    if scoped:
        links = entity_scope.scope(links, tuple("operation__" + path for path in entity_scope.STOCK_OPERATION))
    return [link.operation for link in links]


def materials_cost(project):
    """The project's materials: issued less returned, from posted operations only. ``services.summary`` and
    ``costs.actual_by_heading`` both read this, so they never disagree."""

    return money_round(sum((signed_value(operation) for operation in posted_operations(project)), Decimal("0")))


def net_issued(project, item):
    """(net quantity, net value) of ``item`` issued to ``project``: every posted issue less every posted return, in every
    location and entity (the project's own cost basis, not the stock's)."""

    quantity, value = Decimal("0"), Decimal("0")
    for operation in posted_operations(project, scoped=False):
        if operation.item_id == item.pk:
            quantity += signed_quantity(operation)
            value += signed_value(operation)
    return quantity, value


#: In English, as the engine's own refusals are; ``settings_core.ui_messages`` holds the Arabic.
REVERSAL_MESSAGE = ("This operation is a material issue to project {code} and {returned} of it came back from the site after it. "
                    "Reverse those returns first, then the issue.")


def reversal_refusal(operation_id):
    """HG-127: the refusal for reversing a project issue that a later return of the same item was priced from, or None.

    A return comes in at the project's net issued cost as it stood when it was made, so every issue before it is part of
    that price. Reversing such an issue alone left the project's materials (and 5103) negative or off, with the returned
    units in stock on top of the reversed issue, even when the quantity still balanced (100 at 80.00 and 100 at 100.00,
    100 back at 90.00, then the 100.00 issue reversed: −1,000.00 with nothing left to return; Codex #434). So the returns
    made after it are reversed first. The two places that reverse an operation a person names ask
    ``manufacturing.guards`` first, and it asks here. A return, an issue with no later return of its item, and any other
    operation pass as before."""

    from inventory.models import StockAdjustmentDirection, StockOperation, StockOperationStatus

    from .models import ProjectIssue

    link = ProjectIssue.objects.select_related("project", "operation", "operation__item").filter(operation_id=operation_id).first()
    if link is None or link.operation.status != StockOperationStatus.POSTED:
        return None
    operation = link.operation
    if operation.adjustment_direction == StockAdjustmentDirection.IN:
        return None
    later = [op for op in posted_operations(link.project, scoped=False) if op.item_id == operation.item_id and is_return(op) and op.pk > operation.pk]
    if not later:
        return None
    return REVERSAL_MESSAGE.format(code=link.project.code, returned=_plain(sum((op.quantity for op in later), Decimal("0"))))


def _plain(quantity):
    return format(Decimal(quantity).normalize(), "f")


def return_cost(project, item):
    """The unit cost a return of ``item`` comes back at: the project's net issued cost of it."""

    quantity, value = net_issued(project, item)
    return cost_round(value / quantity) if quantity > 0 else None


def returnable(project):
    """[(item, net quantity)] of the items that can come back from the project's site, by item name."""

    by_item = {}
    for operation in posted_operations(project, scoped=False):
        item = operation.item
        by_item[item.pk] = (item, by_item.get(item.pk, (item, Decimal("0")))[1] + signed_quantity(operation))
    return sorted(((item, quantity) for item, quantity in by_item.values() if quantity > 0), key=lambda row: row[0].item_name)


def voucher_prefix(kind, project, number):
    return f"{kind}-{project.code}-{number:03d}"


def _next_number(project, kind):
    """The next voucher number of this kind on the project: the highest used plus one (not a count: issues and returns
    are both ``ProjectIssue`` rows, and a voucher has several). The caller holds the project's row lock."""

    start = f"{kind}-{project.code}-"
    highest = 0
    for reference in ProjectIssue.objects.filter(project=project, operation__reference_number__startswith=start).values_list(
            "operation__reference_number", flat=True):
        digits = reference[len(start):].split("/", 1)[0]
        if digits.isdigit():
            highest = max(highest, int(digits))
    return highest + 1


def voucher_operations(project, kind, number):
    """The operations of one voucher, in line order; empty when there is none."""

    prefix = voucher_prefix(kind, project, number)
    links = project.issues.select_related("operation", "operation__item", "operation__source_location", "operation__destination_location",
                                          "operation__created_by")
    operations = [link.operation for link in links if link.operation.reference_number == prefix
                  or link.operation.reference_number.startswith(prefix + "/")]

    def line(operation):
        tail = operation.reference_number[len(prefix) + 1:]
        return int(tail) if tail.isdigit() else 0

    return sorted(operations, key=line)


def parse_voucher_reference(reference):
    """(kind, number) of a voucher from one of its operations' reference, or None."""

    kind, _, rest = reference.partition("-")
    head = reference.split("/", 1)[0]
    digits = head.rsplit("-", 1)[-1]
    if kind not in (ISSUE, RETURN) or not rest or not digits.isdigit():
        return None
    return kind, int(digits)


def _quantity(raw):
    try:
        value = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        return Decimal("0")
    return value if value.is_finite() else Decimal("0")


def _clean_lines(lines, lang):
    from .services import MESSAGES

    words, base = WORDS[lang], MESSAGES[lang]
    if not lines:
        raise ValidationError(words["no_lines"])
    if len(lines) > MAX_LINES:
        raise ValidationError(words["too_many"].format(n=MAX_LINES))
    cleaned = []
    several = len(lines) > 1
    for n, line in enumerate(lines, start=1):
        item, quantity = line.get("item"), _quantity(line.get("quantity"))
        label = words["line"].format(n=n, item=item.item_name if item else "—") if several else ""
        if item is None:
            raise ValidationError(label + base["item"])
        if not item.is_stock_tracked:
            raise ValidationError(label + base["stock_item"])
        if quantity <= 0:
            raise ValidationError(label + base["qty"])
        cleaned.append((item, quantity.quantize(Decimal("0.001"))))
    return cleaned


def _engine(call, label, lang):
    """Run one line through the engine; on a refusal, name the line (several lines) in the screen's language."""

    from settings_core.ui_messages import translate

    try:
        return call()
    except ValidationError as exc:
        if not label:
            raise
        raise ValidationError(label + translate(" ".join(exc.messages), lang)) from exc


@transaction.atomic
def issue_voucher(project, user, *, location, lines, operation_date=None, lang="ar"):
    """Issue several items to the site in one voucher: one ``adjust_stock`` out per line, all or none."""

    from inventory.models import StockAdjustmentDirection
    from inventory.services import adjust_stock

    from .services import MESSAGES, _audit, _open

    project = _open(project, MESSAGES[lang])
    if location is None:
        raise ValidationError(MESSAGES[lang]["location"])
    cleaned = _clean_lines(lines, lang)
    day = operation_date or timezone.localdate()
    prefix = voucher_prefix(ISSUE, project, _next_number(project, ISSUE))
    several = len(cleaned) > 1
    operations = []
    for n, (item, quantity) in enumerate(cleaned, start=1):
        reference = f"{prefix}/{n}" if several else prefix
        label = WORDS[lang]["line"].format(n=n, item=item.item_name) if several else ""
        operation = _engine(lambda: adjust_stock(
            reference, day, item, location, StockAdjustmentDirection.OUT, quantity, WORDS[lang]["issue_reason"].format(code=project.code), user,
            stand_in="projects.issue_to_site",  # HG-048: a site engineer issues to the site without general stock adjustment
        ), label, lang)
        ProjectIssue.objects.create(project=project, operation=operation, created_by=user)
        operations.append(operation)
    if several:
        _audit(project, user, "issue_project_materials", {"voucher": prefix, "lines": [
            {"operation": op.reference_number, "item": op.item.item_code, "quantity": str(op.quantity)} for op in operations]})
    else:
        op = operations[0]
        _audit(project, user, "issue_project_materials", {"operation": op.reference_number, "item": op.item.item_code, "quantity": str(op.quantity)})
    return operations


@transaction.atomic
def return_materials(project, user, *, location, lines, operation_date=None, lang="ar"):
    """Bring surplus back from the site: one ``adjust_stock`` in per line, at the project's net issued cost of that item,
    all or none. Allowed whatever the project's status (surplus usually comes back at the end; HG-127 question 1)."""

    from inventory.models import StockAdjustmentDirection
    from inventory.services import adjust_stock

    from .services import MESSAGES, _audit

    words = WORDS[lang]
    project = Project.objects.select_for_update().get(pk=project.pk)
    if location is None:
        raise ValidationError(MESSAGES[lang]["location"])
    cleaned = _clean_lines(lines, lang)
    day = operation_date or timezone.localdate()
    prefix = voucher_prefix(RETURN, project, _next_number(project, RETURN))
    several = len(cleaned) > 1
    operations = []
    for n, (item, quantity) in enumerate(cleaned, start=1):
        label = words["line"].format(n=n, item=item.item_name) if several else ""
        # Read after the earlier lines were written, so two lines of one item cannot together exceed what was issued.
        net_quantity, net_value = net_issued(project, item)
        if net_quantity <= 0:
            raise ValidationError(label + words["not_issued"])
        if quantity > net_quantity:
            from settings_core.templatetags.hesba_format import qty

            raise ValidationError(label + words["over"].format(available=qty(net_quantity)))
        unit_cost = cost_round(net_value / net_quantity)
        reference = f"{prefix}/{n}" if several else prefix
        operation = _engine(lambda: adjust_stock(
            reference, day, item, location, StockAdjustmentDirection.IN, quantity, words["return_reason"].format(code=project.code), user,
            unit_cost=unit_cost,  # the project's own cost (trap 6), worked out here and never typed by a person
            stand_in="projects.issue_to_site",  # HG-048: the same stand-in as the issue
        ), label, lang)
        ProjectIssue.objects.create(project=project, operation=operation, created_by=user)
        operations.append(operation)
    _audit(project, user, "return_project_materials", {"voucher": prefix, "lines": [
        {"operation": op.reference_number, "item": op.item.item_code, "quantity": str(op.quantity), "unit_cost": str(op.unit_cost)}
        for op in operations]})
    return operations
