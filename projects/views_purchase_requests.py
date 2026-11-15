"""Contracting C7 (HG-125): the «طلبات الشراء» screens: the project's tab (its requests), the form that drafts or edits one,
one request (its lines, the engineer's submit, the approver's approval with the supplier and the quantities, the rejection),
and the list across projects: «مستنية موافقة» for the office (the dashboard tile opens it) and «طلباتي» for the engineer.

Every rule is in ``projects.purchase_requests``; the views read, call and render. A request is looked up THROUGH its project,
so another project's request id answers 404, and so does any request from an entity that does not hold the project's money.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from permissions.decorators import require_any_permission
from settings_core.templatetags.hesba_format import day_text, unit_label
from settings_core.ui_messages import translate

from . import purchase_requests as prq
from .contract_views import _context
from .models import Project, PurchaseRequestStatus as Status
from .services import OPEN
from .views import _base, _lang

SCREENS = ("projects.view_projects", "sales.view_sales_invoices", prq.VIEW_PURCHASES)
STATUS_CLASS = {"draft": "draft", "submitted": "open", "approved": "posted", "rejected": "cancelled", "ordered": "completed", "cancelled": "cancelled"}
BLANK_ROWS = 3

WORDS = {
    "ar": {
        "page_title": "طلبات الشراء",
        "intro": "مهندس الموقع بيكتب اللي محتاجه الموقع ويبعته للموافقة. اللي بيوافق من المشتريات بيختار المورد، ولما يوافق بيتعمل أمر شراء مسودة بالأصناف دي. الطلب نفسه مفيهوش فلوس ومبيحرّكش مخزون.",
        "new": "+ طلب شراء جديد", "number": "الطلب", "needed_by": "محتاجينه قبل", "lines_n": "{n} صنف", "requester": "طلبه", "status": "الحالة",
        "order": "أمر الشراء", "none": "لسه مفيش طلبات شراء للمشروع ده.", "ph": "—",
        "s_draft": "مسودة", "s_submitted": "مستني موافقة", "s_approved": "اتوافق عليه", "s_rejected": "اترفض", "s_ordered": "اتعمل أمر شراء", "s_cancelled": "اتلغى",
        "po_cancelled": "أمر الشراء {number} اتلغى؛ الطلب رجع «اتوافق عليه» ومستني أمر شراء جديد.",
        "view_only": "طلبات الشراء للقراءة بس.", "closed": "المشروع اتقفل أو اتلغى؛ طلبات الشراء للقراءة بس.",
        "other_entity": "طلبات الشراء محفوظة في الكيان اللي بتتعمل منه مستخلصات المشروع ده؛ اشتغل من الكيان ده عشان تشوفها.",
        # the form
        "form_new": "طلب شراء جديد", "form_edit": "تعديل {number}",
        "form_intro": "اكتب كل صنف في سطر: اختاره من الأصناف، أو اكتب وصفه لو مش موجود. الصنف اللي من الأصناف بياخد وحدته هو.",
        "f_needed_by": "محتاجينه قبل (اختياري)", "f_note": "ملاحظة للمشتريات (اختياري)", "f_note_ph": "مثلاً: للدور الثالث، التوريد على الموقع",
        "lines": "الأصناف المطلوبة", "f_item": "الصنف", "f_free": "— مش في الأصناف (اكتب الوصف) —", "f_description": "الوصف", "f_description_ph": "مثلاً: أسمنت بورتلاندي",
        "f_quantity": "الكمية", "f_unit": "الوحدة", "f_unit_hint": "للوصف الحر بس", "f_boq": "بند المقايسة (اختياري)", "f_no_boq": "من غير بند",
        "f_line_note": "ملاحظة", "add_row": "+ سطر تاني", "save": "احفظ المسودة", "cancel": "رجوع", "saved": "اتحفظ {number} كمسودة.",
        # one request
        "back": "طلبات الشراء", "edit": "تعديل", "asked": "اتطلب {qty}", "boq": "بند", "decision": "قرار المشتريات", "reason": "سبب الرفض",
        "written": "طلبه {who} يوم {day}", "sent": "اتبعت للموافقة يوم {day}", "decided_ok": "وافق عليه {who} يوم {day}", "decided_no": "رفضه {who} يوم {day}",
        "submit": "ابعته للموافقة", "submitted_msg": "اتبعت {number} للموافقة.", "reopen": "رجّعه مسودة للتعديل", "reopened": "{number} رجع مسودة.",
        "cancel_req": "إلغاء الطلب", "cancel_note": "سبب الإلغاء (اختياري)", "cancelled": "اتلغى {number}.",
        "not_yours": "الطلب ده انت اللي طلبته، فلازم حد تاني من المشتريات يوافق عليه أو يرفضه.",
        "decide_only": "الموافقة على طلبات الشراء للمشتريات (صلاحية عمل فاتورة شراء).",
        "approve_title": "الموافقة وعمل أمر الشراء",
        "approve_hint": "اختار المورد، وغيّر الكمية لو لازم (التغيير بيتكتب في قرار المشتريات). السطور اللي ليها صنف بتتحط في أمر شراء مسودة للمورد ده؛ السطر اللي من غير صنف اختار له الصنف أو هيفضل برّه الأمر.",
        "supplier": "المورد", "choose": "اختار", "approved_qty": "الكمية اللي هتتشتري", "pick_item": "الصنف", "approve_note": "ملاحظة (اختياري)",
        "approve_btn": "وافق واعمل أمر الشراء", "approved_msg": "اتوافق على {number} واتعمل أمر الشراء {order} مسودة.", "approved_no_po": "اتوافق على {number}. مفيش سطر عليه صنف، فمتعملش أمر شراء.",
        "reject_title": "رفض الطلب", "reject_reason": "سبب الرفض", "reject_reason_ph": "مثلاً: الكمية موجودة في المخزن", "reject_btn": "ارفض الطلب", "rejected_msg": "اترفض {number}.",
        "order_title": "أمر شراء جديد", "order_hint": "الطلب ده اتوافق عليه ومستني أمر شراء. اختار المورد ويتعمل أمر شراء مسودة بالسطور اللي ليها صنف.",
        "order_btn": "اعمل أمر الشراء", "ordered_msg": "اتعمل أمر الشراء {order} مسودة من {number}.",
        "open_order": "افتح أمر الشراء", "order_state": "حالة أمر الشراء",
        "no_money": "الطلب وأمر الشراء ميحرّكوش فلوس ولا مخزون. الاستلام بيتم بفاتورة شراء عادية على أمر الشراء.",
        # across projects
        "inbox_title": "طلبات الشراء", "inbox_intro": "طلبات الشراء من كل المشاريع.", "show_waiting": "مستنية موافقة", "show_to_order": "مستنية أمر شراء",
        "show_mine": "طلباتي", "show_all": "الكل", "project": "المشروع", "inbox_none": "مفيش طلبات هنا.", "mine_title": "طلباتي",
    },
    "en": {
        "page_title": "Purchase requests",
        "intro": "The site engineer writes what the site needs and sends it for approval. Whoever approves for purchasing chooses the supplier; approving makes a draft purchase order with these items. A request holds no money and moves no stock.",
        "new": "+ New purchase request", "number": "Request", "needed_by": "Needed by", "lines_n": "{n} items", "requester": "Requested by", "status": "Status",
        "order": "Purchase order", "none": "No purchase requests on this project yet.", "ph": "—",
        "s_draft": "Draft", "s_submitted": "Waiting for approval", "s_approved": "Approved", "s_rejected": "Rejected", "s_ordered": "Ordered", "s_cancelled": "Cancelled",
        "po_cancelled": "Purchase order {number} was cancelled; the request is back to «Approved» and waits for a new purchase order.",
        "view_only": "Purchase requests are read-only for you.", "closed": "This project is closed or cancelled; its purchase requests are read-only.",
        "other_entity": "The purchase requests are kept in the entity that bills this project's certificates; work in that entity to see them.",
        "form_new": "New purchase request", "form_edit": "Edit {number}",
        "form_intro": "One item per line: choose it from the catalogue, or describe it when it is not there. A catalogue item takes its own unit.",
        "f_needed_by": "Needed by (optional)", "f_note": "Note for purchasing (optional)", "f_note_ph": "e.g. for the third floor, deliver to site",
        "lines": "Items requested", "f_item": "Item", "f_free": "— Not in the catalogue (describe it) —", "f_description": "Description", "f_description_ph": "e.g. Portland cement",
        "f_quantity": "Quantity", "f_unit": "Unit", "f_unit_hint": "free text only", "f_boq": "Bill of quantities item (optional)", "f_no_boq": "None",
        "f_line_note": "Note", "add_row": "+ Another line", "save": "Save the draft", "cancel": "Back", "saved": "{number} saved as a draft.",
        "back": "Purchase requests", "edit": "Edit", "asked": "asked {qty}", "boq": "Item", "decision": "Purchasing decision", "reason": "Reason for rejection",
        "written": "Requested by {who} on {day}", "sent": "Sent for approval on {day}", "decided_ok": "Approved by {who} on {day}", "decided_no": "Rejected by {who} on {day}",
        "submit": "Send for approval", "submitted_msg": "{number} sent for approval.", "reopen": "Take back to draft to edit", "reopened": "{number} is a draft again.",
        "cancel_req": "Cancel the request", "cancel_note": "Reason for cancelling (optional)", "cancelled": "{number} cancelled.",
        "not_yours": "You made this request, so someone else in purchasing must approve or reject it.",
        "decide_only": "Purchasing approves purchase requests (the right to make a purchase invoice).",
        "approve_title": "Approve and make the purchase order",
        "approve_hint": "Choose the supplier and change a quantity if needed (each change is written in the purchasing decision). Lines with an item go on a draft purchase order for that supplier; choose an item for a free-text line, or it stays off the order.",
        "supplier": "Supplier", "choose": "Choose", "approved_qty": "Quantity to buy", "pick_item": "Item", "approve_note": "Note (optional)",
        "approve_btn": "Approve and make the purchase order", "approved_msg": "{number} approved; purchase order {order} made as a draft.", "approved_no_po": "{number} approved. No line names an item, so no purchase order was made.",
        "reject_title": "Reject the request", "reject_reason": "Reason for rejection", "reject_reason_ph": "e.g. the quantity is in stock", "reject_btn": "Reject the request", "rejected_msg": "{number} rejected.",
        "order_title": "New purchase order", "order_hint": "This request is approved and waits for a purchase order. Choose the supplier to make a draft purchase order with the lines that have an item.",
        "order_btn": "Make the purchase order", "ordered_msg": "Purchase order {order} made as a draft from {number}.",
        "open_order": "Open the purchase order", "order_state": "Purchase order status",
        "no_money": "Neither the request nor the purchase order moves money or stock. Goods are received on an ordinary purchase invoice against the order.",
        "inbox_title": "Purchase requests", "inbox_intro": "Purchase requests from every project.", "show_waiting": "Waiting for approval", "show_to_order": "Waiting for a purchase order",
        "show_mine": "My requests", "show_all": "All", "project": "Project", "inbox_none": "No requests here.", "mine_title": "My requests",
    },
}


def status_label(status, lang):
    return WORDS["en" if lang == "en" else "ar"][f"s_{status}"]


def _project(pk):
    return get_object_or_404(Project.objects.select_related("customer"), pk=pk)


def _url(name, project, request=None, lang="ar"):
    args = [project.pk] + ([request.pk] if request is not None else [])
    return f"{reverse(f'projects:{name}', args=args)}?lang={lang}"


def _who(user):
    from reports.navigation import display_name

    return display_name(user) if user is not None else ""


def _qty(value):
    from settings_core.templatetags.hesba_format import qty

    return qty(value)


def _order_link(request, order):
    from settings_core.templatetags.hesba_labels import link_ok

    if order is None:
        return ""
    url = reverse("purchase_orders:detail", args=[order.pk])
    return url if link_ok({"request": request}, url) else ""


def _row(request, item, lang, with_project=False):
    words = WORDS[lang]
    state = prq.state(item)
    lines = list(item.lines.all())
    names = "، ".join(line.item.item_name if line.item_id else line.description for line in lines[:3]) if lang != "en" else \
        ", ".join(line.item.item_name if line.item_id else line.description for line in lines[:3])
    return {"request": item, "label": prq.label(item), "state": state, "status_label": status_label(state, lang), "status_class": STATUS_CLASS[state],
            "needed_by": day_text(item.needed_by, lang) if item.needed_by else words["ph"], "count": words["lines_n"].format(n=len(lines)),
            "names": names + ("…" if len(lines) > 3 else ""), "requester": _who(item.requested_by),
            "order": item.purchase_order.number if item.purchase_order_id else "", "order_cancelled": prq.order_was_cancelled(item),
            "url": _url("purchase_request", item.project, item, lang), "project": item.project if with_project else None}


def _context_for(request, project, **extra):
    lang = _lang(request)
    return _context(request, project, "purchase_requests", pw=WORDS[lang], can_draft=prq.can_draft(request.user),
                    can_decide=prq.can_decide(request.user), **extra)


# ---- the project's tab ----

@require_any_permission(*SCREENS)
def requests_tab(request, pk):
    lang = _lang(request)
    project = _project(pk)
    reach = prq.in_reach(project)
    rows = [_row(request, item, lang) for item in prq.for_project(project)] if reach else []
    is_open = project.status in OPEN and reach
    new_url = _url("purchase_request_new", project, lang=lang) if is_open and prq.can_draft(request.user) else ""
    return render(request, "projects/purchase_requests.html", _context_for(
        request, project, rows=rows, other_entity=not reach, is_open=is_open, new_url=new_url,
        page_title=f"{project.name} — {WORDS[lang]['page_title']}"))


# ---- the form ----

def _items():
    from master_data.models import Item

    return Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_name")[:500]


def _typed_rows(post):
    keys = ("item", "description", "unit", "quantity", "boq_line", "note")
    columns = {key: post.getlist(key) for key in keys}
    count = max((len(values) for values in columns.values()), default=0)
    return [{key: (columns[key][index] if index < len(columns[key]) else "") for key in keys} for index in range(count)]


def _stored_rows(item):
    return [{"item": str(line.item_id or ""), "description": line.description, "unit": line.unit if not line.item_id else "",
             "quantity": _qty(line.quantity), "boq_line": str(line.boq_line_id or ""), "note": line.note} for line in item.lines.all()]


def _blank():
    return {"item": "", "description": "", "unit": "", "quantity": "", "boq_line": "", "note": ""}


@require_any_permission(*SCREENS)
def request_form(request, pk, rid=None):
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    if not prq.can_draft(request.user):
        raise PermissionDenied("Purchase requests need projects.record_progress or sales.create_sales_invoice.")
    if not prq.in_reach(project):
        raise Http404("This project's money is in another entity.")
    item = None
    if rid is not None:
        item = prq.get_request(project, rid)
        if item is None:
            raise Http404("No such purchase request on this project.")
        if item.status != Status.DRAFT:
            return redirect(_url("purchase_request", project, item, lang))
    closed = project.status not in OPEN
    error = ""
    if request.method == "POST":
        if closed:
            raise Http404("This project is closed.")
        data = {"needed_by": request.POST.get("needed_by", ""), "note": request.POST.get("note", ""), "lines": _typed_rows(request.POST)}
        try:
            item = prq.save_request(project, request.user, data, item, lang)
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
            form = {"needed_by": data["needed_by"], "note": data["note"],
                    "rows": [row for row in data["lines"] if any(row.values())] or [_blank()]}
        else:
            messages.success(request, words["saved"].format(number=prq.label(item)))
            return redirect(_url("purchase_request", project, item, lang))
    elif item is not None:
        form = {"needed_by": item.needed_by.isoformat() if item.needed_by else "", "note": item.note, "rows": _stored_rows(item) + [_blank()]}
    else:
        form = {"needed_by": "", "note": "", "rows": [_blank() for _ in range(BLANK_ROWS)]}
    form["rows"] = form["rows"][:prq.MAX_LINES]
    heading = words["form_edit"].format(number=prq.label(item)) if item is not None and item.pk else words["form_new"]
    back = _url("purchase_request", project, item, lang) if item is not None and item.pk else _url("purchase_requests", project, lang=lang)
    return render(request, "projects/purchase_request_form.html", _context_for(
        request, project, form=form, form_error=error, closed=closed, items=_items(), boq=list(project.boq.all()), heading=heading,
        back_url=back, max_lines=prq.MAX_LINES, today=timezone.localdate(), page_title=f"{project.name} — {heading}"))


# ---- one request ----

def _suppliers():
    from master_data.models import Supplier

    return Supplier.objects.filter(active=True).order_by("name")[:500]


def _supplier(raw):
    from master_data.models import Supplier

    raw = str(raw or "")
    return Supplier.objects.filter(pk=raw, active=True).first() if raw.isdigit() else None


ACTIONS = {"submit": "draft", "reopen": "draft", "cancel": "any", "approve": "decide", "reject": "decide", "order": "decide"}


@require_any_permission(*SCREENS)
def request_detail(request, pk, rid):
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    item = prq.get_request(project, rid)
    if item is None:
        raise Http404("No such purchase request on this project.")
    user = request.user
    error = ""
    if request.method == "POST":
        action = request.POST.get("action", "")
        need = ACTIONS.get(action)
        if need is None:
            raise Http404
        if (need == "draft" and not prq.can_draft(user)) or (need == "decide" and not prq.can_decide(user)) or \
                (need == "any" and not (prq.can_draft(user) or prq.can_decide(user))):
            raise PermissionDenied("This action on a purchase request needs a right this user does not hold.")
        number = prq.label(item)
        try:
            if action == "submit":
                prq.submit(project, item, user, lang)
                messages.success(request, words["submitted_msg"].format(number=number))
            elif action == "reopen":
                prq.reopen(project, item, user, lang)
                messages.success(request, words["reopened"].format(number=number))
            elif action == "cancel":
                prq.cancel(project, item, user, request.POST.get("note", ""), lang)
                messages.success(request, words["cancelled"].format(number=number))
            elif action == "reject":
                prq.reject(project, item, user, request.POST.get("reason", ""), lang)
                messages.success(request, words["rejected_msg"].format(number=number))
            elif action == "approve":
                lines = list(item.lines.all())
                quantities = {line.pk: request.POST.get(f"q_{line.pk}", "") for line in lines}
                items = {line.pk: request.POST.get(f"i_{line.pk}", "") for line in lines if not line.item_id}
                done = prq.approve(project, item, user, supplier=_supplier(request.POST.get("supplier")), quantities=quantities, items=items,
                                   note=request.POST.get("note", ""), lang=lang)
                messages.success(request, words["approved_msg"].format(number=number, order=done.purchase_order.number) if done.purchase_order_id
                                 else words["approved_no_po"].format(number=number))
            else:
                done = prq.make_order(project, item, user, supplier=_supplier(request.POST.get("supplier")), lang=lang)
                messages.success(request, words["ordered_msg"].format(order=done.purchase_order.number, number=number))
            return redirect(_url("purchase_request", project, item, lang))
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
            item = prq.get_request(project, rid)
    state = prq.state(item)
    is_open = project.status in OPEN
    mine = item.requested_by_id == user.pk
    can_draft, can_decide = prq.can_draft(user), prq.can_decide(user)
    lines = []
    for line in item.lines.select_related("item", "boq_line"):
        lines.append({"line": line, "what": line.item.item_name if line.item_id else line.description, "code": line.item.item_code if line.item_id else "",
                      "unit": unit_label(line.unit, lang) if line.item_id else line.unit, "qty": _qty(line.quantity), "asked": words["asked"].format(qty=_qty(line.requested_quantity)) if line.requested_quantity is not None else "",
                      "boq": f"{line.boq_line.code or line.boq_line.line_number} {line.boq_line.description}" if line.boq_line_id else ""})
    deciding = is_open and can_decide and not mine
    decided = ""
    if item.decided_by_id and item.decided_at:
        key = "decided_no" if item.status == Status.REJECTED else "decided_ok"
        decided = words[key].format(who=_who(item.decided_by), day=day_text(timezone.localtime(item.decided_at).date(), lang))
    order = item.purchase_order
    return render(request, "projects/purchase_request.html", _context_for(
        request, project, item=item, state=state, status_label=status_label(state, lang), status_class=STATUS_CLASS[state], label=prq.label(item),
        lines=lines, form_error=error, is_open=is_open, mine=mine,
        needed_by=day_text(item.needed_by, lang) if item.needed_by else "",
        written=words["written"].format(who=_who(item.requested_by), day=day_text(timezone.localtime(item.created_at).date(), lang)),
        sent=words["sent"].format(day=day_text(timezone.localtime(item.submitted_at).date(), lang)) if item.submitted_at else "", decided=decided,
        can_edit=is_open and can_draft and state == Status.DRAFT, can_submit=is_open and can_draft and state == Status.DRAFT,
        can_reopen=is_open and can_draft and state == Status.SUBMITTED,
        can_cancel=is_open and ((can_draft and state in (Status.DRAFT, Status.SUBMITTED)) or (can_decide and state == Status.APPROVED)),
        can_approve=deciding and state == Status.SUBMITTED, can_reject=deciding and state == Status.SUBMITTED,
        can_order=deciding and state == Status.APPROVED, not_yours=is_open and can_decide and mine and state in (Status.SUBMITTED, Status.APPROVED),
        decide_only=is_open and not can_decide and state == Status.SUBMITTED,
        order=order, order_url=_order_link(request, order), order_status=_order_status(order, lang) if order else "",
        cancelled_text=words["po_cancelled"].format(number=order.number) if prq.order_was_cancelled(item) and state == Status.APPROVED else "", suppliers=_suppliers() if deciding and state in (Status.SUBMITTED, Status.APPROVED) else [],
        items=_items() if deciding and state == Status.SUBMITTED and any(not row["line"].item_id for row in lines) else [],
        back_url=_url("purchase_requests", project, lang=lang), page_title=f"{project.name} — {prq.label(item)}"))


def _order_status(order, lang):
    from orders import purchasing

    return purchasing.STATUS_LABELS["en" if lang == "en" else "ar"].get(purchasing.state(order), order.status)


# ---- across projects: waiting for approval (the office) and mine (the engineer) ----

SHOWS = ("waiting", "to_order", "mine", "all")


@require_any_permission(*SCREENS)
def inbox(request, show=None):
    lang = _lang(request)
    words = WORDS[lang]
    user = request.user
    show = show or request.GET.get("show") or ("waiting" if prq.can_decide(user) else "mine")
    if show not in SHOWS:
        show = "waiting"
    rows = prq.visible(user).prefetch_related("lines__item")
    if show == "waiting":
        rows = rows.filter(status=Status.SUBMITTED)
    elif show == "to_order":
        rows = rows.filter(prq.TO_ORDER)
    elif show == "mine":
        rows = rows.filter(requested_by=user)
    rows = rows.order_by("-created_at", "-pk")[:200]
    tabs = [{"key": key, "label": words[f"show_{key}"], "current": key == show,
             "url": f"{reverse('projects:purchase_requests_mine')}?lang={lang}" if key == "mine" else f"{reverse('projects:purchase_requests_all')}?show={key}&lang={lang}"}
            for key in SHOWS]
    title = words["mine_title"] if show == "mine" else words["inbox_title"]
    return render(request, "projects/purchase_requests_inbox.html", _base(
        request, pw=words, rows=[_row(request, item, lang, with_project=True) for item in rows], show=show, tabs=tabs, title=title,
        page_title=title))
