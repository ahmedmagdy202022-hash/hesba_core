"""Contracting C7 (HG-125): purchase requests from the site, with approval (طلبات الشراء).

A request is a document. It holds no money and moves no stock; nothing here posts. Rules, stated once:

* **Who.** Seeing a project's requests: the project right (``projects.view_projects`` or ``sales.view_sales_invoices``) or the
  purchases right (``purchases.view_purchase_invoices``, as on the subcontractors tab), so the stock keeper reads them.
  Drafting, editing a draft, submitting and cancelling one's draft: whoever records progress on the project
  (``projects.record_progress`` or ``sales.create_sales_invoice``). **Deciding** (approve, reject, make the purchase order,
  cancel an approved request): ``purchases.create_purchase_invoice`` **and** the project right, and **never the requester**
  (owner question 18: any purchaser, never the requester, no amount threshold). The stock keeper holds the purchases right
  but not the project's, so he reads only.
* **Steps.** draft → submitted (the engineer) → approved / rejected (the approver). Approving may change quantities (each
  change is written into the decision note and the asked quantity kept on the line) and may choose a catalogue item for a
  line typed as free text. **In the same transaction** the lines that have an item become an ordinary **draft purchase
  order** for the chosen supplier, made by ``orders.purchasing.create_order`` and ``add_line`` unchanged; the request is then
  «ordered» and keeps the order in ``purchase_order``. A request with no item on any line is approved with no order.
  Rejecting needs a reason and makes no order. A submitted request can go back to draft for edits.
* **A cancelled purchase order** brings the request back to approved: ``state`` reads the order's own status (a cancelled
  order has no way back in ``orders.purchasing.NEXT``), nothing is written to the request, and the approver may make a new
  order from it, which replaces the link. ``orders`` gains no hook and no column.
* **Units.** A line with an item is in the item's own unit (the unit the purchase order and the purchase invoice count in);
  only a free-text line carries a typed unit.
* **Entities.** Requests follow the project's money (``contract.in_reach``), as the schedule and the variation orders do.
"""

from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission
from settings_core.templatetags.hesba_format import unit_label

from . import access, contract, services
from .models import BoqLine, Project, PurchaseRequest, PurchaseRequestLine, PurchaseRequestStatus as Status

VIEW_PURCHASES, BUY = "purchases.view_purchase_invoices", "purchases.create_purchase_invoice"
MAX_LINES = 15  # the form's «+ سطر تاني» stops at 15 rows (static/hesba/js/site_report.js)
MAX_QUANTITY = Decimal("99999999999.999")
THOUSANDTH = Decimal("0.001")
#: Where an approved request waits for its purchase order: approved with none yet, or ordered and that order was cancelled.
TO_ORDER = Q(status=Status.APPROVED) | Q(status=Status.ORDERED, purchase_order__status="cancelled")

MESSAGES = {
    "ar": {
        "no_lines": "ضيف سطر واحد على الأقل: الصنف أو وصفه والكمية.",
        "too_many": "الطلب الواحد فيه {max} سطر بالكتير؛ اعمل طلب تاني للباقي.",
        "line_what": "السطر {n}: اختار الصنف أو اكتب وصفه.",
        "line_quantity": "السطر {n}: الكمية لازم تكون رقم أكبر من صفر.",
        "line_item": "السطر {n}: الصنف ده مش موجود أو مش نشط.",
        "line_boq": "السطر {n}: اختار بند مقايسة من نفس المشروع.",
        "same_item": "«{item}» موجود في سطرين؛ اجمع الكمية في سطر واحد.",
        "needed_by": "تاريخ «محتاجينه قبل» مش مظبوط.",
        "not_draft": "الطلب ده اتبعت للموافقة؛ رجّعه مسودة الأول عشان تعدّله.",
        "not_submitted": "الطلب ده مش مستني موافقة دلوقتي.",
        "own": "مينفعش توافق على طلب انت اللي طلبته؛ لازم حد تاني من المشتريات يوافق عليه.",
        "own_reject": "ده طلبك انت؛ لو مبقيتش محتاجه الغيه بدل ما ترفضه.",
        "reason": "اكتب سبب الرفض.",
        "supplier": "اختار المورد اللي هيتعمل له أمر الشراء.",
        "quantity": "«{what}»: الكمية لازم تكون رقم أكبر من صفر.",
        "item": "«{what}»: الصنف اللي اخترته مش موجود أو مش نشط.",
        "purchases_off": "المشتريات مقفولة في الإعدادات، فمينفعش يتعمل أمر شراء.",
        "not_to_order": "الطلب ده مش مستني أمر شراء.",
        "nothing_to_order": "مفيش ولا سطر عليه صنف من الأصناف؛ اختار الصنف للسطور الأول.",
        "cannot_cancel": "الطلب ده عليه أمر شراء شغّال؛ الغي أمر الشراء الأول لو مش هتشتري.",
        "cancel_decide": "إلغاء طلب اتوافق عليه للي بيوافق على طلبات الشراء بس.",
        "changed_qty": "«{what}»: اتطلب {before} واتوافق على {after} {unit}",
        "chose_item": "«{what}»: اتحدد له الصنف «{item}»",
        "not_ordered": "مش في أمر الشراء (من غير صنف): {names}",
        "po_note": "طلب شراء {number} · {project}",
    },
    "en": {
        "no_lines": "Add at least one line: the item or its description, and the quantity.",
        "too_many": "A request holds {max} lines at most; make another request for the rest.",
        "line_what": "Line {n}: choose the item or describe it.",
        "line_quantity": "Line {n}: the quantity must be a number above zero.",
        "line_item": "Line {n}: that item does not exist or is not active.",
        "line_boq": "Line {n}: choose an item of this project's bill of quantities.",
        "same_item": "«{item}» is on two lines; put its quantity on one line.",
        "needed_by": "The «needed by» date is not valid.",
        "not_draft": "This request was sent for approval; take it back to draft to edit it.",
        "not_submitted": "This request is not waiting for approval now.",
        "own": "You cannot approve a request you made yourself; someone else who buys must approve it.",
        "own_reject": "This is your own request; cancel it instead of rejecting it if it is no longer needed.",
        "reason": "Type the reason for the rejection.",
        "supplier": "Choose the supplier the purchase order is for.",
        "quantity": "«{what}»: the quantity must be a number above zero.",
        "item": "«{what}»: the item you chose does not exist or is not active.",
        "purchases_off": "Purchases are switched off in the settings, so no purchase order can be made.",
        "not_to_order": "This request is not waiting for a purchase order.",
        "nothing_to_order": "No line names a catalogue item; choose the items for the lines first.",
        "cannot_cancel": "This request has a live purchase order; cancel the purchase order first if nothing will be bought.",
        "cancel_decide": "Only someone who approves purchase requests can cancel an approved one.",
        "changed_qty": "«{what}»: asked {before}, approved {after} {unit}",
        "chose_item": "«{what}»: item «{item}» chosen",
        "not_ordered": "Not on the purchase order (no item): {names}",
        "po_note": "Purchase request {number} · {project}",
    },
}


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


# ---- who ----

def can_view(user):
    return access.can_view(user) or user_has_permission(user, VIEW_PURCHASES)


def can_draft(user):
    """Draft, edit a draft, submit, take back to draft, cancel a draft: whoever records progress on the project."""

    return access.can_view(user) and access.can_write(user)


def can_decide(user):
    """Approve, reject, make the purchase order: who buys, and sees the project. Never the requester (checked per request)."""

    return access.can_view(user) and user_has_permission(user, BUY)


def in_reach(project):
    return contract.in_reach(project)


def _need(allowed, what):
    if not allowed:
        raise PermissionDenied(what)


# ---- reading ----

def label(request):
    return f"PR-{request.project.code}-{request.number:02d}"


def state(request):
    """The request's status as the screens read it: «ordered» whose purchase order was cancelled reads as approved."""

    if request.status == Status.ORDERED and request.purchase_order_id and request.purchase_order.status == "cancelled":
        return Status.APPROVED
    return request.status


def order_was_cancelled(request):
    return request.purchase_order_id is not None and request.purchase_order.status == "cancelled"


def visible(user):
    """Every request this viewer may see: of the projects whose money is in the entity being worked in (all for the group)."""

    from entities.current import current_entity

    _need(can_view(user), "Purchase requests need the project or the purchases view right.")
    rows = PurchaseRequest.objects.select_related("project", "requested_by", "purchase_order", "purchase_order__supplier")
    if not access.module_open():
        return rows.none()
    if current_entity() is None:
        return rows
    ids = [project.pk for project in Project.objects.filter(purchase_requests__isnull=False).distinct() if in_reach(project)]
    return rows.filter(project_id__in=ids)


def for_project(project):
    return list(PurchaseRequest.objects.filter(project=project).select_related("requested_by", "purchase_order", "purchase_order__supplier")
                .prefetch_related("lines"))


def get_request(project, pk):
    """A request of this project by id, else None: another project's id finds nothing, nor does any id from an entity that
    does not hold this project's money (the screens answer 404)."""

    raw = str(pk or "")
    if not raw.isdigit() or not in_reach(project):
        return None
    return (PurchaseRequest.objects.select_related("project", "requested_by", "decided_by", "purchase_order", "purchase_order__supplier")
            .filter(project=project, pk=raw).first())


def waiting_count(user):
    """Requests waiting for approval that this viewer may see (the office's dashboard tile)."""

    return visible(user).filter(status=Status.SUBMITTED).count()


# ---- helpers ----

def _audit(user, action, request, after, event=AuditEventType.UPDATE, before=None):
    AuditLog.objects.create(event_type=event, actor=user, module="projects", action=action, object_type="projects.PurchaseRequest",
                            object_id=str(request.pk), before_data=before or {}, after_data={"number": label(request), **after})


def _quantity(raw):
    try:
        value = Decimal(str(raw).strip().replace(",", "")) if raw not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None
    if value is None or not value.is_finite():
        return None
    value = value.quantize(THOUSANDTH)
    return value if Decimal("0") < value <= MAX_QUANTITY else None


def _item(raw):
    from master_data.models import Item

    if hasattr(raw, "pk"):
        return raw if raw.active else None
    raw = str(raw or "").strip()
    return Item.objects.filter(pk=raw, active=True).first() if raw.isdigit() else None


def _text(value):
    return f"{value.normalize():f}"


def _what(line):
    return line.item.item_name if line.item_id else line.description


def _project_locked(project, lang):
    """The project row, locked, refused when it is done or cancelled or its money lives in another entity."""

    project = services._open(project, services.MESSAGES["en" if lang == "en" else "ar"])
    if not in_reach(project):
        raise ValidationError(contract.MESSAGES["en" if lang == "en" else "ar"]["entity"])
    return project


def _request_locked(project, request):
    return PurchaseRequest.objects.select_for_update().select_related("project").get(pk=request.pk, project=project)


def _parse_lines(project, rows, words):
    """``rows``: dicts of item, description, unit, quantity, boq_line, note (as typed). Blank rows are skipped."""

    lines, seen = [], set()
    for row in rows:
        item_raw, description = str(row.get("item") or "").strip(), (row.get("description") or "").strip()[:255]
        quantity_raw, boq_raw = str(row.get("quantity") or "").strip(), str(row.get("boq_line") or "").strip()
        note, unit = (row.get("note") or "").strip()[:255], (row.get("unit") or "").strip()[:50]
        if not (item_raw or description or quantity_raw or note):
            continue
        n = len(lines) + 1
        if n > MAX_LINES:
            raise ValidationError(words["too_many"].format(max=MAX_LINES))
        item = None
        if item_raw:
            item = _item(item_raw)
            if item is None:
                raise ValidationError(words["line_item"].format(n=n))
        elif not description:
            raise ValidationError(words["line_what"].format(n=n))
        quantity = _quantity(quantity_raw)
        if quantity is None:
            raise ValidationError(words["line_quantity"].format(n=n))
        boq = None
        if boq_raw:
            boq = BoqLine.objects.filter(project=project, pk=boq_raw).first() if boq_raw.isdigit() else None
            if boq is None:
                raise ValidationError(words["line_boq"].format(n=n))
        if item is not None:
            if item.pk in seen:
                raise ValidationError(words["same_item"].format(item=item.item_name))
            seen.add(item.pk)
            unit = item.unit  # the purchase order counts in the item's own unit
        lines.append({"item": item, "description": description, "unit": unit, "quantity": quantity, "boq_line": boq, "note": note})
    if not lines:
        raise ValidationError(words["no_lines"])
    return lines


def _needed_by(raw, words):
    from datetime import date

    raw = str(raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValidationError(words["needed_by"]) from exc


def _snapshot(request):
    return [{"item": line.item.item_code if line.item_id else "", "description": line.description, "unit": line.unit,
             "quantity": str(line.quantity)} for line in request.lines.select_related("item")]


# ---- the engineer ----

@transaction.atomic
def save_request(project, user, data, request=None, lang="ar"):
    """Draft a new request or edit a draft. ``data``: needed_by, note, and ``lines`` (dicts as typed)."""

    words = _words(lang)
    _need(can_draft(user), "Purchase requests need projects.record_progress or sales.create_sales_invoice.")
    project = _project_locked(project, lang)
    needed_by = _needed_by(data.get("needed_by"), words)
    lines = _parse_lines(project, data.get("lines") or [], words)
    note = (data.get("note") or "").strip()[:255]
    if request is None:
        number = (PurchaseRequest.objects.filter(project=project).aggregate(top=Max("number"))["top"] or 0) + 1
        request = PurchaseRequest.objects.create(project=project, number=number, needed_by=needed_by, note=note, requested_by=user)
        before, event = {}, AuditEventType.CREATE
    else:
        request = _request_locked(project, request)
        if request.status != Status.DRAFT:
            raise ValidationError(words["not_draft"])
        before, event = {"needed_by": str(request.needed_by or ""), "lines": _snapshot(request)}, AuditEventType.UPDATE
        request.needed_by, request.note = needed_by, note
        request.save(update_fields=["needed_by", "note", "updated_at"])
        request.lines.all().delete()
    PurchaseRequestLine.objects.bulk_create([PurchaseRequestLine(request=request, position=index, **line) for index, line in enumerate(lines, 1)])
    _audit(user, "save_purchase_request", request, {"needed_by": str(needed_by or ""), "lines": _snapshot(request)}, event, before)
    return request


@transaction.atomic
def submit(project, request, user, lang="ar"):
    words = _words(lang)
    _need(can_draft(user), "Submitting a purchase request needs projects.record_progress or sales.create_sales_invoice.")
    project = _project_locked(project, lang)
    request = _request_locked(project, request)
    if request.status != Status.DRAFT:
        raise ValidationError(words["not_draft"])
    if not request.lines.exists():
        raise ValidationError(words["no_lines"])
    request.status, request.submitted_at = Status.SUBMITTED, timezone.now()
    request.save(update_fields=["status", "submitted_at", "updated_at"])
    _audit(user, "submit_purchase_request", request, {"status": request.status}, before={"status": Status.DRAFT})
    return request


@transaction.atomic
def reopen(project, request, user, lang="ar"):
    """A submitted request goes back to draft for edits (before anyone decided)."""

    words = _words(lang)
    _need(can_draft(user), "Taking a purchase request back needs projects.record_progress or sales.create_sales_invoice.")
    project = _project_locked(project, lang)
    request = _request_locked(project, request)
    if request.status != Status.SUBMITTED:
        raise ValidationError(words["not_submitted"])
    request.status, request.submitted_at = Status.DRAFT, None
    request.save(update_fields=["status", "submitted_at", "updated_at"])
    _audit(user, "reopen_purchase_request", request, {"status": request.status}, before={"status": Status.SUBMITTED})
    return request


@transaction.atomic
def cancel(project, request, user, note="", lang="ar"):
    """A draft or submitted request: whoever drafts. An approved one with no live purchase order: whoever decides."""

    words = _words(lang)
    _need(can_draft(user) or can_decide(user), "Cancelling a purchase request needs the right to draft or to decide.")
    project = _project_locked(project, lang)
    request = _request_locked(project, request)
    current = state(request)
    if current in (Status.DRAFT, Status.SUBMITTED):
        _need(can_draft(user), "Cancelling a draft purchase request needs projects.record_progress or sales.create_sales_invoice.")
    elif current == Status.APPROVED:
        if not can_decide(user):
            raise ValidationError(words["cancel_decide"])
    else:
        raise ValidationError(words["cannot_cancel"] if current == Status.ORDERED else words["not_submitted"])
    request.status = Status.CANCELLED
    note = (note or "").strip()[:255]
    fields = ["status", "updated_at"]
    if note:
        request.decision_note = "\n".join(part for part in (request.decision_note, note) if part)
        fields.append("decision_note")
    request.save(update_fields=fields)
    _audit(user, "cancel_purchase_request", request, {"status": Status.CANCELLED, "note": note}, before={"status": current})
    return request


# ---- the approver ----

def _decider(request, user, words, key="own"):
    _need(can_decide(user), "Deciding a purchase request needs purchases.create_purchase_invoice and the project right.")
    if request.requested_by_id == user.pk:
        raise ValidationError(words[key])


@transaction.atomic
def reject(project, request, user, reason, lang="ar"):
    words = _words(lang)
    _need(can_decide(user), "Deciding a purchase request needs purchases.create_purchase_invoice and the project right.")
    project = _project_locked(project, lang)
    request = _request_locked(project, request)
    _decider(request, user, words, "own_reject")
    if request.status != Status.SUBMITTED:
        raise ValidationError(words["not_submitted"])
    reason = (reason or "").strip()[:1000]
    if not reason:
        raise ValidationError(words["reason"])
    request.status, request.decided_by, request.decided_at, request.decision_note = Status.REJECTED, user, timezone.now(), reason
    request.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "updated_at"])
    _audit(user, "reject_purchase_request", request, {"status": Status.REJECTED, "reason": reason}, before={"status": Status.SUBMITTED})
    return request


def _make_order(project, request, supplier, user, words, lang):
    """An ordinary draft purchase order for ``supplier`` with the request's lines that name an item (one order line per item),
    made by ``orders.purchasing`` unchanged. Its refusals (an inactive supplier or item) undo the whole approval."""

    from master_data.models import Supplier
    from orders import purchasing

    if not purchasing.enabled():
        raise ValidationError(words["purchases_off"])
    if not isinstance(supplier, Supplier):
        raise ValidationError(words["supplier"])
    merged = OrderedDict()
    for line in request.lines.select_related("item").filter(item__isnull=False):
        entry = merged.setdefault(line.item_id, {"item": line.item, "quantity": Decimal("0"), "notes": []})
        entry["quantity"] += line.quantity
        if line.note:
            entry["notes"].append(line.note)
    if not merged:
        raise ValidationError(words["nothing_to_order"])
    today = timezone.localdate()
    expected = request.needed_by if request.needed_by and request.needed_by >= today else None
    order = purchasing.create_order({"supplier": supplier, "order_date": today, "expected_on": expected,
                                     "notes": words["po_note"].format(number=label(request), project=project.name)}, user, lang)
    for entry in merged.values():
        purchasing.add_line(order, entry["item"], user, quantity=str(entry["quantity"]), description=" · ".join(entry["notes"])[:255], lang=lang)
    return order


@transaction.atomic
def approve(project, request, user, *, supplier=None, quantities=None, items=None, note="", lang="ar"):
    """Approve a submitted request, optionally changing quantities (``{line pk: typed}``) and choosing items for free-text lines
    (``{line pk: item or id}``), and make the draft purchase order in the same transaction when any line names an item."""

    words = _words(lang)
    _need(can_decide(user), "Deciding a purchase request needs purchases.create_purchase_invoice and the project right.")
    project = _project_locked(project, lang)
    request = _request_locked(project, request)
    _decider(request, user, words)
    if request.status != Status.SUBMITTED:
        raise ValidationError(words["not_submitted"])
    quantities, items = quantities or {}, items or {}
    changes, missing = [], []
    for line in request.lines.select_related("item"):
        what = _what(line)
        fields = []
        raw_item = items.get(line.pk) or items.get(str(line.pk))
        if line.item_id is None and raw_item:
            item = _item(raw_item)
            if item is None:
                raise ValidationError(words["item"].format(what=what))
            line.item, line.unit = item, item.unit
            fields += ["item", "unit"]
            changes.append(words["chose_item"].format(what=what, item=item.item_name))
        raw_quantity = quantities.get(line.pk, quantities.get(str(line.pk)))
        if raw_quantity not in (None, ""):
            quantity = _quantity(raw_quantity)
            if quantity is None:
                raise ValidationError(words["quantity"].format(what=what))
            if quantity != line.quantity:
                unit = unit_label(line.unit, lang) if line.item_id else line.unit
                changes.append(words["changed_qty"].format(what=_what(line), before=_text(line.quantity), after=_text(quantity), unit=unit).strip())
                line.requested_quantity, line.quantity = line.quantity, quantity
                fields += ["requested_quantity", "quantity"]
        if fields:
            line.save(update_fields=fields)
        if line.item_id is None:
            missing.append(line.description)
    if missing and len(missing) < request.lines.count():
        changes.append(words["not_ordered"].format(names="، ".join(missing) if lang != "en" else ", ".join(missing)))
    note = (note or "").strip()[:1000]
    request.decision_note = "\n".join(part for part in (*changes, note) if part)
    request.status, request.decided_by, request.decided_at = Status.APPROVED, user, timezone.now()
    if len(missing) < request.lines.count():
        request.purchase_order = _make_order(project, request, supplier, user, words, lang)
        request.status = Status.ORDERED
    request.save(update_fields=["status", "decided_by", "decided_at", "decision_note", "purchase_order", "updated_at"])
    _audit(user, "approve_purchase_request", request, {"status": request.status, "changes": changes, "note": note,
                                                       "purchase_order": request.purchase_order.number if request.purchase_order_id else ""},
           before={"status": Status.SUBMITTED})
    return request


@transaction.atomic
def make_order(project, request, user, *, supplier=None, lang="ar"):
    """A new draft purchase order for an approved request: one approved with no order yet, or whose order was cancelled."""

    from orders.models import PurchaseOrder

    words = _words(lang)
    _need(can_decide(user), "Deciding a purchase request needs purchases.create_purchase_invoice and the project right.")
    project = _project_locked(project, lang)
    request = _request_locked(project, request)
    _decider(request, user, words)
    if request.purchase_order_id:  # re-read the old order under its lock: it must still be cancelled
        old = PurchaseOrder.objects.select_for_update().get(pk=request.purchase_order_id)
        request.purchase_order = old
    if state(request) != Status.APPROVED:
        raise ValidationError(words["not_to_order"])
    before = {"status": request.status, "purchase_order": request.purchase_order.number if request.purchase_order_id else ""}
    request.purchase_order = _make_order(project, request, supplier, user, words, lang)
    request.status = Status.ORDERED
    request.save(update_fields=["status", "purchase_order", "updated_at"])
    _audit(user, "order_purchase_request", request, {"status": request.status, "purchase_order": request.purchase_order.number}, before=before)
    return request
