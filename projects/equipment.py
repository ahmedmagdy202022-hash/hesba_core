"""Contracting C6 (HG-129): equipment, owned and hired, with operating hours.

* **Equipment:** a machine the company owns (an internal hour rate) or hires from a supplier (the supplier's hour rate).
  It belongs to an entity; every lookup here goes through the entity being worked in.
* **Hours (``EquipmentLog``):** the site engineer logs a machine's hours on a project day. The rate is copied onto the log,
  so a later change of the machine's rate moves no hour already logged.
* **Hired machines:** «اعمل فاتورة الإيجار» turns the unbilled hours of one supplier on one project into a **draft**
  purchase invoice through the unchanged ``create_purchase_draft_with_tax`` (one service line per machine and rate), linked
  as ``ProjectPurchase(heading=equipment)``. It is posted from the purchases screen and only posting counts as project cost
  (``costs.actual_by_heading``, untouched). Hours are billed once: an hour is billed while its line sits on an invoice that is
  not cancelled. A cancelled bill frees them with nothing written, and a deleted draft frees them (``SET_NULL``).
* **Owned machines:** hours x the internal rate is a **memo** shown on the project («تحميل معدات ملك»). It is never in the
  ledger and never in the company's profit, because the machine's depreciation is already there (owner question 10); the
  project shows its profit before and after the memo. ``services.summary`` and ``costs.actual_by_heading`` are untouched.
"""

from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import ProtectedError, Q, Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from entities import scope as entity_scope

from . import costs, services
from .models import CostHeading, Equipment, EquipmentKind, EquipmentLog, Project, ProjectPurchase

ZERO = Decimal("0")
MAX_DAY_HOURS = Decimal("24")
EQUIPMENT_ITEM_CODE = "PRJ-EQP"
UNBILLED = Q(purchase_line__isnull=True) | Q(purchase_line__invoice__status="cancelled")

MESSAGES = {
    "ar": {
        "name": "اكتب اسم المعدة.", "kind": "اختار نوع المعدة: ملك أو إيجار.", "rate": "اكتب سعر الساعة أكبر من صفر.",
        "supplier": "اختار المورد اللي بتستأجر منه المعدة.", "asset": "الأصل الثابت لازم يكون نشط.", "kind_locked": "المعدة دي عليها ساعات تشغيل؛ مينفعش نوعها يتغيّر.",
        "equipment": "اختار المعدة.", "inactive": "المعدة دي موقوفة؛ فعّلها الأول من سجل المعدات.", "hours": "اكتب عدد الساعات: أكبر من صفر ومايزيدش عن 24 في اليوم.",
        "day_hours": "ساعات المعدة دي في اليوم ده ({total}) بتعدّي 24 ساعة.", "date": "التاريخ مش صحيح.", "future": "مينفعش تسجّل ساعات بتاريخ جاي.",
        "closed": "المشروع ده خلص أو اتلغى.", "entity": "المعدة دي تابعة لكيان تاني؛ اشتغل من الكيان ده.",
        "supplier_pick": "اختار المورد.", "no_hours": "مفيش ساعات إيجار لسه ما اتفوترتش للمورد ده على المشروع ده.", "setup": "لازم يكون فيه مخزن استلام نشط.",
        "billed_log": "الساعات دي اتفوترت في فاتورة إيجار؛ اسحب الفاتورة أو الغيها الأول.", "not_draft": "فاتورة الإيجار دي اترحّلت؛ الغيها من شاشة المشتريات لو فيها غلط.",
        "withdraw_blocked": "الفاتورة دي مربوطة بحاجة تانية ومينفعش تتسحب.", "not_hire": "دي مش فاتورة إيجار معدات.",
    },
    "en": {
        "name": "Enter the machine's name.", "kind": "Choose the kind: owned or hired.", "rate": "Enter an hour rate above zero.",
        "supplier": "Choose the supplier the machine is hired from.", "asset": "The fixed asset must be active.", "kind_locked": "This machine has logged hours; its kind cannot change.",
        "equipment": "Choose the machine.", "inactive": "This machine is switched off; switch it on in the equipment register first.",
        "hours": "Enter the hours: above zero and no more than 24 a day.", "day_hours": "This machine's hours on that day ({total}) pass 24.",
        "date": "Invalid date.", "future": "Hours cannot be logged for a future date.", "closed": "This project is done or cancelled.",
        "entity": "This machine belongs to another entity; work in that entity.", "supplier_pick": "Choose the supplier.",
        "no_hours": "There are no unbilled hire hours for this supplier on this project.", "setup": "An active receiving location is needed.",
        "billed_log": "These hours are on a hire bill; withdraw or cancel the bill first.", "not_draft": "This hire bill is posted; cancel it on the purchases screen if it is wrong.",
        "withdraw_blocked": "This invoice is linked to something else and cannot be withdrawn.", "not_hire": "This is not an equipment hire bill.",
    },
}


def _audit(project, user, action, after, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="projects", action=action, object_type="projects.Project",
                            object_id=str(project.pk), after_data=after)


def _decimal(value):
    try:
        number = Decimal(str(value if value not in (None, "") else "0").strip().replace(",", "."))
    except (InvalidOperation, AttributeError):
        return None
    return number if number.is_finite() else None


def in_scope(queryset=None):
    """Only the machines the entity being worked in may use (everything for the whole group)."""

    return entity_scope.scope(queryset if queryset is not None else Equipment.objects.all(), "entity")


def logs_in_scope(queryset=None):
    return entity_scope.scope(queryset if queryset is not None else EquipmentLog.objects.all(), "equipment__entity")


def _reach(equipment, words):
    if not in_scope(Equipment.objects.filter(pk=equipment.pk)).exists():
        raise ValidationError(words["entity"])


# ---- the register ----

@transaction.atomic
def save_equipment(user, data, equipment=None, lang="ar"):
    """Add or change a machine. ``data``: name, kind, hour_rate, supplier (a Supplier), fixed_asset (a FixedAsset), active."""

    words = MESSAGES[lang]
    name = (data.get("name") or "").strip()[:255]
    if not name:
        raise ValidationError(words["name"])
    kind = data.get("kind")
    if kind not in EquipmentKind.values:
        raise ValidationError(words["kind"])
    rate = _decimal(data.get("hour_rate"))
    if rate is None or rate <= 0:
        raise ValidationError(words["rate"])
    supplier, asset = data.get("supplier"), data.get("fixed_asset")
    if kind == EquipmentKind.HIRED:
        if supplier is None:
            raise ValidationError(words["supplier"])
        asset = None  # a hired machine is nobody's asset
    else:
        supplier = None  # an owned machine has no supplier
        if asset is not None and asset.status != "active":
            raise ValidationError(words["asset"])
    created = equipment is None
    if not created:
        equipment = Equipment.objects.select_for_update().get(pk=equipment.pk)
        _reach(equipment, words)
        if kind != equipment.kind and equipment.logs.exists():
            raise ValidationError(words["kind_locked"])
    else:
        from entities.current import current_entity

        equipment = Equipment(created_by=user, entity=current_entity())
    values = {"name": name, "kind": kind, "hour_rate": money_round(rate), "supplier": supplier, "fixed_asset": asset,
              "active": bool(data.get("active", True))}
    for key, value in values.items():
        setattr(equipment, key, value)
    equipment.save()
    AuditLog.objects.create(event_type=AuditEventType.CREATE if created else AuditEventType.UPDATE, actor=user, module="projects",
                            action="create_equipment" if created else "change_equipment", object_type="projects.Equipment", object_id=str(equipment.pk),
                            after_data={"name": name, "kind": kind, "hour_rate": str(equipment.hour_rate), "active": equipment.active})
    return equipment


# ---- hours ----

@transaction.atomic
def log_hours(project, user, *, equipment, work_date=None, hours, operator="", lang="ar"):
    words = MESSAGES[lang]
    project = services._open(project, services.MESSAGES[lang])
    if equipment is None:
        raise ValidationError(words["equipment"])
    equipment = Equipment.objects.select_for_update().get(pk=equipment.pk)
    _reach(equipment, words)
    if not equipment.active:
        raise ValidationError(words["inactive"])
    day = work_date or timezone.localdate()
    if day > timezone.localdate():
        raise ValidationError(words["future"])
    amount = _decimal(hours)
    if amount is None or amount <= 0 or amount > MAX_DAY_HOURS:
        raise ValidationError(words["hours"])
    amount = amount.quantize(Decimal("0.01"))
    if amount <= 0:
        raise ValidationError(words["hours"])
    already = EquipmentLog.objects.filter(equipment=equipment, work_date=day).aggregate(total=Sum("hours"))["total"] or ZERO
    if already + amount > MAX_DAY_HOURS:
        raise ValidationError(words["day_hours"].format(total=already + amount))
    log = EquipmentLog.objects.create(project=project, equipment=equipment, work_date=day, hours=amount, rate=equipment.hour_rate,
                                      operator=(operator or "").strip()[:120], created_by=user)
    _audit(project, user, "log_equipment_hours", {"equipment": equipment.pk, "date": day.isoformat(), "hours": str(amount), "rate": str(log.rate)},
           AuditEventType.CREATE)
    return log


def is_billed(log):
    """Billed while its line sits on an invoice that is not cancelled (read, never written, on cancel)."""

    return log.purchase_line_id is not None and log.purchase_line.invoice.status != "cancelled"


@transaction.atomic
def delete_log(project, log, user, lang="ar"):
    words = MESSAGES[lang]
    project = services._open(project, services.MESSAGES[lang])
    log = EquipmentLog.objects.select_for_update(of=("self",)).select_related("equipment", "purchase_line__invoice").get(pk=log.pk, project=project)
    _reach(log.equipment, words)
    if is_billed(log):
        raise ValidationError(words["billed_log"])
    details = {"equipment": log.equipment_id, "date": log.work_date.isoformat(), "hours": str(log.hours)}
    log.delete()
    _audit(project, user, "delete_equipment_hours", details, AuditEventType.DELETE)


# ---- hired machines: the hire bill ----

def equipment_item():
    """The service line a hire bill uses."""

    return services.service_item(EQUIPMENT_ITEM_CODE, "إيجار معدات")


def unbilled_hire(project, supplier=None):
    """The hired machines' hours on this project that no live bill holds (this entity's)."""

    logs = logs_in_scope(project.equipment_logs.filter(UNBILLED, equipment__kind=EquipmentKind.HIRED))
    if supplier is not None:
        logs = logs.filter(equipment__supplier=supplier)
    return logs


def hire_groups(project):
    """Unbilled hours by supplier: [{supplier, hours, amount, machines}] (the bill button is per group)."""

    groups = {}
    for log in unbilled_hire(project).select_related("equipment__supplier").order_by("equipment__supplier__name", "equipment__name", "work_date", "pk"):
        group = groups.setdefault(log.equipment.supplier_id, {"supplier": log.equipment.supplier, "hours": ZERO, "amount": ZERO, "machines": {}})
        group["hours"] += log.hours
        group["amount"] += money_round(log.hours * log.rate)
        group["machines"][log.equipment.name] = group["machines"].get(log.equipment.name, ZERO) + log.hours
    return list(groups.values())


@transaction.atomic
def bill_hire(project, user, supplier, *, bill_date=None, lang="ar"):
    """The supplier's unbilled hours on this project as one draft purchase invoice (one line per machine and rate),
    through the unchanged ``create_purchase_draft_with_tax``, linked as a project purchase under «equipment».
    A machine of another entity makes its own invoice (an invoice has one entity). Returns the invoices made."""

    from entities import scope as scoped
    from master_data.models import Location
    from taxes.services import create_purchase_draft_with_tax

    from .contract import entity_locations

    words = MESSAGES[lang]
    if supplier is None:
        raise ValidationError(words["supplier_pick"])
    project = services._open(project, services.MESSAGES[lang])
    logs = list(unbilled_hire(project, supplier).select_for_update(of=("self",)).select_related("equipment")
                .order_by("equipment__entity_id", "equipment__name", "rate", "work_date", "pk"))
    if not logs:
        raise ValidationError(words["no_hours"])
    by_entity = defaultdict(list)
    for log in logs:
        by_entity[log.equipment.entity_id].append(log)
    day = bill_date or timezone.localdate()
    made = []
    for entity_id, entity_logs in by_entity.items():
        locations = entity_locations(scoped.locations(Location.objects).filter(active=True, is_receiving_location=True), entity_id)
        location = locations.order_by("-is_default", "pk").first()
        if location is None:
            raise ValidationError(words["setup"])
        # One line per machine and rate: a machine whose rate changed between two days is two lines at their own rates.
        groups = defaultdict(list)
        for log in entity_logs:
            groups[(log.equipment_id, log.rate)].append(log)
        lines, grouped = [], list(groups.values())
        for group in grouped:
            first, last = group[0], group[-1]
            span = first.work_date.isoformat() if first.work_date == last.work_date else f"{first.work_date.isoformat()} → {last.work_date.isoformat()}"
            lines.append({"item": equipment_item(), "quantity": sum((log.hours for log in group), ZERO), "unit_purchase_price": first.rate,
                          "line_discount_amount": ZERO, "description": f"{first.equipment.name} — {span}"[:255]})
        sequence = ProjectPurchase.objects.filter(project=project, heading=CostHeading.EQUIPMENT).count() + 1
        number = f"EQ-{project.code}-{sequence:02d}"
        from purchases.models import PurchaseInvoice

        while PurchaseInvoice.objects.filter(invoice_number=number).exists():
            sequence += 1
            number = f"EQ-{project.code}-{sequence:02d}"
        invoice = create_purchase_draft_with_tax(
            {"invoice_number": number, "invoice_date": day, "supplier": supplier, "receiving_location": location, "cashbox": None,
             "discount_amount": ZERO, "tax_amount": ZERO, "paid_now": ZERO, "notes": f"مشروع {project.code}: إيجار معدات"[:255]},
            lines, user,
        )
        for line, group in zip(invoice.lines.order_by("line_number"), grouped):
            EquipmentLog.objects.filter(pk__in=[log.pk for log in group]).update(purchase_line=line)
        ProjectPurchase.objects.create(project=project, invoice=invoice, heading=CostHeading.EQUIPMENT, created_by=user)
        made.append(invoice)
        _audit(project, user, "bill_equipment_hire", {"invoice": invoice.invoice_number, "supplier": supplier.pk, "hours": str(sum((log.hours for log in entity_logs), ZERO)),
                                                        "total": str(invoice.total_amount)}, AuditEventType.CREATE)
    return made


def hire_bills(project):
    """The project's hire bills (this entity's), newest first, each with its hours."""

    rows = []
    links = costs._here(project.purchases.filter(heading=CostHeading.EQUIPMENT).select_related("invoice", "invoice__supplier"))
    for link in links.order_by("-pk"):
        logs = EquipmentLog.objects.filter(purchase_line__invoice=link.invoice)
        rows.append({"link": link, "invoice": link.invoice, "hours": logs.aggregate(total=Sum("hours"))["total"] or ZERO,
                     "draft": link.invoice.status == "draft"})
    return rows


@transaction.atomic
def withdraw_hire(project, invoice, user, lang="ar"):
    """Take back a hire bill that is still a draft: its hours are free again (the lines go, ``SET_NULL``)."""

    words = MESSAGES[lang]
    project = Project.objects.select_for_update().get(pk=project.pk)
    link = ProjectPurchase.objects.select_related("invoice").select_for_update(of=("self",)).get(project=project, invoice=invoice, heading=CostHeading.EQUIPMENT)
    costs._visible(link.invoice, costs.MESSAGES[lang])
    invoice = link.invoice
    if invoice.status != "draft":
        raise ValidationError(words["not_draft"])
    details = {"invoice": invoice.invoice_number, "total": str(invoice.total_amount)}
    link.delete()
    try:
        with transaction.atomic():
            invoice.delete()
    except ProtectedError:
        raise ValidationError(words["withdraw_blocked"])
    _audit(project, user, "withdraw_equipment_hire", details, AuditEventType.DELETE)


# ---- owned machines: the memo allocation ----

def owned_logs(project):
    return logs_in_scope(project.equipment_logs.filter(equipment__kind=EquipmentKind.OWNED))


def memo(project, profit=None):
    """«تحميل معدات ملك»: owned machines' hours x the rate copied on each log, for this project. A memo only: it is not in
    the ledger and not in the company's profit (the machine's depreciation already is), so it sits next to the project's
    figures, never inside them. ``profit`` is ``services.summary(project)["profit"]`` when the caller has it."""

    rows = {}
    for log in owned_logs(project).select_related("equipment"):
        row = rows.setdefault(log.equipment_id, {"equipment": log.equipment, "hours": ZERO, "amount": ZERO})
        row["hours"] += log.hours
        row["amount"] += money_round(log.hours * log.rate)
    amount = money_round(sum((row["amount"] for row in rows.values()), ZERO))
    before = money_round(services.summary(project)["profit"] if profit is None else profit)
    return {"rows": sorted(rows.values(), key=lambda row: row["equipment"].name), "hours": sum((row["hours"] for row in rows.values()), ZERO),
            "amount": amount, "profit_before": before, "profit_after": money_round(before - amount)}


def figures(project):
    """What the equipment tab shows: every log (this entity's), the memo, the unbilled hire by supplier and the hire bills."""

    logs = list(logs_in_scope(project.equipment_logs.select_related("equipment", "equipment__supplier", "purchase_line__invoice")))
    rows = []
    for log in logs:
        rows.append({"log": log, "amount": money_round(log.hours * log.rate), "billed": is_billed(log),
                     "invoice": log.purchase_line.invoice if log.purchase_line_id and is_billed(log) else None})
    return {"logs": rows, "total_hours": sum((log.hours for log in logs), ZERO), "groups": hire_groups(project), "bills": hire_bills(project)}
