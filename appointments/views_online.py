"""M10 (HG-096): the clinic's online-consultation settings: the meeting server the call links are made on, «paid first», and whether
the public booking page offers an online visit. Installation-wide ``SystemSetting`` rows (no new table); a medical installation only."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse

from permissions.decorators import require_permission

from . import online
from .views import BOOK, _base
from .views_booking import SETTINGS_PERMISSION, can_manage

WORDS = {
    "ar": {
        "title": "الكشف أونلاين", "intro": "الدكتور والمريض بيدخلوا نفس مكالمة الفيديو بزر من شاشات حسبة. المكالمة نفسها بتتم على خدمة اجتماعات خارج حسبة؛ حسبة بتجهّز الرابط وتحميه فقط.",
        "server": "سيرفر المكالمات", "server_label": "عنوان السيرفر", "server_hint": "الرابط اللي حسبة بتجهّزه بيبدأ بالعنوان ده وبعده اسم غرفة عشوائي طويل. سيبه فاضي لو عايز السيرفر العام ({base}).",
        "jitsi_note": "تنبيه: السيرفر العام (meet.jit.si) بيطلب من أول واحد يفتح الغرفة، وهو الدكتور، يسجّل الدخول بحساب Google أو Facebook أو GitHub. لو ده مش مناسب للعيادة، حط سيرفر Jitsi الخاص بالعيادة هنا، أو سيب الخانة وحط رابط جاهز (Google Meet أو Zoom أو Teams) على كل حجز بزر «حط رابط».",
        "rules": "القواعد", "prepaid": "الكشف الأونلاين مدفوع مقدّماً",
        "prepaid_hint": "لو شغّال: زر المريض مش بيظهر إلا لو على الحجز عربون بسعر الكشف أو أكتر (أو فاتورته نهائية ومدفوعة). مفيش دفع بيتم من صفحة المريض.",
        "public": "اسمح بحجز كشف أونلاين", "public_hint": "لو شغّال: صفحة الحجز العامة بتسأل المريض «في العيادة ولا أونلاين» لكل الكشوفات (مفيش تحديد لكشوفات معينة). الحجز الأونلاين من المريض بيفضل من غير رابط لحد ما حد من العيادة يعمله. محتاجة صفحة الحجز العامة تكون شغالة.",
        "window": "المريض بيلاقي زر المكالمة من 15 دقيقة قبل الميعاد لحد 3 ساعات بعد بدايته، ولو الحجز مؤكَّد. الرابط عمره ما بيتبعت في رسالة.",
        "scope": "الإعدادات دي للتركيب كله (كل الفروع).", "save": "احفظ", "saved": "اتحفظت إعدادات الكشف أونلاين.", "back": "المواعيد",
    },
    "en": {
        "title": "Online visits", "intro": "The doctor and the patient join the same video call from a button on Hesba's screens. The call itself runs on a meeting service outside Hesba; Hesba only makes the link and guards it.",
        "server": "Call server", "server_label": "Server address", "server_hint": "The links Hesba makes are this address followed by a long random room name. Leave it empty to use the public server ({base}).",
        "jitsi_note": "Note: the public server (meet.jit.si) asks the first person who opens a room, the doctor, to sign in with a Google, Facebook or GitHub account. If that does not suit the clinic, put your own Jitsi server here, or leave it and paste a ready link (Google Meet, Zoom or Teams) on each booking with «Use this link».",
        "rules": "Rules", "prepaid": "Online visits are paid in advance",
        "prepaid_hint": "When on, the patient's button shows only if the booking has a deposit of at least the visit's price (or its invoice is posted and paid). Nothing is paid from the patient's page.",
        "public": "Allow booking an online visit", "public_hint": "When on, the public booking page asks the patient «at the clinic or online» for every visit (visits cannot be marked one by one). An online booking by the patient has no link until someone at the clinic makes one. It needs the public booking page to be on.",
        "window": "The patient sees the call button from 15 minutes before the booking until 3 hours after it starts, if it is confirmed. The link is never sent in a message.",
        "scope": "These settings are for the whole installation (every branch).", "save": "Save", "saved": "Online visit settings saved.", "back": "Appointments",
    },
}


@require_permission(BOOK)
def online_settings(request):
    from medical.services import is_medical_install

    if not is_medical_install():
        raise Http404("Online visits are for medical activities.")
    if not can_manage(request.user):
        raise PermissionDenied(f"Online visit settings need {SETTINGS_PERMISSION}.")
    lang = "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"
    words = {**WORDS[lang], "server_hint": WORDS[lang]["server_hint"].format(base=online.DEFAULT_BASE)}
    error = ""
    values = {"base_url": "" if online.base_url() == online.DEFAULT_BASE else online.base_url(), "prepaid": online.prepaid_required(), "public_booking": online.public_choice()}
    if request.method == "POST":
        values = {"base_url": request.POST.get("base_url", ""), "prepaid": bool(request.POST.get("prepaid")), "public_booking": bool(request.POST.get("public_booking"))}
        try:
            online.save_settings(request.POST, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('appointments:online_settings')}?lang={lang}")
    return render(request, "appointments/online_settings.html", _base(request, words=words, page_title=words["title"], error=error, values=values, default_base=online.DEFAULT_BASE))
