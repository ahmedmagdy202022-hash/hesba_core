"""PARTY-001 screens: customer and supplier cards, and their printable statements."""

from datetime import date
from urllib.parse import quote

from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from permissions.services import user_has_permission
from printing.company import company_details
from reports.aging import whatsapp_number
from settings_core.templatetags.hesba_format import money as display_money

from .services import KINDS, card, statement


PERMISSION = {"customer": "reports.view_customer_report", "supplier": "reports.view_supplier_report"}
WORDS = {
    "ar": {
        "customer": "عميل", "supplier": "مورد", "balance": "الرصيد الحالي", "balance_customer": "مستحق على العميل", "balance_supplier": "مستحق للمورد",
        "credit_customer": "رصيد دائن للعميل", "credit_supplier": "رصيد مدين عند المورد", "year_total": "إجمالي فواتيره السنة دي", "year_total_supplier": "مشترياتك منه السنة دي",
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
        "customer": "Customer", "supplier": "Supplier", "balance": "Current balance", "balance_customer": "Owed by the customer", "balance_supplier": "Owed to the supplier",
        "credit_customer": "Customer in credit", "credit_supplier": "Supplier owes you", "year_total": "Sales to them this year", "year_total_supplier": "Bought from them this year",
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


def party_card(request, kind, pk):
    party = _party(request, kind, pk)
    lang = _lang(request)
    words = WORDS[lang]
    info = card(kind, party, timezone.localdate())
    data, date_from, date_to = _statement_context(request, kind, party)
    number = whatsapp_number(getattr(party, "whatsapp", "") or party.phone)
    reminder = ""
    if number:
        text = words["reminder"].format(name=party.name, company=company_details()["name"], amount=f"{display_money(info['balance'])} {company_details()['currency']}")
        reminder = f"https://wa.me/{number}?text={quote(text)}"
    code = getattr(party, f"{kind}_code")
    return render(request, "parties/card.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": party.name, "section": "customers" if kind == "customer" else "suppliers",
        "kind": kind, "party": party, "code": code, "info": info, "data": data, "date_from": date_from, "date_to": date_to, "reminder": reminder,
        "can_sell": user_has_permission(request.user, "sales.create_sales_invoice"), "can_collect": user_has_permission(request.user, "sales.receive_customer_payment"),
        "medical_file": kind == "customer" and _medical_file(request.user),
        "can_buy": user_has_permission(request.user, "purchases.create_purchase_invoice"), "can_pay": user_has_permission(request.user, "purchases.pay_supplier"),
    })


def party_statement_print(request, kind, pk):
    party = _party(request, kind, pk)
    lang = _lang(request)
    data, date_from, date_to = _statement_context(request, kind, party)
    return render(request, "parties/statement_print.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "kind": kind, "party": party, "code": getattr(party, f"{kind}_code"),
        "data": data, "date_from": date_from, "date_to": date_to, "company": company_details(), "printed_by": request.user.get_username(),
    })
