"""CONTRACT-001 screens: the projects list and one project with its bills, materials and costs."""

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from expenses.models import Expense
from master_data.models import Customer, Item, Location
from permissions.decorators import require_any_permission, require_permission
from permissions.services import user_has_permission
from sales.models import SalesInvoice
from settings_core.display_labels import choice_label, localized_choices

from . import returns, services
from .models import Project, ProjectExpense, ProjectInvoice, ProjectStatus
from entities import scope as entity_scope


VIEW, MANAGE, PROFIT, ISSUE = "sales.view_sales_invoices", "sales.create_sales_invoice", "reports.view_profit_report", "inventory.adjust_stock"
#: HG-048: a site engineer reads the projects without the sales screens, and issues materials to the site
#: without general stock adjustment (the stock engine accepts projects.issue_to_site for that one flow).
VIEW_PROJECTS, ISSUE_TO_SITE = "projects.view_projects", "projects.issue_to_site"
PROJECT_SCREENS = (VIEW, VIEW_PROJECTS)


def can_issue(user):
    return user_has_permission(user, ISSUE) or user_has_permission(user, ISSUE_TO_SITE)
WORDS = {
    "ar": {
        "page_title": "المشاريع", "title": "المشاريع", "intro": "كل مشروع بعميله وقيمة عقده: المستخلصات، الخامات المصروفة، المصروفات، والربح.",
        "new": "مشروع جديد", "code": "الكود", "name": "اسم المشروع", "customer": "العميل", "site": "الموقع", "contract": "قيمة العقد", "start": "البداية",
        "end": "النهاية المتوقعة", "status": "الحالة", "notes": "ملاحظات", "save": "حفظ", "created": "اتعمل المشروع {code}.", "saved": "اتحفظ المشروع.",
        "empty": "لسه مفيش مشاريع.", "all": "كل الحالات", "show": "عرض", "back": "المشاريع", "edit": "تعديل المشروع",
        "billed": "اتفوتر (من غير ضريبة)", "collected": "اتحصّل", "due": "باقي على العميل", "remaining": "باقي من العقد", "over": "زيادة عن العقد",
        "progress": "نسبة الفوترة", "cost": "التكلفة", "materials": "الخامات", "expenses": "المصروفات", "profit": "الربح", "loss": "خسارة", "margin": "هامش الربح", "drafts": "مسودات لسه مترحّلتش",
        "bills": "المستخلصات والفواتير", "bill_new": "مستخلص جديد", "amount": "المبلغ", "description": "الوصف (مثلاً: مستخلص 2 - أعمال خرسانة)", "service": "بند الفاتورة",
        "default_service": "مستخلص أعمال (افتراضي)", "bill_btn": "اعمل المستخلص", "billed_ok": "اتعملت مسودة {number}؛ رحّلها من شاشة الفاتورة.",
        "link_invoice": "اربط فاتورة موجودة للعميل ده", "link": "اربط", "linked": "اتربط.", "unlink": "فك الربط", "unlinked": "اتفك الربط.",
        "invoice": "الفاتورة", "date": "التاريخ", "total": "الإجمالي", "doc_status": "الحالة",
        "materials_title": "الخامات المصروفة للموقع", "issue_btn": "اصرف من المخزن", "item": "الصنف", "location": "المخزن", "qty": "الكمية", "unit_cost": "تكلفة الوحدة",
        "issued": "اتصرفت الخامات ({ref}).", "issue_note": "الصرف بيطلع من المخزون بتكلفة متوسط الصنف، وبيتسجل كتسوية خروج مرتبطة بالمشروع.",
        "voucher_note": "إذن صرف واحد لكذا صنف من نفس المخزن: كل الأسطر بتتسجل مع بعض أو مفيش ولا سطر. سيب السطر فاضي لو مش محتاجه.",
        "line_n": "سطر {n}", "direction": "النوع", "dir_issue": "صرف", "dir_return": "ارتجاع", "print": "اطبع الإذن", "value": "القيمة",
        "return_title": "ارتجاع خامات من الموقع", "return_btn": "سجّل الارتجاع", "returned": "رجعت الخامات للمخزن ({ref}).",
        "return_note": "الفايض بيرجع المخزن بنفس تكلفة صرفه على المشروع (صافي المصروف من الصنف)، مش بمتوسط النهارده، وبينزل من تكلفة المشروع بنفس القيمة. مينفعش يرجع أكتر من الصافي المصروف.",
        "returnable": "المتاح للارتجاع", "nothing_out": "مفيش خامات مصروفة للمشروع لسه عشان ترجع.",
        "expenses_title": "مصروفات المشروع", "link_expense": "اربط مصروف متسجل", "expense_hint": "سجّل المصروف الأول من شاشة المصروفات، وبعدين اربطه هنا.",
        "none": "مفيش.", "view_only": "التعديل لصاحب صلاحية البيع بس.", "statuses": "غيّر الحالة",
    },
    "en": {
        "page_title": "Projects", "title": "Projects", "intro": "Each project with its customer and contract value: progress bills, materials used, expenses and profit.",
        "new": "New project", "code": "Code", "name": "Project name", "customer": "Customer", "site": "Site", "contract": "Contract value", "start": "Start",
        "end": "Expected end", "status": "Status", "notes": "Notes", "save": "Save", "created": "Project {code} created.", "saved": "Project saved.",
        "empty": "No projects yet.", "all": "All statuses", "show": "Show", "back": "Projects", "edit": "Edit project",
        "billed": "Billed (before tax)", "collected": "Collected", "due": "Customer owes", "remaining": "Left on contract", "over": "Over contract",
        "progress": "Billed share", "cost": "Cost", "materials": "Materials", "expenses": "Expenses", "profit": "Profit", "loss": "Loss", "margin": "Margin", "drafts": "Drafts not posted yet",
        "bills": "Progress bills & invoices", "bill_new": "New progress bill", "amount": "Amount", "description": "Description (e.g. bill 2 - concrete works)", "service": "Invoice line",
        "default_service": "Progress bill (default)", "bill_btn": "Make the bill", "billed_ok": "Draft {number} made; post it from the invoice screen.",
        "link_invoice": "Link an existing invoice of this customer", "link": "Link", "linked": "Linked.", "unlink": "Unlink", "unlinked": "Unlinked.",
        "invoice": "Invoice", "date": "Date", "total": "Total", "doc_status": "Status",
        "materials_title": "Materials sent to site", "issue_btn": "Issue from stock", "item": "Item", "location": "Location", "qty": "Qty", "unit_cost": "Unit cost",
        "issued": "Materials issued ({ref}).", "issue_note": "Issuing takes the stock out at the item's average cost, recorded as an adjustment out linked to the project.",
        "voucher_note": "One issue voucher for several items from one location: every line is saved together, or none is. Leave a line blank if you do not need it.",
        "line_n": "Line {n}", "direction": "Type", "dir_issue": "Issue", "dir_return": "Return", "print": "Print voucher", "value": "Value",
        "return_title": "Materials back from the site", "return_btn": "Record the return", "returned": "Materials returned to stock ({ref}).",
        "return_note": "Surplus comes back to stock at the cost it was issued to the project at (the project's net issued cost of the item), not today's average, and the project's cost falls by the same amount. No more than the net quantity issued can come back.",
        "returnable": "Can come back", "nothing_out": "Nothing has been issued to this project yet, so nothing can come back.",
        "expenses_title": "Project expenses", "link_expense": "Link a recorded expense", "expense_hint": "Record the expense on the expenses screen first, then link it here.",
        "none": "None.", "view_only": "Only users who can sell can change projects.", "statuses": "Change status",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _pick(model, raw, **filters):
    raw = str(raw or "")
    return model.objects.filter(pk=raw, **filters).first() if raw.isdigit() else None


def _date(raw):
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _statuses(lang):
    return [(value, choice_label(Project(status=value), "status", lang)) for value in ProjectStatus.values]


def _base(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"],
               "can_manage": user_has_permission(request.user, MANAGE), "can_profit": user_has_permission(request.user, PROFIT),
               "can_issue": can_issue(request.user), "can_view_sales": user_has_permission(request.user, VIEW), "statuses": _statuses(lang)}
    context.update(extra)
    return context


def _form_data(request):
    return {"name": request.POST.get("name", ""), "customer": _pick(Customer, request.POST.get("customer"), active=True), "site": request.POST.get("site", ""),
            "contract_value": request.POST.get("contract_value", ""), "start_date": _date(request.POST.get("start_date")),
            "end_date": _date(request.POST.get("end_date")), "notes": request.POST.get("notes", ""), "status": request.POST.get("status")}


#: Contracting C12 (HG-127): blank lines the voucher form offers; empty ones are skipped.
VOUCHER_ROWS = 5


def _voucher_lines(request):
    """The voucher's lines as typed: one per row with an item or a quantity. One row is the single issue form as before."""

    items, quantities = request.POST.getlist("item"), request.POST.getlist("quantity")
    lines = []
    for n in range(max(len(items), len(quantities))):
        raw_item = items[n] if n < len(items) else ""
        raw_quantity = quantities[n] if n < len(quantities) else ""
        if not str(raw_item).strip() and not str(raw_quantity).strip():
            continue
        lines.append({"item": _pick(Item, raw_item, active=True), "quantity": raw_quantity})
    return lines


def _voucher_label(operations):
    return operations[0].reference_number.split("/", 1)[0]


def _material_rows(project, lang):
    """The materials table: each issue and return, with its direction and its voucher's print page."""

    rows = []
    for link in project.issues.select_related("operation", "operation__item", "operation__source_location", "operation__destination_location"):
        operation = link.operation
        back = returns.is_return(operation)
        voucher = returns.parse_voucher_reference(operation.reference_number)
        print_url = ""
        if voucher:
            kind, number = voucher
            print_url = reverse("projects:voucher_print", args=[project.pk, "return" if kind == returns.RETURN else "issue", number]) + f"?lang={lang}"
        rows.append({"op": operation, "is_return": back, "direction": WORDS[lang]["dir_return" if back else "dir_issue"],
                     "location": operation.destination_location if back else operation.source_location, "print_url": print_url})
    return rows


@require_any_permission(*PROJECT_SCREENS)
def project_list(request):
    lang = _lang(request)
    words = WORDS[lang]
    error, form = "", {"status": ProjectStatus.ACTIVE, "start_date": timezone.localdate().isoformat()}
    if request.method == "POST":
        if not user_has_permission(request.user, MANAGE):
            raise PermissionDenied("Projects need sales.create_sales_invoice.")
        form = request.POST
        try:
            project = services.save_project(_form_data(request), request.user, lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["created"].format(code=project.code))
            return redirect(f"{reverse('projects:detail', args=[project.pk])}?lang={lang}")
    status = request.GET.get("status", "")
    rows = Project.objects.select_related("customer")
    if status in ProjectStatus.values:
        rows = rows.filter(status=status)
    from .contract import due_now_many

    page = list(rows[:200])
    due = due_now_many(page)  # grouped queries, not one contract position per row
    rows = [{"project": project, "status_label": choice_label(project, "status", lang), "figures": services.summary(project), "due_now": due[project.pk]}
            for project in page]
    return render(request, "projects/list.html", _base(request, rows=rows, status=status, form=form, error=error, report_label=REPORT_WORDS[lang]["report"],
                                                        customers=Customer.objects.filter(active=True).order_by("name")))


@require_any_permission(*PROJECT_SCREENS)
def project_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    project = get_object_or_404(Project.objects.select_related("customer"), pk=pk)
    error = ""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action in ("issue", "return"):  # Contracting C12 (HG-127): a return from the site takes the same right as the issue
            if not can_issue(request.user):
                raise PermissionDenied("Issuing materials needs inventory.adjust_stock or projects.issue_to_site.")
        elif not user_has_permission(request.user, MANAGE):
            raise PermissionDenied("Projects need sales.create_sales_invoice.")
        try:
            if action == "edit":
                services.save_project(_form_data(request), request.user, project, lang)
                messages.success(request, words["saved"])
            elif action == "bill":
                invoice = services.bill_progress(project, request.user, amount=request.POST.get("amount"), description=request.POST.get("description", ""),
                                                 item=_pick(Item, request.POST.get("item"), active=True, is_stock_tracked=False), lang=lang)
                messages.success(request, words["billed_ok"].format(number=invoice.invoice_number))
            elif action == "link_invoice":
                # Only what the working entity may see, as the picker offers (HG-034).
                raw = str(request.POST.get("invoice") or "")
                invoice = entity_scope.scope(SalesInvoice.objects, entity_scope.SALES_INVOICE).filter(pk=raw).first() if raw.isdigit() else None
                if invoice is None:
                    raise ValidationError(services.MESSAGES[lang]["not_posted"])
                services.link_invoice(project, invoice, request.user, lang)
                messages.success(request, words["linked"])
            elif action == "link_expense":
                raw = str(request.POST.get("expense") or "")
                expense = entity_scope.scope(Expense.objects, entity_scope.EXPENSE).filter(pk=raw).first() if raw.isdigit() else None
                if expense is None:
                    raise ValidationError(services.MESSAGES[lang]["not_posted"])
                services.link_expense(project, expense, request.user, lang, heading=request.POST.get("heading"))
                messages.success(request, words["linked"])
            elif action in ("unlink_invoice", "unlink_expense"):
                model = ProjectInvoice if action == "unlink_invoice" else ProjectExpense
                link = _pick(model, request.POST.get("link"), project=project)
                if link:
                    services.unlink(project, link, request.user, lang)
                    messages.success(request, words["unlinked"])
            elif action == "issue":
                operations = returns.issue_voucher(project, request.user, location=_pick(Location, request.POST.get("location"), active=True),
                                                   lines=_voucher_lines(request), lang=lang)
                messages.success(request, words["issued"].format(ref=_voucher_label(operations)))
            elif action == "return":
                operations = returns.return_materials(project, request.user, location=_pick(Location, request.POST.get("location"), active=True),
                                                      lines=_voucher_lines(request), lang=lang)
                messages.success(request, words["returned"].format(ref=_voucher_label(operations)))
        except ValidationError as exc:
            from settings_core.ui_messages import translate

            error = translate(" ".join(exc.messages), lang)
        else:
            return redirect(f"{reverse('projects:detail', args=[project.pk])}?lang={lang}")
    invoices = project.invoices.select_related("invoice")
    linked_ids = ProjectInvoice.objects.values_list("invoice_id", flat=True)
    since = timezone.localdate() - timedelta(days=120)
    context = _base(
        request, project=project, status_label=choice_label(project, "status", lang), figures=services.summary(project), error=error,
        invoices=[{"link": link, "status_label": choice_label(link.invoice, "status", lang)} for link in invoices],
        issues=_material_rows(project, lang), returnable=returns.returnable(project), voucher_rows=[(n, WORDS[lang]["line_n"].format(n=n)) for n in range(1, VOUCHER_ROWS + 1)],
        expense_links=project.expenses.select_related("expense", "expense__category", "expense__cashbox_operation"),
        free_invoices=entity_scope.scope(SalesInvoice.objects, entity_scope.SALES_INVOICE).filter(customer=project.customer).exclude(status="cancelled").exclude(pk__in=linked_ids).order_by("-invoice_date")[:50],
        free_expenses=entity_scope.scope(Expense.objects, entity_scope.EXPENSE).filter(expense_date__gte=since, cashbox_operation__status="posted", project_link__isnull=True).order_by("-expense_date")[:50],
        services_list=Item.objects.filter(active=True, is_stock_tracked=False).exclude(item_code__startswith=services.BILLING_ITEM_CODE).order_by("item_name"),
        stock_items=Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_name"), locations=entity_scope.locations(Location.objects).filter(active=True),
        customers=Customer.objects.filter(active=True).order_by("name"),
        form={"name": project.name, "customer": str(project.customer_id), "site": project.site, "contract_value": project.contract_value,
              "start_date": project.start_date.isoformat() if project.start_date else "", "end_date": project.end_date.isoformat() if project.end_date else "",
              "notes": project.notes, "status": project.status},
        is_open=project.status in services.OPEN,
    )
    from .contract import position
    from .contract_views import WORDS as CONTRACT_WORDS

    from .views_reports import cta

    from . import variations

    context.update(tab="overview", cwords=CONTRACT_WORDS[lang], site_cta=cta(project, request.user, lang), position=position(project) if context["can_view_sales"] else None,
                   variation=variations.figures(project) if context["can_view_sales"] and variations.in_reach(project) else None,  # Contracting C1 (HG-103)
                   expense_headings=localized_choices(ProjectExpense, "heading", lang))
    from .views_schedule import header_flag

    context["schedule_flag"] = header_flag(project, request.user, lang)  # Contracting C3 (HG-124): the delay line under the title
    return render(request, "projects/detail.html", context)


REPORT_WORDS = {
    "ar": {"report": "تقرير المشاريع", "intro": "كل مشروع: العقد واللي اتفوتر، والجدول الزمني قصاد الفوترة، والتكلفة والربح، وضمان الأعمال المحجوز. من المستندات النهائية بس.",
           "all": "كل الحالات", "show": "اعرض", "project": "المشروع", "contract": "العقد", "billed": "اتفوتر", "progress": "نسبة الفوترة",
           "time": "اللي فات من المدة", "behind": "متأخر عن الجدول", "cost": "التكلفة", "profit": "الربح", "loss": "خسارة", "margin": "الهامش", "due": "على العميل",
           "retention": "ضمان محجوز", "losing": "مشاريع خسرانة", "behind_n": "متأخرة عن الجدول", "overdue": "مستخلصات متأخرة",
           "overdue_intro": "مستخلصات مترحّلة لسه عليها فلوس وعدّى عليها أكتر من {days} يوم (مدة الآجل في إعدادات أعمار الديون).",
           "certificate": "المستخلص", "date": "التاريخ", "owed": "الباقي", "age": "عمره (يوم)", "late": "متأخر (يوم)", "customer": "العميل",
           "none": "مفيش مشاريع.", "no_overdue": "مفيش مستخلصات متأخرة.", "total": "الإجمالي",
           "plan": "الجدول: المخطط {planned}% والمنفّذ {actual}%"},
    "en": {"report": "Projects report", "intro": "Each project: contract and billed, the calendar against billing, cost and profit, and the retention held. Posted documents only.",
           "all": "All statuses", "show": "Show", "project": "Project", "contract": "Contract", "billed": "Billed", "progress": "Billed %",
           "time": "Time gone", "behind": "Behind schedule", "cost": "Cost", "profit": "Profit", "loss": "Loss", "margin": "Margin", "due": "Owed by client",
           "retention": "Retention held", "losing": "Projects losing money", "behind_n": "behind schedule", "overdue": "Overdue certificates",
           "overdue_intro": "Posted certificates still owed and older than {days} days (the credit days in the aging settings).",
           "certificate": "Certificate", "date": "Date", "owed": "Owed", "age": "Age (days)", "late": "Late (days)", "customer": "Client",
           "none": "No projects.", "no_overdue": "No overdue certificates.", "total": "Total",
           "plan": "Schedule: planned {planned}%, done {actual}%"},
}


@require_permission(PROFIT)
def report(request):
    from . import insights

    if not user_has_permission(request.user, VIEW):  # it shows the projects and their invoices, as the list does
        raise PermissionDenied("The projects report also needs sales.view_sales_invoices.")
    lang = _lang(request)
    status = request.GET.get("status", ProjectStatus.ACTIVE)
    status = status if status in ProjectStatus.values else ""
    rows, totals = insights.portfolio(status)
    overdue, overdue_total, terms = insights.overdue_certificates(status=status)
    words = REPORT_WORDS[lang]
    from .views_schedule import _pct

    for row in rows:  # Contracting C3 (HG-124): a project with a schedule shows its planned and done progress beside the flag
        plan = row["schedule"]
        row["plan_text"] = words["plan"].format(planned=_pct(plan["planned"]), actual=_pct(plan["actual"])) if plan else ""
    return render(request, "projects/report.html", _base(request, page_title=words["report"], rw=words, rows=rows, totals=totals, status=status,
                                                       overdue=overdue, overdue_total=overdue_total,
                                                       overdue_intro=words["overdue_intro"].format(days=terms)))


VOUCHER_WORDS = {
    "ar": {"issue": "إذن صرف خامات", "return": "إذن ارتجاع خامات", "number": "رقم الإذن", "date": "التاريخ", "project": "المشروع", "site": "الموقع",
           "from": "من مخزن", "to": "إلى مخزن", "by": "سجّله", "n": "م", "code": "الكود", "item": "الصنف", "unit": "الوحدة", "qty": "الكمية",
           "unit_cost": "تكلفة الوحدة", "value": "القيمة", "total": "الإجمالي", "lines": "عدد الأسطر", "storekeeper": "أمين المخزن",
           "engineer": "مهندس الموقع", "received_by": "استلم", "handed_by": "سلّم", "cancelled": "ملغي", "print": "اطبع", "back": "رجوع للمشروع",
           "hint": "مقاس A4. اختار «حفظ PDF» من نافذة الطباعة لو عايز نسخة رقمية.",
           "return_note": "الخامات رجعت المخزن بتكلفة صرفها على المشروع، ونزلت من تكلفة المشروع بنفس القيمة."},
    "en": {"issue": "Materials issue voucher", "return": "Materials return voucher", "number": "Voucher no.", "date": "Date", "project": "Project", "site": "Site",
           "from": "From location", "to": "To location", "by": "Recorded by", "n": "#", "code": "Code", "item": "Item", "unit": "Unit", "qty": "Qty",
           "unit_cost": "Unit cost", "value": "Value", "total": "Total", "lines": "Lines", "storekeeper": "Storekeeper",
           "engineer": "Site engineer", "received_by": "received", "handed_by": "handed over", "cancelled": "Cancelled", "print": "Print", "back": "Back to the project",
           "hint": "A4 size. Choose «Save as PDF» in the print dialog for a digital copy.",
           "return_note": "The materials came back to stock at the cost they were issued to the project at, and the project's cost fell by the same amount."},
}


@require_any_permission(*PROJECT_SCREENS)
def voucher_print(request, pk, kind, number):
    """Contracting C12 (HG-127): the printed «إذن صرف» or «إذن ارتجاع» on A4, signed by the storekeeper and the site engineer."""

    from django.http import Http404

    from config.money import money_round
    from printing.company import company_details

    from .documents import when_text

    lang = _lang(request)
    project = get_object_or_404(Project.objects.select_related("customer"), pk=pk)
    if kind not in ("issue", "return"):
        raise Http404
    operations = returns.voucher_operations(project, returns.RETURN if kind == "return" else returns.ISSUE, number)
    if not operations:
        raise Http404
    show_cost = user_has_permission(request.user, PROFIT)
    back = kind == "return"
    rows = [{"n": n, "op": op, "value": money_round(op.quantity * op.unit_cost)} for n, op in enumerate(operations, start=1)]
    first = operations[0]
    location = first.destination_location if back else first.source_location
    words = VOUCHER_WORDS[lang]
    return render(request, "projects/voucher_print.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "vw": words, "project": project, "company": company_details(), "kind": kind,
        "heading": words[kind], "reference": first.reference_number.split("/", 1)[0], "day": first.operation_date, "location": location,
        "location_label": words["to" if back else "from"], "rows": rows, "show_cost": show_cost,
        "total": money_round(sum((row["value"] for row in rows), money_round(0))), "cancelled": all(op.status != "posted" for op in operations),
        "recorder": first.created_by, "printed": when_text(timezone.now(), lang),
        "back_url": f"{reverse('projects:detail', args=[project.pk])}?lang={lang}",
    })
