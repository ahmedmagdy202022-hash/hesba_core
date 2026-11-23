"""S9: the technician's "my jobs today" screen (mobile first). Behaviour is in ``staff.myday``."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone

from settings_core.ui_messages import translate

from . import myday

WORDS = {
    "ar": {
        "title": "شغلي النهارده", "page_title": "شغلي النهارده",
        "visits": "مواعيد وزيارات النهارده", "orders": "أوامر الشغل اللي معايا",
        "empty": "مفيش شغل متسجّل عليك النهارده.", "call": "اتصل", "map": "افتح الخريطة", "details": "التفاصيل", "no_address": "مفيش عنوان متسجّل",
        "no_phone": "مفيش رقم", "visit": "زيارة عند العميل", "shop": "ميعاد في المحل", "work_order": "أمر شغل", "late": "متأخر", "due_today": "ميعاده النهارده",
        "promised": "الميعاد", "next": "المحطة الجاية", "done_note": "اتنفّذت", "route": "خط السير", "route_open": "افتح خط السير كله في الخرايط",
        "route_empty": "مفيش زيارات بعنوان فاضلة النهارده.", "route_hint": "الزرار بيفتح تطبيق الخرايط عندك بالمحطات الباقية بالترتيب من مكانك. حِسبة ما بترسمش خريطة وما بتبعتش العناوين لأي جهة.",

    },
    "en": {
        "title": "My jobs today", "page_title": "My jobs today",
        "visits": "Today's appointments and visits", "orders": "Work orders on my bench",
        "empty": "Nothing is assigned to you today.", "call": "Call", "map": "Open map", "details": "Details", "no_address": "No address on file",
        "no_phone": "No number", "visit": "Visit to the customer", "shop": "At the shop", "work_order": "Work order", "late": "Late", "due_today": "Due today",
        "promised": "Promised", "next": "Next stop", "done_note": "Done", "route": "Route", "route_open": "Open the whole route in Maps",
        "route_empty": "No visits with an address left today.", "route_hint": "The button opens your Maps app with the remaining stops in order, from where you are. Hesba draws no map and sends the addresses nowhere.",

    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def my_day(request):
    lang = _lang(request)
    if request.method == "POST":
        pk = request.POST.get("pk", "")
        if not pk.isdigit():
            raise Http404("No such job.")
        try:
            ref = myday.act(request.user, request.POST.get("kind", ""), int(pk), request.POST.get("to", ""), lang)
        except ValidationError as exc:
            messages.error(request, translate(" ".join(exc.messages), lang))
        else:
            messages.success(request, myday.MESSAGES[lang]["moved"].format(ref=ref))
        return redirect(f"{reverse('staff:my_day')}?lang={lang}")
    if myday.employee_of(request.user) is None:
        # An owner or a manager has no employee record of their own: send them to the screen that shows everyone's day.
        # Someone with no right to either screen still gets the 403 from ``my_day`` below.
        target = myday.overview_url(request.user)
        if target:
            messages.info(request, myday.MESSAGES[lang]["overview"])
            return redirect(f"{target}?lang={lang}")
    day = myday.my_day(request.user, lang, timezone.localdate())
    words = WORDS[lang]
    return render(request, "staff/my_day.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["page_title"], "section": "my_day", "day": day,
        "max_stops": myday.MAX_ROUTE_STOPS,
    })
