"""Contracting C4 (HG-123) screens: «خطابات الضمان» (the list with its alerts and filters), the form, and one
guarantee's page with its history and actions. Nothing here moves money."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from master_data.models import Customer
from permissions.decorators import require_any_permission
from settings_core.ui_messages import translate

from . import services
from .models import EventKind, Guarantee, GuaranteeKind, GuaranteeStatus

WORDS = {
    "ar": {
        "title": "خطابات الضمان", "intro": "كل خطاب ضمان بنكي بمبلغه وتاريخ انتهائه، والتنبيه قبل ما ينتهي. الخطاب مبيحرّكش فلوس: الغطاء النقدي والعمولة للعلم بس.",
        "new": "خطاب ضمان جديد", "edit": "تعديل الخطاب", "back": "خطابات الضمان", "open": "فتح",
        "kind": "النوع", "number": "رقم الخطاب", "bank": "البنك", "beneficiary": "المستفيد", "project": "المشروع", "no_project": "من غير مشروع",
        "amount": "المبلغ", "currency": "العملة", "issued_on": "تاريخ الإصدار", "expires_on": "ينتهي يوم", "margin": "الغطاء النقدي %",
        "margin_amount": "الغطاء النقدي (للعلم)", "commission": "عمولة البنك (للعلم)", "status": "الحالة", "notes": "ملاحظات", "save": "حفظ",
        "all": "الكل", "show": "عرض", "filter_alerts": "التنبيهات", "show_all": "كل الخطابات", "show_active": "السارية بس",
        "show_expiring": "قرّبت تنتهي", "show_expired": "انتهت وهي سارية", "all_banks": "كل البنوك", "all_projects": "كل المشاريع",
        "none": "مفيش خطابات ضمان هنا.", "expiring_n": "خطابات هتنتهي خلال {days} يوم", "expired_n": "خطابات انتهت وهي لسه سارية",
        "active_total": "إجمالي السارية", "days_left": "باقي {days} يوم", "days_over": "عدّى {days} يوم",
        "alert_days": "التنبيه قبل الانتهاء بـ", "days": "يوم", "save_days": "احفظ", "days_saved": "اتحفظ: التنبيه قبل الانتهاء بـ {days} يوم.",
        "no_banks": "مفيش حساب بنكي في الشركة دي لسه. ضيف حساب بنكي من شاشة الخزن الأول (نوعه «حساب بنكي»).",
        "created": "اتسجل خطاب الضمان {number}.", "saved": "اتحفظ الخطاب.", "done": "تم.",
        "history": "حركات الخطاب", "issued_row": "الإصدار", "date": "التاريخ", "event": "الحركة", "from": "من", "to": "لـ", "note": "ملاحظة", "by": "بواسطة",
        "actions": "الإجراءات", "extend": "تمديد", "new_expiry": "تاريخ الانتهاء الجديد", "reduce": "تخفيض", "new_amount": "المبلغ الجديد",
        "release": "إفراج", "liquidate": "تسييل", "cancel": "إلغاء الخطاب", "event_date": "تاريخ الحركة", "reason": "السبب",
        "release_hint": "البنك رجّع الخطاب أو المستفيد تنازل عنه. الحالة بتبقى «مُفرج عنه»، ومفيش فلوس بتتحرك.",
        "liquidate_hint": "المستفيد سيّل الخطاب. هنا بتتسجل الحركة بس؛ الفلوس اللي البنك صرفها من الحساب سجّلها صرف عادي من حساب البنك.",
        "cancel_hint": "لو الخطاب اتسجل غلط أو مااتصدرش أصلاً. لازم تكتب السبب.",
        "advance_title": "الدفعة المقدمة على المشروع", "advance_received": "اتحصّل دفعة مقدمة", "advance_left": "الباقي منها (لسه ماتخصمش)",
        "suggest_reduce": "الباقي من الدفعة المقدمة أقل من مبلغ الخطاب. ممكن تخفّض الخطاب لـ {amount}؛ التخفيض مش بيحصل لوحده.",
        "suggest_release": "الدفعة المقدمة اتخصمت كلها من المستخلصات. ممكن تفرج عن الخطاب؛ الإفراج مش بيحصل لوحده.",
        "retention_note": "خطاب بدل المحتجزات بيتسجل بس. الإفراج عن المحتجزات النقدية على المشروع إجراء عادي من شاشة دفعات المشروع، ومش بيحصل لوحده.",
        "memo_note": "الغطاء النقدي والعمولة أرقام للعلم: العمولة سجّلها مصروف عادي (مصاريف بنكية).",
        "issued_amount": "مبلغ الإصدار", "first_expiry": "أول تاريخ انتهاء", "card_title": "خطابات الضمان على المشروع", "add_for_project": "سجّل خطاب للمشروع ده",
    },
    "en": {
        "title": "Letters of guarantee", "intro": "Every bank letter of guarantee with its amount and expiry, and a warning before it runs out. A letter moves no money: the cash margin and the commission are memo figures.",
        "new": "New letter of guarantee", "edit": "Edit the letter", "back": "Letters of guarantee", "open": "Open",
        "kind": "Kind", "number": "Letter number", "bank": "Bank", "beneficiary": "Beneficiary", "project": "Project", "no_project": "No project",
        "amount": "Amount", "currency": "Currency", "issued_on": "Issued on", "expires_on": "Expires on", "margin": "Cash margin %",
        "margin_amount": "Cash margin (memo)", "commission": "Bank commission (memo)", "status": "Status", "notes": "Notes", "save": "Save",
        "all": "All", "show": "Show", "filter_alerts": "Alerts", "show_all": "All letters", "show_active": "Active only",
        "show_expiring": "Expiring soon", "show_expired": "Expired, still active", "all_banks": "All banks", "all_projects": "All projects",
        "none": "No letters of guarantee here.", "expiring_n": "Letters expiring within {days} days", "expired_n": "Letters expired but still active",
        "active_total": "Total active", "days_left": "{days} days left", "days_over": "{days} days past",
        "alert_days": "Warn before expiry by", "days": "days", "save_days": "Save", "days_saved": "Saved: warn {days} days before expiry.",
        "no_banks": "This entity has no bank account yet. Add one on the cashboxes screen first (kind «Bank account»).",
        "created": "Letter of guarantee {number} recorded.", "saved": "Letter saved.", "done": "Done.",
        "history": "The letter's history", "issued_row": "Issued", "date": "Date", "event": "Event", "from": "From", "to": "To", "note": "Note", "by": "By",
        "actions": "Actions", "extend": "Extend", "new_expiry": "New expiry", "reduce": "Reduce", "new_amount": "New amount",
        "release": "Release", "liquidate": "Liquidate", "cancel": "Cancel the letter", "event_date": "Event date", "reason": "Reason",
        "release_hint": "The bank took the letter back or the beneficiary waived it. Its status becomes «Released»; no money moves.",
        "liquidate_hint": "The beneficiary called the letter. Only the event is recorded here; record what the bank paid out of the account as an ordinary bank cash-out.",
        "cancel_hint": "If the letter was recorded by mistake or never issued. A reason is required.",
        "advance_title": "The project's advance payment", "advance_received": "Advance received", "advance_left": "Advance left (not yet recovered)",
        "suggest_reduce": "Less of the advance is left than the letter covers. You may reduce the letter to {amount}; it is not reduced by itself.",
        "suggest_release": "The advance has been fully recovered on the certificates. You may release the letter; it is not released by itself.",
        "retention_note": "A retention guarantee is recorded only. Releasing the cash retention held on the project is the ordinary action on the project's payments page, never automatic.",
        "memo_note": "The cash margin and the commission are memo figures: record the commission as an ordinary expense (bank charges).",
        "issued_amount": "Issued amount", "first_expiry": "First expiry", "card_title": "Letters of guarantee on the project", "add_for_project": "Record a letter for this project",
    },
}

SHOW = ("active", "expiring", "expired", "all")


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _pick(queryset, raw):
    raw = str(raw or "")
    return queryset.filter(pk=raw).first() if raw.isdigit() else None


def _context(request, **extra):
    lang = _lang(request)
    words = WORDS[lang]
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"],
            "labels": services.LABELS[lang], **extra}


def _error(request, exc, lang):
    if isinstance(exc, PermissionDenied):
        messages.error(request, services.MESSAGES[lang]["denied"])
        return
    messages.error(request, " ".join(translate(message, lang) for message in getattr(exc, "messages", [str(exc)])))


def _guarantee_or_404(pk):
    guarantee = services.scoped(Guarantee.objects.select_related("bank", "beneficiary", "project", "created_by")).filter(pk=pk).first()
    if guarantee is None:
        raise Http404
    return guarantee


def _projects():
    from projects.models import Project

    return Project.objects.order_by("-pk")


def _row(guarantee, lang, on, days):
    state = services.alert(guarantee, on, days)
    left = (guarantee.expires_on - on).days
    words = WORDS[lang]
    return {"g": guarantee, "alert": state, "kind": services.label("kinds", guarantee.kind, lang),
            "status": services.label("statuses", guarantee.status, lang), "alert_label": services.label("alerts", state, lang) if state else "",
            "left": words["days_left"].format(days=left) if left >= 0 else words["days_over"].format(days=-left)}


@require_any_permission(*services.MANAGE)
def guarantee_list(request):
    lang = _lang(request)
    words = WORDS[lang]
    if request.method == "POST" and request.POST.get("action") == "alert_days":
        try:
            days = services.save_alert_days(request.POST.get("days"), request.user, lang)
        except (ValidationError, PermissionDenied) as exc:
            _error(request, exc, lang)
        else:
            messages.success(request, words["days_saved"].format(days=days))
        return redirect(f"{reverse('guarantees:list')}?lang={lang}")
    on, days = services.today(), services.alert_days()
    base = services.scoped(Guarantee.objects.select_related("bank", "beneficiary", "project"))
    show = request.GET.get("show", "active")
    show = show if show in SHOW else "active"
    rows = base
    if show == "active":
        rows = rows.filter(status=GuaranteeStatus.ACTIVE)
    elif show == "expiring":
        rows = services.expiring(rows, on, days)
    elif show == "expired":
        rows = services.expired(rows, on)
    bank = _pick(services.banks(), request.GET.get("bank"))
    if bank is not None:
        rows = rows.filter(bank=bank)
    project = _pick(_projects(), request.GET.get("project"))
    if project is not None:
        rows = rows.filter(project=project)
    active = base.filter(status=GuaranteeStatus.ACTIVE)
    return render(request, "guarantees/list.html", _context(
        request, rows=[_row(g, lang, on, days) for g in rows[:300]], show=show, bank=bank, project=project, days=days,
        expiring_count=services.expiring(base, on, days).count(), expired_count=services.expired(base, on).count(),
        active_count=active.count(), banks=services.banks(), projects=_projects()[:300],
        shows=[(key, words[f"show_{key}"]) for key in SHOW],
        expiring_label=words["expiring_n"].format(days=days), can_set_days=services.can_set_days(request.user)))


def _form_data(request):
    return {"kind": request.POST.get("kind"), "number": request.POST.get("number"), "bank": _pick(services.banks(), request.POST.get("bank")),
            "beneficiary": _pick(Customer.objects.filter(active=True), request.POST.get("beneficiary")),
            "project": _pick(_projects(), request.POST.get("project")), "amount": request.POST.get("amount"),
            "issued_on": request.POST.get("issued_on"), "expires_on": request.POST.get("expires_on"),
            "margin_percent": request.POST.get("margin_percent"), "commission": request.POST.get("commission"), "notes": request.POST.get("notes")}


def _bad_project(request, data):
    raw = str(request.POST.get("project") or "")
    return bool(raw) and data["project"] is None


@require_any_permission(*services.MANAGE)
def guarantee_form(request, pk=None):
    lang = _lang(request)
    words = WORDS[lang]
    guarantee = _guarantee_or_404(pk) if pk is not None else None
    if guarantee is not None and guarantee.status != GuaranteeStatus.ACTIVE:
        return redirect(f"{reverse('guarantees:detail', args=[guarantee.pk])}?lang={lang}")  # a closed letter is history
    error = ""
    if request.method == "POST":
        data = _form_data(request)
        form = request.POST
        try:
            if _bad_project(request, data):
                raise ValidationError(services.MESSAGES[lang]["project"])
            saved = services.save_guarantee(data, request.user, guarantee, lang)
        except (ValidationError, PermissionDenied) as exc:
            error = services.MESSAGES[lang]["denied"] if isinstance(exc, PermissionDenied) else " ".join(translate(m, lang) for m in exc.messages)
        else:
            messages.success(request, words["saved"] if guarantee else words["created"].format(number=saved.number))
            return redirect(f"{reverse('guarantees:detail', args=[saved.pk])}?lang={lang}")
    elif guarantee is not None:
        form = {"kind": guarantee.kind, "number": guarantee.number, "bank": str(guarantee.bank_id), "beneficiary": str(guarantee.beneficiary_id),
                "project": str(guarantee.project_id or ""), "amount": f"{guarantee.amount:.2f}", "issued_on": guarantee.issued_on.isoformat(),
                "expires_on": guarantee.expires_on.isoformat(), "margin_percent": "" if guarantee.margin_percent is None else f"{guarantee.margin_percent:.2f}",
                "commission": "" if guarantee.commission is None else f"{guarantee.commission:.2f}", "notes": guarantee.notes}
    else:
        project = _pick(_projects(), request.GET.get("project"))
        form = {"kind": GuaranteeKind.PERFORMANCE, "issued_on": services.today().isoformat(), "project": str(project.pk) if project else "",
                "beneficiary": str(project.customer_id) if project else ""}
    locked = guarantee is not None and guarantee.events.exists()
    return render(request, "guarantees/form.html", _context(
        request, guarantee=guarantee, form=form, error=error, locked=locked, banks=services.banks(),
        customers=Customer.objects.filter(active=True).order_by("name")[:1000], projects=_projects()[:300],
        kinds=[(value, services.label("kinds", value, lang)) for value in GuaranteeKind.values],
        page_title=words["edit"] if guarantee else words["new"]))


ACTIONS = {
    "extend": lambda g, r, lang: services.extend(g, r.user, r.POST.get("new_expiry"), r.POST.get("event_date"), r.POST.get("note", ""), lang),
    "reduce": lambda g, r, lang: services.reduce(g, r.user, r.POST.get("new_amount"), r.POST.get("event_date"), r.POST.get("note", ""), lang),
    "release": lambda g, r, lang: services.release(g, r.user, r.POST.get("event_date"), r.POST.get("note", ""), lang),
    "liquidate": lambda g, r, lang: services.liquidate(g, r.user, r.POST.get("event_date"), r.POST.get("note", ""), lang),
    "cancel": lambda g, r, lang: services.cancel(g, r.user, r.POST.get("event_date"), r.POST.get("note", ""), lang),
}


@require_any_permission(*services.MANAGE)
def guarantee_detail(request, pk):
    lang = _lang(request)
    words = WORDS[lang]
    guarantee = _guarantee_or_404(pk)
    if request.method == "POST":
        action = ACTIONS.get(request.POST.get("action", ""))
        if action is not None:
            try:
                action(guarantee, request, lang)
            except (ValidationError, PermissionDenied) as exc:
                _error(request, exc, lang)
            else:
                messages.success(request, words["done"])
        return redirect(f"{reverse('guarantees:detail', args=[guarantee.pk])}?lang={lang}")
    on, days = services.today(), services.alert_days()
    events = [{"e": event, "label": services.label("events", event.kind, lang)} for event in guarantee.events.select_related("user")]
    suggestion = services.advance_suggestion(guarantee)
    is_active = guarantee.status == GuaranteeStatus.ACTIVE
    return render(request, "guarantees/detail.html", _context(
        request, g=guarantee, row=_row(guarantee, lang, on, days), events=events, suggestion=suggestion, is_active=is_active,
        margin_amount=services.margin_amount(guarantee), issued_amount=services.issued_amount(guarantee), first_expiry=services.first_expiry(guarantee),
        suggest_reduce=words["suggest_reduce"].format(amount=f"{suggestion['amount']:,.2f}") if suggestion and suggestion["action"] == "reduce" else "",
        today=on, is_retention=guarantee.kind == GuaranteeKind.RETENTION, extend_kind=EventKind.EXTEND,
        page_title=f"{words['title']} · {guarantee.number}"))
