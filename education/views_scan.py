"""E1 screens: the student's card sheet, and the scan screen for attendance (the nursery's check-in scan lives on its own page)."""

from django.core.exceptions import PermissionDenied
from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from printing.company import company_details

from . import attendance as register
from . import cards, scan, services
from .views import _base, _lang

WORDS = {
    "ar": {"title": "امسح الحضور", "intro": "امسح كارت الطالب بالكاميرا أو بجهاز الباركود، والحضور بيتسجّل على طول.",
           "label": "كود الطالب", "placeholder": "امسح أو اكتب الكود", "go": "سجّل", "type": "اكتب الكود", "ready": "جاهز للمسح",
           "ready_hint": "وجّه الكاميرا على الكارت أو استخدم جهاز المسح.", "working": "ثانية واحدة…", "which": "اختار الحصة", "empty": "امسح الكارت أو اكتب الكود",
           "recent": "آخر اللي اتمسحوا", "offline": "مفيش اتصال. جرّب تاني.", "failed": "حصل خطأ. جرّب تاني.", "waiting": "كروت مستنية دورها: {n}", "none": "لسه محدش اتمسح.",
           "closed": "الحصة اتقفلت؟ اللي ما اتمسحوش بيتعلّموا غايبين، ومن يمسح بعد كده بيتسجّل متأخر.", "today": "حصص النهارده",
           "card_title": "كروت الطلاب", "card_for": "كارت {name}", "cards_for": "كروت طلاب {name}", "print": "اطبع", "back": "رجوع",
           "code": "الكود", "tip": "قصّ الكروت على الخطوط. الـ QR فيه الكود بس.", "no_cards": "مفيش طلاب نشطين في المجموعة دي.",
           "pdf_hint": "من نافذة الطباعة تقدر تحفظها PDF.", "n_title": "امسح الحضور والانصراف", "n_go": "سجّل وصول"},
    "en": {"title": "Scan attendance", "intro": "Scan a student's card with the camera or a barcode scanner and attendance is recorded at once.",
           "label": "Student code", "placeholder": "Scan or type the code", "go": "Record", "type": "Type the code", "ready": "Ready to scan",
           "ready_hint": "Point the camera at the card, or use the scanner.", "working": "One moment…", "which": "Choose the class", "empty": "Scan the card or type the code",
           "recent": "Scanned lately", "offline": "No connection. Try again.", "failed": "Something went wrong. Try again.", "waiting": "Cards waiting their turn: {n}", "none": "Nobody scanned yet.",
           "closed": "Class already closed? Students not scanned are marked absent, and anyone who scans after that is marked late.", "today": "Today's classes",
           "card_title": "Student cards", "card_for": "{name}'s card", "cards_for": "Cards for {name}", "print": "Print", "back": "Back",
           "code": "Code", "tip": "Cut the cards along the lines. The QR holds the code only.", "no_cards": "No active students in this group.",
           "pdf_hint": "In the print window you can save it as a PDF.", "n_title": "Scan to check in", "n_go": "Check in"},
}


def _pk(raw):
    return int(raw) if str(raw or "").isdigit() else 0


def _install():
    if not services.is_education_install():
        raise Http404("Cards and scanning are for education activities.")


def card_sheet(request):
    """A4 sheet of cards: ``?student=<id>`` or ``?group=<id>``. Whoever may manage students (the same guard as editing one)."""

    _install()
    if not services.can_manage(request.user):
        raise PermissionDenied("Printing cards needs master_data.manage_parties.")
    lang = _lang(request)
    words = WORDS[lang]
    if request.GET.get("student"):
        student = cards.find_student(_pk(request.GET["student"]))
        if student is None:
            raise Http404("No such student in this entity.")
        deck, title = cards.cards_for(student=student), words["card_for"].format(name=student.name)
        back = reverse("education:student", args=[student.pk])
    else:
        group = cards.find_group(_pk(request.GET.get("group")))
        if group is None:
            raise Http404("Choose a student or a group.")
        deck, title = cards.cards_for(group=group), words["cards_for"].format(name=group.name)
        back = reverse("education:group", args=[group.pk])
    return render(request, "education/cards.html", {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": title, "title": title,
                                                    "pages": cards.pages(deck), "count": len(deck), "company": company_details(), "back_url": f"{back}?lang={lang}"})


def scan_page(request):
    _install()
    if not register.can_scan(request.user):
        raise PermissionDenied("Scanning attendance needs managing students, or teaching a group.")
    lang = _lang(request)
    return render(request, "education/scan.html", _base(request, lang, "scan", sw=WORDS[lang], page_title=WORDS[lang]["title"]))


STATUS_CODE = {"not_found": 404, "refused": 403, "cancelled": 409}


@never_cache
@require_POST
def scan_mark(request):
    """JSON: mark the student with ``code`` for the class they are at; ``group`` answers a «which class?» choice."""

    _install()
    lang = _lang(request)
    try:
        result = scan.mark(request.POST.get("code"), request.user, group=request.POST.get("group") or None, lang=lang)
    except PermissionDenied as exc:
        return JsonResponse({"status": "denied", "message": str(exc)}, status=403)
    return JsonResponse(result.as_json(), status=STATUS_CODE.get(result.status, 200))
