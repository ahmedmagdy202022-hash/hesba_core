"""CONTRACT-001 screens: the projects list and one project with its bills, materials and costs."""

from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from expenses.models import Expense
from master_data.models import Customer, Item, Location
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from sales.models import SalesInvoice
from settings_core.display_labels import choice_label

from . import services
from .models import Project, ProjectExpense, ProjectInvoice, ProjectStatus


VIEW, MANAGE, PROFIT, ISSUE = "sales.view_sales_invoices", "sales.create_sales_invoice", "reports.view_profit_report", "inventory.adjust_stock"
WORDS = {
    "ar": {
        "page_title": "المشاريع", "title": "المشاريع", "intro": "كل مشروع بعميله وقيمة عقده: المستخلصات، الخامات المصروفة، المصروفات، والربح.",
        "new": "مشروع جديد", "code": "الكود", "name": "اسم المشروع", "customer": "العميل", "site": "الموقع", "contract": "قيمة العقد", "start": "البداية",
        "end": "النهاية المتوقعة", "status": "الحالة", "notes": "ملاحظات", "save": "حفظ", "created": "اتعمل المشروع {code}.", "saved": "اتحفظ المشروع.",
        "empty": "لسه مفيش مشاريع.", "all": "كل الحالات", "show": "عرض", "back": "المشاريع", "edit": "تعديل المشروع",
        "billed": "اتفوتر (من غير ضريبة)", "collected": "اتحصّل", "due": "باقي على العميل", "remaining": "باقي من العقد", "over": "زيادة عن العقد",
        "progress": "نسبة الفوترة", "cost": "التكلفة", "materials": "الخامات", "expenses": "المصروفات", "profit": "الربح", "margin": "هامش الربح", "drafts": "مسودات لسه مترحّلتش",
        "bills": "المستخلصات والفواتير", "bill_new": "مستخلص جديد", "amount": "المبلغ", "description": "الوصف (مثلاً: مستخلص 2 - أعمال خرسانة)", "service": "بند الفاتورة",
        "default_service": "مستخلص أعمال (افتراضي)", "bill_btn": "اعمل المستخلص", "billed_ok": "اتعملت مسودة {number}؛ رحّلها من شاشة الفاتورة.",
        "link_invoice": "اربط فاتورة موجودة للعميل ده", "link": "اربط", "linked": "اتربط.", "unlink": "فك الربط", "unlinked": "اتفك الربط.",
        "invoice": "الفاتورة", "date": "التاريخ", "total": "الإجمالي", "doc_status": "الحالة",
        "materials_title": "الخامات المصروفة للموقع", "issue_btn": "اصرف من المخزن", "item": "الصنف", "location": "المخزن", "qty": "الكمية", "unit_cost": "تكلفة الوحدة",
        "issued": "اتصرفت الخامات ({ref}).", "issue_note": "الصرف بيطلع من المخزون بتكلفة متوسط الصنف، وبيتسجل كتسوية خروج مرتبطة بالمشروع.",
        "expenses_title": "مصروفات المشروع", "link_expense": "اربط مصروف متسجل", "expense_hint": "سجّل المصروف الأول من شاشة المصروفات، وبعدين اربطه هنا.",
        "none": "مفيش.", "view_only": "التعديل لصاحب صلاحية البيع بس.", "statuses": "غيّر الحالة",
    },
    "en": {
        "page_title": "Projects", "title": "Projects", "intro": "Each project with its customer and contract value: progress bills, materials used, expenses and profit.",
        "new": "New project", "code": "Code", "name": "Project name", "customer": "Customer", "site": "Site", "contract": "Contract value", "start": "Start",
        "end": "Expected end", "status": "Status", "notes": "Notes", "save": "Save", "created": "Project {code} created.", "saved": "Project saved.",
        "empty": "No projects yet.", "all": "All statuses", "show": "Show", "back": "Projects", "edit": "Edit project",
        "billed": "Billed (before tax)", "collected": "Collected", "due": "Customer owes", "remaining": "Left on contract", "over": "Over contract",
        "progress": "Billed share", "cost": "Cost", "materials": "Materials", "expenses": "Expenses", "profit": "Profit", "margin": "Margin", "drafts": "Drafts not posted yet",
        "bills": "Progress bills & invoices", "bill_new": "New progress bill", "amount": "Amount", "description": "Description (e.g. bill 2 - concrete works)", "service": "Invoice line",
        "default_service": "Progress bill (default)", "bill_btn": "Make the bill", "billed_ok": "Draft {number} made; post it from the invoice screen.",
        "link_invoice": "Link an existing invoice of this customer", "link": "Link", "linked": "Linked.", "unlink": "Unlink", "unlinked": "Unlinked.",
        "invoice": "Invoice", "date": "Date", "total": "Total", "doc_status": "Status",
        "materials_title": "Materials sent to site", "issue_btn": "Issue from stock", "item": "Item", "location": "Location", "qty": "Qty", "unit_cost": "Unit cost",
        "issued": "Materials issued ({ref}).", "issue_note": "Issuing takes the stock out at the item's average cost, recorded as an adjustment out linked to the project.",
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
               "can_issue": user_has_permission(request.user, ISSUE), "statuses": _statuses(lang)}
    context.update(extra)
    return context


def _form_data(request):
    return {"name": request.POST.get("name", ""), "customer": _pick(Customer, request.POST.get("customer"), active=True), "site": request.POST.get("site", ""),
            "contract_value": request.POST.get("contract_value", ""), "start_date": _date(request.POST.get("start_date")),
            "end_date": _date(request.POST.get("end_date")), "notes": request.POST.get("notes", ""), "status": request.POST.get("status")}


@require_permission(VIEW)
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
    rows = [{"project": project, "status_label": choice_label(project, "status", lang), "figures": services.summary(project)} for project in rows[:200]]
    return render(request, "projects/list.html", _base(request, rows=rows, status=status, form=form, error=error,
                                                        customers=Customer.objects.filter(active=True).order_by("name")))


@require_permission(VIEW)
def project_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    project = get_object_or_404(Project.objects.select_related("customer"), pk=pk)
    error = ""
    if request.method == "POST":
        if not user_has_permission(request.user, MANAGE):
            raise PermissionDenied("Projects need sales.create_sales_invoice.")
        action = request.POST.get("action", "")
        try:
            if action == "edit":
                services.save_project(_form_data(request), request.user, project, lang)
                messages.success(request, words["saved"])
            elif action == "bill":
                invoice = services.bill_progress(project, request.user, amount=request.POST.get("amount"), description=request.POST.get("description", ""),
                                                 item=_pick(Item, request.POST.get("item"), active=True, is_stock_tracked=False), lang=lang)
                messages.success(request, words["billed_ok"].format(number=invoice.invoice_number))
            elif action == "link_invoice":
                invoice = _pick(SalesInvoice, request.POST.get("invoice"))
                if invoice is None:
                    raise ValidationError(services.MESSAGES[lang]["not_posted"])
                services.link_invoice(project, invoice, request.user, lang)
                messages.success(request, words["linked"])
            elif action == "link_expense":
                expense = _pick(Expense, request.POST.get("expense"))
                if expense is None:
                    raise ValidationError(services.MESSAGES[lang]["not_posted"])
                services.link_expense(project, expense, request.user, lang)
                messages.success(request, words["linked"])
            elif action in ("unlink_invoice", "unlink_expense"):
                model = ProjectInvoice if action == "unlink_invoice" else ProjectExpense
                link = _pick(model, request.POST.get("link"), project=project)
                if link:
                    services.unlink(project, link, request.user)
                    messages.success(request, words["unlinked"])
            elif action == "issue":
                if not user_has_permission(request.user, ISSUE):
                    raise PermissionDenied("Issuing materials needs inventory.adjust_stock.")
                operation = services.issue_materials(project, request.user, item=_pick(Item, request.POST.get("item"), active=True),
                                                     location=_pick(Location, request.POST.get("location"), active=True), quantity=request.POST.get("quantity", ""), lang=lang)
                messages.success(request, words["issued"].format(ref=operation.reference_number))
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
        issues=project.issues.select_related("operation", "operation__item", "operation__source_location"),
        expense_links=project.expenses.select_related("expense", "expense__category", "expense__cashbox_operation"),
        free_invoices=SalesInvoice.objects.filter(customer=project.customer).exclude(status="cancelled").exclude(pk__in=linked_ids).order_by("-invoice_date")[:50],
        free_expenses=Expense.objects.filter(expense_date__gte=since, cashbox_operation__status="posted", project_link__isnull=True).order_by("-expense_date")[:50],
        services_list=Item.objects.filter(active=True, is_stock_tracked=False).exclude(item_code=services.BILLING_ITEM_CODE).order_by("item_name"),
        stock_items=Item.objects.filter(active=True, is_stock_tracked=True).order_by("item_name"), locations=Location.objects.filter(active=True),
        customers=Customer.objects.filter(active=True).order_by("name"),
        form={"name": project.name, "customer": str(project.customer_id), "site": project.site, "contract_value": project.contract_value,
              "start_date": project.start_date.isoformat() if project.start_date else "", "end_date": project.end_date.isoformat() if project.end_date else "",
              "notes": project.notes, "status": project.status},
        is_open=project.status in services.OPEN,
    )
    return render(request, "projects/detail.html", context)
