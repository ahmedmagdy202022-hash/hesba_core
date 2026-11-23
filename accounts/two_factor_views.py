"""SEC-002: the user's own two-step sign-in screen (switch on, new recovery codes, switch off)."""

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache

from permissions.decorators import require_permission

from . import two_factor
from .user_services import MANAGE_PERMISSION


SETUP_KEY = "hesba_two_factor_setup_secret"
WORDS = {
    "ar": {
        "page_title": "التحقق بخطوتين", "title": "التحقق بخطوتين", "back": "ملفي الشخصي",
        "intro": "بعد تفعيله، الدخول محتاج كلمة السر + كود من تطبيق على موبايلك. لو حد عرف كلمة السر مش هيقدر يدخل من غير الموبايل.",
        "on": "مفعّل", "off": "مش مفعّل", "since": "من", "codes_left": "أكواد استرجاع متبقية",
        "step1": "نزّل تطبيق مصادقة على موبايلك: Google Authenticator أو Microsoft Authenticator.",
        "step2": "في التطبيق اختار «إضافة حساب» ← «إدخال مفتاح الإعداد» واكتب المفتاح ده (نوع الحساب: حسب الوقت):",
        "open_app": "أو افتحه في التطبيق مباشرة (من الموبايل)", "copy": "نسخ المفتاح",
        "step3": "اكتب الكود المكوّن من 6 أرقام اللي ظهر في التطبيق:", "code": "الكود من التطبيق", "enable": "تفعيل",
        "wrong": "الكود غلط. اتأكد إن وقت الموبايل مضبوط واكتب الكود الظاهر دلوقتي.",
        "codes_title": "أكواد الاسترجاع — احفظها دلوقتي", "codes_help": "كل كود بيتستخدم مرة واحدة لو الموبايل مش معاك. مش هتظهر تاني. اطبعها أو احفظها في مكان آمن.",
        "print": "طباعة الأكواد", "done": "حفظتها",
        "new_codes": "أكواد استرجاع جديدة", "new_codes_help": "الأكواد القديمة هتبطل.",
        "disable": "إيقاف التحقق بخطوتين", "disable_help": "اكتب كلمة السر وكود من التطبيق (أو كود استرجاع).", "password": "كلمة السر",
        "bad_password": "كلمة السر غلط.", "disabled": "تم إيقاف التحقق بخطوتين.", "enabled": "تم تفعيل التحقق بخطوتين.",
        "reset_done": "تم إيقاف التحقق بخطوتين للمستخدم. يقدر يدخل بكلمة السر ويفعّله تاني.",
    },
    "en": {
        "page_title": "Two-step sign-in", "title": "Two-step sign-in", "back": "My profile",
        "intro": "Once on, signing in needs your password plus a code from an app on your phone. Someone who learns your password still cannot sign in without the phone.",
        "on": "On", "off": "Off", "since": "since", "codes_left": "Recovery codes left",
        "step1": "Install an authenticator app on your phone: Google Authenticator or Microsoft Authenticator.",
        "step2": "In the app choose “Add account” → “Enter a setup key” and type this key (type: time based):",
        "open_app": "Or open it in the app directly (on the phone)", "copy": "Copy key",
        "step3": "Type the 6-digit code the app shows:", "code": "Code from the app", "enable": "Switch on",
        "wrong": "Wrong code. Check the phone's clock is right and type the code shown now.",
        "codes_title": "Recovery codes — save them now", "codes_help": "Each code works once if you do not have your phone. They will not be shown again. Print them or keep them somewhere safe.",
        "print": "Print codes", "done": "I saved them",
        "new_codes": "New recovery codes", "new_codes_help": "The old codes will stop working.",
        "disable": "Switch off two-step sign-in", "disable_help": "Enter your password and a code from the app (or a recovery code).", "password": "Password",
        "bad_password": "Wrong password.", "disabled": "Two-step sign-in switched off.", "enabled": "Two-step sign-in is on.",
        "reset_done": "Two-step sign-in switched off for this user. They can sign in with the password and switch it on again.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


@never_cache
def two_factor_settings(request):
    lang = _lang(request)
    words = WORDS[lang]
    user = request.user
    device = two_factor.device_for(user)
    codes, error = None, ""
    if request.method == "POST":
        action = request.POST.get("action")
        code = request.POST.get("code")
        if action == "enable" and device is None:
            secret = request.session.get(SETUP_KEY)
            codes = two_factor.enable(user, secret, code) if secret else None
            if codes:
                request.session.pop(SETUP_KEY, None)
                messages.success(request, words["enabled"])
            else:
                error = words["wrong"]
        elif action == "new_codes" and device is not None:
            codes = two_factor.new_recovery_codes(user, code)
            if not codes:
                error = words["wrong"]
        elif action == "disable" and device is not None:
            if not user.check_password(request.POST.get("password") or ""):
                error = words["bad_password"]
            elif not two_factor.verify(user, code):
                error = words["wrong"]
            else:
                two_factor.disable(user, user)
                messages.success(request, words["disabled"])
                return redirect(f"/profile/two-factor/?lang={lang}")
        device = two_factor.device_for(user)
    secret = None
    if device is None:
        secret = request.session.get(SETUP_KEY) or two_factor.new_secret()
        request.session[SETUP_KEY] = secret
    return render(request, "accounts/two_factor.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["page_title"],
        "device": device, "codes": codes, "error": error,
        "secret": secret, "secret_grouped": two_factor.grouped(secret) if secret else "",
        "otpauth": two_factor.otpauth_uri(secret, user.get_username()) if secret else "",
        "codes_left": len(device.recovery_hashes) if device else 0,
    })


@require_permission(MANAGE_PERMISSION)
def user_reset_two_factor(request, pk):
    """For a user who lost their phone and their recovery codes."""

    lang = _lang(request)
    target = get_object_or_404(get_user_model(), pk=pk, is_superuser=False)
    if request.method == "POST" and two_factor.disable(target, request.user):
        messages.success(request, WORDS[lang]["reset_done"])
    return redirect(f"/settings/users/{target.pk}/?lang={lang}")
