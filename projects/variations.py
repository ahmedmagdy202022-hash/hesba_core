"""Contracting C1 (HG-103): variation orders and extra works (أوامر التغيير والأعمال الإضافية).

A variation order is a document the owner is asked to approve. It can:

* raise or cut the quantity of an existing item of the bill of quantities (a cut is an omission);
* add new items, each with its own quantity and rate (extra works);
* extend the time of the contract by a number of days;
* on a project billed as a lump sum (no bill of quantities), add or take off one amount.

Rules, stated once:

* **a draft changes nothing**: its new items do not exist in the bill of quantities, so no certificate can bill them;
* **approval writes into the bill of quantities**, in one transaction with the project row and its items locked: each
  changed item's quantity moves, each new item becomes a line marked with the order (``BoqLine.variation``). On a lump-sum
  project the typed contract value moves by the order's amount. From then on the unchanged certificate engine
  (``contract.create_certificate``) bills the new work at the project's own retention and recovery rates;
* **the original contract value is kept**: ``Project.original_value`` is written once, at the first approval, from the
  contract value as it stood. Original + the approved orders = the contract value, always; the approval itself checks it
  and writes nothing when it does not close;
* **an omission never goes below what is certified**: checked when the order is drafted and again at approval, because a
  certificate may have been made in between;
* **a rate is never changed**: work at another rate is a new item;
* **the owner's approval is recorded by staff** (the date and the owner's reference) and needs the signed copy attached
  to the order first;
* **an approved, rejected or cancelled order is never edited or deleted**: a mistake is corrected by another order.

Nothing here moves money: no invoice, no payment, no stock, cashbox, customer-ledger or journal row is written, and no
certificate is touched. Every rule lives in this module; the views only call it.
"""

from datetime import date as date_type
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from config.money import money_round
from permissions.services import user_has_permission

from . import access, contract
from .models import BoqLine, Project, VariationLine, VariationOrder, VariationStatus

ZERO = Decimal("0")
QTY = Decimal("0.001")
MAX_LINES = 60
MAX_EXTENSION = 3650
MAX_TEXT = 3000
MAX_TITLE = 160

MESSAGES = {
    "ar": {
        "denied": "تسجيل أمر التغيير لمهندس الموقع أو لصاحب صلاحية الفواتير بس.",
        "decide_denied": "اعتماد أمر التغيير أو رفضه لصاحب صلاحية الفواتير بس.",
        "not_draft": "أمر التغيير ده اتقفل بالاعتماد أو الرفض أو الإلغاء؛ أي تعديل جديد يكون بأمر تغيير تاني.",
        "title": "اكتب عنوان أمر التغيير.", "title_long": "عنوان أمر التغيير أطول من المسموح ({limit} حرف).",
        "date": "اختار تاريخ أمر التغيير.", "future": "تاريخ أمر التغيير لازم يكون النهارده أو قبله.",
        "days": "مدة التمديد لازم تكون عدد أيام صحيح من 0 لـ {max}.", "reason_long": "سبب التغيير أطول من المسموح ({limit} حرف).",
        "unknown_item": "البند المختار مش من مقايسة المشروع ده.", "twice": "البند {code} مكتوب مرتين في نفس الأمر.",
        "change": "التغيير في كمية البند {code} لازم يكون رقم بالزيادة أو بالنقص (مثلاً 20 أو -100).",
        "below_certified": "كمية البند {code} بعد التعديل هتبقى {after}، وده أقل من الكمية اللي اتعمل بيها مستخلصات ({qty}).",
        "below_zero": "كمية البند {code} بعد التعديل مينفعش تبقى أقل من صفر.",
        "new_description": "اكتب وصف البند الجديد في الصف {n}.", "new_qty": "كمية البند الجديد في الصف {n} لازم تكون أكبر من صفر.",
        "new_rate": "اكتب فئة البند الجديد في الصف {n} (سعر الوحدة)، صفر أو أكبر.", "lines_many": "عدد بنود الأمر أكبر من المسموح ({n} بند).",
        "empty": "اكتب بند واحد على الأقل، أو مدة تمديد.", "empty_lump": "اكتب قيمة أمر التغيير، أو مدة تمديد.",
        "amount": "قيمة أمر التغيير لازم تكون رقم بالزيادة أو بالنقص.",
        "below_billed": "قيمة العقد بعد الأمر هتبقى {after}، وده أقل من قيمة المستخلصات اللي اتعملت ({billed}).",
        "lump_mode": "المشروع ده من غير مقايسة؛ أمر التغيير عليه بيكون بقيمة إجمالية.",
        "boq_mode": "المشروع ده ليه مقايسة؛ أمر التغيير عليه بيكون ببنود وكميات.",
        "signed_copy": "ارفع نسخة أمر التغيير الموقّعة من المالك قبل الاعتماد.",
        "approved_on": "تاريخ اعتماد المالك مش صحيح.", "approved_future": "تاريخ اعتماد المالك لازم يكون النهارده أو قبله.",
        "approved_before": "تاريخ اعتماد المالك مينفعش يكون قبل تاريخ الأمر ({date}).",
        "mismatch": "قيمة العقد بعد الاعتماد مش مساوية للقيمة الأصلية زائد أوامر التغيير المعتمدة، فالاعتماد ما اتسجّلش. تواصل مع الدعم الفني.",
    },
    "en": {
        "denied": "Only the site engineer, or someone who may create invoices, can record a variation order.",
        "decide_denied": "Only someone who may create invoices can approve or reject a variation order.",
        "not_draft": "This variation order is closed (approved, rejected or cancelled); a further change is another variation order.",
        "title": "Type the title of the variation order.", "title_long": "The title is longer than allowed ({limit} characters).",
        "date": "Choose the date of the variation order.", "future": "The date of the variation order must be today or earlier.",
        "days": "The extension must be a whole number of days from 0 to {max}.", "reason_long": "The reason is longer than allowed ({limit} characters).",
        "unknown_item": "The chosen item is not on this project's bill of quantities.", "twice": "Item {code} is on this order twice.",
        "change": "The change in the quantity of item {code} must be a number, up or down (for example 20 or -100).",
        "below_certified": "Item {code} would be left at {after}, below what certificates already billed ({qty}).",
        "below_zero": "The quantity of item {code} cannot go below zero.",
        "new_description": "Describe the new item in row {n}.", "new_qty": "The quantity of the new item in row {n} must be above zero.",
        "new_rate": "Type the rate of the new item in row {n} (price per unit), 0 or more.", "lines_many": "The order has more items than allowed ({n}).",
        "empty": "Add at least one item, or an extension of time.", "empty_lump": "Type the amount of the variation order, or an extension of time.",
        "amount": "The amount of the variation order must be a number, up or down.",
        "below_billed": "The contract value would be left at {after}, below what certificates already billed ({billed}).",
        "lump_mode": "This project has no bill of quantities; a variation order on it is one amount.",
        "boq_mode": "This project has a bill of quantities; a variation order on it is made of items and quantities.",
        "signed_copy": "Attach the copy of the variation order signed by the owner before approving it.",
        "approved_on": "The date of the owner's approval is not valid.", "approved_future": "The date of the owner's approval must be today or earlier.",
        "approved_before": "The owner's approval cannot be dated before the order ({date}).",
        "mismatch": "The contract value after the approval would not equal the original value plus the approved variation orders, so nothing was recorded. Contact support.",
    },
}

STATUS = {
    "ar": {"draft": "مسودة", "approved": "معتمد", "rejected": "مرفوض", "cancelled": "ملغي"},
    "en": {"draft": "Draft", "approved": "Approved", "rejected": "Rejected", "cancelled": "Cancelled"},
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def status_label(status, lang):
    return STATUS["en" if lang == "en" else "ar"].get(status, status)


# ---- who ----

def can_view(user):
    return access.can_view(user)


def can_draft(user):
    """Write and edit a draft, attach files to it, withdraw it: whoever records progress on the project (the site engineer, the office)."""

    return access.can_view(user) and access.can_write(user)


def can_decide(user):
    """Record the owner's approval or refusal: the office only (``sales.create_sales_invoice``). A site engineer drafts and never approves."""

    return access.can_view(user) and user_has_permission(user, access.MANAGE)


# ---- small readers ----

def parse_date(raw):
    """A posted day (YYYY-MM-DD) or a date, else None."""

    if isinstance(raw, date_type) and not isinstance(raw, datetime):
        return raw
    try:
        return date_type.fromisoformat(str(raw or "").strip())
    except ValueError:
        return None


def qty_text(number):
    """A quantity as a person writes it: 750, 12.5 (never 7.5E+2)."""

    return format(Decimal(number).normalize(), "f")


def _label(item):
    return item.code or str(item.line_number)


def change_value(item, change):
    """What a change in an item's quantity adds to the bill of quantities: the item's amount after, less its amount before.
    (The difference of the two rounded amounts, not the rounded product, so the items always add up to the contract value.)"""

    before = contract.line_amount(item)
    after = money_round((Decimal(item.quantity) + Decimal(change)) * Decimal(item.rate))
    return after - before


def billed_to_date(project):
    """The work value live certificates (drafts too) billed on this project, after posted returns."""

    return money_round(sum((contract.effective(c)[0] for c in contract.live_certificates(project).select_related("invoice")), ZERO))


def _check_floor(item, change, words):
    after = Decimal(item.quantity) + Decimal(change)
    if after < 0:
        raise ValidationError(words["below_zero"].format(code=_label(item)))
    certified = contract.certified_quantity(item)
    if after < certified:
        raise ValidationError(words["below_certified"].format(code=_label(item), after=qty_text(after), qty=qty_text(certified)))


def _check_lump_floor(project, amount, words):
    after = Decimal(project.contract_value) + amount
    billed = billed_to_date(project)
    if after < 0 or after < billed:
        raise ValidationError(words["below_billed"].format(after=money_round(after), billed=billed))


def _audit(order, user, action, after, before=None, event=AuditEventType.UPDATE):
    AuditLog.objects.create(event_type=event, actor=user, module="projects", action=action, object_type="projects.VariationOrder",
                            object_id=str(order.pk), before_data=before or {}, after_data=after)


def _snapshot(order):
    """What the audit row keeps of an order: its figures, never the free text."""

    lines = list(order.lines.select_related("boq_line"))
    return {"project": order.project.code, "number": order.number, "date": order.order_date.isoformat(), "status": order.status,
            "extension_days": order.extension_days, "lump_amount": str(order.lump_amount) if order.lump_amount is not None else "",
            "value": str(order_value(order, lines)),
            "lines": [{"item": line.code, "new": line.boq_line_id is None, "change": str(line.quantity_change), "rate": str(line.rate), "amount": str(line.amount)}
                      for line in lines]}


# ---- drafting ----

def _typed_changes(changes, boq, words):
    """[(item, change)] from the (item id, typed change) pairs; blank and zero rows are dropped."""

    rows, seen = [], set()
    for raw_id, raw in changes:
        text = str(raw if raw is not None else "").strip()
        if not text:
            continue
        item = boq.get(int(raw_id)) if str(raw_id).isdigit() else None
        if item is None:
            raise ValidationError(words["unknown_item"])
        number = contract._decimal(text)
        if number is None:
            raise ValidationError(words["change"].format(code=_label(item)))
        number = number.quantize(QTY)
        if number == 0:
            continue
        if item.pk in seen:
            raise ValidationError(words["twice"].format(code=_label(item)))
        seen.add(item.pk)
        _check_floor(item, number, words)
        rows.append((item, number))
    return rows


def _typed_new_items(new_items, words):
    """[{code, description, unit, quantity, rate}] from the typed rows; a row left wholly blank is dropped."""

    rows = []
    for n, raw in enumerate(new_items, start=1):
        typed = {key: str(raw.get(key) or "").strip() for key in ("code", "description", "unit", "quantity", "rate")}
        if not any(typed.values()):
            continue
        if not typed["description"]:
            raise ValidationError(words["new_description"].format(n=n))
        quantity = contract._decimal(typed["quantity"])
        if quantity is None or quantity <= 0:
            raise ValidationError(words["new_qty"].format(n=n))
        rate = contract._decimal(typed["rate"]) if typed["rate"] else None
        if rate is None or rate < 0:
            raise ValidationError(words["new_rate"].format(n=n))
        rows.append({"code": typed["code"][:20], "description": typed["description"][:255], "unit": typed["unit"][:30],
                     "quantity": quantity.quantize(QTY), "rate": money_round(rate)})
    return rows


@transaction.atomic
def save_order(project, user, *, order=None, order_date=None, title="", reason="", extension_days=0, changes=(), new_items=(), amount=None, lang="ar"):
    """Write a draft variation order on ``project``, or edit ``order`` while it is still a draft. Returns the order.

    ``changes`` is (item id, typed change in quantity) pairs; ``new_items`` is rows of code, description, unit, quantity and
    rate; ``amount`` is the order's value on a project without a bill of quantities. The lines are replaced as typed.
    Needs the right to record progress; the project must be open and its money in the entity being worked in. A draft
    changes nothing but itself."""

    words = _words(lang)
    if not can_draft(user):
        raise PermissionDenied(words["denied"])
    project = contract._locked(project, lang)
    if order is not None:
        order = VariationOrder.objects.select_for_update().get(pk=order.pk, project=project)
        if order.status != VariationStatus.DRAFT:
            raise ValidationError(words["not_draft"])
    title = str(title or "").strip()
    if not title:
        raise ValidationError(words["title"])
    if len(title) > MAX_TITLE:
        raise ValidationError(words["title_long"].format(limit=MAX_TITLE))
    day = parse_date(order_date)
    if day is None:
        raise ValidationError(words["date"])
    if day > timezone.localdate():
        raise ValidationError(words["future"])
    reason = str(reason or "").strip()
    if len(reason) > MAX_TEXT:
        raise ValidationError(words["reason_long"].format(limit=MAX_TEXT))
    days_text = str(extension_days if extension_days not in (None, "") else "0").strip()
    if not days_text.isascii() or not days_text.isdigit() or int(days_text) > MAX_EXTENSION:
        raise ValidationError(words["days"].format(max=MAX_EXTENSION))
    days = int(days_text)

    boq = {item.pk: item for item in project.boq.select_for_update()}
    changes, new_items = list(changes or ()), list(new_items or ())
    lines, lump = [], None
    if boq:
        typed_amount = contract._decimal(amount)
        if typed_amount is None or typed_amount != 0:
            raise ValidationError(words["boq_mode"])
        for item, change in _typed_changes(changes, boq, words):
            lines.append(VariationLine(boq_line=item, code=item.code or str(item.line_number), description=item.description, unit=item.unit,
                                       quantity_change=change, rate=item.rate, amount=change_value(item, change)))
        for row in _typed_new_items(new_items, words):
            lines.append(VariationLine(boq_line=None, code=row["code"], description=row["description"], unit=row["unit"], quantity_change=row["quantity"],
                                       rate=row["rate"], amount=money_round(row["quantity"] * row["rate"])))
        if len(lines) > MAX_LINES:
            raise ValidationError(words["lines_many"].format(n=MAX_LINES))
        if not lines and not days:
            raise ValidationError(words["empty"])
    else:
        if any(str(raw if raw is not None else "").strip() for _, raw in changes) or _typed_new_items(new_items, words):
            raise ValidationError(words["lump_mode"])
        typed_amount = contract._decimal(amount)
        if typed_amount is None:
            raise ValidationError(words["amount"])
        typed_amount = money_round(typed_amount)
        if typed_amount != 0:
            lump = typed_amount
            if lump < 0:
                _check_lump_floor(project, lump, words)
        if lump is None and not days:
            raise ValidationError(words["empty_lump"])

    created = order is None
    before = {} if created else _snapshot(order)
    if created:
        top = project.variation_orders.order_by("-number").values_list("number", flat=True).first() or 0
        order = VariationOrder(project=project, number=top + 1, created_by=user)
    else:
        order.updated_by = user
    order.order_date, order.title, order.reason, order.extension_days, order.lump_amount = day, title, reason, days, lump
    order.save()
    order.lines.all().delete()
    for position, line in enumerate(lines):
        line.order, line.position = order, position
    VariationLine.objects.bulk_create(lines)
    _audit(order, user, "save_variation_order", _snapshot(order), before, AuditEventType.CREATE if created else AuditEventType.UPDATE)
    return order


# ---- the decision ----

def signed_copies(order):
    """How many files are attached to the order (the copy the owner signed)."""

    from django.contrib.contenttypes.models import ContentType

    from attachments.models import Attachment

    return Attachment.objects.filter(content_type=ContentType.objects.get_for_model(VariationOrder), object_id=order.pk).count()


@transaction.atomic
def approve(project, order, user, *, approved_on=None, owner_reference="", lang="ar"):
    """Record the owner's approval of a draft order and write it into the contract. Returns the order.

    One transaction, with the project row, the order and the items of the bill of quantities locked. Needs
    ``sales.create_sales_invoice`` and the signed copy attached. The omission floor is checked again here. Writes no money."""

    words = _words(lang)
    if not can_decide(user):
        raise PermissionDenied(words["decide_denied"])
    given = project
    project = contract._locked(project, lang)
    order = VariationOrder.objects.select_for_update().get(pk=order.pk, project=project)
    if order.status != VariationStatus.DRAFT:
        raise ValidationError(words["not_draft"])
    today = timezone.localdate()
    day = today if approved_on in (None, "") else parse_date(approved_on)
    if day is None:
        raise ValidationError(words["approved_on"])
    if day > today:
        raise ValidationError(words["approved_future"])
    if day < order.order_date:
        from settings_core.templatetags.hesba_format import day_text

        raise ValidationError(words["approved_before"].format(date=day_text(order.order_date, "en" if lang == "en" else "ar")))
    if not signed_copies(order):
        raise ValidationError(words["signed_copy"])

    before = contract.contract_value(project)
    boq = {item.pk: item for item in project.boq.select_for_update()}
    lines = list(order.lines.all())
    total = ZERO
    if boq:
        if order.lump_amount:
            raise ValidationError(words["boq_mode"])
        top = max(item.line_number for item in boq.values())
        for line in lines:
            if line.boq_line_id is not None:
                item = boq.get(line.boq_line_id)
                if item is None:
                    raise ValidationError(words["unknown_item"])
                _check_floor(item, line.quantity_change, words)  # a certificate may have been made since the draft
                line.rate, line.amount = item.rate, change_value(item, line.quantity_change)
                item.quantity = Decimal(item.quantity) + line.quantity_change
                item.save(update_fields=["quantity"])
            else:
                top += 1
                item = BoqLine.objects.create(project=project, line_number=top, code=line.code, description=line.description, unit=line.unit,
                                              quantity=line.quantity_change, rate=line.rate, variation=order, created_by=user)
                line.amount = contract.line_amount(item)
            line.save(update_fields=["rate", "amount"])
            total += line.amount
    else:
        if lines:
            raise ValidationError(words["lump_mode"])
        total = money_round(order.lump_amount or ZERO)
        if total < 0:
            _check_lump_floor(project, total, words)
        project.contract_value = money_round(Decimal(project.contract_value) + total)
    if project.original_value is None:
        project.original_value = before  # once, for good: the contract value before its first variation
    project.save(update_fields=["contract_value", "original_value"])
    order.status, order.value, order.approved_on = VariationStatus.APPROVED, money_round(total), day
    order.owner_reference, order.decided_by, order.decided_at = str(owner_reference or "").strip()[:80], user, timezone.now()
    order.save()
    after = contract.contract_value(project)
    if after != money_round(before + total) or revised_value(project) != after:
        raise ValidationError(words["mismatch"])  # the arithmetic must close, or the whole approval is undone
    _audit(order, user, "approve_variation_order",
           {**_snapshot(order), "approved_on": day.isoformat(), "contract_before": str(before), "contract_after": str(after),
            "original_value": str(project.original_value), "signed_copies": signed_copies(order)},
           {"status": VariationStatus.DRAFT, "contract_before": str(before)})
    given.contract_value, given.original_value = project.contract_value, project.original_value  # the caller's copy reads the new figures too
    return order


@transaction.atomic
def close(project, order, user, *, status, note="", lang="ar"):
    """Reject a draft (the owner refused: the office records it) or cancel it (withdrawn before it was decided: whoever may
    draft). The order stays as a record; nothing else changes."""

    words = _words(lang)
    if status not in (VariationStatus.REJECTED, VariationStatus.CANCELLED):
        raise ValueError(status)
    if not (can_decide(user) if status == VariationStatus.REJECTED else can_draft(user)):
        raise PermissionDenied(words["decide_denied" if status == VariationStatus.REJECTED else "denied"])
    # The project row first (``_held``, not ``_locked``: a closed project's drafts can still be tidied up), then the order:
    # the same lock order as drafting and approving, and the same entity rule.
    project = contract._held(project, lang)
    order = VariationOrder.objects.select_for_update().get(pk=order.pk, project=project)
    if order.status != VariationStatus.DRAFT:
        raise ValidationError(words["not_draft"])
    order.status, order.decided_by, order.decided_at, order.decision_note = status, user, timezone.now(), str(note or "").strip()[:255]
    order.save()
    _audit(order, user, "reject_variation_order" if status == VariationStatus.REJECTED else "cancel_variation_order",
           _snapshot(order), {"status": VariationStatus.DRAFT})
    return order


# ---- reading ----

def in_reach(project):
    """Whether the entity being worked in holds this project's money. A variation order changes the base its certificates
    bill from, so, like the certificates themselves, it is not shown or changed from another entity."""

    return contract.in_reach(project)


def get_order(project, pk):
    """An order of this project by id, else None: another project's id finds nothing, and neither does any id from an
    entity that does not hold this project's money (the screens answer 404)."""

    raw = str(pk or "")
    if not raw.isdigit() or not in_reach(project):
        return None
    return VariationOrder.objects.select_related("project", "created_by", "decided_by").filter(project=project, pk=raw).first()


def orders_for(project):
    """The project's orders, newest first; none at all from another entity."""

    if not in_reach(project):
        return VariationOrder.objects.none()
    return VariationOrder.objects.filter(project=project).select_related("created_by", "decided_by").order_by("-number")


def order_value(order, lines=None):
    """What the order adds to the contract: fixed once approved; for a draft, read from the bill of quantities as it stands
    now; for a rejected or cancelled one, as it was last saved."""

    if order.status == VariationStatus.APPROVED:
        return money_round(order.value)
    if order.lump_amount is not None:
        return money_round(order.lump_amount)
    lines = list(order.lines.select_related("boq_line")) if lines is None else lines
    if order.status != VariationStatus.DRAFT:
        return money_round(sum((line.amount for line in lines), ZERO))
    return money_round(sum((change_value(line.boq_line, line.quantity_change) if line.boq_line_id else line.amount for line in lines), ZERO))


def line_rows(order):
    """The order's lines as a reader sees them. For a draft, a changed item also shows its quantity now and after."""

    rows = []
    draft = order.status == VariationStatus.DRAFT
    for line in order.lines.select_related("boq_line"):
        item = line.boq_line
        row = {"line": line, "new": item is None, "amount": line.amount, "rate": line.rate, "now": None, "after": None}
        if item is not None and draft:
            row.update(now=item.quantity, after=Decimal(item.quantity) + line.quantity_change, rate=item.rate, amount=change_value(item, line.quantity_change))
        rows.append(row)
    return rows


def approved_total(project):
    return money_round(project.variation_orders.filter(status=VariationStatus.APPROVED).aggregate(total=Sum("value"))["total"] or ZERO)


def original_value(project):
    """The contract value before any variation order: the stored one, or the contract value itself while none is approved."""

    stored = Project.objects.filter(pk=project.pk).values_list("original_value", flat=True).first()  # the row, never a stale instance
    return money_round(stored) if stored is not None else contract.contract_value(project)


def revised_value(project):
    """The original contract value plus the approved variation orders. By construction it equals ``contract.contract_value``."""

    return money_round(original_value(project) + approved_total(project))


def extension_days(project):
    """The days approved variation orders added to the contract's time."""

    return project.variation_orders.filter(status=VariationStatus.APPROVED).aggregate(total=Sum("extension_days"))["total"] or 0


def planned_end(project):
    """The project's planned end with the approved extensions of time; the end date itself when there are none (or no date)."""

    if project.end_date is None:
        return None
    days = extension_days(project)
    return project.end_date + timedelta(days=days) if days else project.end_date


def contract_before(order):
    """The contract value this order starts from: for an approved one, as it stood when it was approved; else as it stands now."""

    project = order.project
    if order.status != VariationStatus.APPROVED:
        return contract.contract_value(project)
    earlier = project.variation_orders.filter(status=VariationStatus.APPROVED).exclude(pk=order.pk)
    earlier = [other for other in earlier if (other.decided_at, other.pk) < (order.decided_at, order.pk)]
    return money_round(original_value(project) + sum((other.value for other in earlier), ZERO))


def figures(project):
    """What the project's screens show of its variation orders: the original value, the approved orders, the revised value,
    the drafts waiting (a memo, not in the contract) and the extension of time."""

    orders = list(project.variation_orders.all())
    approved = [order for order in orders if order.status == VariationStatus.APPROVED]
    drafts = [order for order in orders if order.status == VariationStatus.DRAFT]
    original = original_value(project)
    approved_sum = money_round(sum((order.value for order in approved), ZERO))
    days = sum(order.extension_days for order in approved)
    current = contract.contract_value(project)
    return {"count": len(orders), "approved_count": len(approved), "pending_count": len(drafts), "original": original, "approved": approved_sum,
            "revised": money_round(original + approved_sum), "contract": current, "reconciled": money_round(original + approved_sum) == current,
            "pending": money_round(sum((order_value(order) for order in drafts), ZERO)), "extension_days": days,
            "end": project.end_date, "planned_end": project.end_date + timedelta(days=days) if project.end_date and days else project.end_date}


def order_numbers(project):
    """{bill-of-quantities line id: the number of the variation order that added it}."""

    return dict(project.boq.filter(variation__isnull=False).values_list("pk", "variation__number"))
