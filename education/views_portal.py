"""E3 (HG-101): the guardian portal's public page, and the centre's two actions on the student file (make a link, revoke it).

The public page (``page``) is ``medical.public.page`` defence for defence; the rules and the reasons are in ``education.portal``. It needs no
account, shows only the centre's name until the second factor is answered, and answers every failure with one identical page.

The staff side follows the exam screens' guard order: not an education install (404), then the record in the entity being worked in (404),
then the permission (403): making and revoking need ``master_data.manage_parties`` and no new permission. Nothing here moves money or sends a
message: the centre opens WhatsApp itself with a text that holds only a greeting and the link.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from . import portal, services
from .models import Student
from .views import _back, _base, _lang

STAFF_WORDS = {
    "ar": {"h": "بوابة ولي الأمر", "intro": "ولي الأمر يفتح رابط واحد ويكتب سنة ميلاد أحد الأبناء، ويشوف المواعيد والحضور والدرجات المنشورة والمصروفات، من غير حساب.",
           "send": "ابعت بوابة ولي الأمر", "send_again": "ابعت رابط جديد", "revoke": "إلغاء الرابط", "none": "مفيش رابط شغال لولي الأمر ده.",
           "live": "فيه رابط شغال", "made": "اتعمل يوم", "until": "صالح لحد", "opened": "آخر فتح", "never_opened": "لسه ماتفتحش",
           "again_hint": "الرابط بيظهر مرة واحدة بس وقت عمله. «ابعت رابط جديد» بيوقّف القديم.",
           "kind_year": "السؤال: سنة ميلاد أحد الأبناء.", "kind_phone": "السؤال: آخر 4 أرقام من موبايل ولي الأمر.", "kind_none": "مينفعش تبعت البوابة دلوقتي: ضيف تاريخ ميلاد لأحد الأبناء أو رقم موبايل لولي الأمر.",
           "children": "الأبناء اللي هتظهر", "made_h": "الرابط جاهز", "made_p": "الرابط ده بيظهر هنا مرة واحدة بس، وبيوقّف أي رابط قبله لنفس ولي الأمر.",
           "copy": "انسخ الرابط", "whatsapp": "ابعته واتساب", "back": "رجوع لملف الطالب", "link_made": "اتعمل الرابط.", "link_revoked": "الرابط اتلغى، ومبقاش بيفتح حاجة.",
           "revoke_hint": "إلغاء الرابط بيقفله فورًا، وتقدر تبعت رابط جديد بعد كده.", "warn_h": "تنبيه", "expired": "انتهى",
           "no_phone": "ولي الأمر ده مالوش رقم واتساب أو موبايل، فانسخ الرابط وابعته بنفسك."},
    "en": {"h": "Guardian portal", "intro": "The guardian opens one link, types the birth year of one of the children, and sees schedules, attendance, published marks and fees, with no account.",
           "send": "Send the guardian portal", "send_again": "Send a new link", "revoke": "Revoke the link", "none": "This guardian has no live link.",
           "live": "A link is live", "made": "Made on", "until": "Valid until", "opened": "Last opened", "never_opened": "Not opened yet",
           "again_hint": "The link is shown only once, when it is made. “Send a new link” stops the old one.",
           "kind_year": "The question: the birth year of one of the children.", "kind_phone": "The question: the last 4 digits of the guardian's mobile.", "kind_none": "The portal cannot be sent now: add a child's birth date or the guardian's mobile number.",
           "children": "Children it will show", "made_h": "The link is ready", "made_p": "This link is shown here once only, and it stops any earlier link for the same guardian.",
           "copy": "Copy the link", "whatsapp": "Send on WhatsApp", "back": "Back to the student", "link_made": "The link was made.", "link_revoked": "The link was revoked and opens nothing now.",
           "revoke_hint": "Revoking closes the link at once, and you can send a new one afterwards.", "warn_h": "Note", "expired": "Expired",
           "no_phone": "This guardian has no WhatsApp or mobile number, so copy the link and send it yourself."},
}


# ---- the guardian's page ----

def _public_lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _private(response):
    """No caching, no indexing and no referrer leaving the page: the address itself is half of the guardian's key."""

    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"  # as the lab and share pages: the stricter "no referrer" makes browsers post "Origin: null" (CSRF 403)
    return response


def _company_context(lang):
    from printing.company import company_details
    from reports.aging import whatsapp_number

    company = company_details()
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "company": company, "phone": company["phone"],
            "whatsapp": whatsapp_number(company["phone"]), "ww": portal._words(lang)}


def _dead(request, lang, busy=False):
    """The one page for every link that must show nothing; it knows no guardian, no child, only the centre's own identity."""

    return _private(render(request, "education/portal_dead.html", {**_company_context(lang), "busy": busy}, status=429 if busy else 404))


def _page(request, token, found, lang, message="", status=200, sheet=None):
    here = portal._path(token)
    other = "ar" if lang == "en" else "en"
    words = portal._words(lang)
    context = {**_company_context(lang), "switch_url": f"{here}?lang={other}", "action": f"{here}?lang={lang}", "message": message, "sheet": sheet is not None,
               "kind": found.kind, "ask": words[f"ask_{found.kind}"], "label": words[f"label_{found.kind}"]}
    if sheet is not None:
        context.update(sheet)
    return _private(render(request, "education/portal.html", context, status=status))


def _answer(request, token, found, lang):
    words = portal._words(lang)
    answer = portal._digits(request.POST.get("answer"))
    if len(answer) != 4:  # not counted: a typo in the length proves nothing either way
        return _page(request, token, found, lang, words[f"format_{found.kind}"], status=400)
    outcome = portal.take(found, request)  # the slot is taken before comparing
    if outcome == "dead":
        return _dead(request, lang)
    if outcome == "busy":
        return _page(request, token, found, lang, words["busy"], status=429)
    if not portal._matches(found, answer):
        return _page(request, token, found, lang, words["wrong"], status=400)  # the slot stays taken
    portal.give_back(found, request)  # a right answer is not charged
    response = HttpResponseRedirect(f"{portal._path(token)}?lang={lang}", status=303)  # post, then get: a reload never re-posts the answer
    from django.conf import settings

    response.set_signed_cookie(portal.cookie_name(found), portal.ident(found.link), salt=portal.PASS_SALT, max_age=portal.PASS_SECONDS, path=portal._path(token),
                               secure=request.is_secure() or settings.SESSION_COOKIE_SECURE, httponly=True, samesite="Strict")
    return _private(response)


@csrf_protect
def page(request, token):
    """GET: the question, or the portal once this browser passed it; POST: the answer."""

    if request.method not in ("GET", "POST"):
        return _private(HttpResponseNotAllowed(["GET", "POST"]))
    lang = _public_lang(request)
    if portal.count(portal.PAGE_MINUTE, portal._address(request)) > portal.PAGE_MINUTE[1]:
        return _dead(request, lang, busy=True)
    found = portal.read_token(token)
    if found is None:
        return _dead(request, lang)
    is_passed = request.method == "GET" and portal.passed(request, found)
    if not is_passed and portal.used_up(found):
        return _dead(request, lang)  # its wrong answers are spent: «the centre sends a new link»; a browser that passed keeps its time
    if request.method == "POST":
        return _answer(request, token, found, lang)
    if not is_passed:
        return _page(request, token, found, lang)
    portal.record_open(found)
    return _page(request, token, found, lang, sheet=portal.content(found, lang))


# ---- the centre's side ----

def _install():
    if not services.is_education_install():
        raise Http404("The guardian portal is for education activities.")


def _student(request, pk):
    _install()
    student = get_object_or_404(services.scoped(Student.objects).select_related("payer"), pk=pk)
    if not portal.can_manage(request.user):
        raise PermissionDenied(portal.messages(_lang(request))["forbidden"])
    return student


def student_card(request, student, lang):
    """The context the student page adds for its «بوابة ولي الأمر» card: nothing for anyone who may not manage parties."""

    if not portal.can_manage(request.user):
        return {}
    link = portal.live_link_of(student.payer)
    info = portal.preview(student)
    staff = portal.messages(lang)
    return {"portal_card": True, "gp": STAFF_WORDS[lang], "portal_link": link, "portal_until": portal.expires_at(link) if link else None,
            "portal_kind": info["kind"], "portal_children": info["children"], "portal_warning": staff["phone_only"] if info["kind"] == portal.PHONE else ""}


@require_POST
def student_portal_make(request, pk):
    student = _student(request, pk)
    lang = _lang(request)
    try:
        made = portal.make_link(student, request.user, lang)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
        return _back("education:student", lang, student.pk)
    words = STAFF_WORDS[lang]
    response = render(request, "education/portal_made.html", _base(request, lang, "students", student=student, made=made, gp=words, page_title=words["h"],
                                                                    back_url=f"{reverse('education:student', args=[student.pk])}?lang={lang}"))
    response["Cache-Control"] = "no-store"  # the page holds a live link: never kept by a browser or a proxy
    return response


@require_POST
def student_portal_revoke(request, pk):
    student = _student(request, pk)
    lang = _lang(request)
    raw = request.POST.get("link") or ""
    link = get_object_or_404(portal.scoped_links(), pk=int(raw) if raw.isdecimal() and raw.isascii() else 0, payer=student.payer)
    try:
        portal.revoke(link, request.user, lang)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, STAFF_WORDS[lang]["link_revoked"])
    return _back("education:student", lang, student.pk)
