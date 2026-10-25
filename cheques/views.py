"""T4 screen: the cheques register — received and issued, what falls due, and each cheque's next step."""

from django.contrib import messages as flash
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer, Supplier
from permissions.decorators import require_any_permission
from config.paging import page_of

from . import services
from .models import ChequeDirection, ChequeStatus

WORDS = {
    "ar": {
        "title": "الشيكات", "intro": "الشيكات اللي استلمتها من العملاء واللي اديتها للموردين. شيك العميل بيقلل رصيده يوم ما تستلمه ويفضل «في الإيد» لحد ما تودعه في البنك؛ لو رجع مرتد بيرجع على حسابه تاني. شيكك للمورد بيسدد حسابه ويفضل عليك لحد ما يتصرف من البنك.",
        "tab_in": "شيكات واردة", "tab_out": "شيكات صادرة", "tab_due": "تستحق قريب", "in_hand": "في الإيد", "issued": "صادرة ولسه ماتصرفتش",
        "receive": "استلام شيك من عميل", "issue": "إصدار شيك لمورد", "customer": "العميل", "supplier": "المورد", "number": "رقم الشيك", "bank_name": "بنك الشيك",
        "due_date": "تاريخ الاستحقاق", "amount": "المبلغ", "received_on": "تاريخ الاستلام", "issued_on": "تاريخ الإصدار", "drawn_on": "مسحوب على حسابنا في",
        "notes": "ملاحظات", "save": "حفظ", "party": "الطرف", "state": "الحالة", "date": "التاريخ", "bank": "البنك",
        "deposit": "إيداع", "clear_in": "اتحصّل", "clear_out": "اتصرف", "bounce": "مرتد", "cancel": "إلغاء", "reason": "السبب",
        "saved": "اتحفظ.", "none": "مفيش شيكات هنا.", "no_banks": "مفيش حساب بنكي؛ اعمل واحد من «البنوك» الأول.", "due_note": "تستحق خلال {n} أيام أو فات ميعادها.",
        "all": "الكل",
    },
    "en": {
        "title": "Cheques", "intro": "Cheques received from customers and given to suppliers. A customer's cheque lowers their balance the day you receive it and stays “in hand” until you deposit it; if it bounces, it is on their account again. Your cheque to a supplier pays them and stays owed until the bank pays it.",
        "tab_in": "Received", "tab_out": "Issued", "tab_due": "Due soon", "in_hand": "In hand", "issued": "Issued, not yet paid",
        "receive": "Receive a cheque from a customer", "issue": "Issue a cheque to a supplier", "customer": "Customer", "supplier": "Supplier", "number": "Cheque number",
        "bank_name": "Cheque's bank", "due_date": "Due date", "amount": "Amount", "received_on": "Received on", "issued_on": "Issued on", "drawn_on": "Drawn on our account at",
        "notes": "Notes", "save": "Save", "party": "Party", "state": "State", "date": "Date", "bank": "Bank",
        "deposit": "Deposit", "clear_in": "Collected", "clear_out": "Paid", "bounce": "Bounced", "cancel": "Cancel", "reason": "Reason",
        "saved": "Saved.", "none": "No cheques here.", "no_banks": "No bank account yet; make one from “Banks” first.", "due_note": "Due within {n} days or overdue.",
        "all": "All",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _error(exc, lang):
    from settings_core.ui_messages import translate

    return " ".join(translate(text, lang) for text in getattr(exc, "messages", [str(exc)]))


@require_any_permission("cashboxes.view_finance", services.RECEIVE, services.ISSUE)
def register(request):
    lang = _lang(request)
    words = WORDS[lang]
    if not services.can_view(request.user):
        raise PermissionDenied
    tab = request.GET.get("tab") if request.GET.get("tab") in ("in", "out", "due") else "in"
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        tab = request.POST.get("tab") if request.POST.get("tab") in ("in", "out", "due") else tab
        try:
            if action == "receive":
                customer = Customer.objects.filter(pk=request.POST.get("customer") or 0, active=True).first()
                services.receive(customer, request.POST, request.user, lang)
            elif action == "issue":
                supplier = Supplier.objects.filter(pk=request.POST.get("supplier") or 0, active=True).first()
                bank = services.banks().filter(pk=request.POST.get("bank") or 0).first()
                services.issue(supplier, bank, request.POST, request.user, lang)
            elif action in ("deposit", "clear", "bounce", "cancel"):
                cheque = get_object_or_404(services.scoped(), pk=request.POST.get("cheque") or 0)
                if action == "deposit":
                    services.deposit(cheque, services.banks().filter(pk=request.POST.get("bank") or 0).first(), request.user, lang, request.POST.get("on"))
                elif action == "clear":
                    services.clear(cheque, request.user, lang, request.POST.get("on"))
                elif action == "bounce":
                    services.bounce(cheque, request.POST.get("reason"), request.user, lang, request.POST.get("on"))
                else:
                    services.cancel(cheque, request.POST.get("reason"), request.user, lang)
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        except PermissionDenied:
            raise
        else:
            flash.success(request, words["saved"])
            return redirect(f"{reverse('cheques:register')}?lang={lang}&tab={tab}")
    rows = services.scoped().select_related("customer", "supplier", "bank")
    if tab == "due":
        rows = services.due_soon()
    else:
        rows = rows.filter(direction=ChequeDirection.IN if tab == "in" else ChequeDirection.OUT)
        status = request.GET.get("status")
        if status in ChequeStatus.values:
            rows = rows.filter(status=status)
    page = page_of(request, list(rows.order_by("due_date", "pk")))
    return render(request, "cheques/register.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "tab": tab, "page": page, "error": error,
        "today": timezone.localdate(), "totals": services.totals(), "due_count": services.due_soon().count(), "banks": list(services.banks()),
        "customers": Customer.objects.filter(active=True).order_by("name")[:500] if services.can_receive(request.user) else [],
        "suppliers": Supplier.objects.filter(active=True).order_by("name")[:500] if services.can_issue(request.user) else [],
        "can_receive": services.can_receive(request.user), "can_issue": services.can_issue(request.user), "can_move": services.can_move(request.user),
        "due_days": services.DUE_SOON_DAYS, "statuses": ChequeStatus, "status": request.GET.get("status", ""),
        "due_note": words["due_note"].format(n=services.DUE_SOON_DAYS)})
