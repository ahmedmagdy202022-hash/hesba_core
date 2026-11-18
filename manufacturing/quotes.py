"""Manufacturing F7 (HG-138): a quotation line with specifications and a cost sheet, made into a custom production order.

Rules, stated once:

* **Specifications** follow a template written in code, one per kind of made-to-order work (``TEMPLATES``: printing,
  furniture, and a general made-to-measure one for every other factory), like the stage lists in ``stages.py``. The
  sub-activity chosen at setup picks the default; the user may choose another. The answers print under the line on the
  quotation and on the sales order. Anyone who may make quotations may write them.
* **The cost sheet** is the estimate behind the price: materials (stock items, at today's authoritative average unless
  another cost is typed), labour, overhead and outside work, each a quantity x a unit cost for the whole line, then a
  margin. Unit price = cost x (1 + margin / 100) / the line's quantity, to the piastre; the user may override it.
  Seeing and writing the sheet needs ``inventory.view_cost`` (and ``sales.create_sales_invoice`` to write it); everyone
  else sees the specifications and the price only. While the quotation is still offered, saving the price writes it on
  the quotation line (the same guards as the quotation's own edits: the capability, the permission, the order locked,
  draft or sent only). After the customer accepts, the price is fixed.
* **Nothing posts.** Writing the specifications or the sheet, and confirming the order (the T1 move, unchanged), write
  no stock movement, no cashbox row and no journal row.
* **«حوّله لأمر إنتاج خاص»** on a confirmed line with a sheet (``convert``), in one transaction:
  - creates one stock item for this job, ``JOB-<order number>-<line position>``, named after the line, sold at the
    line's price, with the placeholder item's unit, category and VAT rate link (trap 13: jobs on a shared item would
    blend their costs through the average);
  - writes its recipe from the sheet's materials **per unit** (output 1) when every quantity divides exactly to three
    decimals; otherwise one batch is the whole job (output = the line's quantity), so no material is lost to rounding
    (trap 10);
  - points the sales order line at the job item, so the ordinary invoice of the sales order sells the job item and
    takes its own cost (the order line had no invoice and no production order yet: both are refused);
  - opens the production order through F1 (``make_to_order.make``) for the whole line, with planned labour and overhead
    from the sheet. Outside work becomes a note on the order (no amounts): a stage is sent out with F4 only when it is
    on the floor, so no ``OutsideJob`` is made here.
* The till never offers ``JOB-`` items (one filter line in ``sales/pos.py``); the invoice form does, because the job is
  invoiced from there.

No line of sales posting, purchase posting, the inventory engine, the projector, average cost or the permission core is
changed here.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission

from .models import CostSheet, CostSheetLine, CostSheetLineKind, OrderStatus, ProductionOrder, RunStatus

ZERO = Decimal("0")
HUNDRED = Decimal("100")
COST_QUANT = Decimal("0.0001")
QTY_QUANT = Decimal("0.001")
JOB_PREFIX = "JOB-"
VIEW, MANAGE = "sales.view_sales_invoices", "sales.create_sales_invoice"
COST, PRODUCE, ITEMS = "inventory.view_cost", "inventory.adjust_stock", "master_data.manage_items"
ANSWER_LENGTH = 120

#: The specification fields per kind of work: (key, Arabic label, English label).
TEMPLATES = {
    "printing": {"ar": "طباعة", "en": "Printing", "fields": (
        ("size", "المقاس", "Size"), ("pages", "عدد الصفحات", "Pages"), ("paper", "نوع الورق ووزنه", "Paper and weight"),
        ("colours", "الألوان", "Colours"), ("finishing", "التشطيب والتجليد", "Finishing and binding"),
        ("copies", "عدد النسخ في الوحدة", "Copies per unit"))},
    "furniture": {"ar": "أثاث", "en": "Furniture", "fields": (
        ("width", "العرض بالسنتيمتر", "Width (cm)"), ("depth", "العمق بالسنتيمتر", "Depth (cm)"), ("height", "الارتفاع بالسنتيمتر", "Height (cm)"),
        ("wood", "نوع الخشب", "Wood"), ("finish", "التشطيب", "Finish"), ("colour", "اللون", "Colour"), ("upholstery", "التنجيد", "Upholstery"))},
    "custom": {"ar": "شغل حسب المقاس", "en": "Made to measure", "fields": (
        ("dimensions", "المقاسات", "Dimensions"), ("material", "الخامة", "Material"), ("finish", "التشطيب", "Finish"))},
}
#: The sub-activity picks the default template; every other factory gets the general made-to-measure one.
SUB_ACTIVITY_TEMPLATE = {"printing": "printing", "furniture": "furniture"}
DEFAULT_TEMPLATE = "custom"

KIND_LABELS = {
    "ar": {"material": "خامة", "labour": "عمالة", "overhead": "مصاريف تشغيل", "outside": "تشغيل لدى الغير"},
    "en": {"material": "Material", "labour": "Labour", "overhead": "Overhead", "outside": "Outside work"},
}

MESSAGES = {
    "ar": {
        "no_capability": "عروض الأسعار مقفولة في الإعدادات.",
        "permission": "تعديل المواصفات محتاج صلاحية عمل عروض الأسعار.",
        "cost_permission": "بيان التكلفة بيبيّن تكلفة الخامات، فتعديله محتاج صلاحية رؤية التكلفة وصلاحية عمل عروض الأسعار.",
        "convert_permission": "تحويل البند لأمر إنتاج خاص بيعمل صنف ووصفة وأمر إنتاج، فمحتاج صلاحية تسوية المخزون وصلاحية إدارة الأصناف.",
        "template": "اختار نوع المواصفات من القايمة.",
        "locked": "البند ده اتحوّل لأمر إنتاج خاص؛ المواصفات وبيان التكلفة مبقوش بيتعدّلوا.",
        "closed": "الأمر ده مبقاش مفتوح؛ المواصفات وبيان التكلفة مبقوش بيتعدّلوا.",
        "kind": "اختار نوع السطر.", "item": "اختار الخامة من الأصناف المتتبعة في المخزون.",
        "description": "اكتب بيان السطر.", "quantity": "الكمية لازم تكون رقم أكبر من صفر.",
        "unit_cost": "تكلفة الوحدة لازم تكون رقم صفر أو أكبر.",
        "margin": "نسبة الربح لازم تكون رقم من 0 لـ 1000.", "override": "السعر لازم يكون رقم صفر أو أكبر، أو سيبه فاضي.",
        "price_offered": "السعر بيتغيّر على عرض السعر وهو لسه مسودة أو اتبعت بس؛ بعد موافقة العميل السعر بيتثبت.",
        "no_sheet": "البند ده ملوش بيان تكلفة؛ اعمل البيان الأول.",
        "not_confirmed": "أمر البيع لسه مش مؤكد؛ العميل لازم يوافق الأول.",
        "already": "البند ده اتحوّل لأمر إنتاج خاص قبل كده.",
        "invoiced": "البند ده عليه فاتورة؛ مينفعش يتحوّل لأمر إنتاج خاص.",
        "in_production": "البند ده ليه أمر إنتاج شغال بالفعل.",
        "no_materials": "ضيف خامة واحدة على الأقل لبيان التكلفة؛ الوصفة بتتعمل من الخامات.",
        "pending": "البند ده ليه بيان تكلفة؛ حوّله لأمر إنتاج خاص من صفحة المواصفات والتكلفة.",
        "outside_note": "في مراحل متوقع تتعمل لدى الغير ({items}). سجّل إرسال كل مرحلة من صفحة الأمر لما توصل لها.",
        "recipe_note": "من بيان تكلفة البند في أمر البيع {number} بعد موافقة العميل",
    },
    "en": {
        "no_capability": "Quotations are switched off in Settings.",
        "permission": "Changing the specifications needs the permission to make quotations.",
        "cost_permission": "The cost sheet shows material costs, so changing it needs the permission to see costs and the permission to make quotations.",
        "convert_permission": "Making the line a custom production order creates an item, a recipe and a production order, so it needs the stock adjustment and item management permissions.",
        "template": "Choose the kind of specifications from the list.",
        "locked": "This line is already a custom production order; its specifications and cost sheet no longer change.",
        "closed": "This order is no longer open; its specifications and cost sheet no longer change.",
        "kind": "Choose the kind of line.", "item": "Choose a stock-tracked material.",
        "description": "Write what the line is for.", "quantity": "The quantity must be a number above zero.",
        "unit_cost": "The unit cost must be a number, zero or more.",
        "margin": "The margin must be a number from 0 to 1000.", "override": "The price must be a number, zero or more, or leave it empty.",
        "price_offered": "The price changes only while the quotation is a draft or sent; once the customer accepts, it is fixed.",
        "no_sheet": "This line has no cost sheet; make the sheet first.",
        "not_confirmed": "This sales order is not confirmed yet; the customer has to accept it first.",
        "already": "This line is already a custom production order.",
        "invoiced": "This line has an invoice, so it cannot become a custom production order.",
        "in_production": "This line already has a production order running.",
        "no_materials": "Add at least one material to the cost sheet; the recipe is made from the materials.",
        "pending": "This line has a cost sheet; make it a custom production order from its specifications and cost page.",
        "outside_note": "Some stages are expected to be done outside ({items}). Record sending each stage from the order page when it gets there.",
        "recipe_note": "From the cost sheet of sales order {number}",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _audit(obj, user, action, after, *, before=None, event=AuditEventType.UPDATE, object_type="manufacturing.CostSheet"):
    AuditLog.objects.create(event_type=event, actor=user, module="manufacturing", action=action, object_type=object_type,
                            object_id=str(obj.pk), before_data=before or {}, after_data=after)


def _number(raw, quant):
    """A typed number (commas as thousands separators), or None."""

    from orders.services import _decimal

    try:
        value = _decimal(raw, {"x": "x"}, "x")
    except ValidationError:
        return None
    return value.quantize(quant, rounding=ROUND_HALF_UP)


# ---- templates ----

def default_template():
    from settings_core.models import ClientProfile

    profile = ClientProfile.get_active()
    return SUB_ACTIVITY_TEMPLATE.get(profile.sub_activity_slug if profile else "", DEFAULT_TEMPLATE)


def template_choices(lang="ar"):
    key = "en" if lang == "en" else "ar"
    return [(slug, spec[key]) for slug, spec in TEMPLATES.items()]


def fields_of(template, lang="ar"):
    """[(key, label)] of a template's fields."""

    index = 2 if lang == "en" else 1
    return [(field[0], field[index]) for field in TEMPLATES.get(template, TEMPLATES[DEFAULT_TEMPLATE])["fields"]]


def spec_rows(sheet, lang="ar"):
    """[(label, answer)] of the answered fields, in the template's order: what prints under the line."""

    if sheet is None:
        return []
    return [(label, sheet.spec[key]) for key, label in fields_of(sheet.template, lang) if str(sheet.spec.get(key) or "").strip()]


def print_specs(order, lang="ar"):
    """Per line pk of ``order``: the answered specifications, for the quotation's print (specifications only, never a cost)."""

    return {sheet.sales_order_line_id: spec_rows(sheet, lang) for sheet in CostSheet.objects.filter(sales_order_line__order=order)}


# ---- where a sheet stands ----

def scoped_lines():
    """The sales order lines of the entity being worked in."""

    from orders import services as sales
    from orders.models import SalesOrderLine

    return SalesOrderLine.objects.filter(order__in=sales.scoped())


def sheet_of(line):
    return CostSheet.objects.filter(sales_order_line=line).first()


def converted(sheet):
    return sheet is not None and sheet.job_item_id is not None


def editable(order, sheet):
    """The specifications and the sheet change while the quotation is offered, or on an open order not yet converted."""

    from orders.models import OFFERED, Status

    return not converted(sheet) and order.status in (*OFFERED, Status.CONFIRMED)


def price_open(order):
    from orders.models import OFFERED

    return order.status in OFFERED


def figures(sheet, quantity):
    """Per kind totals, the cost, the margin, the price for the whole line and per unit (computed and as used)."""

    totals = {kind: ZERO for kind in CostSheetLineKind.values}
    for line in sheet.lines.all():
        totals[line.kind] += money_round(line.quantity * line.unit_cost)
    cost = sum(totals.values(), ZERO)
    price_total = money_round(cost * (HUNDRED + sheet.margin_percent) / HUNDRED)
    computed = money_round(price_total / quantity) if quantity else ZERO
    return {**totals, "cost": cost, "margin": price_total - cost, "price_total": price_total, "computed": computed,
            "unit_cost": money_round(cost / quantity) if quantity else ZERO,
            "unit_price": sheet.price_override if sheet.price_override is not None else computed, "overridden": sheet.price_override is not None}


def line_amount(line):
    return money_round(line.quantity * line.unit_cost)


# ---- writing ----

def _guard(user, words, *codes, message="permission"):
    from orders import services as sales

    if not sales.enabled():
        raise ValidationError(words["no_capability"])
    if not all(user_has_permission(user, code) for code in codes):
        raise PermissionDenied(words[message])


def _locked(line, words, *, need_sheet=False):
    """The line's order row locked (as every writer of a quotation locks it), the line, and its sheet row locked."""

    from orders.models import SalesOrder, SalesOrderLine

    order = SalesOrder.objects.select_for_update().get(pk=line.order_id)
    line = SalesOrderLine.objects.select_related("item").get(pk=line.pk)
    sheet = CostSheet.objects.select_for_update().filter(sales_order_line=line).first()
    if need_sheet and sheet is None:
        raise ValidationError(words["no_sheet"])
    if converted(sheet):
        raise ValidationError(words["locked"])
    if not editable(order, sheet):
        raise ValidationError(words["closed"])
    return order, line, sheet


def _new_sheet(line, user):
    return CostSheet.objects.create(sales_order_line=line, template=default_template(), created_by=user)


@transaction.atomic
def save_spec(line, user, template, answers, lang="ar"):
    """The specifications of a line: a template key and its answers (blank answers are dropped)."""

    words = _words(lang)
    _guard(user, words, MANAGE)
    if template not in TEMPLATES:
        raise ValidationError(words["template"])
    _order, line, sheet = _locked(line, words)
    sheet = sheet or _new_sheet(line, user)
    before = {"template": sheet.template, "spec": sheet.spec}
    keys = [key for key, _label in fields_of(template)]
    sheet.template = template
    sheet.spec = {key: str(answers.get(key) or "").strip()[:ANSWER_LENGTH] for key in keys if str(answers.get(key) or "").strip()}
    sheet.save(update_fields=["template", "spec", "updated_at"])
    _audit(sheet, user, "save_quote_spec", {"line": line.pk, "template": template, "spec": sheet.spec}, before=before)
    return sheet


@transaction.atomic
def add_cost_line(line, user, *, kind, item=None, description="", quantity=None, unit_cost=None, lang="ar"):
    """One cost line. A material's unit cost left blank is its authoritative average cost today."""

    from inventory.services import get_item_authoritative_average_cost

    words = _words(lang)
    _guard(user, words, COST, MANAGE, message="cost_permission")
    if kind not in CostSheetLineKind.values:
        raise ValidationError(words["kind"])
    _order, line, sheet = _locked(line, words)
    sheet = sheet or _new_sheet(line, user)
    description = (description or "").strip()[:255]
    if kind == CostSheetLineKind.MATERIAL:
        if item is None or not item.active or not item.is_stock_tracked or item.item_code.startswith(JOB_PREFIX):
            raise ValidationError(words["item"])
    else:
        item = None
        if not description:
            raise ValidationError(words["description"])
    count = _number(quantity, QTY_QUANT)
    if count is None or count <= 0:
        raise ValidationError(words["quantity"])
    if unit_cost in (None, "") and item is not None:
        cost = Decimal(get_item_authoritative_average_cost(item)).quantize(COST_QUANT, rounding=ROUND_HALF_UP)
    else:
        cost = _number(unit_cost, COST_QUANT)
    if cost is None or cost < 0:
        raise ValidationError(words["unit_cost"])
    row = CostSheetLine.objects.create(sheet=sheet, kind=kind, item=item, description=description, quantity=count, unit_cost=cost)
    sheet.save(update_fields=["updated_at"])
    _audit(sheet, user, "add_quote_cost_line", {"line": line.pk, "kind": kind, "item": item.item_code if item else "", "description": description,
                                                 "quantity": str(count), "unit_cost": str(cost)})
    return row


@transaction.atomic
def remove_cost_line(row, user, lang="ar"):
    words = _words(lang)
    _guard(user, words, COST, MANAGE, message="cost_permission")
    _order, line, sheet = _locked(row.sheet.sales_order_line, words, need_sheet=True)
    row = CostSheetLine.objects.get(pk=row.pk, sheet=sheet)
    _audit(sheet, user, "remove_quote_cost_line", {"line": line.pk}, before={"kind": row.kind, "item": row.item.item_code if row.item_id else "",
                                                                             "description": row.description, "quantity": str(row.quantity),
                                                                             "unit_cost": str(row.unit_cost)})
    row.delete()
    sheet.save(update_fields=["updated_at"])


@transaction.atomic
def set_price(line, user, *, margin=None, override=None, lang="ar"):
    """The margin and the optional price override; the sheet's unit price then goes on the quotation line. Only while the
    quotation is offered (draft or sent): the price the customer accepted is fixed."""

    words = _words(lang)
    _guard(user, words, COST, MANAGE, message="cost_permission")
    order, line, sheet = _locked(line, words, need_sheet=True)
    if not price_open(order):
        raise ValidationError(words["price_offered"])
    percent = ZERO if margin in (None, "") else _number(margin, Decimal("0.01"))
    if percent is None or percent < 0 or percent > 1000:
        raise ValidationError(words["margin"])
    fixed = None
    if override not in (None, ""):
        fixed = _number(override, Decimal("0.01"))
        if fixed is None or fixed < 0:
            raise ValidationError(words["override"])
    before = {"margin_percent": str(sheet.margin_percent), "price_override": str(sheet.price_override or ""), "unit_price": str(line.unit_price)}
    sheet.margin_percent, sheet.price_override = percent, fixed
    sheet.save(update_fields=["margin_percent", "price_override", "updated_at"])
    price = figures(sheet, line.quantity)["unit_price"]
    line.unit_price = price
    line.save(update_fields=["unit_price"])
    _audit(sheet, user, "price_quote_line", {"line": line.pk, "margin_percent": str(percent), "price_override": str(fixed or ""), "unit_price": str(price)},
           before=before)
    return sheet


# ---- the custom production order ----

def _position(line):
    return list(line.order.lines.order_by("pk").values_list("pk", flat=True)).index(line.pk) + 1


def _job_code(order, line):
    from master_data.models import Item

    stem = f"{JOB_PREFIX}{order.order_number}-{_position(line)}"
    code, number = stem, 1
    while Item.objects.filter(item_code=code).exists():  # a code typed by hand long ago: never take it over
        number += 1
        code = f"{stem}-{number}"
    return code


def _job_item(order, line):
    from master_data.models import Item

    placeholder = line.item
    item = Item.objects.create(item_code=_job_code(order, line), item_name=(line.description or placeholder.item_name)[:255], is_stock_tracked=True,
                               unit=placeholder.unit, category=placeholder.category, default_sale_price=line.unit_price)
    from taxes.models import ItemTaxRate

    link = ItemTaxRate.objects.filter(item=placeholder).first()
    if link is not None:  # the job is invoiced at the VAT rate of the line the customer accepted
        ItemTaxRate.objects.create(item=item, tax_rate=link.tax_rate)
    return item


def recipe_shape(materials, quantity):
    """(output per batch, [(item, quantity per batch)]): per unit when every material divides exactly to three decimals,
    else one batch for the whole job, so nothing is lost to rounding (trap 10)."""

    per_unit = [(item, (amount / quantity).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)) for item, amount in materials]
    if all(share > 0 and share * quantity == amount for (_item, share), (_same, amount) in zip(per_unit, materials)):
        return Decimal("1"), per_unit
    return quantity, list(materials)


def _merged_materials(sheet):
    merged = {}
    for row in sheet.lines.filter(kind=CostSheetLineKind.MATERIAL).select_related("item").order_by("pk"):
        item, amount = merged.get(row.item_id, (row.item, ZERO))
        merged[row.item_id] = (item, amount + row.quantity)
    return list(merged.values())


def _live_production(line):
    return (ProductionOrder.objects.filter(sales_order_line=line, status__in=(OrderStatus.PLANNED, OrderStatus.IN_PROGRESS, OrderStatus.DONE))
            .exclude(status=OrderStatus.DONE, run__status=RunStatus.CANCELLED))


def pending(line_pks):
    """The pks among ``line_pks`` whose line has a sheet not converted yet (F1's plain «اعمل أمر إنتاج» is not offered there)."""

    return set(CostSheet.objects.filter(sales_order_line_id__in=line_pks, job_item__isnull=True).values_list("sales_order_line_id", flat=True))


@transaction.atomic
def convert(line, user, data=None, lang="ar"):
    """«حوّله لأمر إنتاج خاص»: the job's own item and recipe, the order line pointed at it, and the production order opened
    through F1 for the whole line with planned labour and overhead from the sheet. Returns the ProductionOrder."""

    from orders.models import OrderInvoiceLine, SalesOrder, SalesOrderLine, Status

    from . import make_to_order, services

    words = _words(lang)
    data = data or {}
    _guard(user, words, PRODUCE, ITEMS, message="convert_permission")
    order = SalesOrder.objects.select_for_update().get(pk=line.order_id)
    line = SalesOrderLine.objects.select_related("item", "order").get(pk=line.pk)
    sheet = CostSheet.objects.select_for_update().filter(sales_order_line=line).first()
    if sheet is None:
        raise ValidationError(words["no_sheet"])
    if converted(sheet):
        raise ValidationError(words["already"])
    if order.status != Status.CONFIRMED:
        raise ValidationError(words["not_confirmed"])
    if OrderInvoiceLine.objects.filter(order_line=line).exclude(sales_line__invoice__status="cancelled").exists():
        raise ValidationError(words["invoiced"])
    if _live_production(line).exists():
        raise ValidationError(words["in_production"])
    materials = _merged_materials(sheet)
    if not materials:
        raise ValidationError(words["no_materials"])
    item = _job_item(order, line)
    output, lines = recipe_shape(materials, line.quantity)
    recipe = services.save_recipe({"product": item, "output_quantity": str(output), "name": item.item_name,
                                   "notes": words["recipe_note"].format(number=order.order_number)}, [(m, str(q)) for m, q in lines], user, lang=lang)
    placeholder = line.item
    line.item = item
    line.save(update_fields=["item"])
    _audit(line, user, "point_line_at_job_item", {"order": order.order_number, "item": item.item_code}, before={"item": placeholder.item_code},
           object_type="orders.SalesOrderLine")
    sheet.job_item, sheet.job_recipe, sheet.converted_at = item, recipe, timezone.now()
    sheet.save(update_fields=["job_item", "job_recipe", "converted_at", "updated_at"])
    numbers = figures(sheet, line.quantity)
    outside = [row.description for row in sheet.lines.filter(kind=CostSheetLineKind.OUTSIDE).order_by("pk")]
    note = words["outside_note"].format(items="، ".join(outside))[:255] if outside else ""
    production = make_to_order.make(line, user, {"location": data.get("location"), "labor_cost": numbers["labour"],
                                                 "overhead_cost": numbers["overhead"], "notes": note}, lang)
    _audit(sheet, user, "convert_quote_line", {"line": line.pk, "order": order.order_number, "item": item.item_code, "recipe": recipe.code,
                                               "output": str(output), "production_order": production.number, "labour": str(numbers["labour"]),
                                               "overhead": str(numbers["overhead"])}, event=AuditEventType.CREATE)
    return production
