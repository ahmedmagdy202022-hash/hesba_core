"""PARTY-001 screens: customer and supplier cards, and their printable statements."""

from datetime import date
from urllib.parse import quote

from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from permissions.services import user_has_permission
from printing.company import company_details
from reports.aging import whatsapp_number
from settings_core.templatetags.hesba_format import money as display_money

from .services import KINDS, balance, card, statement
from reports.navigation import display_name


PERMISSION = {"customer": "reports.view_customer_report", "supplier": "reports.view_supplier_report"}
WORDS = {
    "ar": {
        "customer": "عميل", "supplier": "مورد", "balance": "الرصيد الحالي", "balance_supplier": "مستحق للمورد",
        "credit_supplier": "رصيد مدين عند المورد", "year_total_supplier": "مشترياتك منه السنة دي",
        "invoices": "الفواتير", "last_invoice": "آخر فاتورة", "last_payment": "آخر دفعة", "none": "—", "recent_invoices": "آخر الفواتير", "recent_payments": "آخر الدفعات",
        "statement": "كشف الحساب", "from": "من", "to": "إلى", "show": "عرض", "print": "طباعة الكشف", "opening": "رصيد أول المدة", "closing": "رصيد آخر المدة",
        "date": "التاريخ", "type": "البيان", "ref": "المستند", "debit": "مدين (عليه)", "credit": "دائن (له)", "running": "الرصيد", "totals": "الإجماليات",
        "empty": "مفيش حركات في الفترة دي.", "call": "اتصال", "whatsapp": "واتساب", "new_invoice": "فاتورة جديدة", "collect": "تحصيل", "pay": "سداد",
        "phone": "التليفون", "address": "العنوان", "number": "الرقم", "total": "الإجمالي", "remaining": "المتبقي", "amount": "المبلغ", "back": "رجوع",
        "types": {"sales_due": "فاتورة بيع آجل", "customer_payment": "تحصيل", "sales_return": "مرتجع بيع", "purchase_due": "فاتورة شراء آجل",
                  "supplier_payment": "سداد للمورد", "purchase_return": "مرتجع شراء", "opening_balance": "رصيد افتتاحي", "adjustment": "تسوية"},
        "reminder": "أهلاً {name}، ده ملخص حسابك عند {company}: الرصيد المستحق {amount}. لو محتاج كشف الحساب التفصيلي قولنا. شكرًا لحضرتك.",
        "note": "الرصيد محسوب من نفس حركات تقرير العملاء والموردين. الفواتير النقدية المدفوعة بالكامل مبتظهرش هنا لأنها مش بتسيب مستحق.",
        "printed": "طُبع", "period": "الفترة", "all_time": "من البداية",
    },
    "en": {
        "customer": "Customer", "supplier": "Supplier", "balance": "Current balance", "balance_supplier": "Owed to the supplier",
        "credit_supplier": "Supplier owes you", "year_total_supplier": "Bought from them this year",
        "invoices": "Invoices", "last_invoice": "Last invoice", "last_payment": "Last payment", "none": "—", "recent_invoices": "Recent invoices", "recent_payments": "Recent payments",
        "statement": "Account statement", "from": "From", "to": "To", "show": "Show", "print": "Print statement", "opening": "Balance brought forward", "closing": "Closing balance",
        "date": "Date", "type": "Details", "ref": "Document", "debit": "Debit (owes)", "credit": "Credit (paid)", "running": "Balance", "totals": "Totals",
        "empty": "No entries in this period.", "call": "Call", "whatsapp": "WhatsApp", "new_invoice": "New invoice", "collect": "Collect", "pay": "Pay",
        "phone": "Phone", "address": "Address", "number": "Number", "total": "Total", "remaining": "Remaining", "amount": "Amount", "back": "Back",
        "types": {"sales_due": "Credit sale", "customer_payment": "Collection", "sales_return": "Sales return", "purchase_due": "Credit purchase",
                  "supplier_payment": "Payment to supplier", "purchase_return": "Purchase return", "opening_balance": "Opening balance", "adjustment": "Adjustment"},
        "reminder": "Hello {name}, here is your account summary with {company}: balance due {amount}. Ask us for the detailed statement any time. Thank you.",
        "note": "The balance comes from the same entries as the customer and supplier reports. Fully paid cash invoices do not appear because they leave nothing due.",
        "printed": "Printed", "period": "Period", "all_time": "From the start",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _party(request, kind, pk):
    if kind not in KINDS:
        raise Http404
    if not user_has_permission(request.user, PERMISSION[kind]):
        raise PermissionDenied(f"The {kind} card needs {PERMISSION[kind]}.")
    return get_object_or_404(KINDS[kind]["model"], pk=pk)


def _date(raw):
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _statement_context(request, kind, party):
    date_from, date_to = _date(request.GET.get("from")), _date(request.GET.get("to"))
    data = statement(kind, party, date_from, date_to)
    labels = WORDS[_lang(request)]["types"]
    for row in data["rows"]:
        row["type_label"] = labels.get(row["entry"].entry_type, row["entry"].entry_type)
    return data, date_from, date_to


def _medical_file(user):
    from medical.services import shows_files

    return shows_files(user)


def _words(lang, kind, party):
    """The card's words. WATCH1B (CL-01): what a customer owes is «مستحق على المريض» in a clinic, the pet owner's in a vet's, the
    customer's in a shop. An insurance company is a customer record too (MED-008), so it keeps the general wording."""

    from settings_core.vocabulary import BASE, active_vocabulary, term

    words = dict(WORDS[lang])
    table = BASE if kind != "customer" or hasattr(party, "insurance_contract") else active_vocabulary()
    words.update(balance_customer=term("party_owes", lang, table), credit_customer=term("party_in_credit", lang, table),
                 year_total=term("party_year_total", lang, table))
    return words


def party_card(request, kind, pk):
    party = _party(request, kind, pk)
    lang = _lang(request)
    words = _words(lang, kind, party)
    info = card(kind, party, timezone.localdate())
    data, date_from, date_to = _statement_context(request, kind, party)
    number = whatsapp_number(getattr(party, "whatsapp", "") or party.phone)
    reminder = ""
    if number:
        text = words["reminder"].format(name=party.name, company=company_details()["name"], amount=f"{display_money(info['balance'])} {company_details()['currency']}")
        reminder = f"https://wa.me/{number}?text={quote(text)}"
    code = getattr(party, f"{kind}_code")
    from settings_core.module_gate import closed_module

    sales_on, purchases_on = closed_module("/sales/") is None, closed_module("/purchases/") is None
    from loyalty import services as loyalty

    points = loyalty.balance(party) if kind == "customer" and loyalty.enabled() else None  # T6
    return render(request, "parties/card.html", {
        "points": points, "points_rows": loyalty.statement(party, 10) if points is not None else [],
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": party.name, "section": "customers" if kind == "customer" else "suppliers",
        "kind": kind, "party": party, "code": code, "info": info, "data": data, "date_from": date_from, "date_to": date_to, "reminder": reminder,
        # AUDIT-1: an action shows only when its screen opens: permission and module both.
        "can_sell": sales_on and user_has_permission(request.user, "sales.create_sales_invoice"),
        "can_collect": sales_on and user_has_permission(request.user, "sales.receive_customer_payment"),
        "medical_file": kind == "customer" and _medical_file(request.user),
        "can_buy": purchases_on and user_has_permission(request.user, "purchases.create_purchase_invoice"),
        "can_pay": purchases_on and user_has_permission(request.user, "purchases.pay_supplier"),
        "docs_open": (sales_on and user_has_permission(request.user, "sales.view_sales_invoices")) if kind == "customer"
        else (purchases_on and user_has_permission(request.user, "purchases.view_purchase_invoices")),
    })


def party_statement_print(request, kind, pk):
    party = _party(request, kind, pk)
    lang = _lang(request)
    data, date_from, date_to = _statement_context(request, kind, party)
    return render(request, "parties/statement_print.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "kind": kind, "party": party, "code": getattr(party, f"{kind}_code"),
        "data": data, "date_from": date_from, "date_to": date_to, "company": company_details(), "printed_by": display_name(request.user),
    })


#: AN3: whoever writes the invoice may see the balance it adds to.
INVOICE_PERMISSION = {"customer": "sales.create_sales_invoice", "supplier": "purchases.create_purchase_invoice"}


def may_see_balance(user, kind):
    """Who may read a party's balance: its report, or writing its invoices (AN3). UX Wave 2: the payment forms ask the same."""

    return user_has_permission(user, PERMISSION[kind]) or user_has_permission(user, INVOICE_PERMISSION[kind])


def due_on_form(user, kind, party_id, choices):
    """UX Wave 2: the balance a payment form shows for the party it opened with («مستحق عليه …»), or None.

    Read-only, the same figure as the party card and the balance endpoint; None when the reader may not see it.
    ``choices`` is the form's own party queryset (active parties only): a party the form would not accept shows nothing."""

    if kind not in KINDS or not str(party_id or "").isdigit() or not may_see_balance(user, kind):
        return None
    party = choices.filter(pk=party_id).first()
    return balance(kind, party) if party is not None else None


def party_balance(request, kind, pk):
    """AN3 (Ahmed: "الملخص محتاج يبين قيمة الفاتورة قبل ما يضفها للاجمالي"): the balance before this invoice, for its summary."""

    if kind not in KINDS:
        raise Http404
    if not may_see_balance(request.user, kind):
        raise PermissionDenied(f"The {kind} balance needs {PERMISSION[kind]}.")
    party = get_object_or_404(KINDS[kind]["model"], pk=pk)
    answer = {"balance": str(balance(kind, party))}
    from loyalty import services as loyalty

    if kind == "customer" and loyalty.enabled():  # T6: the points the invoice can spend
        answer["points"] = max(loyalty.balance(party)["points"], 0)
    return JsonResponse(answer)
