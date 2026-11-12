"""HG-071 (S6) screens: the cards (sell one), a card's page (its entries, write off, reverse), its receipt, and
paying a posted invoice with a card."""

from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.cache import never_cache

from approvals.gate import held
from audit.models import AuditEventType, AuditLog
from cashboxes.models import Cashbox
from entities import scope as entity_scope
from master_data.models import Customer
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import services
from .models import EntryKind, GiftCard, GiftCardEntry

RECEIPT_ONCE = "giftcards.receipt_once"
#: BE-02: the most one card may hold. The app's other typed money fields (service contracts, packages, dental and inpatient
#: prices) stop at 10 million, and nothing sold from this screen comes near it; "99,999,999,999" was a slip of the finger.
FACE_VALUE_LIMIT = Decimal("10000000")

WORDS = {
    "ar": {
        "title": "كروت الهدايا", "intro": "فلوس الكارت بتدخل الخزنة يوم البيع، بس متتحسبش مبيعات: بتفضل التزام لحامل الكارت ({account}) لحد ما يدفع بيه فاتورة أو في الكاشير. الكارت اللي ميعاده عدّى بيفضل شغال لحد ما مدير يشطب باقيه بسبب.",
        "too_big": "قيمة الكارت كبيرة أوي. أقصى قيمة للكارت الواحد {limit}.",
        "sell": "بيع كارت هدية", "value": "قيمة الكارت", "cashbox": "الخزنة اللي استلمت الفلوس", "holder": "اسم حامل الكارت (اختياري)", "phone": "موبايل حامل الكارت (اختياري)",
        "buyer": "العميل اللي اشترى (اختياري)", "nobody": "—", "expires": "ينتهي يوم (اختياري)", "save_sell": "بيع واطبع الإيصال",
        "number": "رقم الكارت", "code": "كود الصرف", "balance": "الرصيد", "status": "الحالة", "sold_on": "اتباع يوم", "none": "مفيش كروت لسه.",
        "open": "فتح", "find": "دوّر بالكود", "find_go": "دوّر", "outstanding": "اللي على الكروت كلها (التزام)", "expired": "ميعاده عدّى",
        "entries": "حركات الكارت", "date": "التاريخ", "kind": "الحركة", "amount": "المبلغ", "invoice": "الفاتورة", "reason": "السبب",
        "breakage": "اشطب الباقي (إيراد متنوع)", "breakage_hint": "بقرار منك بس: الباقي على الكارت هيتحوّل إيراد متنوع (4201) والكارت مش هيدفع تاني.",
        "reverse": "اعكس", "reverse_date": "تاريخ العكس", "cancel_redemption": "الغِ الصرف", "receipt": "إيصال الكارت", "print": "اطبع",
        "sold": "اتباع كارت {number} بمبلغ {value}.", "done": "تم.", "face": "القيمة", "receipt_note": "الكارت ده بيدفع بيه حامله في نفس الفرع. مش فاتورة ضريبية.",
        "back": "رجوع لكروت الهدايا", "nothing": "مفيش حاجة تتعمل.", "reveal": "اطبع بالكود كامل (بيتسجّل)",
    },
    "en": {
        "title": "Gift cards", "intro": "A card's money reaches the cashbox the day it is sold but is not counted as sales: it stays owed to the holder ({account}) until they pay an invoice or at the till with it. An expired card keeps working until a manager writes its rest off with a reason.",
        "too_big": "The card value is too large. The most one card can hold is {limit}.",
        "sell": "Sell a gift card", "value": "Card value", "cashbox": "Cashbox that took the money", "holder": "Holder's name (optional)", "phone": "Holder's mobile (optional)",
        "buyer": "Customer who bought it (optional)", "nobody": "—", "expires": "Expires on (optional)", "save_sell": "Sell and print the receipt",
        "number": "Card number", "code": "Code to spend", "balance": "Balance", "status": "Status", "sold_on": "Sold on", "none": "No cards yet.",
        "open": "Open", "find": "Find by code", "find_go": "Find", "outstanding": "Held on all cards (liability)", "expired": "Expired",
        "entries": "The card's entries", "date": "Date", "kind": "Entry", "amount": "Amount", "invoice": "Invoice", "reason": "Reason",
        "breakage": "Write the rest off (sundry income)", "breakage_hint": "Only by your decision: the rest on the card becomes sundry income (4201) and the card pays no more.",
        "reverse": "Reverse", "reverse_date": "Reversal date", "cancel_redemption": "Cancel the payment", "receipt": "Card receipt", "print": "Print",
        "sold": "Card {number} sold for {value}.", "done": "Done.", "face": "Value", "receipt_note": "The holder pays with this card in the same entity. Not a tax invoice.",
        "back": "Back to gift cards", "nothing": "Nothing to do.", "reveal": "Reprint with the full code (audited)",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _liability_code():
    from ledger.models import Account

    return Account.objects.filter(control="gift_card_liability").values_list("code", flat=True).first() or "—"


def _context(request, **extra):
    lang = _lang(request)
    words = dict(WORDS[lang])
    words["intro"] = words["intro"].format(account=_liability_code())
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "section": "sales", **extra}


def _error(request, exc, lang):
    from settings_core.ui_messages import translate

    if isinstance(exc, PermissionDenied):
        messages.error(request, "You do not have the permission for this." if lang == "en" else "مش معاك صلاحية للعملية دي.")
        return
    messages.error(request, " ".join(translate(message, lang) for message in getattr(exc, "messages", [str(exc)])))


def _check_face_value(value, words):
    """BE-02: refuse an absurd card value before the service runs; zero, negative and letters are the service's own refusals."""

    try:
        number = Decimal(str(value if value not in (None, "") else "0").strip().replace(",", ""))
    except (InvalidOperation, ValueError):
        return
    if number.is_finite() and number > FACE_VALUE_LIMIT:
        raise ValidationError(words["too_big"].format(limit=f"{FACE_VALUE_LIMIT:,.2f}"))


def _cards():
    return entity_scope.scope(GiftCard.objects.select_related("buyer", "entity"), entity_scope.GIFT_CARD)


def _card_or_404(pk):
    card = _cards().filter(pk=pk).first()
    if card is None:
        raise Http404
    return card


def _gate():
    if not services.enabled():
        raise Http404


@require_permission(services.SELL)
def card_list(request):
    _gate()
    lang = _lang(request)
    words = WORDS[lang]
    cashboxes = entity_scope.cashboxes(Cashbox.objects).filter(active=True)
    if request.method == "POST":
        cashbox = cashboxes.filter(pk=request.POST.get("cashbox")).first() if (request.POST.get("cashbox") or "").isdigit() else None
        buyer = Customer.objects.filter(pk=request.POST.get("buyer"), active=True).first() if (request.POST.get("buyer") or "").isdigit() else None
        expires = parse_date(request.POST.get("expires_on") or "") if request.POST.get("expires_on") else None
        try:
            _check_face_value(request.POST.get("face_value"), words)
            card = services.sell(request.POST.get("face_value"), cashbox, request.user, request.POST.get("holder_name", ""),
                                 request.POST.get("holder_phone", ""), buyer, expires, lang)
        except (ValidationError, PermissionDenied) as exc:
            _error(request, exc, lang)
        else:
            messages.success(request, words["sold"].format(number=card.number, value=f"{card.face_value:,.2f}"))
            # The full code is shown once, on the receipt that follows; a set, so two sales in two tabs each get theirs.
            request.session[RECEIPT_ONCE] = sorted(set(request.session.get(RECEIPT_ONCE) or []) | {card.pk})
            return redirect(f"{reverse('giftcards:receipt', args=[card.pk])}?lang={lang}")
    query = services.normalize(request.GET.get("q", ""))
    cards = _cards()
    if query:
        cards = cards.filter(code=query) | cards.filter(number__iexact=request.GET.get("q", "").strip())
    cards = list(cards[:200])
    held_on = services.balances(cards)
    today = timezone.localdate()
    rows = [{"card": card, "balance": held_on.get(card.pk), "expired": bool(card.expires_on and card.expires_on < today), "code": services.masked(card.code)} for card in cards]
    return render(request, "giftcards/list.html", _context(
        request, rows=rows, cashboxes=cashboxes, query=request.GET.get("q", ""), customers=Customer.objects.filter(active=True).order_by("name")[:500],
        outstanding=sum(services.balances(_cards()).values(), services.ZERO), statuses=services.MESSAGES[lang]["statuses"]))


@require_permission(services.SELL)
def card_detail(request, pk):
    _gate()
    lang = _lang(request)
    words = WORDS[lang]
    card = _card_or_404(pk)
    if request.method == "POST":
        action = request.POST.get("action", "")
        reason = request.POST.get("reason", "")
        try:
            if action in ("breakage", "reverse"):
                services._require(request.user, services.BREAKAGE)  # before a request is held, not only when it runs
            if action == "breakage":
                if not held(request, "giftcards.breakage", card.pk, {"reason": reason}, card.number):
                    services.breakage(card, request.user, reason, lang)
                    messages.success(request, words["done"])
            elif action == "reverse":
                entry = GiftCardEntry.objects.filter(pk=request.POST.get("entry") if (request.POST.get("entry") or "").isdigit() else None, card=card).first()
                day = parse_date(request.POST.get("reversal_date") or "") or timezone.localdate()
                if entry is None:
                    raise ValidationError(services.MESSAGES[lang]["wrong_entry"])
                if not held(request, "giftcards.reverse", entry.pk, {"reversal_date": day, "reason": reason}, f"{card.number} · {entry.get_kind_display()}"):
                    services.reverse(entry, day, reason, request.user, lang)
                    messages.success(request, words["done"])
            elif action == "cancel_redemption":
                entry = GiftCardEntry.objects.filter(pk=request.POST.get("entry") if (request.POST.get("entry") or "").isdigit() else None, card=card,
                                                     kind=EntryKind.REDEEMED).first()
                if entry is None:
                    raise ValidationError(services.MESSAGES[lang]["wrong_entry"])
                if not held(request, "sales.cancel_payment", entry.payment_id, {"reason": reason}, entry.payment.payment_number):
                    services.cancel_redemption(entry, request.user, reason, lang)
                    messages.success(request, words["done"])
        except (ValidationError, PermissionDenied) as exc:
            _error(request, exc, lang)
        return redirect(f"{reverse('giftcards:detail', args=[card.pk])}?lang={lang}")
    entries = list(card.entries.select_related("operation", "payment", "invoice").order_by("pk"))
    reversed_ids = {entry.reverses_id for entry in entries if entry.reverses_id}
    rows = []
    for entry in entries:
        live = entry.pk not in reversed_ids and entry.kind != EntryKind.REVERSED and (
            (entry.operation_id and entry.operation.status == "posted") or (entry.payment_id and entry.payment.status == "posted"))
        rows.append({"entry": entry, "label": services.kind_label(entry, lang), "live": live})
    return render(request, "giftcards/detail.html", _context(
        request, card=card, code=services.masked(card.code), figures=services.figures(card), rows=rows, status=services.status_label(card, lang),
        expired=bool(card.expires_on and card.expires_on < timezone.localdate()), today=timezone.localdate(),
        can_manage=user_has_permission(request.user, services.BREAKAGE)))


@never_cache  # the full code must not come back from the browser's cache or back button
@require_permission(services.SELL)
def receipt(request, pk):
    """The card's receipt. The full code shows once, right after the sale (a one-time session flag); later, only a
    manager (``giftcards.breakage``) asking for it sees it, and that is audited. Everyone else sees the last four."""

    _gate()
    card = _card_or_404(pk)
    pending = list(request.session.get(RECEIPT_ONCE) or [])
    full = card.pk in pending
    if full:
        pending.remove(card.pk)
        request.session[RECEIPT_ONCE] = pending
    elif request.GET.get("reveal") == "1" and user_has_permission(request.user, services.BREAKAGE):
        full = True
        AuditLog.objects.create(event_type=AuditEventType.EXPORT, actor=request.user,
                                module="giftcards", action="reveal_gift_card_code", object_type="giftcards.GiftCard", object_id=str(card.pk),
                                after_data={"number": card.number})
    code = services.pretty(card.code) if full else services.masked(card.code)
    return render(request, "giftcards/receipt.html", _context(request, card=card, code=code, full=full, balance=services.balance(card),
                                                              can_reveal=user_has_permission(request.user, services.BREAKAGE)))


@require_permission(services.BREAKAGE)
def entry_detail(request, pk):
    """Where an approval request held per entry points: the entry's card page."""

    _gate()
    entry = GiftCardEntry.objects.filter(pk=pk, card__in=_cards()).first()
    if entry is None:
        raise Http404
    return redirect(f"{reverse('giftcards:detail', args=[entry.card_id])}?lang={_lang(request)}")


@require_permission(services.SELL)
def pay_invoice(request, invoice_pk):
    """Pay what a posted invoice still asks for, or part of it, with a gift card (the invoice page's box)."""

    from sales.models import SalesInvoice

    lang = _lang(request)
    invoice = entity_scope.scope(SalesInvoice.objects, entity_scope.SALES_INVOICE).filter(pk=invoice_pk).first()
    if invoice is None:
        raise Http404
    if request.method == "POST":
        try:
            entry = services.redeem(services.find(request.POST.get("code", "")), invoice, request.POST.get("amount"), request.user, lang)
        except (ValidationError, PermissionDenied) as exc:
            _error(request, exc, lang)
        else:
            messages.success(request, WORDS[lang]["done"] + f" {entry.amount:,.2f}")
    return redirect(f"{reverse('sales:detail', args=[invoice.pk])}?lang={lang}")
