"""USAGE-002: the owner sees how full the database plan is, and what fills it."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.urls import reverse

from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import storage


WORDS = {
    "ar": {
        "page_title": "المساحة", "title": "مساحة قاعدة البيانات", "back": "العودة للإعدادات",
        "lead": "الحجم الحقيقي لقاعدة بياناتك مقارنة بحدود خطتك. التنبيه بيظهر على لوحة القيادة قبل ما توصل للحد بوقت كافي.",
        "used": "المستخدم", "of": "من", "left": "تقدير الوقت الباقي", "months": "{n} شهر تقريبًا بنفس معدل الاستخدام", "unknown": "محتاج شهر استخدام على الأقل عشان نقدّر",
        "full": "وصلت للحد", "biggest": "أكبر الجداول", "clean": "تنظيف البيانات المؤقتة", "cleaned": "اتمسح {n} سجل مؤقت. الفواتير والحسابات والسجل ما اتلمسوش.",
        "clean_help": "بيمسح الجلسات المنتهية ومحاولات الدخول الغلط القديمة بس. الفواتير والقيود والمخزون وسجل المراجعة مابيتمسحوش أبدًا.",
        "green": "المساحة كويسة.", "yellow": "استخدمت نص المساحة. مفيش حاجة مطلوبة دلوقتي.",
        "orange": "المساحة بتقرب من الحد. ابدأ تجهّز للترقية.",
        "red": "المساحة قربت تخلص. رقّي خطة قاعدة البيانات عشان الشغل مايقفش.",
        "advice": "لما المساحة تقرب تخلص: رقّي خطة قاعدة البيانات من حسابك (في Supabase: خطة Pro). حسبة مابتمسحش فواتير أو حسابات قديمة عشان توفّر مساحة، لأن الدفاتر لازم تفضل كاملة.",
        "no_size": "المحرك ده مابيقولش حجمه.",
    },
    "en": {
        "page_title": "Storage", "title": "Database storage", "back": "Back to settings",
        "lead": "Your database's real size against your plan's limit. The dashboard warns you well before the limit is reached.",
        "used": "Used", "of": "of", "left": "Estimated time left", "months": "about {n} months at the current pace", "unknown": "needs at least a month of use to estimate",
        "full": "Limit reached", "biggest": "Biggest tables", "clean": "Clean temporary data", "cleaned": "{n} temporary rows removed. Invoices, accounts and the audit trail were not touched.",
        "clean_help": "Removes expired sessions and old failed sign-in attempts only. Invoices, entries, stock and the audit trail are never deleted.",
        "green": "Storage is fine.", "yellow": "Half the space is used. Nothing to do yet.",
        "orange": "Storage is getting close to the limit. Start preparing to upgrade.",
        "red": "Storage is nearly full. Upgrade the database plan so work does not stop.",
        "advice": "When space runs short, upgrade the database plan from your account (on Supabase: the Pro plan). Hesba never deletes old invoices or accounts to save space: the books must stay complete.",
        "no_size": "This database engine does not report its size.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


@require_permission("settings.view_settings")
def storage_status(request):
    lang = _lang(request)
    words = WORDS[lang]
    can_manage = user_has_permission(request.user, "settings.manage_settings")
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Cleaning needs settings.manage_settings.")
        removed = storage.clean_temporary(request.user)
        messages.success(request, words["cleaned"].format(n=removed))
        return redirect(f"{reverse('settings_core:storage')}?lang={lang}")
    info = storage.status()
    left = info["months_left"]
    left_text = words["full"] if left == 0 else words["months"].format(n=left) if left else words["unknown"]
    return render(request, "settings_core/storage.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["page_title"],
        "info": info, "message": words[info["level"]], "left_text": left_text, "can_manage": can_manage,
        "tables": [(name, round(size / storage.MB, 1)) for name, size in info["tables"]],
        "back_url": f"{reverse('settings_core:overview')}?lang={lang}",
    })
