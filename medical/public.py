"""M6 (HG-085): a lab patient opens their results of one day from a link.

The lab makes the link (``make_link``) for one patient and one sample day and sends it by WhatsApp. The patient
opens it on a phone, answers one question (the second factor), and sees the same content as the printed lab
sheet. Until then the page shows only the lab's name and the question.

The factor is the birth year when the file has a birth date, and only then; the last 4 digits of the phone are the
factor only for a file with no birth date. The link goes to the phone on file, so if that number is wrong the
stranger who receives it owns those 4 digits: they protect nothing in the mis-send case, and the lab is told so.

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
- answers are counted per token and per address in Django's default cache (per process, as the booking and
  payment pages do). A slot is taken before the answer is compared (so parallel answers cannot slip past the
  limit) and given back for a right one. A token has at most TOKEN_LIFE (8) wrong answers in its whole life, then
  it is dead; within that, 5 per 15 minutes, after which even the right answer waits.

Every failure (forged, altered, expired, stale, wrong patient or day, nothing left to prove) shows the same page
with the same status. Nothing is shown when in doubt.
"""

import hashlib
import hmac
import json
import secrets
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
# (name, limit, window in seconds; None = the token's whole life, kept for its max age)
TOKEN_LIFE = ("tokenlife", 8, None)  # then the link is dead: «ask the lab to send it again» (a re-sent link starts clean)
TOKEN_SHORT = ("token15", 5, 15 * 60)
ADDRESS_HOUR = ("ip", 50, 3600)
PAGE_MINUTE = ("page", 60, 60)  # any request to the page, per address: blunts a flood
YEAR, PHONE = "year", "phone"

WORDS = {
    "ar": {
        "sub": "نتيجة التحاليل", "title": "نتيجة التحاليل",
        "ask_h": "نتيجة تحاليلك جاهزة",
        "ask_year": "عشان نتأكد إنك صاحب النتيجة، اكتب سنة ميلادك (4 أرقام، زي 1985).", "label_year": "سنة الميلاد",
        "ask_phone": "عشان نتأكد إنك صاحب النتيجة، اكتب آخر 4 أرقام من رقم الموبايل اللي سجّلته في المعمل.", "label_phone": "آخر 4 أرقام من الموبايل",
        "open": "افتح النتيجة",
        "wrong": "البيانات دي مش مطابقة للي عندنا. اتأكد وجرّب تاني، أو كلّم المعمل.",
        "format_year": "اكتب سنة ميلادك 4 أرقام (زي 1985).", "format_phone": "اكتب آخر 4 أرقام من الموبايل.",
        "busy": "محاولات كتير. استنى شوية وجرّب تاني، أو كلّم المعمل.",
        "print": "اطبع أو احفظ PDF", "private": "النتيجة دي خاصة بيك. بلاش تشارك اللينك مع حد.",
        "dead_h": "اللينك ده مبقاش شغال", "dead": "ممكن تكون مدته خلصت أو النتيجة اتعدّلت. اطلب من المعمل يبعته تاني.",
        "call": "كلّم المعمل", "whatsapp": "واتساب",
        # the lab's side
        "no_results": "مفيش نتايج في اليوم ده.",
        "no_factor": "مينفعش تبعت النتيجة أونلاين: ملف المريض مفيهوش سنة ميلاد ولا رقم موبايل، والمريض لازم يكتب واحد منهم عشان يفتحها. ضيف واحد منهم في ملف المريض.",
        "no_base_url": "عنوان الموقع مش متظبط (PUBLIC_BASE_URL)؛ ظبّطه الأول عشان اللينك يتبعت.",
        "wa_text_year": "أهلاً، نتيجة تحاليلك من {lab} جاهزة. افتح اللينك واكتب سنة ميلادك. اللينك شغال {days} أيام: {url}",
        "wa_text_phone": "أهلاً، نتيجة تحاليلك من {lab} جاهزة. افتح اللينك واكتب آخر 4 أرقام من موبايلك المسجّل عندنا. اللينك شغال {days} أيام: {url}",
        "phone_only": "ملف المريض ده مفيهوش تاريخ ميلاد، فلينك النتيجة محمي بآخر 4 أرقام من الموبايل بس، ودي مش بتحمي لو الرقم نفسه غلط "
                      "(اللي هيستلم اللينك هو صاحب الرقم). الأحسن تضيف تاريخ الميلاد في ملف المريض الأول.",
    },
    "en": {
        "sub": "Lab results", "title": "Lab results",
        "ask_h": "Your lab results are ready",
        "ask_year": "To make sure the results are yours, type your birth year (4 digits, like 1985).", "label_year": "Birth year",
        "ask_phone": "To make sure the results are yours, type the last 4 digits of the phone number you gave the lab.", "label_phone": "The phone's last 4 digits",
        "open": "Open the results",
        "wrong": "That does not match the details the lab has. Check and try again, or contact the lab.",
        "format_year": "Type your birth year as 4 digits (like 1985).", "format_phone": "Type the last 4 digits of your phone.",
        "busy": "Too many tries. Please wait a while and try again, or contact the lab.",
        "print": "Print or save as PDF", "private": "These results are private to you. Please do not share the link.",
        "dead_h": "This link no longer works", "dead": "It may have expired or the results were updated. Ask the lab to send it again.",
        "call": "Call the lab", "whatsapp": "WhatsApp",
        "no_results": "There are no results on that day.",
        "no_factor": "The results cannot be sent online: the patient's file has no birth date and no phone number, and the patient must type one of them to open the link. Add one of them to the patient's file.",
        "no_base_url": "The site address (PUBLIC_BASE_URL) is not set; set it first so the link can be sent.",
        "wa_text_year": "Hello, your lab results from {lab} are ready. Open the link and type your birth year. The link works for {days} days: {url}",
        "wa_text_phone": "Hello, your lab results from {lab} are ready. Open the link and type the last 4 digits of the phone number we have. The link works for {days} days: {url}",
        "phone_only": "This patient's file has no birth date, so the results link is protected only by the phone's last 4 digits, which does not help "
                      "if the number itself is wrong (whoever receives the link owns that number). Better add the birth date to the file first.",
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


def factor(patient, file=None):
    """(kind, the 4 digits) that open this patient's links, or None.

    The birth year when the file has a birth date, and then only it: the link goes to the phone on file, so a stranger
    who got it through a wrong number owns that phone's last 4 digits. Those are the factor only when there is no
    birth date (and the lab is told what that means)."""

    file = file if file is not None else _file(patient)
    if file is not None and file.date_of_birth:
        return YEAR, f"{file.date_of_birth.year:04d}"
    phone = _digits(patient.phone)
    if len(phone) >= MIN_PHONE_DIGITS:
        return PHONE, phone[-4:]
    return None


def can_share(patient):
    return factor(patient) is not None


def _matches(patient, file, answer):
    found = factor(patient, file)
    return found is not None and hmac.compare_digest(found[1], answer)


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
    # "n": a few random bytes, so every «ابعت تاني» is a new token (with fresh counters) even within the same second
    return signing.dumps({"p": int(patient.pk), "d": day.isoformat(), "f": fingerprint(patient, results, file), "n": secrets.token_hex(4)},
                         salt=SALT, compress=True)


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
    if factor(patient, file) is None:
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
    found = factor(patient, file)
    if found is None:
        raise ValidationError(words["no_factor"])
    token = make_token(patient, day, results, file)
    url = link_url(token, lang)
    if url is None:
        raise ValidationError(words["no_base_url"])
    text = words[f"wa_text_{found[0]}"].format(lab=company_details()["name"], days=days(), url=url)  # never a test name, value or diagnosis
    AuditLog.objects.create(event_type=AuditEventType.EXPORT, actor=user, module="medical", action="send_lab_result_link",
                            object_type="master_data.Customer", object_id=str(patient.pk),
                            after_data={"patient": patient.pk, "day": day.isoformat(), "results": len(results), "days": days()})
    return {"url": url, "whatsapp": whatsapp_link(patient.whatsapp or patient.phone, text), "days": days(), "day": day, "kind": found[0],
            "warning": words["phone_only"] if found[0] == PHONE else ""}


# ---- throttling (the booking and payment pages' pattern: Django's default cache, per process) ----

def _address(request):
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def _key(bucket, who):
    name, limit, window = bucket
    if window is None:  # the token's whole life
        return f"medical-lab:{name}:{who}"
    return f"medical-lab:{name}:{who}:{int(timezone.now().timestamp()) // window}"


def _ttl(bucket):
    window = bucket[2]
    return max_age() + 60 if window is None else window * 2


def _count(bucket, who):
    """Take one slot; the new count (atomic in the cache, so two requests never read the same number)."""

    key = _key(bucket, who)
    if cache.add(key, 1, _ttl(bucket)):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired between the two calls
        cache.add(key, 1, _ttl(bucket))
        return 1


def _give_back(bucket, who):
    try:
        cache.decr(_key(bucket, who))
    except ValueError:  # already gone with its window: nothing to give back
        pass


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _slots(token, request):
    return ((TOKEN_LIFE, _digest(token)), (TOKEN_SHORT, _digest(token)), (ADDRESS_HOUR, _address(request)))


def used_up(token):
    """True once the token had its lifetime of wrong answers (TOKEN_LIFE): it is dead for any new answer."""

    return (cache.get(_key(TOKEN_LIFE, _digest(token))) or 0) >= TOKEN_LIFE[1]


def take(token, request):
    """Count before comparing: take a slot in every bucket, then refuse if any is over its limit.

    Returns "ok" (the slots are held: keep them for a wrong answer, ``give_back`` them for a right one), "dead" (the
    token's lifetime is used up) or "busy" (a window is full). A refused answer gives its slots back, so only answers
    that were actually compared are counted, and N answers at once can never compare more than the limit."""

    taken = []
    for bucket, who in _slots(token, request):
        count = _count(bucket, who)
        taken.append((bucket, who))
        if count > bucket[1]:
            for held in taken:
                _give_back(*held)
            return "dead" if bucket is TOKEN_LIFE else "busy"
    return "ok"


def give_back(token, request):
    for bucket, who in _slots(token, request):
        _give_back(bucket, who)


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
    kind = factor(found.patient, found.file)[0]
    words = _words(lang)
    context = {**_company_context(lang), "switch_url": f"{here}?lang={other}", "action": f"{here}?lang={lang}", "message": message,
               "sheet": sheet, "kind": kind, "ask": words[f"ask_{kind}"], "label": words[f"label_{kind}"]}
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
    kind = factor(found.patient, found.file)[0]
    answer = _digits(request.POST.get("answer"))
    if len(answer) != 4:  # not counted: a typo in the length proves nothing either way
        return _page(request, token, found, lang, words[f"format_{kind}"], status=400)
    outcome = take(token, request)  # the slot is taken before comparing
    if outcome == "dead":
        return _dead(request, lang)
    if outcome == "busy":
        return _page(request, token, found, lang, words["busy"], status=429)
    if not _matches(found.patient, found.file, answer):
        return _page(request, token, found, lang, words["wrong"], status=400)  # the slot stays taken
    give_back(token, request)  # a right answer is not charged
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
    passed = request.method == "GET" and _passed(request, token)
    if not passed and used_up(token):
        return _dead(request, lang)  # its wrong answers are spent: «ask the lab to send it again»; a browser that passed keeps its time
    if request.method == "POST":
        return _answer(request, token, found, lang)
    if not passed:
        return _page(request, token, found, lang)
    AuditLog.objects.create(event_type=AuditEventType.EXPORT, actor=None, module="medical", action="open_lab_result_link",
                            object_type="master_data.Customer", object_id=str(found.patient.pk),
                            after_data={"patient": found.patient.pk, "day": found.day.isoformat()})  # no IP, no device, no token
    return _page(request, token, found, lang, sheet=True)
