"""M6 (HG-085): a lab patient opens their results of one day from a link.

The lab makes the link (``make_link``) for one patient and one sample day and sends it by WhatsApp. The patient
opens it on a phone, types their birth year or the last 4 digits of their phone (the second factor), and sees the
same content as the printed lab sheet. Until then the page shows only the lab's name and the question.

Nothing is stored (no table, no migration):

- the link is a token signed with the SECRET_KEY and its own salt (not printing.share's), so a share link never
  opens this page and this token never opens a share page. It holds the patient's id, the day and a fingerprint,
  and expires after MEDICAL_LINK_DAYS (default 7);
- the fingerprint is a keyed HMAC (never a plain hash: the token is signed, not encrypted, and a plain hash of a
  "negative / positive" result could be guessed offline) of that day's results and of the second-factor details.
  Any edit, addition or removal of a result, or a change of the birth date or the phone, kills old links;
  «ابعت تاني» simply makes a fresh one;
- a passed second factor is remembered for this browser by a short-lived signed cookie bound to this token
  (never the Django session, so a staff login in the same browser neither bypasses nor is touched by it);
- wrong answers are counted per token and per address in Django's default cache (per process, as the booking and
  payment pages do), and a locked token refuses even the right answer until the window passes.

Every failure (forged, altered, expired, stale, wrong patient or day, nothing left to prove) shows the same page
with the same status. Nothing is shown when in doubt.
"""

import hashlib
import hmac
import json
from datetime import date

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.csrf import csrf_protect

from audit.models import AuditEventType, AuditLog

SALT = "hesba.medical.lab.v1"
FINGERPRINT_SALT = "hesba.medical.lab.fingerprint.v1"
PASS_SALT = "hesba.medical.lab.pass.v1"
PASS_SECONDS = 30 * 60  # how long a passed second factor is remembered in this browser
COOKIE_PREFIX = "hs_lab_"
MIN_PHONE_DIGITS = 7  # fewer digits is not a phone number; its last 4 would be a guess, not a factor

# Wrong answers: per token is the real limit; per address is generous because behind the host's proxy many
# visitors can share one address (HG-070), and a tight one would let one person lock every patient out.
TOKEN_SHORT = ("token15", 5, 15 * 60)
TOKEN_DAY = ("tokenday", 10, 24 * 3600)
ADDRESS_HOUR = ("ip", 50, 3600)
PAGE_MINUTE = ("page", 60, 60)  # any request to the page, per address: blunts a flood

WORDS = {
    "ar": {
        "sub": "نتيجة التحاليل", "title": "نتيجة التحاليل",
        "ask_h": "نتيجة تحاليلك جاهزة", "ask": "عشان نتأكد إنك صاحب النتيجة، اكتب سنة ميلادك (زي 1985) أو آخر 4 أرقام من رقم الموبايل اللي سجّلته في المعمل.",
        "label": "سنة الميلاد أو آخر 4 أرقام من الموبايل", "open": "افتح النتيجة",
        "wrong": "البيانات دي مش مطابقة للي عندنا. اتأكد وجرّب تاني، أو كلّم المعمل.",
        "format": "اكتب 4 أرقام: سنة الميلاد (زي 1985) أو آخر 4 أرقام من الموبايل.",
        "busy": "محاولات كتير. استنى شوية وجرّب تاني، أو كلّم المعمل.",
        "print": "اطبع أو احفظ PDF", "private": "النتيجة دي خاصة بيك. بلاش تشارك اللينك مع حد.",
        "dead_h": "اللينك ده مبقاش شغال", "dead": "ممكن تكون مدته خلصت أو النتيجة اتعدّلت. اطلب من المعمل يبعته تاني.",
        "call": "كلّم المعمل", "whatsapp": "واتساب",
        # the lab's side
        "no_results": "مفيش نتايج في اليوم ده.",
        "no_factor": "مينفعش تبعت النتيجة أونلاين: ملف المريض مفيهوش سنة ميلاد ولا رقم موبايل، والمريض لازم يكتب واحد منهم عشان يفتحها. ضيف واحد منهم في ملف المريض.",
        "no_base_url": "عنوان الموقع مش متظبط (PUBLIC_BASE_URL)؛ ظبّطه الأول عشان اللينك يتبعت.",
        "wa_text": "أهلاً، نتيجة تحاليلك من {lab} جاهزة. افتح اللينك واكتب سنة ميلادك أو آخر 4 أرقام من موبايلك المسجّل عندنا. اللينك شغال {days} أيام: {url}",
    },
    "en": {
        "sub": "Lab results", "title": "Lab results",
        "ask_h": "Your lab results are ready", "ask": "To make sure the results are yours, type your birth year (like 1985) or the last 4 digits of the phone number you gave the lab.",
        "label": "Birth year or the phone's last 4 digits", "open": "Open the results",
        "wrong": "That does not match the details the lab has. Check and try again, or contact the lab.",
        "format": "Type 4 digits: your birth year (like 1985) or the last 4 digits of your phone.",
        "busy": "Too many tries. Please wait a while and try again, or contact the lab.",
        "print": "Print or save as PDF", "private": "These results are private to you. Please do not share the link.",
        "dead_h": "This link no longer works", "dead": "It may have expired or the results were updated. Ask the lab to send it again.",
        "call": "Call the lab", "whatsapp": "WhatsApp",
        "no_results": "There are no results on that day.",
        "no_factor": "The results cannot be sent online: the patient's file has no birth date and no phone number, and the patient must type one of them to open the link. Add one of them to the patient's file.",
        "no_base_url": "The site address (PUBLIC_BASE_URL) is not set; set it first so the link can be sent.",
        "wa_text": "Hello, your lab results from {lab} are ready. Open the link and type your birth year or the last 4 digits of the phone number we have. The link works for {days} days: {url}",
    },
}


def _words(lang):
    return WORDS["en" if lang == "en" else "ar"]


def days():
    return getattr(settings, "MEDICAL_LINK_DAYS", 7)


def max_age():
    return days() * 86400


# ---- the second factor ----

def _digits(text):
    from .lab import ARABIC_DIGITS

    return "".join(ch for ch in str(text or "").translate(ARABIC_DIGITS) if ch in "0123456789")


def _file(patient):
    """The patient's file, read only (the public page never creates one)."""

    from .models import PatientFile

    return PatientFile.objects.filter(customer=patient).first()


def factors(patient, file=None):
    """The 4-digit answers that open this patient's links: the birth year and the phone's last 4 digits, as on file."""

    file = file if file is not None else _file(patient)
    found = []
    if file is not None and file.date_of_birth:
        found.append(f"{file.date_of_birth.year:04d}")
    phone = _digits(patient.phone)
    if len(phone) >= MIN_PHONE_DIGITS:
        found.append(phone[-4:])
    return found


def can_share(patient):
    return bool(factors(patient))


def _matches(patient, file, answer):
    ok = False
    for expected in factors(patient, file):
        ok = hmac.compare_digest(expected, answer) or ok  # every factor is compared, so the time says nothing
    return ok


# ---- the token ----

def _results(patient, day):
    from .models import LabResult

    return list(LabResult.objects.filter(patient=patient, taken_on=day).select_related("test"))


def fingerprint(patient, results, file):
    """Keyed hash of the day's results (what the sheet shows) and of the second-factor details."""

    def text(value):
        return "" if value is None else str(value)

    rows = sorted([result.pk, result.test_id, text(result.value), result.text_value, result.unit, text(result.low), text(result.high), result.flag]
                  for result in results)
    basis = [text(file.date_of_birth) if file is not None else "", _digits(patient.phone)]
    data = json.dumps([patient.pk, rows, basis], ensure_ascii=False)
    return salted_hmac(FINGERPRINT_SALT, data, algorithm="sha256").hexdigest()[:32]


def make_token(patient, day, results, file):
    return signing.dumps({"p": int(patient.pk), "d": day.isoformat(), "f": fingerprint(patient, results, file)}, salt=SALT, compress=True)


class Found:
    def __init__(self, patient, day, results, file):
        self.patient, self.day, self.results, self.file = patient, day, results, file


def read_token(token):
    """What the link opens, or None for anything that must show nothing (one answer for every failure)."""

    from master_data.models import Customer

    try:
        data = signing.loads(token, salt=SALT, max_age=max_age())
    except signing.BadSignature:  # also SignatureExpired
        return None
    if not isinstance(data, dict):
        return None
    pk, raw_day, mark = data.get("p"), data.get("d"), data.get("f")
    if not isinstance(pk, int) or not isinstance(raw_day, str) or not isinstance(mark, str):
        return None
    try:
        day = date.fromisoformat(raw_day)
    except ValueError:
        return None
    patient = Customer.objects.exclude(customer_code="WALK-IN").filter(pk=pk).first()
    if patient is None:
        return None
    results = _results(patient, day)
    if not results:
        return None
    file = _file(patient)
    if not hmac.compare_digest(fingerprint(patient, results, file), mark):
        return None  # a result (or the birth date / phone) changed since the link was made
    if not factors(patient, file):
        return None  # nothing left the patient could prove
    return Found(patient, day, results, file)


def _path(token):
    return reverse("lab_result_link", args=[token])


def link_url(token, lang="ar"):
    """Built from settings.PUBLIC_BASE_URL only (Ahmed's standing rule, as S2), never from the request's host; None without it."""

    base = getattr(settings, "PUBLIC_BASE_URL", "")
    if not base:
        return None
    return f"{base.rstrip('/')}{_path(token)}?lang={'en' if lang == 'en' else 'ar'}"


def make_link(patient, day, user, lang="ar"):
    """The lab's «ابعت النتيجة للمريض»: {url, whatsapp, days, day}. Lab staff who may write records only."""

    from printing.company import company_details
    from printing.share import whatsapp_link

    from . import lab, services

    words = _words(lang)
    if not (lab.is_lab() and services.can_write(user)):
        raise PermissionDenied("Sending lab results needs medical.write_records on a lab.")
    results = _results(patient, day)
    if not results:
        raise ValidationError(words["no_results"])
    file = services.file_for(patient)  # opened here, so the sheet the patient sees carries its file number
    if not factors(patient, file):
        raise ValidationError(words["no_factor"])
    token = make_token(patient, day, results, file)
    url = link_url(token, lang)
    if url is None:
        raise ValidationError(words["no_base_url"])
    text = words["wa_text"].format(lab=company_details()["name"], days=days(), url=url)  # never a test name, value or diagnosis
    AuditLog.objects.create(event_type=AuditEventType.EXPORT, actor=user, module="medical", action="send_lab_result_link",
                            object_type="master_data.Customer", object_id=str(patient.pk),
                            after_data={"patient": patient.pk, "day": day.isoformat(), "results": len(results), "days": days()})
    return {"url": url, "whatsapp": whatsapp_link(patient.whatsapp or patient.phone, text), "days": days(), "day": day}


# ---- throttling (the booking and payment pages' pattern: Django's default cache, per process) ----

def _address(request):
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def _key(bucket, who, window):
    return f"medical-lab:{bucket[0]}:{who}:{int(timezone.now().timestamp()) // window}"


def _over(bucket, who):
    name, limit, window = bucket
    return (cache.get(_key(bucket, who, window)) or 0) >= limit


def _count(bucket, who):
    name, limit, window = bucket
    key = _key(bucket, who, window)
    try:
        return cache.incr(key)
    except ValueError:
        cache.set(key, 1, window * 2)
        return 1


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _locked(token, request):
    digest = _digest(token)
    return _over(TOKEN_SHORT, digest) or _over(TOKEN_DAY, digest) or _over(ADDRESS_HOUR, _address(request))


def _wrong(token, request):
    digest = _digest(token)
    _count(TOKEN_SHORT, digest)
    _count(TOKEN_DAY, digest)
    _count(ADDRESS_HOUR, _address(request))


# ---- the patient's page ----

def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _private(response):
    """No caching, no indexing and no referrer leaving the page: the address itself is half of the patient's key."""

    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"  # as S2: the stricter "no referrer" policy makes browsers post "Origin: null" (CSRF 403)
    return response


def _company_context(lang):
    from printing.company import company_details
    from reports.aging import whatsapp_number

    company = company_details()
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "company": company, "phone": company["phone"],
            "whatsapp": whatsapp_number(company["phone"]), "ww": _words(lang)}


def _dead(request, lang, busy=False):
    """The one page for every link that must show nothing; it knows no patient, only the lab's own identity."""

    context = {**_company_context(lang), "busy": busy}
    return _private(render(request, "medical/lab_link_dead.html", context, status=429 if busy else 404))


def _cookie_name(token):
    return COOKIE_PREFIX + _digest(token)[:16]


def _passed(request, token):
    value = request.get_signed_cookie(_cookie_name(token), default=None, salt=PASS_SALT, max_age=PASS_SECONDS)
    return value is not None and hmac.compare_digest(value, _digest(token))


def _page(request, token, found, lang, message="", status=200, sheet=False):
    here = _path(token)
    other = "ar" if lang == "en" else "en"
    context = {**_company_context(lang), "switch_url": f"{here}?lang={other}", "action": f"{here}?lang={lang}", "message": message,
               "sheet": sheet}
    if sheet:
        from . import lab, services
        from .views import LAB_WORDS

        labels = lab.FLAG_LABELS[lang]
        for result in found.results:
            result.flag_label = labels[result.flag]
        context.update(lw=LAB_WORDS[lang], patient=found.patient, file=found.file, age=services.age(found.file, found.day), taken_on=found.day,
                       results=found.results)
    return _private(render(request, "medical/lab_link.html", context, status=status))


def _answer(request, token, found, lang):
    words = _words(lang)
    if _locked(token, request):
        return _page(request, token, found, lang, words["busy"], status=429)
    answer = _digits(request.POST.get("answer"))
    if len(answer) != 4:  # not counted: a typo in the length proves nothing either way
        return _page(request, token, found, lang, words["format"], status=400)
    if not _matches(found.patient, found.file, answer):
        _wrong(token, request)
        return _page(request, token, found, lang, words["wrong"], status=400)
    response = HttpResponseRedirect(f"{_path(token)}?lang={lang}", status=303)  # post, then get: a reload never re-posts the answer
    response.set_signed_cookie(_cookie_name(token), _digest(token), salt=PASS_SALT, max_age=PASS_SECONDS, path=_path(token),
                               secure=request.is_secure() or settings.SESSION_COOKIE_SECURE, httponly=True, samesite="Strict")
    return _private(response)


@csrf_protect
def page(request, token):
    """GET: the question, or the sheet once this browser passed it; POST: the answer."""

    if request.method not in ("GET", "POST"):
        return _private(HttpResponseNotAllowed(["GET", "POST"]))
    lang = _lang(request)
    if _count(PAGE_MINUTE, _address(request)) > PAGE_MINUTE[1]:
        return _dead(request, lang, busy=True)
    found = read_token(token)
    if found is None:
        return _dead(request, lang)
    if request.method == "POST":
        return _answer(request, token, found, lang)
    if not _passed(request, token):
        return _page(request, token, found, lang)
    AuditLog.objects.create(event_type=AuditEventType.EXPORT, actor=None, module="medical", action="open_lab_result_link",
                            object_type="master_data.Customer", object_id=str(found.patient.pk),
                            after_data={"patient": found.patient.pk, "day": found.day.isoformat()})  # no IP, no device, no token
    return _page(request, token, found, lang, sheet=True)
