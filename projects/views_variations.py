"""Contracting C1 (HG-103): the «أوامر التغيير» screens: the register, the form (new and edit), one order with its signed
copy and its decision, and the print.

Every rule is in ``projects.variations`` and the attachments registry; the views read, call and render. Every screen looks
the order up THROUGH its project, so another project's order id answers 404. Reading needs the project right; drafting
needs the right to record progress; approving or rejecting needs ``sales.create_sales_invoice`` (403 without).
"""

from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from attachments import services, views as attachment_views
from settings_core.templatetags.hesba_format import day_text, money
from settings_core.ui_messages import translate

from . import access, contract, documents, variations
from .contract_views import _context
from .models import Project, VariationStatus
from .services import OPEN

ORDER = "projects.variationorder"
NEW_ROWS = 2  # blank rows for new items a form offers (the page adds more with one tap)
STATUS_CLASS = {"draft": "draft", "approved": "posted", "rejected": "cancelled", "cancelled": "cancelled"}

WORDS = {
    "ar": {
        "title": "أوامر التغيير", "page_title": "أوامر التغيير والأعمال الإضافية",
        "intro": "أي تعديل على العقد بعد التعاقد بيتسجّل هنا: زيادة أو نقص في كمية بند، بنود جديدة للأعمال الإضافية، أو تمديد لمدة التنفيذ. قيمة العقد والمقايسة ما بيتغيّروش غير بعد تسجيل اعتماد المالك.",
        "new": "أمر تغيير جديد", "original": "قيمة العقد الأصلية", "approved": "أوامر تغيير معتمدة", "revised": "قيمة العقد بعد التعديل",
        "pending": "في انتظار اعتماد المالك", "extension": "تمديد المدة", "days_n": "{n} يوم", "planned_end": "نهاية التنفيذ بعد التمديد", "orders_n": "{n} أمر",
        "number": "الأمر", "date": "التاريخ", "status": "الحالة", "value": "القيمة", "ph": "—",
        "none": "لسه مفيش أوامر تغيير على المشروع ده.", "view_only": "تسجيل أمر التغيير لمهندس الموقع أو لصاحب صلاحية الفواتير بس.",
        "closed": "المشروع اتقفل أو اتلغى؛ أوامر التغيير للقراءة بس.", "order_n": "أمر تغيير رقم {n}",
        "form_new": "أمر تغيير جديد", "form_edit": "تعديل أمر تغيير رقم {n}",
        "form_intro": "الأمر بيتحفظ مسودة: المقايسة وقيمة العقد ما بيتغيّروش لحد ما يتسجّل اعتماد المالك.",
        "f_date": "تاريخ الأمر", "f_title": "عنوان الأمر", "f_title_ph": "مثلاً: أعمال عزل إضافية للسطح", "f_reason": "سبب التغيير",
        "f_reason_ph": "طلب المالك، أو تعليمات الاستشاري، أو ظروف ظهرت في الموقع", "f_days": "تمديد مدة التنفيذ (بالأيام)", "f_days_hint": "اكتب صفر لو المدة ما اتغيّرتش.",
        "changes_title": "تعديل كميات بنود المقايسة",
        "changes_hint": "اختار «زيادة» أو «نقص» واكتب الكمية قدام البند اللي اتغيّر، وسيب الباقي فاضي. الفئة ثابتة: الأعمال بفئة مختلفة بتتسجّل كبند جديد.",
        "direction": "نوع التغيير", "up": "زيادة", "down": "نقص", "change_qty": "الكمية", "now": "الكمية الحالية", "certified": "اتعمل بيه مستخلصات",
        "new_title": "بنود جديدة (أعمال إضافية)", "new_hint": "كل بند جديد بكميته وفئته، وبيدخل المقايسة بعد الاعتماد.",
        "code": "كود البند", "description": "وصف البند", "unit": "الوحدة", "qty": "الكمية", "rate": "الفئة", "amount": "القيمة", "add_row": "+ بند تاني",
        "lump_title": "قيمة أمر التغيير", "lump_amount": "القيمة",
        "lump_hint": "المشروع ده من غير مقايسة، فالأمر بيتسجّل بقيمة إجمالية بتتضاف على قيمة العقد أو بتتخصم منها بعد الاعتماد.",
        "save": "حفظ المسودة", "cancel": "رجوع",
        "saved": "اتحفظ أمر التغيير رقم {n} كمسودة. اطبعه للتوقيع، وارفع النسخة الموقّعة قبل الاعتماد.", "edited": "اتعدّل أمر التغيير رقم {n}.",
        "edit": "تعديل الأمر", "print": "طباعة الأمر", "back": "أوامر التغيير", "reason": "سبب التغيير", "lines": "بنود الأمر",
        "kind_change": "تعديل كمية", "kind_new": "بند جديد", "after": "بعد التعديل", "total": "قيمة الأمر",
        "contract_before": "قيمة العقد قبل الأمر", "contract_after": "قيمة العقد بعد الأمر", "no_lines": "الأمر ده تمديد لمدة التنفيذ بس، من غير بنود.",
        "written": "سجّله {who} يوم {when}", "decided_approved": "سجّل الاعتماد {who} يوم {when}", "decided_rejected": "سجّل الرفض {who} يوم {when}",
        "decided_cancelled": "ألغاه {who} يوم {when}", "owner_approved_on": "تاريخ اعتماد المالك", "owner_ref": "مرجع المالك", "decision_note": "ملاحظة",
        "copy_title": "النسخة الموقّعة من المالك",
        "copy_intro": "اطبع الأمر، وبعد توقيع المالك أو الاستشاري صوّر النسخة أو ارفعها ملف PDF. الاعتماد ما بيتسجّلش من غيرها.",
        "copy_none": "لسه مفيش نسخة موقّعة مرفوعة.", "camera": "صوّر", "choose": "اختار ملف", "delete": "امسح", "by": "رفعه", "open": "افتح", "pdf": "PDF",
        "photo_of": "صورة النسخة الموقّعة", "formats": "صور JPG أو PNG أو WEBP وملفات PDF بس، لحد {limit} للملف.",
        "space": "{files} من {max_files} ملف، والمساحة الفاضلة {left}.", "blocked": "إضافة المرفقات لمهندس الموقع أو لصاحب صلاحية الفواتير بس.",
        "copy_kept": "الأمر اتقفل، والنسخة الموقّعة محفوظة زي ما هي.",
        "approve_title": "تسجيل اعتماد المالك",
        "approve_hint": "بعد الاعتماد بنود الأمر بتدخل المقايسة وقيمة العقد بتتعدّل، والأمر نفسه ما بيتعدّلش بعدها. لازم النسخة الموقّعة تكون مرفوعة.",
        "approved_on": "تاريخ اعتماد المالك", "owner_reference": "مرجع المالك", "owner_reference_ph": "رقم الخطاب أو المحضر (اختياري)", "approve_btn": "اعتماد الأمر",
        "approved_msg": "اتسجّل اعتماد أمر التغيير رقم {n}، وقيمة العقد بقت {value}.",
        "close_title": "رفض الأمر أو إلغاؤه", "close_hint": "الرفض بيسجّل إن المالك ما وافقش، والإلغاء بيسحب المسودة. في الحالتين الأمر بيفضل محفوظ في السجل.",
        "note": "ملاحظة (اختياري)", "reject_btn": "تسجيل رفض المالك", "cancel_btn": "إلغاء المسودة",
        "rejected_msg": "اتسجّل رفض أمر التغيير رقم {n}.", "cancelled_msg": "اتلغى أمر التغيير رقم {n}.",
        "decide_only": "اعتماد الأمر أو رفضه لصاحب صلاحية الفواتير بس.",
        "p_title": "أمر تغيير", "p_project": "المشروع", "p_site": "الموقع", "p_owner": "صاحب المشروع", "p_item": "البند", "p_kind": "النوع",
        "p_change": "التغيير في الكمية", "p_extension": "تمديد مدة التنفيذ", "p_contractor": "المقاول", "p_consultant": "الاستشاري", "p_owner_sign": "المالك",
        "p_back": "رجوع", "p_print": "اطبع", "p_hint": "عشان تحفظه PDF اختار «حفظ كـ PDF» من شاشة الطباعة.", "p_no_extension": "من غير تمديد",
    },
    "en": {
        "title": "Variation orders", "page_title": "Variation orders and extra works",
        "intro": "Every change to the contract after it is signed is recorded here: more or less of an item, new items for extra works, or more time. The contract value and the bill of quantities change only once the owner's approval is recorded.",
        "new": "New variation order", "original": "Original contract value", "approved": "Approved variation orders", "revised": "Revised contract value",
        "pending": "Awaiting the owner's approval", "extension": "Extension of time", "days_n": "{n} days", "planned_end": "Planned end with the extension", "orders_n": "{n} orders",
        "number": "Order", "date": "Date", "status": "Status", "value": "Value", "ph": "—",
        "none": "No variation orders on this project yet.", "view_only": "Only the site engineer, or someone who may create invoices, can record a variation order.",
        "closed": "This project is closed or cancelled; its variation orders are read-only.", "order_n": "Variation order {n}",
        "form_new": "New variation order", "form_edit": "Edit variation order {n}",
        "form_intro": "The order is saved as a draft: the bill of quantities and the contract value do not change until the owner's approval is recorded.",
        "f_date": "Order date", "f_title": "Title", "f_title_ph": "e.g. Extra roof waterproofing", "f_reason": "Reason for the change",
        "f_reason_ph": "The owner's request, the consultant's instruction, or site conditions", "f_days": "Extension of time (days)", "f_days_hint": "Type 0 when the time does not change.",
        "changes_title": "Changes to quantities of the bill of quantities",
        "changes_hint": "Choose “Increase” or “Decrease” and type the quantity next to each item that changes; leave the rest blank. The rate is fixed: work at another rate is a new item.",
        "direction": "Kind of change", "up": "Increase", "down": "Decrease", "change_qty": "Quantity", "now": "Quantity now", "certified": "Certified",
        "new_title": "New items (extra works)", "new_hint": "Each new item with its quantity and rate; it joins the bill of quantities once approved.",
        "code": "Item code", "description": "Item description", "unit": "Unit", "qty": "Quantity", "rate": "Rate", "amount": "Amount", "add_row": "+ Another item",
        "lump_title": "Value of the variation order", "lump_amount": "Amount",
        "lump_hint": "This project has no bill of quantities, so the order is one amount, added to or taken off the contract value once approved.",
        "save": "Save the draft", "cancel": "Back",
        "saved": "Variation order {n} is saved as a draft. Print it for signature, and attach the signed copy before approving it.", "edited": "Variation order {n} was updated.",
        "edit": "Edit the order", "print": "Print the order", "back": "Variation orders", "reason": "Reason for the change", "lines": "Items of the order",
        "kind_change": "Quantity change", "kind_new": "New item", "after": "After", "total": "Order value",
        "contract_before": "Contract value before the order", "contract_after": "Contract value after the order", "no_lines": "This order only extends the time; it has no items.",
        "written": "Recorded by {who} on {when}", "decided_approved": "Approval recorded by {who} on {when}", "decided_rejected": "Rejection recorded by {who} on {when}",
        "decided_cancelled": "Cancelled by {who} on {when}", "owner_approved_on": "Owner approved on", "owner_ref": "Owner's reference", "decision_note": "Note",
        "copy_title": "The copy signed by the owner",
        "copy_intro": "Print the order; once the owner or the consultant signs it, photograph the copy or upload it as a PDF. The approval cannot be recorded without it.",
        "copy_none": "No signed copy attached yet.", "camera": "Take photo", "choose": "Choose a file", "delete": "Delete", "by": "Added by", "open": "Open", "pdf": "PDF",
        "photo_of": "Photo of the signed copy", "formats": "JPG, PNG or WEBP images and PDF files only, up to {limit} each.",
        "space": "{files} of {max_files} files, and {left} of room left.", "blocked": "Only the site engineer, or someone who may create invoices, can add files.",
        "copy_kept": "The order is closed; its signed copy is kept as it is.",
        "approve_title": "Record the owner's approval",
        "approve_hint": "Once approved, the order's items join the bill of quantities and the contract value changes; the order itself can no longer be edited. The signed copy must be attached.",
        "approved_on": "Owner approved on", "owner_reference": "Owner's reference", "owner_reference_ph": "Letter or minutes number (optional)", "approve_btn": "Approve the order",
        "approved_msg": "The approval of variation order {n} is recorded; the contract value is now {value}.",
        "close_title": "Reject or cancel the order", "close_hint": "Rejecting records that the owner refused; cancelling withdraws the draft. Either way the order stays on the register.",
        "note": "Note (optional)", "reject_btn": "Record the owner's rejection", "cancel_btn": "Cancel the draft",
        "rejected_msg": "The rejection of variation order {n} is recorded.", "cancelled_msg": "Variation order {n} was cancelled.",
        "decide_only": "Only someone who may create invoices can approve or reject the order.",
        "p_title": "Variation order", "p_project": "Project", "p_site": "Site", "p_owner": "Project owner", "p_item": "Item", "p_kind": "Kind",
        "p_change": "Change in quantity", "p_extension": "Extension of time", "p_contractor": "Contractor", "p_consultant": "Consultant", "p_owner_sign": "Owner",
        "p_back": "Back", "p_print": "Print", "p_hint": "To save a PDF, choose \"Save as PDF\" in the print window.", "p_no_extension": "No extension",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _who(user):
    return (user.get_full_name() or user.get_username()) if user is not None else ""


def signed_money(value):
    """An amount with its direction, for a figure that can go either way: «+90,000.00», «−20,000.00», «0.00»."""

    value = Decimal(value or 0)
    return f"+{money(value)}" if value > 0 else (f"−{money(abs(value))}" if value < 0 else money(value))


def signed_qty(value):
    value = Decimal(value or 0)
    text = variations.qty_text(abs(value))
    return f"+{text}" if value > 0 else (f"−{text}" if value < 0 else text)


def _need_view(request):
    if not variations.can_view(request.user):
        raise PermissionDenied("Projects need projects.view_projects or sales.view_sales_invoices.")


def _need_draft(request):
    _need_view(request)
    if not variations.can_draft(request.user):
        raise PermissionDenied("Variation orders need projects.record_progress or sales.create_sales_invoice.")


def _need_decide(request):
    _need_view(request)
    if not variations.can_decide(request.user):
        raise PermissionDenied("Approving or rejecting a variation order needs sales.create_sales_invoice.")


def _project(pk):
    return get_object_or_404(Project.objects.select_related("customer"), pk=pk)


def _order(project, oid):
    order = variations.get_order(project, oid)
    if order is None:
        raise Http404("No such variation order on this project.")
    return order


def _url(name, project, order=None, lang="ar"):
    args = [project.pk] + ([order.pk] if order is not None else [])
    return f"{reverse(f'projects:{name}', args=args)}?lang={lang}"


def _days_text(days, lang):
    return WORDS[lang]["days_n"].format(n=days)


def _context_for(request, project, **extra):
    lang = _lang(request)
    return _context(request, project, "variations", vw=WORDS[lang], can_draft=variations.can_draft(request.user), can_decide=variations.can_decide(request.user), **extra)


# ---- the register ----

def variations_list(request, pk):
    _need_view(request)
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    figures = variations.figures(project)
    rows = []
    for order in variations.orders_for(project):
        rows.append({"order": order, "heading": words["order_n"].format(n=order.number), "day": day_text(order.order_date, lang),
                     "status_label": variations.status_label(order.status, lang), "status_class": STATUS_CLASS[order.status],
                     "value_text": signed_money(variations.order_value(order)), "days_text": _days_text(order.extension_days, lang) if order.extension_days else "",
                     "url": _url("variation", project, order, lang)})
    summary = {"original": money(figures["original"]), "approved": signed_money(figures["approved"]), "revised": money(figures["revised"]),
               "pending": signed_money(figures["pending"]), "approved_n": words["orders_n"].format(n=figures["approved_count"]),
               "pending_n": words["orders_n"].format(n=figures["pending_count"]),
               "extension": _days_text(figures["extension_days"], lang) if figures["extension_days"] else "",
               "planned_end": day_text(figures["planned_end"], lang) if figures["extension_days"] and figures["planned_end"] else ""}
    is_open = project.status in OPEN and variations.in_reach(project)
    return render(request, "projects/variations.html", _context_for(
        request, project, rows=rows, figures=figures, summary=summary, new_url=_url("variation_new", project, lang=lang) if is_open and variations.can_draft(request.user) else "",
        page_title=f"{project.name} — {words['page_title']}"))


# ---- the form ----

def _number(raw):
    return contract._decimal(str(raw if raw is not None else "").strip())


def _directed(raw, direction):
    """The typed quantity or amount with the direction chosen beside it: «نقص» makes a plain number negative. A number typed
    with its own minus sign stays negative. What cannot be read is passed on as typed, for the service to refuse in words."""

    text = str(raw if raw is not None else "").strip()
    if not text:
        return ""
    number = _number(text)
    if number is None:
        return text
    return str(-abs(number)) if direction == "down" or number < 0 else str(number)


def _typed(post, boq):
    """The form as typed, for showing it again after a refusal."""

    return {"date": post.get("order_date", ""), "title": post.get("title", ""), "reason": post.get("reason", ""), "days": post.get("extension_days", ""),
            "changes": {item.pk: (post.get(f"d_{item.pk}", "up"), post.get(f"c_{item.pk}", "")) for item in boq},
            "new": [dict(zip(("code", "description", "unit", "quantity", "rate"), row)) for row in
                    zip(post.getlist("new_code"), post.getlist("new_description"), post.getlist("new_unit"), post.getlist("new_quantity"), post.getlist("new_rate"))],
            "amount": post.get("amount", ""), "amount_direction": post.get("amount_direction", "up")}


def _stored(order):
    changes, new = {}, []
    for line in order.lines.all():
        if line.boq_line_id is not None:
            changes[line.boq_line_id] = ("down" if line.quantity_change < 0 else "up", variations.qty_text(abs(line.quantity_change)))
        else:
            new.append({"code": line.code, "description": line.description, "unit": line.unit, "quantity": variations.qty_text(line.quantity_change), "rate": str(line.rate)})
    amount = order.lump_amount
    return {"date": order.order_date.isoformat(), "title": order.title, "reason": order.reason, "days": str(order.extension_days), "changes": changes, "new": new,
            "amount": str(abs(amount)) if amount is not None else "", "amount_direction": "down" if amount is not None and amount < 0 else "up"}


def variation_form(request, pk, oid=None):
    """New and edit (a draft only), in one screen."""

    _need_draft(request)
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    if not variations.in_reach(project):
        raise Http404("This project's money is in another entity.")  # as the certificate screens: another entity sees none of this
    order = _order(project, oid) if oid is not None else None
    if order is not None and order.status != VariationStatus.DRAFT:
        return redirect(_url("variation", project, order, lang))
    boq = list(project.boq.all())
    today = timezone.localdate()
    error, form = "", None
    if request.method == "POST":
        post = request.POST
        form = _typed(post, boq)
        try:
            saved = variations.save_order(
                project, request.user, order=order, order_date=post.get("order_date"), title=post.get("title"), reason=post.get("reason"),
                extension_days=post.get("extension_days"), changes=[(item_pk, _directed(typed, direction)) for item_pk, (direction, typed) in form["changes"].items()],
                new_items=form["new"], amount=_directed(form["amount"], form["amount_direction"]) if not boq else None, lang=lang)
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
        else:
            messages.success(request, words["saved" if order is None else "edited"].format(n=saved.number))
            return redirect(_url("variation", project, saved, lang))
    elif order is not None:
        form = _stored(order)
    else:
        form = {"date": today.isoformat(), "title": "", "reason": "", "days": "0", "changes": {}, "new": [], "amount": "", "amount_direction": "up"}
    blank = {"code": "", "description": "", "unit": "", "quantity": "", "rate": ""}
    form["new"] = list(form["new"]) + [blank] * max(0, NEW_ROWS - len(form["new"]))
    boq_rows = []
    for item in boq:
        direction, typed = form["changes"].get(item.pk, ("up", ""))
        boq_rows.append({"item": item, "certified": contract.certified_quantity(item), "direction": direction, "value": typed})
    title = words["form_edit"].format(n=order.number) if order is not None else words["form_new"]
    return render(request, "projects/variation_form.html", _context_for(
        request, project, form=form, order=order, boq_rows=boq_rows, form_error=error, today=today, form_title=title, closed=project.status not in OPEN,
        back_url=_url("variation", project, order, lang) if order is not None else _url("variations", project, lang=lang), page_title=f"{project.name} — {title}"))


# ---- one order ----

def _line_items(order, lang):
    words = WORDS[lang]
    rows = []
    for row in variations.line_rows(order):
        line = row["line"]
        rows.append({"line": line, "new": row["new"], "kind": words["kind_new" if row["new"] else "kind_change"], "change_text": signed_qty(line.quantity_change),
                     "rate": row["rate"], "amount_text": signed_money(row["amount"]), "now": row["now"], "after": row["after"]})
    return rows


def copy_card(project, order, user, lang):
    """The signed-copy card of an order: its files, whether this user may add or remove one, and the room left."""

    rules = services.OWNERS[ORDER]
    words = WORDS[lang]
    closed = bool(rules["closed"](order))
    can_add = bool(rules["can_attach"](user, order)) and not closed
    can_delete = bool(rules["can_delete"](user, order)) and not closed  # once the order is decided, its signed copy is the record
    room = services.room(order, lang)
    items = [{"row": row, "url": documents.file_url(project, row, lang), "delete_url": documents.delete_url(project, row, lang), "can_delete": can_delete,
              "who": _who(row.uploaded_by), "when": documents.when_text(row.created_at, lang)} for row in services.attachments_for(order, user, lang)]
    return {"words": dict(words, formats=words["formats"].format(limit=services._human(services.max_bytes(), lang))), "items": items, "can_add": can_add, "closed": closed,
            "blocked": "" if can_add or closed else words["blocked"], "space": words["space"].format(files=room["files"], max_files=room["max_files"], left=room["left_text"]),
            "upload_url": _url("variation_files", project, order, lang)}


def _decision_text(order, lang):
    words = WORDS[lang]
    if order.status == VariationStatus.DRAFT or order.decided_by_id is None:
        return ""
    return words[f"decided_{order.status}"].format(who=_who(order.decided_by), when=day_text(timezone.localtime(order.decided_at).date(), lang))


def _order_context(order, lang):
    """What the order's screen and its print both show."""

    words = WORDS[lang]
    value = variations.order_value(order)
    before = variations.contract_before(order)
    live = order.status in (VariationStatus.DRAFT, VariationStatus.APPROVED)
    return {"order": order, "heading": words["order_n"].format(n=order.number), "day": day_text(order.order_date, lang), "rows": _line_items(order, lang),
            "status_label": variations.status_label(order.status, lang), "status_class": STATUS_CLASS[order.status], "value": value, "value_text": signed_money(value),
            "days_text": _days_text(order.extension_days, lang) if order.extension_days else "",
            "contract_before": before if live else None, "contract_after": before + value if live else None,
            "approved_day": day_text(order.approved_on, lang) if order.approved_on else "",
            "written": words["written"].format(who=_who(order.created_by), when=day_text(timezone.localtime(order.created_at).date(), lang)),
            "decided": _decision_text(order, lang)}


def variation_detail(request, pk, oid):
    _need_view(request)
    lang = _lang(request)
    words = WORDS[lang]
    project = _project(pk)
    order = _order(project, oid)
    error = ""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "approve":
            _need_decide(request)
        elif action == "reject":
            _need_decide(request)
        elif action == "cancel":
            _need_draft(request)
        else:
            raise Http404
        try:
            if action == "approve":
                variations.approve(project, order, request.user, approved_on=request.POST.get("approved_on"), owner_reference=request.POST.get("owner_reference", ""), lang=lang)
                messages.success(request, words["approved_msg"].format(n=order.number, value=money(contract.contract_value(project))))
            else:
                status = VariationStatus.REJECTED if action == "reject" else VariationStatus.CANCELLED
                variations.close(project, order, request.user, status=status, note=request.POST.get("note", ""), lang=lang)
                messages.success(request, words["rejected_msg" if action == "reject" else "cancelled_msg"].format(n=order.number))
        except ValidationError as exc:
            error = translate(" ".join(exc.messages), lang)
        else:
            return redirect(_url("variation", project, order, lang))
    is_open = project.status in OPEN
    draft = order.status == VariationStatus.DRAFT
    card = copy_card(project, order, request.user, lang)
    context = _order_context(order, lang)
    return render(request, "projects/variation.html", _context_for(
        request, project, error=error, att=card, aw=card["words"], can_edit=draft and is_open and variations.can_draft(request.user),
        can_approve=draft and is_open and variations.can_decide(request.user), can_reject=draft and variations.can_decide(request.user),
        can_cancel=draft and variations.can_draft(request.user), today=timezone.localdate(), page_title=f"{project.name} — {context['heading']}", **context))


@csrf_exempt  # CSRF is checked inside, after the upload cap handler is installed (the attachments views' own rule)
@require_POST
def variation_files(request, pk, oid):
    """The signed copy of the order: a photo or a PDF. Fields: ``file``, ``kind`` (photo|document). The order is looked up through its project."""

    lang = "en" if request.GET.get("lang") == "en" else "ar"  # not ``_lang``: reading POST here would close the upload handlers before the cap handler is installed
    try:
        project = services.get_owner("projects.project", pk, request.user)
    except services.OwnerNotFound:
        return attachment_views._fail(lang, "not_found", 404)
    except PermissionDenied:
        return attachment_views._fail(lang, "denied", 403)
    order = variations.get_order(project, oid)
    if order is None:
        return attachment_views._fail(lang, "not_found", 404)
    return attachment_views._capped_upload(request, order.pk, ORDER)


# ---- the print ----

def variation_print(request, pk, oid):
    from printing.company import company_details

    _need_view(request)
    lang = _lang(request)
    project = _project(pk)
    order = _order(project, oid)
    return render(request, "projects/variation_print.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "vw": WORDS[lang], "project": project, "company": company_details(),
        "printed": documents.when_text(timezone.now(), lang), "back_url": _url("variation", project, order, lang), **_order_context(order, lang)})
