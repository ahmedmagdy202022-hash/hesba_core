"""T8 screen: the till's device, the receipts waiting to be sent (24 hours each), and the ones still missing data."""


from django.contrib import messages as flash
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from permissions.decorators import require_permission
from permissions.services import user_has_permission
from sales.models import SalesInvoiceStatus

from . import services
from .models import Receipt, ReceiptStatus

MANAGE = "settings.manage_settings"
WORDS = {
    "ar": {
        "title": "الإيصال الإلكتروني (B2C)", "intro": "كل بيعة من الكاشير بيطلعلها إيصال إلكتروني بصيغة مصلحة الضرايب (v1.2) بسلسلة لكل جهاز وQR على الإيصال المطبوع. المنظومة بتقبل الإيصال لحد 24 ساعة من إصداره؛ الإرسال محتاج بيانات الجهاز على بوابة الإيصالات.",
        "device": "جهاز الكاشير", "serial": "الرقم المسلسل للجهاز (من بوابة الإيصالات)", "branch": "كود الفرع", "save": "حفظ", "saved": "اتحفظ.",
        "queue": "مستنية الإرسال", "number": "رقم الإيصال", "issued": "اتعمل", "due": "آخر ميعاد", "late": "فات الـ 24 ساعة", "json": "JSON", "all_json": "تنزيل كله JSON",
        "missing": "متعملتش: بيانات ناقصة", "again": "اعمله تاني", "none": "مفيش.", "total": "الإجمالي", "last": "آخر إيصال في السلسلة",
        "os_version": "إصدار نظام الجهاز (زي ما اتسجل على البوابة)", "model_framework": "موديل الجهاز / الإطار (زي ما اتسجل على البوابة)",
        "connection": "الربط ببوابة الإيصالات", "ready": "جاهز للإرسال", "needs": "ناقص قبل الإرسال", "send": "ابعت المستني دلوقتي",
        "sent": "اتقبل {accepted}، واترفض {rejected}.", "send_failed": "ماتبعتش: {reason}", "state": "الحالة",
        "secrets_note": "بيانات الدخول السرية (Client ID وSecret وPre-shared key) بتتحط في إعدادات السيرفر، مش هنا.",
    },
    "en": {
        "title": "E-receipts (B2C)", "intro": "Every till sale gets an e-receipt in the Tax Authority's format (v1.2), chained per device, with a QR on the printed receipt. The authority accepts a receipt up to 24 hours after it is issued; sending needs the device's details on the receipt portal.",
        "device": "Till device", "serial": "Device serial number (from the receipt portal)", "branch": "Branch code", "save": "Save", "saved": "Saved.",
        "queue": "Waiting to be sent", "number": "Receipt number", "issued": "Issued", "due": "Due by", "late": "Past 24 hours", "json": "JSON", "all_json": "Download all as JSON",
        "missing": "Not issued: data missing", "again": "Issue it now", "none": "None.", "total": "Total", "last": "Last receipt in the chain",
        "os_version": "Device OS version (as registered on the portal)", "model_framework": "Device model / framework (as registered on the portal)",
        "connection": "Receipt portal connection", "ready": "Ready to send", "needs": "Still needed before sending", "send": "Send what is waiting now",
        "sent": "{accepted} accepted, {rejected} rejected.", "send_failed": "Not sent: {reason}", "state": "State",
        "secrets_note": "The secret login details (client ID, secret and pre-shared key) go in the server's settings, not here.",
    },
}


def _needs():
    from . import portal

    return portal.missing()


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


@require_permission("sales.view_sales_invoices")
def board(request):
    lang = _lang(request)
    words = WORDS[lang]
    can_manage = user_has_permission(request.user, MANAGE)
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("The e-receipt device needs settings.manage_settings.")
        if request.POST.get("action") == "device":
            serial = (request.POST.get("serial_number") or "").strip()[:100]
            if serial:
                services.register(serial, request.POST.get("branch_code"), request.POST.get("os_version", ""), request.POST.get("model_framework", ""))
        elif request.POST.get("action") == "send":  # T8 live: now, instead of waiting for the scheduled run
            from . import portal

            result = portal.send()
            portal.follow_up()
            if result["message"]:
                flash.error(request, words["send_failed"].format(reason=result["message"]))
            else:
                flash.success(request, words["sent"].format(accepted=result["accepted"], rejected=result["rejected"]))
            return redirect(f"{reverse('ereceipt:board')}?lang={lang}")
        elif request.POST.get("action") == "again":
            services.issue_again(get_object_or_404(services.scoped(Receipt.objects), pk=request.POST.get("receipt") or 0, status=ReceiptStatus.NOT_ISSUED,
                                                 invoice__status=SalesInvoiceStatus.POSTED), lang)
        flash.success(request, words["saved"])
        return redirect(f"{reverse('ereceipt:board')}?lang={lang}")
    if request.GET.get("format") == "json":
        body = '{"receipts": [' + ", ".join(row["receipt"].document_text for row in services.queue()) + "]}"  # each exactly as hashed
        response = HttpResponse(body, content_type="application/json; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="ereceipts-queue.json"'
        return response
    return render(request, "ereceipt/board.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "can_manage": can_manage,
        "device": services.device(), "queue": services.queue(), "needs": _needs(), "sent_rows": services.scoped(Receipt.objects.filter(
            status__in=[ReceiptStatus.SUBMITTED, ReceiptStatus.VALID, ReceiptStatus.INVALID])).select_related("invoice").order_by("-submitted_at", "-pk")[:30], "missing": services.scoped(Receipt.objects.filter(status=ReceiptStatus.NOT_ISSUED, invoice__status=SalesInvoiceStatus.POSTED)).select_related("invoice")[:50]})


@require_permission("sales.view_sales_invoices")
def receipt_json(request, pk):
    receipt = get_object_or_404(services.scoped(Receipt.objects), pk=pk)  # Codex #271: the user's own entity only
    response = HttpResponse(receipt.document_text, content_type="application/json; charset=utf-8")  # exactly as hashed
    response["Content-Disposition"] = f'attachment; filename="ereceipt-{receipt.invoice.invoice_number}.json"'
    return response
