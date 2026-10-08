"""SHIFT-001 screens: my shift (open / live summary / close), shift history, one shift's report."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .models import Shift
from .services import close_shift, open_shift, open_shift_for, post_difference, summary
from entities import scope as entity_scope


WORDS = {
    "ar": {
        "page_title": "الوردية", "title": "وردية الكاشير",
        "intro": "افتح الوردية بعدّ الفكّة اللي في الدرج، وفي الآخر اعدّ الدرج تاني. حسبة بتحسب المفروض يكون كام من حركاتك إنت على الخزنة وتطلّع الفرق.",
        "open": "فتح وردية", "cashbox": "الخزنة", "float": "الفكّة في الدرج دلوقتي", "opened": "اتفتحت الوردية.", "close": "قفل الوردية", "counted": "الموجود في الدرج فعلاً",
        "notes": "ملاحظات", "closed": "اتقفلت الوردية.", "since": "مفتوحة من", "expected": "المفروض يكون في الدرج", "difference": "الفرق", "shortage": "عجز", "overage": "زيادة", "exact": "مظبوط",
        "groups": {"sales": "مبيعات نقدي", "refunds": "مرتجعات نقدي", "collections": "تحصيلات", "supplier_payments": "مدفوع لموردين", "purchases": "مشتريات نقدي",
                   "purchase_refunds": "مرتجع مشتريات", "cash_in": "إيداع", "cash_out": "صرف", "other": "أخرى"},
        "invoices": "عدد الفواتير", "history": "الورديات السابقة", "cashier": "الكاشير", "opened_at": "الفتح", "closed_at": "القفل", "report": "تقرير الوردية",
        "post": "سجّل الفرق في الخزنة", "posted": "اتسجل الفرق في الخزنة.", "posted_ref": "اتسجل بالمرجع", "empty": "مفيش ورديات.", "bad": "المبلغ مش صحيح.",
        "print": "طباعة", "back": "الوردية", "float_label": "فكّة البداية", "prev": "السابق", "next": "التالي", "live": "لحد دلوقتي",
    },
    "en": {
        "page_title": "Shift", "title": "Cashier shift",
        "intro": "Open the shift by counting the float in the drawer, and count the drawer again at the end. Hesba works out what should be there from your own cash movements and shows the difference.",
        "open": "Open shift", "cashbox": "Cashbox", "float": "Float in the drawer now", "opened": "Shift opened.", "close": "Close shift", "counted": "Cash actually in the drawer",
        "notes": "Notes", "closed": "Shift closed.", "since": "Open since", "expected": "Should be in the drawer", "difference": "Difference", "shortage": "Short", "overage": "Over", "exact": "Exact",
        "groups": {"sales": "Cash sales", "refunds": "Cash refunds", "collections": "Collections", "supplier_payments": "Paid to suppliers", "purchases": "Cash purchases",
                   "purchase_refunds": "Purchase refunds", "cash_in": "Cash in", "cash_out": "Cash out", "other": "Other"},
        "invoices": "Invoices", "history": "Past shifts", "cashier": "Cashier", "opened_at": "Opened", "closed_at": "Closed", "report": "Shift report",
        "post": "Post the difference to the cashbox", "posted": "Difference posted to the cashbox.", "posted_ref": "Posted as", "empty": "No shifts.", "bad": "Invalid amount.",
        "print": "Print", "back": "Shift", "float_label": "Opening float", "prev": "Previous", "next": "Next", "live": "so far",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _amount(raw, words):
    try:
        return Decimal((raw or "").strip().replace(",", ""))
    except InvalidOperation:
        raise ValidationError(words["bad"])


def _rows(data, words):
    return [(words["groups"].get(key, key), Decimal(value)) for key, value in data.get("groups", {}).items()]


@require_permission("sales.create_sales_invoice")
def my_shift(request):
    lang = _lang(request)
    words = WORDS[lang]
    current = open_shift_for(request.user)
    if request.method == "POST":
        try:
            if request.POST.get("action") == "open":
                cashbox = entity_scope.cashboxes(Cashbox.objects).filter(active=True, pk=request.POST.get("cashbox") or 0).first()
                if cashbox is None:
                    raise ValidationError(words["bad"])
                open_shift(request.user, cashbox, _amount(request.POST.get("opening_float"), words), lang)
                messages.success(request, words["opened"])
            elif current is not None:
                shift = close_shift(current, _amount(request.POST.get("counted_cash"), words), request.user, request.POST.get("notes", ""), lang)
                messages.success(request, words["closed"])
                return redirect(f"{reverse('shifts:detail', args=[shift.pk])}?lang={lang}")
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(f"{reverse('shifts:mine')}?lang={lang}")
    live = summary(current, timezone.now()) if current else None
    can_all = user_has_permission(request.user, "cashboxes.view_finance")
    history = Shift.objects.select_related("cashier", "cashbox").filter(status="closed")
    if not can_all:
        history = history.filter(cashier=request.user)
    page = Paginator(history, 25).get_page(request.GET.get("page"))
    cashboxes = entity_scope.cashboxes(Cashbox.objects).filter(active=True)
    return render(request, "shifts/mine.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["page_title"], "section": "pos",
        "current": current, "live": live, "live_rows": _rows(live, words) if live else [], "live_expected": Decimal(live["expected"]) if live else None,
        "page": page, "can_all": can_all, "cashboxes": cashboxes, "default_cashbox": cashboxes.filter(is_default=True).first() or cashboxes.first(),
    })


@require_permission("sales.create_sales_invoice")
def shift_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    shift = get_object_or_404(Shift.objects.select_related("cashier", "cashbox", "difference_operation"), pk=pk)
    if shift.cashier_id != request.user.pk and not user_has_permission(request.user, "cashboxes.view_finance"):
        raise PermissionDenied("Other cashiers' shifts need cashboxes.view_finance.")
    if request.method == "POST":
        if not user_has_permission(request.user, "cashboxes.move_cash"):
            raise PermissionDenied("Posting a shift difference needs cashboxes.move_cash.")
        try:
            post_difference(shift, request.user, lang)
            messages.success(request, words["posted"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(f"{reverse('shifts:detail', args=[shift.pk])}?lang={lang}")
    return render(request, "shifts/detail.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["report"], "section": "pos", "shift": shift,
        "rows": _rows(shift.summary, words), "can_post": user_has_permission(request.user, "cashboxes.move_cash"),
    })
