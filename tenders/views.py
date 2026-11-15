"""Contracting C8 (HG-126) screens: «العطاءات» (the list), the form, a tender's page (its items with their cost lines, the
summary and the won / lost / withdrawn actions), one item's page (edit it and its cost lines), and the two prints: the
priced bill of quantities for submission (no cost shown) and the internal analysis (costs shown). Nothing here moves money."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer
from permissions.decorators import require_permission
from settings_core.ui_messages import translate

from . import services
from .models import Tender, TenderItem, TenderStatus

WORDS = {
    "ar": {
        "title": "العطاءات", "intro": "دراسة العطاء قبل التقديم: بنود المقايسة بكمياتها، وتحليل سعر كل بند من خامات وعمالة ومعدات، ونسبة المصاريف العامة والربح. العطاء مبيحرّكش فلوس؛ ولو اترسّت عليك بيتعمل منه مشروع بمقايسته وميزانيته.",
        "new": "عطاء جديد", "edit": "تعديل العطاء", "back": "العطاءات", "open": "فتح", "save": "حفظ", "show": "عرض", "all": "كل الحالات",
        "name": "اسم العطاء", "customer": "العميل", "no_customer": "لسه مش عميل", "prospect": "الجهة صاحبة العطاء (لو لسه مش عميل)",
        "party": "صاحب العطاء", "due_on": "ميعاد التقديم", "status": "الحالة", "notes": "ملاحظات", "items_n": "البنود",
        "overhead": "المصاريف العامة %", "profit": "الربح %", "total": "إجمالي العطاء", "none": "مفيش عطاءات هنا.",
        "created": "اتسجل العطاء. ضيف بنود المقايسة وسعّر كل بند.", "saved": "اتحفظ.", "done": "تم.",
        "items": "بنود المقايسة", "code": "الكود", "description": "الوصف", "unit": "الوحدة", "qty": "الكمية", "rate": "سعر الوحدة",
        "amount": "الإجمالي", "add_item": "إضافة بند", "no_items": "مفيش بنود لسه. ضيف أول بند من المقايسة.",
        "edit_item": "تعديل البند وتحليل سعره", "change": "تعديل", "delete_item": "امسح البند", "item_title": "بند {line}",
        "costs": "تحليل سعر الوحدة", "heading": "بند التكلفة", "cost_note": "البيان", "unit_cost": "تكلفة الوحدة", "add_cost": "إضافة سطر تكلفة",
        "delete": "امسح", "no_costs": "البند ده مالوش تكلفة لسه.", "direct": "التكلفة المباشرة للوحدة", "after_overhead": "بعد المصاريف العامة",
        "unpriced": "مش متسعّر", "summary": "ملخص التكلفة", "direct_total": "التكلفة المباشرة", "overhead_amount": "المصاريف العامة",
        "profit_amount": "الربح", "by_heading": "التكلفة حسب البند", "share": "النسبة",
        "costs_hidden": "تحليل التكلفة والنسب بيظهروا للي معاه صلاحية تقرير الأرباح بس.",
        "actions": "الحالة والقرار", "submit": "اتقدّم", "submit_hint": "سجّل إن العطاء اتقدّم للجهة. تقدر تعدّل الأسعار بعدها لحد ما يترسي.",
        "win": "اترسّت علينا", "win_hint": "بيتعمل مشروع جديد باسم العطاء: مقايسته بنفس البنود والكميات وبسعر الوحدة اللي في التحليل، وميزانيته حسب بند التكلفة. بعدها العطاء بيتقفل للقراءة بس.",
        "win_customer": "العميل اللي هيتعمل له المشروع", "choose": "اختار", "lose": "مارسيتش علينا", "withdraw": "انسحبنا",
        "reason": "السبب", "close_hint": "لازم تكتب السبب؛ العطاء بيتقفل للقراءة بس.", "on": "التاريخ",
        "won_note": "العطاء ده اترسّى واتعمل منه المشروع {code}. العطاء بقى للقراءة بس.", "open_project": "افتح المشروع",
        "closed_note": "العطاء ده اتقفل ({status}) يوم {day}: {reason}", "won_made": "اترسّى العطاء واتعمل المشروع {code} بمقايسته وميزانيته.",
        "print_boq": "طباعة المقايسة المسعّرة (للتقديم)", "print_analysis": "طباعة تحليل الأسعار (داخلي)",
        "p_print": "اطبع", "p_back": "ارجع للعطاء", "p_hint": "للطباعة على A4. تقدر تحفظها PDF من نافذة الطباعة.",
        "p_boq_title": "مقايسة مسعّرة", "p_analysis_title": "تحليل أسعار العطاء", "p_to": "مقدّم إلى", "p_internal": "داخلي — مش للتقديم",
        "p_no": "م", "p_signature": "توقيع وختم المقاول", "p_date": "تاريخ الطباعة",
    },
    "en": {
        "title": "Tenders", "intro": "Price a tender before you submit it: the bill of quantities with its quantities, each item's price analysis by materials, labour and equipment, and the overhead and profit percents. A tender moves no money; once won it becomes a project with its bill of quantities and budget.",
        "new": "New tender", "edit": "Edit the tender", "back": "Tenders", "open": "Open", "save": "Save", "show": "Show", "all": "All statuses",
        "name": "Tender name", "customer": "Customer", "no_customer": "Not a customer yet", "prospect": "Inviting party (if not a customer yet)",
        "party": "Invited by", "due_on": "Submission date", "status": "Status", "notes": "Notes", "items_n": "Items",
        "overhead": "Overhead %", "profit": "Profit %", "total": "Tender total", "none": "No tenders here.",
        "created": "Tender recorded. Add the items of the bill of quantities and price each one.", "saved": "Saved.", "done": "Done.",
        "items": "Bill of quantities", "code": "Code", "description": "Description", "unit": "Unit", "qty": "Quantity", "rate": "Unit rate",
        "amount": "Amount", "add_item": "Add an item", "no_items": "No items yet. Add the first item of the bill of quantities.",
        "edit_item": "Edit the item and its price analysis", "change": "Edit", "delete_item": "Remove the item", "item_title": "Item {line}",
        "costs": "Unit price analysis", "heading": "Cost heading", "cost_note": "Details", "unit_cost": "Unit cost", "add_cost": "Add a cost line",
        "delete": "Remove", "no_costs": "This item has no cost yet.", "direct": "Direct unit cost", "after_overhead": "After overhead",
        "unpriced": "Not priced", "summary": "Cost summary", "direct_total": "Direct cost", "overhead_amount": "Overhead",
        "profit_amount": "Profit", "by_heading": "Cost by heading", "share": "Share",
        "costs_hidden": "The cost analysis and the percents show only to holders of the profit report permission.",
        "actions": "Status and decision", "submit": "Submitted", "submit_hint": "Record that the tender was submitted. Prices stay editable until it is decided.",
        "win": "Won", "win_hint": "A new project is made under the tender's name: its bill of quantities with the same items and quantities at the analysed unit rates, and its budget by cost heading. The tender then becomes read-only.",
        "win_customer": "The customer the project is for", "choose": "Choose", "lose": "Lost", "withdraw": "Withdrawn",
        "reason": "Reason", "close_hint": "A reason is required; the tender becomes read-only.", "on": "Date",
        "won_note": "This tender was won and became project {code}. The tender is read-only now.", "open_project": "Open the project",
        "closed_note": "This tender was closed ({status}) on {day}: {reason}", "won_made": "Tender won: project {code} was made with its bill of quantities and budget.",
        "print_boq": "Print the priced bill of quantities (to submit)", "print_analysis": "Print the price analysis (internal)",
        "p_print": "Print", "p_back": "Back to the tender", "p_hint": "For A4. You can save it as PDF from the print dialog.",
        "p_boq_title": "Priced bill of quantities", "p_analysis_title": "Tender price analysis", "p_to": "Submitted to", "p_internal": "Internal — not for submission",
        "p_no": "No.", "p_signature": "Contractor's signature and stamp", "p_date": "Printed on",
    },
}

STATUS_TONE = {"studying": "open", "submitted": "open", "won": "posted", "lost": "cancelled", "withdrawn": "cancelled"}
CUSTOMER_PICKER = 1000


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    words = WORDS[lang]
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], **extra}


def _error_text(exc, lang):
    if isinstance(exc, PermissionDenied):
        return str(exc) or services.MESSAGES[lang]["denied"]
    return " ".join(translate(message, lang) for message in getattr(exc, "messages", [str(exc)]))


def _tender_or_404(pk):
    tender = services.scoped(Tender.objects.select_related("customer", "project", "created_by")).filter(pk=pk).first()
    if tender is None:
        raise Http404
    return tender


def _item_or_404(tender, iid):
    item = TenderItem.objects.filter(pk=iid, tender=tender).first()
    if item is None:
        raise Http404
    return item


def _detail_url(tender, lang):
    return f"{reverse('tenders:detail', args=[tender.pk])}?lang={lang}"


def _customers(own=None):
    rows = list(Customer.objects.filter(active=True).order_by("name")[:CUSTOMER_PICKER])
    if own is not None and all(row.pk != own.pk for row in rows):
        rows.insert(0, own)
    return rows


def _customer(raw):
    raw = str(raw or "")
    return Customer.objects.filter(pk=raw).first() if raw.isdigit() else None


def _party(tender, lang):
    return tender.customer.name if tender.customer_id else (tender.prospect_name or WORDS[lang]["no_customer"])


def _status(tender, lang):
    return {"value": tender.status, "label": services.label("statuses", tender.status, lang), "tone": STATUS_TONE.get(tender.status, "open")}


@require_permission(services.MANAGE)
def tender_list(request):
    lang = _lang(request)
    status = request.GET.get("status", "")
    status = status if status in TenderStatus.values else ""
    tenders = services.scoped(Tender.objects.select_related("customer", "project"))
    if status:
        tenders = tenders.filter(status=status)
    rows = []
    for tender in tenders[:300]:
        figures = services.analysis(tender, lang)
        rows.append({"t": tender, "party": _party(tender, lang), "status": _status(tender, lang), "total": figures["total"],
                     "items": len(figures["rows"])})
    return render(request, "tenders/list.html", _context(
        request, rows=rows, status=status, statuses=[(value, services.label("statuses", value, lang)) for value in TenderStatus.values]))


@require_permission(services.MANAGE)
def tender_form(request, pk=None):
    lang = _lang(request)
    words = WORDS[lang]
    tender = _tender_or_404(pk) if pk is not None else None
    if tender is not None and not tender.is_open:
        return redirect(_detail_url(tender, lang))  # a decided tender is history
    can_costs = services.can_see_costs(request.user)
    error = ""
    if request.method == "POST":
        form = request.POST
        data = {"name": form.get("name"), "customer": _customer(form.get("customer")), "prospect_name": form.get("prospect_name"),
                "due_on": form.get("due_on") or "", "overhead_percent": form.get("overhead_percent"), "profit_percent": form.get("profit_percent"),
                "notes": form.get("notes")}
        try:
            if form.get("customer") and data["customer"] is None:
                raise ValidationError(services.MESSAGES[lang]["customer"])
            saved = services.save_tender(data, request.user, tender, lang)
        except (ValidationError, PermissionDenied) as exc:
            error = _error_text(exc, lang)
        else:
            messages.success(request, words["saved"] if tender else words["created"])
            return redirect(_detail_url(saved, lang))
    elif tender is not None:
        form = {"name": tender.name, "customer": str(tender.customer_id or ""), "prospect_name": tender.prospect_name,
                "due_on": tender.due_on.isoformat() if tender.due_on else "", "overhead_percent": f"{tender.overhead_percent:.2f}",
                "profit_percent": f"{tender.profit_percent:.2f}", "notes": tender.notes}
    else:
        form = {"overhead_percent": "10.00", "profit_percent": "10.00"}
    return render(request, "tenders/form.html", _context(
        request, tender=tender, form=form, error=error, can_costs=can_costs, customers=_customers(tender.customer if tender else None),
        page_title=words["edit"] if tender else words["new"]))


def _post_detail(request, tender, lang):
    action = request.POST.get("action", "")
    user = request.user
    if action == "add_item":
        services.save_item(tender, user, {key: request.POST.get(key) for key in ("code", "description", "unit", "quantity")}, lang=lang)
        return WORDS[lang]["saved"]
    if action == "submit":
        services.submit(tender, user, lang=lang)
        return WORDS[lang]["done"]
    if action in (TenderStatus.LOST, TenderStatus.WITHDRAWN):
        services.close(tender, user, action, request.POST.get("reason"), lang=lang)
        return WORDS[lang]["done"]
    if action == "win":
        raw = request.POST.get("customer")
        customer = _customer(raw)
        if raw and customer is None:
            raise ValidationError(services.MESSAGES[lang]["customer"])
        project = services.win(tender, user, customer=customer, lang=lang)
        return WORDS[lang]["won_made"].format(code=project.code)
    return ""


@require_permission(services.MANAGE)
def tender_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    tender = _tender_or_404(pk)
    if request.method == "POST":
        try:
            done = _post_detail(request, tender, lang)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc, lang))
        else:
            if done:
                messages.success(request, done)
        return redirect(_detail_url(tender, lang))
    figures = services.analysis(tender, lang)
    can_costs = services.can_see_costs(request.user)
    rows = [dict(row, cost_rows=[{"c": cost, "heading": services.label("headings", cost.heading, lang)} for cost in row["costs"]]) for row in figures["rows"]]
    closed_note = ""
    if tender.status in (TenderStatus.LOST, TenderStatus.WITHDRAWN):
        from settings_core.templatetags.hesba_format import day_text

        closed_note = words["closed_note"].format(status=services.label("statuses", tender.status, lang),
                                                  day=day_text(tender.status_on, lang) if tender.status_on else "—", reason=tender.close_reason)
    return render(request, "tenders/detail.html", _context(
        request, t=tender, party=_party(tender, lang), status=_status(tender, lang), figures=figures, rows=rows, can_costs=can_costs,
        is_open=tender.is_open, is_studying=tender.status == TenderStatus.STUDYING, customers=_customers(tender.customer) if tender.is_open else [],
        won_note=words["won_note"].format(code=tender.project.code) if tender.project_id else "", closed_note=closed_note,
        page_title=f"{words['title']} · {tender.name}"))


ITEM_ACTIONS = ("save_item", "delete_item", "add_cost", "save_cost", "delete_cost")


@require_permission(services.MANAGE)
def item_detail(request, pk, iid):
    lang = _lang(request)
    words = WORDS[lang]
    tender = _tender_or_404(pk)
    item = _item_or_404(tender, iid)
    item_url = f"{reverse('tenders:item', args=[tender.pk, item.pk])}?lang={lang}"
    if request.method == "POST":
        action = request.POST.get("action", "")
        try:
            if action == "save_item":
                services.save_item(tender, request.user, {key: request.POST.get(key) for key in ("code", "description", "unit", "quantity")}, item, lang)
            elif action == "delete_item":
                services.delete_item(tender, item, request.user, lang)
                messages.success(request, words["done"])
                return redirect(_detail_url(tender, lang))
            elif action in ("add_cost", "save_cost", "delete_cost"):
                cost = None
                if action != "add_cost":
                    raw = str(request.POST.get("cost") or "")
                    cost = item.costs.filter(pk=raw).first() if raw.isdigit() else None
                    if cost is None:
                        raise Http404
                if action == "delete_cost":
                    services.delete_cost(cost, request.user, lang)
                else:
                    services.save_cost(item, request.user, {key: request.POST.get(key) for key in ("heading", "description", "unit_cost")}, cost, lang)
        except (ValidationError, PermissionDenied) as exc:
            messages.error(request, _error_text(exc, lang))
        else:
            if action in ITEM_ACTIONS:
                messages.success(request, words["saved"])
        return redirect(item_url)
    can_costs = services.can_see_costs(request.user)
    row = services.price_item(item, tender.overhead_percent, tender.profit_percent)
    return render(request, "tenders/item.html", _context(
        request, t=tender, item=item, row=row, can_costs=can_costs, is_open=tender.is_open,
        cost_rows=[{"c": cost, "heading": services.label("headings", cost.heading, lang)} for cost in row["costs"]],
        headings=[(value, services.label("headings", value, lang)) for value in services.CostHeading.values],
        item_title=words["item_title"].format(line=item.code or item.line_number),
        page_title=f"{tender.name} · {words['item_title'].format(line=item.code or item.line_number)}"))


def _print_context(request, tender, lang):
    from printing.company import company_details
    from settings_core.templatetags.hesba_format import day_text

    figures = services.analysis(tender, lang)
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "t": tender, "party": _party(tender, lang),
            "status": _status(tender, lang), "figures": figures, "company": company_details(), "back_url": _detail_url(tender, lang),
            "printed": day_text(timezone.localdate(), lang), "due": day_text(tender.due_on, lang) if tender.due_on else ""}


@require_permission(services.MANAGE)
def boq_print(request, pk):
    """The priced bill of quantities to submit: items, quantities, unit rates and totals. No cost, markup or heading."""

    lang = _lang(request)
    tender = _tender_or_404(pk)
    return render(request, "tenders/boq_print.html", _print_context(request, tender, lang))


@require_permission(services.PROFIT)
def analysis_print(request, pk):
    """The internal price analysis: each item's cost lines, its direct cost, markups and rate, and the cost by heading."""

    if not services.can_manage(request.user):
        raise PermissionDenied(services.MESSAGES[_lang(request)]["denied"])
    lang = _lang(request)
    tender = _tender_or_404(pk)
    context = _print_context(request, tender, lang)
    context["rows"] = [dict(row, cost_rows=[{"c": cost, "heading": services.label("headings", cost.heading, lang)} for cost in row["costs"]])
                       for row in context["figures"]["rows"]]
    return render(request, "tenders/analysis_print.html", context)
