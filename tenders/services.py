"""Contracting C8 (HG-126): the rules of a tender study. Nothing here moves money.

* **Price analysis.** An item's direct unit cost is the sum of its cost lines (each per unit of the item). Its unit rate
  is direct × (1 + overhead%) × (1 + profit%), rounded once to 0.01 (half up). Its total is quantity × rate rounded to
  0.01, exactly as ``projects.contract.line_amount`` prices a line of the bill of quantities, so the tender total is the
  won project's bill of quantities total to the piastre.
* **By heading.** A heading's cost on the whole tender is Σ(unit cost × quantity) over the items, rounded to 0.01; the
  same figure is the won project's budget for that heading.
* **Won («اترسّت علينا»).** In one transaction, through the unchanged ``projects`` services: the project
  (``services.save_project``, the tender total as its contract value), its bill of quantities at the analysed rates
  (``contract.save_boq_line``) and its budget per heading (``costs.save_budget``). If any of them refuses, nothing is
  kept. The tender then points at the project and is read-only.
* **Lost / withdrawn** need a reason and are read-only too. Only a tender under study or submitted takes a change.
* **Who.** Opening and changing tenders: ``sales.create_sales_invoice`` (the project office, as the projects' own
  office actions). The cost lines, the markups, the summary by heading and the internal analysis: also
  ``reports.view_profit_report`` (the right that shows a project's budget). A site engineer holds neither. No permission
  code is new.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope
from permissions.services import user_has_permission
from projects.models import CostHeading

from .models import OPEN_STATUSES, Tender, TenderCost, TenderItem, TenderStatus

ZERO = Decimal("0")
HUNDRED = Decimal("100")
QTY = Decimal("0.001")
MANAGE = "sales.create_sales_invoice"
PROFIT = "reports.view_profit_report"
#: Upper bounds that keep a tender readable and inside its fields.
MAX_ITEMS = 500
MAX_COSTS = 30
LIMIT = Decimal("1000000000")
#: The largest amount a project's money fields hold (14 digits, 2 after the point): the won total and each heading must fit.
FIELD_MAX = Decimal("999999999999.99")

LABELS = {
    "ar": {
        "statuses": {"studying": "تحت الدراسة", "submitted": "اتقدّم", "won": "اترسّت علينا", "lost": "مارسيتش علينا", "withdrawn": "انسحبنا"},
        "headings": {"materials": "خامات", "subcontract": "مقاولو باطن", "labour": "عمالة", "equipment": "معدات", "other": "أخرى"},
    },
    "en": {
        "statuses": {"studying": "Under study", "submitted": "Submitted", "won": "Won", "lost": "Lost", "withdrawn": "Withdrawn"},
        "headings": {"materials": "Materials", "subcontract": "Subcontractors", "labour": "Labour", "equipment": "Equipment", "other": "Other"},
    },
}

MESSAGES = {
    "ar": {
        "denied": "مش معاك صلاحية تدير العطاءات.", "denied_costs": "تحليل التكلفة محتاج صلاحية تقرير الأرباح.",
        "name": "اكتب اسم العطاء.", "party": "اختار العميل أو اكتب اسم الجهة صاحبة العطاء.", "customer": "العميل ده مش موجود.",
        "due": "اكتب ميعاد التقديم صح.", "percent": "النسبة لازم تكون رقم من 0 لـ 100.",
        "closed": "العطاء ده اتقفل ({status})، فمينفعش يتعدّل.", "description": "اكتب وصف البند.",
        "qty": "الكمية لازم تكون رقم أكبر من صفر.", "too_big": "الرقم أكبر من المسموح.", "items_limit": "العطاء وصل لأقصى عدد بنود ({max}).",
        "heading": "اختار بند التكلفة (خامات، عمالة، معدات، ...).", "unit_cost": "تكلفة الوحدة لازم تكون رقم مش سالب.",
        "costs_limit": "البند وصل لأقصى عدد سطور تكلفة ({max}).", "no_items": "العطاء مافيهوش بنود لسه.",
        "unpriced": "البند {line} ({description}) مالوش تكلفة لسه؛ سعّره الأول.", "win_customer": "اختار العميل اللي هيتعمل له المشروع.", "too_big": "إجمالي العطاء أو أحد بنود التكلفة أكبر من أقصى قيمة يشيلها المشروع (999,999,999,999.99)؛ راجع الكميات والتكاليف.",
        "reason": "اكتب السبب.", "not_studying": "العطاء اتقدّم بالفعل.",
    },
    "en": {
        "denied": "You do not have the permission to manage tenders.", "denied_costs": "The cost analysis needs the profit report permission.",
        "name": "Type the tender's name.", "party": "Choose the customer or type the name of the party inviting the tender.", "customer": "That customer does not exist.",
        "due": "Type a valid submission date.", "percent": "The percent must be a number from 0 to 100.",
        "closed": "This tender is closed ({status}), so it cannot change.", "description": "Describe the item.",
        "qty": "The quantity must be a number above zero.", "too_big": "The number is larger than allowed.", "items_limit": "The tender has reached the most items allowed ({max}).",
        "heading": "Choose the cost heading (materials, labour, equipment, ...).", "unit_cost": "The unit cost must be a number, not negative.",
        "costs_limit": "The item has reached the most cost lines allowed ({max}).", "no_items": "The tender has no items yet.",
        "unpriced": "Item {line} ({description}) has no cost yet; price it first.", "win_customer": "Choose the customer the project is for.", "too_big": "The tender total or one cost heading is larger than a project can hold (999,999,999,999.99); check the quantities and costs.",
        "reason": "Type the reason.", "not_studying": "The tender has already been submitted.",
    },
}


def today():
    """The one place «today» comes from, so a test can fix the date."""

    return timezone.localdate()


def _lang(lang):
    return "en" if lang == "en" else "ar"


def label(group, value, lang="ar"):
    return LABELS[_lang(lang)][group].get(value, value)


# ---- who ------------------------------------------------------------------------------------------------------------


def can_manage(user):
    return user_has_permission(user, MANAGE)


def can_see_costs(user):
    """The cost lines, the markups and the analysis: the tender office with the profit report right."""

    return can_manage(user) and user_has_permission(user, PROFIT)


def _require(user, lang, costs=False):
    words = MESSAGES[_lang(lang)]
    if not can_manage(user):
        raise PermissionDenied(words["denied"])
    if costs and not user_has_permission(user, PROFIT):
        raise PermissionDenied(words["denied_costs"])


# ---- what -----------------------------------------------------------------------------------------------------------


def scoped(queryset=None):
    """The tenders of the entity being worked in (HG-034; NULL is the main entity)."""

    queryset = Tender.objects.all() if queryset is None else queryset
    return entity_scope.scope(queryset, "entity")


def _decimal(value):
    try:
        number = Decimal(str(value if value not in (None, "") else "").strip().replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None
    return number if number.is_finite() else None


def _percent(value, words):
    number = _decimal(value if value not in (None, "") else "0")
    if number is None or number < 0 or number > HUNDRED:
        raise ValidationError(words["percent"])
    return number.quantize(Decimal("0.01"))


def _quantity(value, words):
    number = _decimal(value)
    if number is None or number <= 0:
        raise ValidationError(words["qty"])
    number = number.quantize(QTY)
    if number <= 0:
        raise ValidationError(words["qty"])
    if number >= LIMIT:
        raise ValidationError(words["too_big"])
    return number


def _unit_cost(value, words):
    number = _decimal(value)
    if number is None or number < 0:
        raise ValidationError(words["unit_cost"])
    number = money_round(number)
    if number >= LIMIT:
        raise ValidationError(words["too_big"])
    return number


def _audit(tender, user, action, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="tenders", action=action, object_type="tenders.Tender",
                            object_id=str(tender.pk), before_data=before or {}, after_data=after)


def _locked(tender, lang):
    """The tender row, locked, refused unless it is still being priced. No ``select_related`` here: both of its nullable
    foreign keys would put the lock on the nullable side of an outer join, which PostgreSQL refuses."""

    tender = Tender.objects.select_for_update().get(pk=tender.pk)
    if tender.status not in OPEN_STATUSES:
        words = MESSAGES[_lang(lang)]
        raise ValidationError(words["closed"].format(status=label("statuses", tender.status, lang)))
    return tender


# ---- the tender -----------------------------------------------------------------------------------------------------


@transaction.atomic
def save_tender(data, user, tender=None, lang="ar"):
    """Create or change a tender. ``data`` holds ``name``, ``customer`` (a Customer or None), ``prospect_name``,
    ``due_on`` (a date, a 'YYYY-MM-DD' string or empty), ``overhead_percent``, ``profit_percent`` and ``notes``. The two
    percents are read only from someone who may see costs; for anyone else they keep their stored values."""

    lang = _lang(lang)
    words = MESSAGES[lang]
    _require(user, lang)
    name = (data.get("name") or "").strip()[:255]
    if not name:
        raise ValidationError(words["name"])
    customer = data.get("customer")
    prospect = (data.get("prospect_name") or "").strip()[:255]
    if customer is None and not prospect:
        raise ValidationError(words["party"])
    due = data.get("due_on")
    if isinstance(due, str):
        from django.utils.dateparse import parse_date

        raw = due.strip()
        try:
            due = parse_date(raw) if raw else None
        except ValueError:
            due = None
        if raw and due is None:
            raise ValidationError(words["due"])
    created = tender is None
    if not created:
        tender = _locked(tender, lang)
    costs = can_see_costs(user)
    overhead = _percent(data.get("overhead_percent"), words) if costs else (tender.overhead_percent if tender else ZERO)
    profit = _percent(data.get("profit_percent"), words) if costs else (tender.profit_percent if tender else ZERO)
    values = {"name": name, "customer": customer, "prospect_name": "" if customer is not None else prospect, "due_on": due,
              "overhead_percent": overhead, "profit_percent": profit, "notes": (data.get("notes") or "").strip()[:2000]}
    before = {} if created else {key: str(getattr(tender, key)) for key in values}
    if created:
        tender = Tender(created_by=user, entity=entity_scope.entity())
    for key, value in values.items():
        setattr(tender, key, value)
    tender.save()
    _audit(tender, user, "create_tender" if created else "change_tender", {key: str(value) for key, value in values.items()}, before,
           AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return tender


# ---- items ----------------------------------------------------------------------------------------------------------


@transaction.atomic
def save_item(tender, user, data, item=None, lang="ar"):
    lang = _lang(lang)
    words = MESSAGES[lang]
    _require(user, lang)
    tender = _locked(tender, lang)
    description = (data.get("description") or "").strip()[:255]
    if not description:
        raise ValidationError(words["description"])
    values = {"code": (data.get("code") or "").strip()[:20], "description": description, "unit": (data.get("unit") or "").strip()[:30],
              "quantity": _quantity(data.get("quantity"), words)}
    created = item is None
    if created:
        if tender.items.count() >= MAX_ITEMS:
            raise ValidationError(words["items_limit"].format(max=MAX_ITEMS))
        top = tender.items.order_by("-line_number").values_list("line_number", flat=True).first() or 0
        item = TenderItem(tender=tender, line_number=top + 1)
    else:
        item = TenderItem.objects.select_for_update().get(pk=item.pk, tender=tender)
    for key, value in values.items():
        setattr(item, key, value)
    item.save()
    _audit(tender, user, "add_tender_item" if created else "change_tender_item",
           {"line": item.line_number, **{key: str(value) for key, value in values.items()}}, event=AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return item


@transaction.atomic
def delete_item(tender, item, user, lang="ar"):
    lang = _lang(lang)
    _require(user, lang)
    tender = _locked(tender, lang)
    item = TenderItem.objects.select_for_update().get(pk=item.pk, tender=tender)
    after = {"line": item.line_number, "description": item.description, "costs": item.costs.count()}
    item.delete()  # its cost lines go with it: they are the tender's own working rows
    _audit(tender, user, "delete_tender_item", after, event=AuditEventType.DELETE)


# ---- cost lines -----------------------------------------------------------------------------------------------------


@transaction.atomic
def save_cost(item, user, data, cost=None, lang="ar"):
    lang = _lang(lang)
    words = MESSAGES[lang]
    _require(user, lang, costs=True)
    tender = _locked(item.tender, lang)
    item = TenderItem.objects.select_for_update().get(pk=item.pk, tender=tender)
    heading = data.get("heading")
    if heading not in CostHeading.values:
        raise ValidationError(words["heading"])
    values = {"heading": heading, "description": (data.get("description") or "").strip()[:255], "unit_cost": _unit_cost(data.get("unit_cost"), words)}
    created = cost is None
    if created:
        if item.costs.count() >= MAX_COSTS:
            raise ValidationError(words["costs_limit"].format(max=MAX_COSTS))
        cost = TenderCost(item=item)
    else:
        cost = TenderCost.objects.select_for_update().get(pk=cost.pk, item=item)
    for key, value in values.items():
        setattr(cost, key, value)
    cost.save()
    _audit(tender, user, "add_tender_cost" if created else "change_tender_cost",
           {"line": item.line_number, **{key: str(value) for key, value in values.items()}}, event=AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return cost


@transaction.atomic
def delete_cost(cost, user, lang="ar"):
    lang = _lang(lang)
    _require(user, lang, costs=True)
    tender = _locked(cost.item.tender, lang)
    cost = TenderCost.objects.select_for_update().get(pk=cost.pk, item__tender=tender)
    after = {"line": cost.item.line_number, "heading": cost.heading, "unit_cost": str(cost.unit_cost)}
    cost.delete()
    _audit(tender, user, "delete_tender_cost", after, event=AuditEventType.DELETE)


# ---- the price analysis ---------------------------------------------------------------------------------------------


def _factor(percent):
    return 1 + Decimal(percent) / HUNDRED


def price_item(item, overhead_percent, profit_percent, costs=None):
    """One item's analysis: its direct unit cost, after overhead, its unit rate and its total.

    The rate is computed from the unrounded product and rounded once; «after overhead» is shown rounded for reading."""

    costs = list(item.costs.all()) if costs is None else costs
    direct = money_round(sum((Decimal(cost.unit_cost) for cost in costs), ZERO))
    with_overhead = direct * _factor(overhead_percent)
    rate = money_round(with_overhead * _factor(profit_percent))
    return {"item": item, "costs": costs, "direct": direct, "with_overhead": money_round(with_overhead), "rate": rate,
            "total": money_round(Decimal(item.quantity) * rate)}


def heading_amounts(rows):
    """Σ(unit cost × quantity) per heading over the priced rows, each rounded once to 0.01; the won project's budget."""

    sums = defaultdict(lambda: ZERO)
    for row in rows:
        for cost in row["costs"]:
            sums[cost.heading] += Decimal(cost.unit_cost) * Decimal(row["item"].quantity)
    return {heading: money_round(sums[heading]) for heading in CostHeading.values if heading in sums}


def analysis(tender, lang="ar"):
    """The whole tender priced: its rows (one per item), the total, the direct cost by heading, overhead and profit."""

    items = list(tender.items.prefetch_related("costs"))
    rows = [price_item(item, tender.overhead_percent, tender.profit_percent, list(item.costs.all())) for item in items]
    total = money_round(sum((row["total"] for row in rows), ZERO))
    by_heading = heading_amounts(rows)
    direct = money_round(sum(by_heading.values(), ZERO))
    overhead = money_round(direct * Decimal(tender.overhead_percent) / HUNDRED)
    headings = [{"heading": heading, "label": label("headings", heading, lang), "amount": amount,
                 "share": (amount / direct * HUNDRED).quantize(Decimal("0.1")) if direct else ZERO} for heading, amount in by_heading.items()]
    return {"rows": rows, "total": total, "by_heading": by_heading, "headings": headings, "direct": direct, "overhead": overhead,
            "profit": money_round(total - direct - overhead), "unpriced": [row for row in rows if row["direct"] <= 0]}


# ---- status ---------------------------------------------------------------------------------------------------------


@transaction.atomic
def submit(tender, user, on=None, lang="ar"):
    lang = _lang(lang)
    words = MESSAGES[lang]
    _require(user, lang)
    tender = _locked(tender, lang)
    if tender.status != TenderStatus.STUDYING:
        raise ValidationError(words["not_studying"])
    if not tender.items.exists():
        raise ValidationError(words["no_items"])
    tender.status, tender.status_on = TenderStatus.SUBMITTED, on or today()
    tender.save(update_fields=["status", "status_on", "updated_at"])
    _audit(tender, user, "submit_tender", {"status": tender.status, "on": str(tender.status_on)})
    return tender


@transaction.atomic
def close(tender, user, status, reason, on=None, lang="ar"):
    """Lost or withdrawn, with its reason."""

    lang = _lang(lang)
    words = MESSAGES[lang]
    _require(user, lang)
    if status not in (TenderStatus.LOST, TenderStatus.WITHDRAWN):
        raise ValidationError(words["reason"])
    reason = (reason or "").strip()[:255]
    if not reason:
        raise ValidationError(words["reason"])
    tender = _locked(tender, lang)
    tender.status, tender.close_reason, tender.status_on = status, reason, on or today()
    tender.save(update_fields=["status", "close_reason", "status_on", "updated_at"])
    _audit(tender, user, f"{status}_tender", {"status": status, "reason": reason, "on": str(tender.status_on)})
    return tender


def win(tender, user, customer=None, on=None, lang="ar"):
    """«اترسّت علينا»: the project, its bill of quantities at the analysed rates and its budget per heading, in one
    transaction through the unchanged projects services. Any refusal undoes all of it."""

    from projects import contract, costs, services as project_services

    lang = _lang(lang)
    words = MESSAGES[lang]
    _require(user, lang)
    with transaction.atomic():
        tender = _locked(tender, lang)  # read again under the lock, so a second click cannot make a second project
        customer = customer or tender.customer
        if customer is None:
            raise ValidationError(words["win_customer"])
        figures = analysis(tender, lang)
        if not figures["rows"]:
            raise ValidationError(words["no_items"])
        if figures["unpriced"]:
            row = figures["unpriced"][0]
            raise ValidationError(words["unpriced"].format(line=row["item"].line_number, description=row["item"].description))
        if figures["total"] > FIELD_MAX or any(amount > FIELD_MAX for amount in figures["by_heading"].values()):
            raise ValidationError(words["too_big"])  # refused here, before the database would refuse the project's money fields
        project = project_services.save_project({"name": tender.name, "customer": customer, "contract_value": figures["total"],
                                                 "notes": f"{label('statuses', 'won', lang)}: {tender.name}"[:255]}, user, lang=lang)
        for row in figures["rows"]:
            item = row["item"]
            contract.save_boq_line(project, user, {"code": item.code, "description": item.description, "unit": item.unit,
                                                   "quantity": item.quantity, "rate": row["rate"]}, lang=lang)
        costs.save_budget(project, user, {heading: amount for heading, amount in figures["by_heading"].items()}, lang=lang)
        before = {"status": tender.status}
        tender.status, tender.project, tender.status_on, tender.customer = TenderStatus.WON, project, on or today(), customer
        tender.prospect_name = ""
        tender.save(update_fields=["status", "project", "status_on", "customer", "prospect_name", "updated_at"])
        _audit(tender, user, "win_tender", {"status": tender.status, "project": project.code, "total": str(figures["total"]),
                                            "budget": {key: str(value) for key, value in figures["by_heading"].items()}}, before)
    return project
