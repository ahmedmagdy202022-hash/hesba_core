"""T9 screen: today's messages to customers, sent through WhatsApp or skipped, and their wording."""

from django.contrib import messages as flash
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from permissions.decorators import require_any_permission
from reports.navigation import CUSTOMER_AUDIENCE

from . import services
from .models import MessageKind, OutboxMessage, OutboxStatus

WORDS = {
    "ar": {
        "title": "رسائل العملاء", "intro": "كل يوم بتتجمع هنا الرسائل اللي تستاهل تتبعت: اللي عليهم فلوس عدّت ميعادها، الأقساط القريبة والمتأخرة، مواعيد بكرة، والروشتات اللي ميعاد تجديدها جه. «ابعت» بيفتح الواتساب والرسالة مكتوبة.",
        "pending": "مستنية", "sent": "اتبعتت", "skipped": "اتشالت", "refresh": "جمّع رسائل النهارده", "added": "اتضاف {n} رسالة.", "nothing_new": "مفيش رسائل جديدة.",
        "kind": "النوع", "to": "لـ", "phone": "الموبايل", "text": "الرسالة", "send": "ابعت واتساب", "skip": "شيل", "open": "افتح",
        "none": "مفيش رسائل هنا.", "templates": "نصوص الرسائل", "active": "تتبعت", "every": "متتكررش لنفس العميل قبل (يوم)", "save": "حفظ",
        "placeholders": "الكلمات اللي بتتغير لوحدها: {name} الاسم، {company} اسم الشركة، {amount} المبلغ، {date} التاريخ، {time} الوقت، {number} رقم الفاتورة أو الروشتة.",
        "api_off": "الإرسال الأوتوماتيك من غير ضغط محتاج حساب WhatsApp Business للمحل نفسه. لحد ما يتربط، الرسالة بتتبعت بضغطة من هنا.",
        "saved": "اتحفظ.", "day": "اليوم", "when": "اتبعتت", "gone": "الرسالة دي مبقتش لازمة: اتدفع المبلغ أو الميعاد اتلغى أو اتغير.",
    },
    "en": {
        "title": "Customer messages", "intro": "Every day the messages worth sending gather here: customers past their credit days, instalments coming up or late, tomorrow's appointments, and prescriptions due for a refill. “Send” opens WhatsApp with the message written.",
        "pending": "Waiting", "sent": "Sent", "skipped": "Skipped", "refresh": "Gather today's messages", "added": "{n} messages added.", "nothing_new": "No new messages.",
        "kind": "Kind", "to": "To", "phone": "Mobile", "text": "Message", "send": "Send on WhatsApp", "skip": "Skip", "open": "Open",
        "none": "No messages here.", "templates": "Message wording", "active": "Send these", "every": "Not again to the same customer within (days)", "save": "Save",
        "placeholders": "Words filled in by themselves: {name} the name, {company} your company, {amount} the amount, {date} the date, {time} the time, {number} the invoice or prescription number.",
        "api_off": "Sending without a click needs the shop's own WhatsApp Business account. Until it is connected, each message goes with one click from here.",
        "saved": "Saved.", "day": "Day", "when": "Sent", "gone": "This message is no longer needed: the amount was paid, or the appointment was cancelled or moved.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _error(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(getattr(exc, "messages", [str(exc)])), lang)


@require_any_permission(*CUSTOMER_AUDIENCE)
def outbox(request):
    lang = _lang(request)
    words = WORDS[lang]
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action in ("send", "skip"):
                pk = request.POST.get("message") or 0
                services.build()  # what was paid or cancelled since the page opened is withdrawn first
                message = services.visible(request.user).filter(pk=pk).first()
                if message is None:
                    if OutboxMessage.objects.filter(pk=pk).exists():
                        raise Http404("Another entity's message.")
                    raise ValidationError(words["gone"])
                message = services.mark(message, OutboxStatus.SENT if action == "send" else OutboxStatus.SKIPPED, request.user, lang)
                if action == "send":
                    return redirect(services.whatsapp_url(message))
                flash.success(request, words["saved"])
            elif action == "refresh":
                added = services.build()
                flash.success(request, words["added"].format(n=added) if added else words["nothing_new"])
            elif action == "template":
                kind = request.POST.get("kind")
                if kind not in MessageKind.values:
                    raise Http404("Unknown kind.")
                services.save_template(kind, {key: request.POST.get(key) for key in request.POST}, request.user, lang)
                flash.success(request, words["saved"])
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = _error(exc, lang)
        else:
            return redirect(f"{reverse('messaging:outbox')}?lang={lang}&show={request.POST.get('show') or 'pending'}")
    services.build()  # opening the outbox gathers what is new today
    show = request.GET.get("show") if request.GET.get("show") in OutboxStatus.values else "pending"
    today = timezone.localdate()
    rows = services.visible(request.user).filter(status=show)
    rows = rows.filter(day=today) if show == "pending" else rows.order_by("-sent_at", "-pk")
    rows = list(rows[:300])
    from permissions.services import user_has_permission

    sees_cards = user_has_permission(request.user, "reports.view_customer_report")  # the customer card checks it inside the view
    for row in rows:
        row.can_open = bool(row.link) and (sees_cards or not row.link.startswith("/parties/"))
    return render(request, "messaging/outbox.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "rows": rows, "show": show,
        "counts": services.counts(request.user, today), "kinds": services.KIND_LABELS[lang], "templates": services.templates(), "can_edit": services.can_edit(request.user),
        "api": services.api_connected(), "error": error, "today": today})
