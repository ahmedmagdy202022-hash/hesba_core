"""PRINT-001: printable invoices, returns and vouchers (A4 and 80 mm receipt).

The browser prints the page, and "Save as PDF" in the same dialog produces the
PDF, so no PDF library is needed. Every document is read-only: nothing here
posts, moves stock or touches a balance. Cost and profit are never printed.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from permissions.decorators import require_permission
from permissions.services import user_has_permission
from purchases.models import PurchaseInvoice, PurchaseReturn, SupplierPayment
from sales.models import CustomerPayment, SalesInvoice, SalesReturn
from settings_core.display_labels import choice_label

from .amount_words import amount_in_words
from .company import FIELDS, company_details, save_company_details


WORDS = {
    "ar": {
        "print": "طباعة",
        "pdf_hint": "لحفظها PDF اختار «حفظ كـ PDF» من نافذة الطباعة.",
        "a4": "A4",
        "receipt": "إيصال 80 مم",
        "back": "رجوع",
        "number": "رقم",
        "date": "التاريخ",
        "status": "الحالة",
        "item": "الصنف",
        "qty": "الكمية",
        "price": "السعر",
        "discount": "الخصم",
        "total": "الإجمالي",
        "subtotal": "الإجمالي قبل الخصم",
        "invoice_discount": "خصم الفاتورة",
        "tax": "الضريبة",
        "grand_total": "الصافي",
        "paid": "المدفوع",
        "remaining": "المتبقي (آجل)",
        "cashbox": "الخزنة",
        "notes": "ملاحظات",
        "amount": "المبلغ",
        "words": "المبلغ بالحروف",
        "phone": "تليفون",
        "tax_number": "رقم ضريبي",
        "cr": "سجل تجاري",
        "customer": "العميل",
        "supplier": "المورد",
        "received_from": "استلمنا من",
        "paid_to": "صرفنا إلى",
        "source_invoice": "عن الفاتورة",
        "reason": "السبب",
        "refund_cash": "رُدّ نقدًا",
        "refund_due": "خُصم من الآجل",
        "signature_receiver": "توقيع المستلم",
        "signature_cashier": "توقيع الكاشير",
        "thanks": "شكرًا لتعاملكم معنا",
        "draft": "مسودة",
        "cancelled": "ملغاة",
        "printed_by": "طُبع بواسطة",
        "sales_invoice": "فاتورة بيع",
        "purchase_invoice": "فاتورة شراء",
        "sales_return": "مرتجع بيع",
        "purchase_return": "مرتجع شراء",
        "customer_payment": "سند قبض",
        "supplier_payment": "سند صرف",
        "company_title": "بيانات الشركة على الفواتير",
        "company_intro": "البيانات دي بتظهر في رأس وأسفل كل فاتورة وإيصال بتطبعه. اسم الشركة بيتاخد من الإعدادات الأساسية.",
        "save": "حفظ",
        "saved": "تم حفظ بيانات الشركة.",
        "unchanged": "مفيش تغيير.",
        "company_name": "اسم الشركة",
        "preview": "معاينة",
    },
    "en": {
        "print": "Print",
        "pdf_hint": "To save it as a PDF, choose “Save as PDF” in the print dialog.",
        "a4": "A4",
        "receipt": "80 mm receipt",
        "back": "Back",
        "number": "No.",
        "date": "Date",
        "status": "Status",
        "item": "Item",
        "qty": "Qty",
        "price": "Price",
        "discount": "Discount",
        "total": "Total",
        "subtotal": "Subtotal",
        "invoice_discount": "Invoice discount",
        "tax": "Tax",
        "grand_total": "Net total",
        "paid": "Paid",
        "remaining": "Remaining (credit)",
        "cashbox": "Cashbox",
        "notes": "Notes",
        "amount": "Amount",
        "words": "Amount in words",
        "phone": "Phone",
        "tax_number": "Tax no.",
        "cr": "C.R.",
        "customer": "Customer",
        "supplier": "Supplier",
        "received_from": "Received from",
        "paid_to": "Paid to",
        "source_invoice": "Against invoice",
        "reason": "Reason",
        "refund_cash": "Refunded in cash",
        "refund_due": "Taken off the balance",
        "signature_receiver": "Receiver's signature",
        "signature_cashier": "Cashier's signature",
        "thanks": "Thank you for your business",
        "draft": "DRAFT",
        "cancelled": "CANCELLED",
        "printed_by": "Printed by",
        "sales_invoice": "Sales invoice",
        "purchase_invoice": "Purchase invoice",
        "sales_return": "Sales return",
        "purchase_return": "Purchase return",
        "customer_payment": "Receipt voucher",
        "supplier_payment": "Payment voucher",
        "company_title": "Company details on documents",
        "company_intro": "These details appear at the top and bottom of every invoice and receipt you print. The company name comes from the basic settings.",
        "save": "Save",
        "saved": "Company details saved.",
        "unchanged": "Nothing changed.",
        "company_name": "Company name",
        "preview": "Preview",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _format(request):
    return "receipt" if request.GET.get("format") == "receipt" else "a4"


def _party(party):
    return {"name": party.name, "phone": party.phone, "address": party.address}


def _item_label(item, description=""):
    return description or item.search_label


def _watermark(status, words):
    if status == "draft":
        return words["draft"]
    if status == "cancelled":
        return words["cancelled"]
    return ""


def _render(request, doc, back_url):
    lang = _lang(request)
    words = WORDS[lang]
    company = company_details()
    doc.setdefault("watermark", _watermark(doc.get("status"), words))
    if "words_amount" in doc:
        doc["amount_words"] = amount_in_words(doc["words_amount"], company["currency"], lang)
    return render(
        request,
        "printing/document.html",
        {
            "lang": lang,
            "dir": "ltr" if lang == "en" else "rtl",
            "words": words,
            "company": company,
            "doc": doc,
            "format": _format(request),
            "back_url": f"{back_url}?lang={lang}",
            "page_title": f"{doc['title']} {doc['number']}",
            "printed_by": getattr(request.user, "get_full_name", lambda: "")() or request.user.get_username(),
        },
    )


def _invoice_doc(invoice, kind, lang, party_kind, price_field):
    words = WORDS[lang]
    lines = [
        {
            "label": _item_label(line.item, line.description),
            "quantity": line.quantity,
            "unit_price": getattr(line, price_field),
            "discount": line.line_discount_amount,
            "total": line.line_total_amount,
        }
        for line in invoice.lines.select_related("item").order_by("line_number")
    ]
    totals = [(words["subtotal"], invoice.subtotal)]
    if invoice.discount_amount:
        totals.append((words["invoice_discount"], -invoice.discount_amount))
    if invoice.tax_amount:
        totals.append((words["tax"], invoice.tax_amount))
    party = invoice.customer if party_kind == "customer" else invoice.supplier
    return {
        "kind": kind,
        "title": words[kind],
        "number": invoice.invoice_number,
        "date": invoice.invoice_date,
        "status": invoice.status,
        "status_label": choice_label(invoice, "status", lang),
        "party_label": words[party_kind],
        "party": _party(party),
        "lines": lines,
        "has_discounts": any(line["discount"] for line in lines),
        "totals": totals,
        "grand_total": invoice.total_amount,
        "paid": invoice.paid_now,
        "remaining": invoice.remaining_due,
        "cashbox": invoice.cashbox,
        "notes": invoice.notes,
        "words_amount": invoice.total_amount,
    }


def _return_doc(document, kind, lang, party_kind, invoice_url):
    words = WORDS[lang]
    source = document.source_invoice
    lines = [
        {
            "label": _item_label(line.source_line.item, line.source_line.description),
            "quantity": line.quantity,
            "unit_price": (line.amount / line.quantity) if line.quantity else line.amount,
            "discount": 0,
            "total": line.amount,
        }
        for line in document.lines.select_related("source_line__item")
    ]
    party = source.customer if party_kind == "customer" else source.supplier
    return {
        "kind": kind,
        "title": words[kind],
        "number": document.return_number,
        "date": document.return_date,
        "status": document.status,
        "status_label": choice_label(document, "status", lang),
        "party_label": words[party_kind],
        "party": _party(party),
        "reference": (words["source_invoice"], source.invoice_number),
        "lines": lines,
        "has_discounts": False,
        "totals": [],
        "grand_total": document.total_amount,
        "refund_cash": document.cash_amount,
        "refund_due": document.due_amount,
        "notes": document.reason,
        "notes_label": words["reason"],
        "words_amount": document.total_amount,
    }


def _voucher_doc(payment, kind, lang, party_kind):
    words = WORDS[lang]
    party = payment.customer if party_kind == "customer" else payment.supplier
    return {
        "kind": kind,
        "voucher": True,
        "title": words[kind],
        "number": payment.payment_number,
        "date": payment.payment_date,
        "status": payment.status,
        "status_label": choice_label(payment, "status", lang),
        "party_label": words["received_from"] if party_kind == "customer" else words["paid_to"],
        "party": _party(party),
        "grand_total": payment.amount,
        "cashbox": payment.cashbox,
        "notes": payment.notes,
        "words_amount": payment.amount,
    }


@require_permission("sales.view_sales_invoices")
def sales_invoice(request, pk):
    invoice = get_object_or_404(SalesInvoice.objects.select_related("customer", "cashbox"), pk=pk)
    return _render(request, _invoice_doc(invoice, "sales_invoice", _lang(request), "customer", "unit_sale_price"), f"/sales/{pk}/")


@require_permission("purchases.view_purchase_invoices")
def purchase_invoice(request, pk):
    invoice = get_object_or_404(PurchaseInvoice.objects.select_related("supplier", "cashbox"), pk=pk)
    return _render(request, _invoice_doc(invoice, "purchase_invoice", _lang(request), "supplier", "unit_purchase_price"), f"/purchases/{pk}/")


@require_permission("sales.view_sales_invoices")
def sales_return(request, pk):
    document = get_object_or_404(SalesReturn.objects.select_related("source_invoice__customer"), pk=pk)
    return _render(request, _return_doc(document, "sales_return", _lang(request), "customer", "sales"), f"/sales/returns/{pk}/")


@require_permission("purchases.view_purchase_invoices")
def purchase_return(request, pk):
    document = get_object_or_404(PurchaseReturn.objects.select_related("source_invoice__supplier"), pk=pk)
    return _render(request, _return_doc(document, "purchase_return", _lang(request), "supplier", "purchases"), f"/purchases/returns/{pk}/")


@require_permission("sales.receive_customer_payment")
def customer_payment(request, pk):
    payment = get_object_or_404(CustomerPayment.objects.select_related("customer", "cashbox"), pk=pk)
    return _render(request, _voucher_doc(payment, "customer_payment", _lang(request), "customer"), "/sales/collections/")


@require_permission("purchases.pay_supplier")
def supplier_payment(request, pk):
    payment = get_object_or_404(SupplierPayment.objects.select_related("supplier", "cashbox"), pk=pk)
    return _render(request, _voucher_doc(payment, "supplier_payment", _lang(request), "supplier"), "/purchases/payments/")


@require_permission("settings.view_settings")
def company_settings(request):
    lang = _lang(request)
    words = WORDS[lang]
    can_manage = user_has_permission(request.user, "settings.manage_settings")
    details = company_details()
    if request.method == "POST":
        if not can_manage:
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("Changing company details needs settings.manage_settings.")
        changed = save_company_details({key: request.POST.get(key, "") for key, *_ in FIELDS}, request.user)
        messages.success(request, words["saved"] if changed else words["unchanged"])
        return redirect(f"/settings/company/?lang={lang}")
    current = dict(zip((key for key, *_ in FIELDS), (details["phone"], details["address"], details["tax_number"], details["commercial_register"], details["footer_note"])))
    fields = [
        {"key": key, "label": label_en if lang == "en" else label_ar, "multiline": multiline, "value": current[key]}
        for key, label_ar, label_en, multiline in FIELDS
    ]
    return render(
        request,
        "printing/company_settings.html",
        {
            "lang": lang,
            "dir": "ltr" if lang == "en" else "rtl",
            "words": words,
            "page_title": words["company_title"],
            "company": details,
            "fields": fields,
            "can_manage": can_manage,
        },
    )
