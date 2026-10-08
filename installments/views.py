"""INSTAL-001 screens: plans list, a new plan from an invoice, and one plan's schedule."""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from printing.company import company_details
from reports.aging import whatsapp_number
from sales.models import SalesInvoice

from .models import InstalmentPlan, PlanStatus
from .services import add_months, cancel_plan, collect, create_plan, is_active, schedule, split
from entities import scope as entity_scope


WORDS = {
    "ar": {
        "page_title": "التقسيط", "title": "البيع بالتقسيط",
        "intro": "خطة أقساط على الباقي من فاتورة بيع مرحّلة. كل تحصيل بيتسجل تحصيل عميل عادي في الخزنة وحساب العميل، والأقدم بيتسدد الأول.",
        "customer": "العميل", "invoice": "الفاتورة", "financed": "المقسّط", "paid": "المحصّل", "remaining": "الباقي", "next_due": "القسط الجاي", "overdue": "متأخر",
        "open": "فتح", "empty": "مفيش خطط تقسيط.", "only_overdue": "المتأخر بس", "filter": "عرض", "all": "كل الخطط النشطة",
        "new": "خطة تقسيط جديدة", "count": "عدد الأقساط", "first_due": "تاريخ أول قسط", "notes": "ملاحظات", "preview": "كل قسط تقريبًا", "create": "إنشاء الخطة",
        "created": "اتعملت خطة التقسيط.", "number": "#", "due_date": "الاستحقاق", "amount": "القسط", "settled": "المسدد", "state": "الحالة",
        "states": {"paid": "مسدد", "overdue": "متأخر", "due_today": "مستحق النهارده", "upcoming": "لسه"}, "partial": "جزئي",
        "collect": "تحصيل", "collect_amount": "المبلغ", "cashbox": "الخزنة", "date": "التاريخ", "collected": "اتسجل التحصيل {number}.", "bad": "بيانات غير صحيحة.",
        "remind": "تذكير واتساب", "cancel": "إلغاء الخطة", "cancelled_msg": "اتلغت الخطة.", "plan_cancelled": "الخطة ملغية.", "invoice_cancelled": "الفاتورة اتلغت؛ الخطة متوقفة.",
        "credited": "مرتجعات خصمت من الباقي", "done": "الخطة اتسددت بالكامل.", "back": "خطط التقسيط", "view_only": "تقدر تشوف بس.", "prev": "السابق", "next": "التالي",
        "reminder": "أهلاً {name}، ده تذكير ودّي من {company} بقسط فاتورة {invoice}: {amount} مستحق يوم {date}. شكرًا لحضرتك.",
    },
    "en": {
        "page_title": "Instalments", "title": "Instalment sales",
        "intro": "An instalment plan for what is left on a posted sales invoice. Every collection is an ordinary customer payment in the cashbox and the customer's account, oldest instalment first.",
        "customer": "Customer", "invoice": "Invoice", "financed": "Financed", "paid": "Collected", "remaining": "Left", "next_due": "Next instalment", "overdue": "Overdue",
        "open": "Open", "empty": "No instalment plans.", "only_overdue": "Overdue only", "filter": "Show", "all": "All active plans",
        "new": "New instalment plan", "count": "Number of instalments", "first_due": "First due date", "notes": "Notes", "preview": "Each instalment about", "create": "Create plan",
        "created": "Instalment plan created.", "number": "#", "due_date": "Due", "amount": "Instalment", "settled": "Paid", "state": "State",
        "states": {"paid": "Paid", "overdue": "Overdue", "due_today": "Due today", "upcoming": "Upcoming"}, "partial": "partly",
        "collect": "Collect", "collect_amount": "Amount", "cashbox": "Cashbox", "date": "Date", "collected": "Collection {number} recorded.", "bad": "Invalid input.",
        "remind": "WhatsApp reminder", "cancel": "Cancel plan", "cancelled_msg": "Plan cancelled.", "plan_cancelled": "This plan is cancelled.", "invoice_cancelled": "The invoice was cancelled; the plan is stopped.",
        "credited": "Returns taken off the balance", "done": "The plan is fully paid.", "back": "Instalment plans", "view_only": "You can view only.", "prev": "Previous", "next": "Next",
        "reminder": "Hello {name}, a friendly reminder from {company} about the instalment on invoice {invoice}: {amount} due on {date}. Thank you.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"], "section": "sales",
               "can_collect": user_has_permission(request.user, "sales.receive_customer_payment"),
               "can_plan": user_has_permission(request.user, "sales.create_sales_invoice")}
    context.update(extra)
    return context


@require_permission("sales.view_sales_invoices")
def plan_list(request):
    today = timezone.localdate()
    only_overdue = request.GET.get("overdue") == "1"
    rows = []
    for plan in entity_scope.scope(InstalmentPlan.objects, "invoice__" + entity_scope.SALES_INVOICE).filter(status=PlanStatus.ACTIVE, invoice__status="posted").select_related("invoice", "customer").prefetch_related("instalments"):
        info = schedule(plan, today)
        if info["done"] or (only_overdue and not info["overdue"]):
            continue
        rows.append({"plan": plan, "info": info})
    rows.sort(key=lambda row: (-row["info"]["overdue"], row["info"]["next_due"].due_date if row["info"]["next_due"] else date.max))
    page = Paginator(rows, 50).get_page(request.GET.get("page"))
    return render(request, "installments/list.html", _context(request, page=page, only_overdue=only_overdue))


@require_permission("sales.create_sales_invoice")
def plan_create(request, invoice_pk):
    lang = _lang(request)
    words = WORDS[lang]
    invoice = get_object_or_404(entity_scope.scope(SalesInvoice.objects.select_related("customer"), entity_scope.SALES_INVOICE), pk=invoice_pk)
    existing = InstalmentPlan.objects.filter(invoice=invoice).first()
    if existing:
        return redirect(f"{reverse('installments:detail', args=[existing.pk])}?lang={lang}")
    default_first = add_months(max(invoice.invoice_date, timezone.localdate()), 1)
    if request.method == "POST":
        try:
            try:
                count = int(request.POST.get("count") or 0)
                first_due = date.fromisoformat(request.POST.get("first_due_date") or "")
            except ValueError:
                raise ValidationError(words["bad"])
            plan = create_plan(invoice, count, first_due, request.user, lang, request.POST.get("notes", ""))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        else:
            messages.success(request, words["created"])
            return redirect(f"{reverse('installments:detail', args=[plan.pk])}?lang={lang}")
    count = 6
    preview = split(invoice.remaining_due, count)[0] if invoice.remaining_due > 0 else Decimal("0.00")
    return render(request, "installments/new.html", _context(request, invoice=invoice, default_first=default_first, count=count, preview=preview))


@require_permission("sales.view_sales_invoices")
def plan_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    plan = get_object_or_404(entity_scope.scope(InstalmentPlan.objects.select_related("invoice", "customer"), "invoice__" + entity_scope.SALES_INVOICE), pk=pk)
    today = timezone.localdate()
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "cancel":
                if not user_has_permission(request.user, "sales.create_sales_invoice"):
                    raise PermissionDenied("Cancelling a plan needs sales.create_sales_invoice.")
                cancel_plan(plan, request.user, lang)
                messages.success(request, words["cancelled_msg"])
            else:
                if not user_has_permission(request.user, "sales.receive_customer_payment"):
                    raise PermissionDenied("Collecting needs sales.receive_customer_payment.")
                cashbox = entity_scope.cashboxes(Cashbox.objects).filter(active=True, pk=request.POST.get("cashbox") or 0).first()
                try:
                    amount = Decimal((request.POST.get("amount") or "").replace(",", "."))
                    when = date.fromisoformat(request.POST.get("payment_date") or "") if request.POST.get("payment_date") else today
                except (InvalidOperation, ValueError):
                    raise ValidationError(words["bad"])
                if cashbox is None:
                    raise ValidationError(words["bad"])
                payment = collect(plan, amount, cashbox, when, request.user, lang)
                messages.success(request, words["collected"].format(number=payment.payment_number))
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(f"{reverse('installments:detail', args=[plan.pk])}?lang={lang}")
    info = schedule(plan, today)
    for row in info["rows"]:
        row["state_label"] = words["states"][row["state"]]
    reminder = ""
    number = whatsapp_number(plan.customer.whatsapp or plan.customer.phone)
    if number and info["next_due"] and is_active(plan):
        text = words["reminder"].format(name=plan.customer.name, company=company_details()["name"], invoice=plan.invoice.invoice_number,
                                        amount=f"{info['next_open']:,.2f}", date=info["next_due"].due_date.isoformat())
        reminder = f"https://wa.me/{number}?text={quote(text)}"
    cashboxes = entity_scope.cashboxes(Cashbox.objects).filter(active=True)
    default_cashbox = cashboxes.filter(is_default=True).first() or cashboxes.first()
    return render(request, "installments/detail.html", _context(request, plan=plan, info=info, active=is_active(plan), reminder=reminder, cashboxes=cashboxes,
                                                               default_cashbox=default_cashbox, today=today))
