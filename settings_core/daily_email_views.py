"""DIGEST-002 screen: choose when the daily follow-up email goes out, and to whom."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse

from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import daily_email


WORDS = {
    "ar": {
        "page_title": "الإيميل اليومي", "title": "إيميل المتابعة اليومي",
        "intro": "حِسبة تبعتلك كل يوم على الإيميل: المطلوب متابعته (عملاء متأخرين، أصناف خلصت، مواعيد، مسودات)، واللي حصل (مبيعات، تحصيل، خزن)، وأهم الأحداث (إلغاءات، تسويات، فروق ورديات).",
        "when": "إمتى يتبعت", "morning": "الصبح (متابعة النهارده + ملخص امبارح)", "evening": "بالليل (ملخص النهارده + متابعة بكرة)", "hour": "الساعة",
        "to": "يتبعت لمين", "no_email": "مفيش إيميل مسجّل — ضيفه من شاشة المستخدمين", "each_sees": "كل واحد بيوصله اللي صلاحياته تسمح بيه بس.",
        "lang": "اللغة", "lang_ar": "العربية", "lang_en": "الإنجليزي", "save": "حفظ", "saved": "اتحفظت إعدادات الإيميل اليومي.", "test": "ابعتلي إيميل تجربة دلوقتي", "tested": "اتبعت إيميل تجربة لـ {email}.",
        "test_failed": "الإيميل ما اتبعتش ({error}). راجع إعدادات السيرفر.", "no_self_email": "حسابك مفيهوش إيميل؛ ضيفه الأول من شاشة المستخدمين.",
        "not_ready": "إرسال الإيميل لسه مش متفعّل على السيرفر. محتاج إعدادات SMTP (EMAIL_HOST وباقي متغيرات EMAIL_*) — الخطوات في docs/DAILY_EMAIL_SETUP.md.",
        "last": "آخر إرسال", "never": "لسه", "next": "الإرسال الجاي", "off": "الإيميل اليومي متوقف (مفيش ميعاد مختار).", "view_only": "التعديل لمدير النظام بس.",
    },
    "en": {
        "page_title": "Daily email", "title": "Daily follow-up email",
        "intro": "Hesba emails you every day: what to follow up (overdue customers, items out of stock, appointments, drafts), what happened (sales, collections, cash) and the key events (cancellations, adjustments, shift differences).",
        "when": "When", "morning": "Morning (today's follow-up + yesterday's summary)", "evening": "Evening (today's summary + tomorrow's follow-up)", "hour": "Hour",
        "to": "Send to", "no_email": "No email on file — add it from the users screen", "each_sees": "Each person only gets what their permissions allow.",
        "lang": "Language", "lang_ar": "Arabic", "lang_en": "English", "save": "Save", "saved": "Daily email settings saved.", "test": "Send me a test email now", "tested": "A test email was sent to {email}.",
        "test_failed": "The email was not sent ({error}). Check the server settings.", "no_self_email": "Your account has no email; add it from the users screen first.",
        "not_ready": "Sending email is not set up on the server yet. It needs SMTP settings (EMAIL_HOST and the other EMAIL_* variables) — see docs/DAILY_EMAIL_SETUP.md.",
        "last": "Last sent", "never": "not yet", "next": "Next send", "off": "The daily email is off (no time chosen).", "view_only": "Only the system manager can change this.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


@require_permission("settings.view_settings")
def daily_email_settings(request):
    lang = _lang(request)
    words = WORDS[lang]
    can_manage = user_has_permission(request.user, "settings.manage_settings")
    here = f"{reverse('settings_core:daily_email')}?lang={lang}"
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("The daily email needs settings.manage_settings.")
        if request.POST.get("action") == "test":
            if not request.user.email:
                messages.error(request, words["no_self_email"])
            else:
                slot = request.POST.get("slot") if request.POST.get("slot") in daily_email.SLOTS else "morning"
                result = daily_email.send_slot(slot, users=[request.user], record=False)
                if result["sent"]:
                    messages.success(request, words["tested"].format(email=request.user.email))
                else:
                    messages.error(request, words["test_failed"].format(error=(result["failed"] or [{"error": "?"}])[0]["error"]))
            return redirect(here)
        hours = {}
        for slot in daily_email.SLOTS:
            try:
                hours[slot] = int(request.POST.get(f"{slot}_hour", daily_email.DEFAULT_HOURS[slot]))
            except ValueError:
                hours[slot] = daily_email.DEFAULT_HOURS[slot]
        recipients = [int(pk) for pk in request.POST.getlist("recipients") if pk.isdigit()]
        daily_email.save_config(slots=request.POST.getlist("slots"), hours=hours, recipients=recipients, lang=request.POST.get("email_lang", "ar"), user=request.user)
        messages.success(request, words["saved"])
        return redirect(here)
    conf = daily_email.config()
    upcoming = daily_email.next_run(conf)
    return render(request, "settings_core/daily_email.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["page_title"], "section": "settings",
        "conf": conf, "ready": daily_email.email_ready(), "users": daily_email.recipients_choices(), "hours": range(24),
        "can_manage": can_manage, "upcoming": upcoming, "slots": [(slot, words[slot]) for slot in daily_email.SLOTS],
    })
