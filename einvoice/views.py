"""EINV-001 screens: issuer details, item codes, customer receiver data, document preview."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from audit.models import AuditEventType, AuditLog
from master_data.models import Customer, Item
from entities import scope as entity_scope
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from printing.company import company_details
from sales.models import SalesInvoice

from .models import ItemCode, ItemCodeType, ReceiverProfile, ReceiverType
from .services import ISSUER_FIELDS, build_document, document_json, issuer_settings, save_issuer_settings


PAGE_SIZE = 40
WORDS = {
    "ar": {
        "page_title": "الفاتورة الإلكترونية", "title": "الفاتورة الإلكترونية",
        "intro": "المرحلة دي بتجهّز بيانات الشركة والعملاء والأصناف وملف الفاتورة بالشكل اللي مصلحة الضرائب بتطلبه. التوقيع والإرسال للمنظومة المرحلة الجاية، ومحتاجين حساب على المنظومة وتوقيع إلكتروني.",
        "issuer": "بيانات الشركة في المنظومة", "tax_number": "الرقم الضريبي (من بيانات الشركة)", "edit_company": "تعديل بيانات الشركة",
        "items": "أكواد الأصناف", "customers": "بيانات العملاء", "save": "حفظ", "saved": "اتحفظ.", "unchanged": "مفيش تغيير.",
        "search": "بحث", "filter": "بحث", "prev": "السابق", "next": "التالي", "item": "الصنف", "code_type": "نوع الكود", "code": "الكود", "unit": "الوحدة",
        "customer": "العميل", "type": "النوع", "tax_id": "الرقم (ضريبي / قومي / جواز)", "governate": "المحافظة", "city": "المدينة", "street": "الشارع", "building": "رقم المبنى",
        "view_only": "تقدر تشوف بس؛ التعديل لصاحب الصلاحية.", "doc": "ملف الفاتورة الإلكترونية", "ready": "الملف جاهز للتوقيع والإرسال.", "problems": "لازم تكمّل الحاجات دي قبل الإرسال:",
        "download": "تنزيل ملف JSON", "back_invoice": "العودة للفاتورة", "items_hint": "كود EGS بتسجّله الأول على بوابة المنظومة، أو استخدم باركود GS1 الدولي. الوحدة من أكواد المنظومة (EA = قطعة، KGM = كيلو، LTR = لتر، BOX = علبة).",
        "customers_hint": "الشركة (B) لازم رقمها الضريبي وعنوانها. الشخص (P) بياناته إجبارية لما الفاتورة توصل للحد المحدد بس. الأجنبي (F) رقم الجواز والعنوان.",
        "threshold_hint": "راجع الحد الحالي مع المصلحة أو محاسبك؛ القيمة الافتراضية 50,000 جنيه.",
    },
    "en": {
        "page_title": "E-invoicing", "title": "E-invoicing",
        "intro": "This phase prepares the company, customer and item data and builds the invoice document in the format the Tax Authority requires. Signing and sending to the portal is the next phase and needs a portal account and an e-signature.",
        "issuer": "Company details on the portal", "tax_number": "Tax registration number (from Company details)", "edit_company": "Edit company details",
        "items": "Item codes", "customers": "Customer data", "save": "Save", "saved": "Saved.", "unchanged": "Nothing changed.",
        "search": "Search", "filter": "Search", "prev": "Previous", "next": "Next", "item": "Item", "code_type": "Code type", "code": "Code", "unit": "Unit",
        "customer": "Customer", "type": "Type", "tax_id": "ID (tax / national / passport)", "governate": "Governorate", "city": "City", "street": "Street", "building": "Building no.",
        "view_only": "You can view; editing needs the right permission.", "doc": "E-invoice document", "ready": "The document is ready to sign and send.", "problems": "Complete these before sending:",
        "download": "Download JSON", "back_invoice": "Back to the invoice", "items_hint": "Register an EGS code on the portal first, or use the international GS1 barcode. Units are portal codes (EA = piece, KGM = kilogram, LTR = litre, BOX = box).",
        "customers_hint": "A business (B) needs its tax number and address. A person (P) only needs details from the set invoice amount. A foreigner (F) needs a passport number and address.",
        "threshold_hint": "Confirm the current threshold with the authority or your accountant; the default is EGP 50,000.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"], "section": "settings"}
    context.update(extra)
    return context


def _page(queryset, request):
    return Paginator(queryset, PAGE_SIZE).get_page(request.GET.get("page") or request.POST.get("page"))


@require_permission("settings.view_settings")
def issuer_view(request):
    lang = _lang(request)
    can_manage = user_has_permission(request.user, "settings.manage_settings")
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Changing e-invoice details needs settings.manage_settings.")
        changed = save_issuer_settings({key: request.POST.get(key, "") for key, *_ in ISSUER_FIELDS}, request.user)
        messages.success(request, WORDS[lang]["saved"] if changed else WORDS[lang]["unchanged"])
        return redirect(f"{reverse('einvoice:issuer')}?lang={lang}")
    values = issuer_settings()
    fields = [{"key": key, "label": label_en if lang == "en" else label_ar, "value": values[key]} for key, label_ar, label_en in ISSUER_FIELDS]
    return render(request, "einvoice/issuer.html", _context(request, fields=fields, company=company_details(), can_manage=can_manage))


@require_permission("master_data.view_master_data")
def item_codes(request):
    lang = _lang(request)
    can_manage = user_has_permission(request.user, "master_data.manage_items")
    query = (request.GET.get("q") or request.POST.get("q") or "").strip()
    items = Item.objects.filter(active=True).order_by("item_code")
    if query:
        items = items.filter(Q(item_code__icontains=query) | Q(item_name__icontains=query) | Q(barcode=query))
    page = _page(items, request)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Managing item codes needs master_data.manage_items.")
        changed = {}
        with transaction.atomic():
            for item in page.object_list:
                if f"shown_{item.pk}" not in request.POST:
                    continue
                code = (request.POST.get(f"code_{item.pk}") or "").strip()[:100]
                code_type = request.POST.get(f"type_{item.pk}") if request.POST.get(f"type_{item.pk}") in ItemCodeType.values else ItemCodeType.EGS
                unit = ((request.POST.get(f"unit_{item.pk}") or "EA").strip().upper() or "EA")[:10]
                current = ItemCode.objects.filter(item=item).first()
                before = (current.code_type, current.code, current.unit_type) if current else None
                if not code:
                    if current:
                        current.delete()
                        changed[item.item_code] = [list(before), None]
                    continue
                after = (code_type, code, unit)
                if before != after:
                    ItemCode.objects.update_or_create(item=item, defaults={"code_type": code_type, "code": code, "unit_type": unit})
                    changed[item.item_code] = [list(before) if before else None, list(after)]
            if changed:
                AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=request.user, module="einvoice", action="set_item_codes", object_type="einvoice.ItemCode", object_id="items",
                                        before_data={k: v[0] for k, v in changed.items()}, after_data={k: v[1] for k, v in changed.items()})
        messages.success(request, WORDS[lang]["saved"])
        return redirect(f"{reverse('einvoice:items')}?lang={lang}&q={query}&page={page.number}")
    codes = {row.item_id: row for row in ItemCode.objects.filter(item__in=page.object_list)}
    rows = [{"item": item, "code": codes.get(item.pk)} for item in page.object_list]
    return render(request, "einvoice/items.html", _context(request, rows=rows, page=page, q=query, can_manage=can_manage, code_types=ItemCodeType.choices))


RECEIVER_FIELDS = ("tax_id", "governate", "region_city", "street", "building_number")


@require_permission("master_data.view_master_data")
def customer_data(request):
    lang = _lang(request)
    can_manage = user_has_permission(request.user, "master_data.manage_parties")
    query = (request.GET.get("q") or request.POST.get("q") or "").strip()
    customers = Customer.objects.filter(active=True).order_by("name")
    if query:
        customers = customers.filter(Q(customer_code__icontains=query) | Q(name__icontains=query) | Q(phone__icontains=query))
    page = _page(customers, request)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Managing customer e-invoice data needs master_data.manage_parties.")
        changed = {}
        with transaction.atomic():
            for customer in page.object_list:
                if f"shown_{customer.pk}" not in request.POST:
                    continue
                values = {name: (request.POST.get(f"{name}_{customer.pk}") or "").strip()[:200] for name in RECEIVER_FIELDS}
                values["receiver_type"] = request.POST.get(f"type_{customer.pk}") if request.POST.get(f"type_{customer.pk}") in ReceiverType.values else ReceiverType.PERSON
                current = ReceiverProfile.objects.filter(customer=customer).first()
                before = {name: getattr(current, name) for name in values} if current else None
                if before != values and (current or any(values[name] for name in RECEIVER_FIELDS) or values["receiver_type"] != ReceiverType.PERSON):
                    ReceiverProfile.objects.update_or_create(customer=customer, defaults=values)
                    changed[customer.customer_code] = [before, values]
            if changed:
                AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=request.user, module="einvoice", action="set_receiver_profiles", object_type="einvoice.ReceiverProfile", object_id="customers",
                                        before_data={k: v[0] for k, v in changed.items()}, after_data={k: v[1] for k, v in changed.items()})
        messages.success(request, WORDS[lang]["saved"])
        return redirect(f"{reverse('einvoice:customers')}?lang={lang}&q={query}&page={page.number}")
    profiles = {row.customer_id: row for row in ReceiverProfile.objects.filter(customer__in=page.object_list)}
    rows = [{"customer": customer, "profile": profiles.get(customer.pk)} for customer in page.object_list]
    return render(request, "einvoice/customers.html", _context(request, rows=rows, page=page, q=query, can_manage=can_manage, receiver_types=ReceiverType.choices))


@require_permission("sales.view_sales_invoices")
def sales_document(request, pk):
    lang = _lang(request)
    invoice = get_object_or_404(entity_scope.scope(SalesInvoice.objects.select_related("customer"), entity_scope.SALES_INVOICE), pk=pk)
    document, problems = build_document(invoice, lang)
    text = document_json(document)
    if request.GET.get("format") == "json":
        response = HttpResponse(text, content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="einvoice-{invoice.invoice_number}.json"'
        return response
    return render(request, "einvoice/document.html", _context(request, invoice=invoice, problems=problems, document_text=text, section="sales"))
