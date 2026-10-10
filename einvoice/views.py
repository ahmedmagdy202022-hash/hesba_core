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
SEND = "sales.create_sales_invoice"  # ETA-002: whoever issues invoices sends them
WORDS = {
    "ar": {
        "page_title": "الفاتورة الإلكترونية", "title": "الفاتورة الإلكترونية",
        "intro": "بيانات الشركة والعملاء والأصناف وملف الفاتورة بالشكل اللي مصلحة الضرائب بتطلبه، والتوقيع والإرسال للمنظومة لما الربط يتظبط (حساب على المنظومة وتوقيع إلكتروني).",
        "issuer": "بيانات الشركة في المنظومة", "tax_number": "الرقم الضريبي (من بيانات الشركة)", "edit_company": "تعديل بيانات الشركة",
        "items": "أكواد الأصناف", "customers": "بيانات العملاء", "save": "حفظ", "saved": "اتحفظ.", "unchanged": "مفيش تغيير.",
        "search": "بحث", "filter": "بحث", "prev": "السابق", "next": "التالي", "item": "الصنف", "code_type": "نوع الكود", "code": "الكود", "unit": "الوحدة",
        "customer": "العميل", "type": "النوع", "tax_id": "الرقم (ضريبي / قومي / جواز)", "governate": "المحافظة", "city": "المدينة", "street": "الشارع", "building": "رقم المبنى",
        "view_only": "تقدر تشوف بس؛ التعديل لصاحب الصلاحية.", "doc": "ملف الفاتورة الإلكترونية", "ready": "الملف جاهز للتوقيع والإرسال.", "problems": "لازم تكمّل الحاجات دي قبل الإرسال:",
        "download": "تنزيل ملف JSON", "back_invoice": "العودة للفاتورة", "items_hint": "كود EGS بتسجّله الأول على بوابة المنظومة، أو استخدم باركود GS1 الدولي. الوحدة من أكواد المنظومة (EA = قطعة، KGM = كيلو، LTR = لتر، BOX = علبة).",
        "customers_hint": "الشركة (B) لازم رقمها الضريبي وعنوانها. الشخص (P) بياناته إجبارية لما الفاتورة توصل للحد المحدد بس. الأجنبي (F) رقم الجواز والعنوان.",
        "threshold_hint": "راجع الحد الحالي مع المصلحة أو محاسبك؛ القيمة الافتراضية 50,000 جنيه.",
        "portal": "الإرسال للمنظومة", "send": "وقّع وابعت للمنظومة", "refresh": "حدّث الحالة من المنظومة", "cancel": "إلغاء على المنظومة", "cancel_reason": "سبب الإلغاء",
        "sent": "اتبعتت الفاتورة؛ المنظومة بتراجعها، حدّث الحالة بعد دقيقة.", "sent_rejected": "المنظومة رفضت الفاتورة: {reason}", "refreshed": "اتحدثت الحالة.",
        "cancelled": "اتبعت طلب الإلغاء للمنظومة؛ حدّث الحالة بعد شوية عشان تشوف النتيجة.", "not_sent": "الفاتورة دي لسه متبعتتش.", "env_preprod": "بيئة التجربة (preprod)", "env_prod": "بيئة الإنتاج",
        "not_set": "الربط مع المنظومة لسه متظبطش على السيرفر. ناقص:", "setup_hint": "الخطوات في docs/ETA_INTEGRATION.md: حساب على بوابة المنظومة، Client ID و Secret للنظام، وتوكن التوقيع مع برنامج التوقيع.",
        "status": "الحالة", "uuid": "رقم المستند في المنظومة", "submitted_at": "اتبعتت", "checked_at": "آخر مراجعة", "public": "صفحة الفاتورة على المنظومة",
        "history": "إرسالات سابقة", "connection": "الربط مع المنظومة", "check": "اختبار الاتصال", "connection_ok": "الاتصال شغال: الدخول للمنظومة وجهاز التوقيع تمام.",
        "connection_bad": "الاتصال فيه مشكلة: {problems}", "connection_ready": "الإعدادات موجودة على السيرفر.", "st_submitted": "اتبعتت — بتتراجع", "st_valid": "مقبولة", "st_invalid": "مرفوضة بعد المراجعة", "st_rejected": "مرفوضة", "st_cancelled": "ملغاة", "st_sending": "بتتبعت دلوقتي", "st_cancel_requested": "طلب إلغاء — مستني رد المستلم والمنظومة؛ دوس تحديث الحالة",
    },
    "en": {
        "page_title": "E-invoicing", "title": "E-invoicing",
        "intro": "The company, customer and item data and the invoice document in the Tax Authority's format, signed and sent to the portal once the connection is set up (a portal account and an e-signature).",
        "issuer": "Company details on the portal", "tax_number": "Tax registration number (from Company details)", "edit_company": "Edit company details",
        "items": "Item codes", "customers": "Customer data", "save": "Save", "saved": "Saved.", "unchanged": "Nothing changed.",
        "search": "Search", "filter": "Search", "prev": "Previous", "next": "Next", "item": "Item", "code_type": "Code type", "code": "Code", "unit": "Unit",
        "customer": "Customer", "type": "Type", "tax_id": "ID (tax / national / passport)", "governate": "Governorate", "city": "City", "street": "Street", "building": "Building no.",
        "view_only": "You can view; editing needs the right permission.", "doc": "E-invoice document", "ready": "The document is ready to sign and send.", "problems": "Complete these before sending:",
        "download": "Download JSON", "back_invoice": "Back to the invoice", "items_hint": "Register an EGS code on the portal first, or use the international GS1 barcode. Units are portal codes (EA = piece, KGM = kilogram, LTR = litre, BOX = box).",
        "customers_hint": "A business (B) needs its tax number and address. A person (P) only needs details from the set invoice amount. A foreigner (F) needs a passport number and address.",
        "threshold_hint": "Confirm the current threshold with the authority or your accountant; the default is EGP 50,000.",
        "portal": "Sending to the portal", "send": "Sign and send", "refresh": "Refresh the status", "cancel": "Cancel on the portal", "cancel_reason": "Cancellation reason",
        "sent": "Sent; the portal is checking it. Refresh the status in a minute.", "sent_rejected": "The portal rejected the invoice: {reason}", "refreshed": "Status refreshed.",
        "cancelled": "The cancellation request was sent; refresh the status shortly to see the outcome.", "not_sent": "This invoice has not been sent yet.", "env_preprod": "Pre-production (preprod)", "env_prod": "Production",
        "not_set": "The portal connection is not set up on the server yet. Missing:", "setup_hint": "The steps are in docs/ETA_INTEGRATION.md: a portal account, the system's client ID and secret, and the signing token with its signer.",
        "status": "Status", "uuid": "Portal document ID", "submitted_at": "Sent", "checked_at": "Last checked", "public": "The invoice on the portal",
        "history": "Earlier sendings", "connection": "Portal connection", "check": "Test the connection", "connection_ok": "The connection works: portal login and signer are fine.",
        "connection_bad": "The connection has a problem: {problems}", "connection_ready": "The settings are on the server.", "st_submitted": "Sent — being checked", "st_valid": "Valid", "st_invalid": "Invalid after checking", "st_rejected": "Rejected", "st_cancelled": "Cancelled", "st_sending": "Sending now", "st_cancel_requested": "Cancellation requested — waiting for the receiver and the portal; use refresh",
    },
}


MISSING_LABELS = {
    "ar": {"ETA_CLIENT_ID": "رقم النظام على المنظومة (Client ID)", "ETA_CLIENT_SECRET": "كلمة سر النظام (Client Secret)", "ETA_SIGNER_URL": "عنوان برنامج التوقيع", "ETA_SIGNER_TOKEN": "مفتاح برنامج التوقيع (Signer token)"},
    "en": {"ETA_CLIENT_ID": "the system's client ID", "ETA_CLIENT_SECRET": "the system's client secret", "ETA_SIGNER_URL": "the signer's address", "ETA_SIGNER_TOKEN": "the signer's token"},
}


def _missing(lang):
    """What the server still needs, in words (ETA-002)."""

    from . import portal

    return [MISSING_LABELS[lang].get(name, name) for name in portal.missing()]


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
        if request.POST.get("action") == "check_connection":  # ETA-002
            from . import portal

            ok, problems = portal.check_connection()
            if ok:
                messages.success(request, WORDS[lang]["connection_ok"])
            else:
                messages.error(request, WORDS[lang]["connection_bad"].format(problems=", ".join(problems)))
            return redirect(f"{reverse('einvoice:issuer')}?lang={lang}")
        changed = save_issuer_settings({key: request.POST.get(key, "") for key, *_ in ISSUER_FIELDS}, request.user)
        messages.success(request, WORDS[lang]["saved"] if changed else WORDS[lang]["unchanged"])
        return redirect(f"{reverse('einvoice:issuer')}?lang={lang}")
    values = issuer_settings()
    fields = [{"key": key, "label": label_en if lang == "en" else label_ar, "value": values[key]} for key, label_ar, label_en in ISSUER_FIELDS]
    from . import portal

    return render(request, "einvoice/issuer.html", _context(request, fields=fields, company=company_details(), can_manage=can_manage,
                                                           missing=_missing(lang), environment=portal.settings()["environment"]))


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
    from django.core.exceptions import ValidationError

    from . import portal
    from .services import cancel_submission, current_submission, public_url, refresh_submission, send_invoice

    lang = _lang(request)
    words = WORDS[lang]
    invoice = get_object_or_404(entity_scope.scope(SalesInvoice.objects.select_related("customer"), entity_scope.SALES_INVOICE), pk=pk)
    can_send = user_has_permission(request.user, SEND)
    if request.method == "POST":
        if not can_send:
            raise PermissionDenied("Sending e-invoices needs sales.create_sales_invoice.")
        action = request.POST.get("action", "")
        try:
            if action == "send":
                submission = send_invoice(invoice, request.user, lang)
                if submission.status == "rejected":
                    messages.error(request, words["sent_rejected"].format(reason=submission.message))
                else:
                    messages.success(request, words["sent"])
            elif action in ("refresh", "cancel"):
                submission = current_submission(invoice)
                if submission is None:
                    raise ValidationError(words["not_sent"])
                if action == "refresh":
                    refresh_submission(submission, request.user, lang)
                    messages.success(request, words["refreshed"])
                else:
                    cancel_submission(submission, request.user, request.POST.get("reason", ""), lang)
                    messages.success(request, words["cancelled"])
        except ValidationError as exc:
            messages.error(request, " ".join(exc.messages))
        return redirect(f"{reverse('einvoice:sales_document', args=[invoice.pk])}?lang={lang}")
    document, problems = build_document(invoice, lang)
    text = document_json(document)
    if request.GET.get("format") == "json":
        response = HttpResponse(text, content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="einvoice-{invoice.invoice_number}.json"'
        return response
    submission = current_submission(invoice)
    return render(request, "einvoice/document.html", _context(
        request, invoice=invoice, problems=problems, document_text=text, section="sales", can_send=can_send, missing=_missing(lang),
        environment=portal.settings()["environment"], submission=submission, history=invoice.eta_submissions.exclude(pk=getattr(submission, "pk", None))[:5], public_url=public_url(submission),
    ))
