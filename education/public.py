"""E6: the public page that says whether a completion certificate is genuine (the address the sheet's QR carries).

``/verify/c/<token>/`` opens with no account. The token is signed with its own salt (``education.certificates.SALT``, never
``printing.share``'s: a share link never opens this page and this token never opens a share page), names a certificate's id and number,
and is checked against the row; it cannot be reached from the printed running number.

The page says only what the sheet itself prints: the number, the student's name, the course (with its level), the period and the day of
issue, the grade when the sheet prints one, the issuing business, and whether it stands or was revoked. **Never** a phone, a guardian, a
date of birth, a school, notes, hours, an attendance rate, the group, the teacher, or the reason for a revoke. A revoked certificate
says «revoked» and nothing about why.

Every failure (forged, altered, another salt's token, a number that is not the row's, an unknown id) is the same plain page with the
same status: nothing is shown when in doubt. GET (and HEAD) only; every response, including the errors, carries no-store, noindex and a
same-origin referrer. Requests are counted per address in Django's default cache (per process, as the booking and lab pages do): a flood
of requests, or of misses, gets the plain page with status 429.
"""

from django.core.cache import cache
from django.http import HttpResponseNotAllowed
from django.shortcuts import render
from django.utils import timezone
from django.utils.cache import patch_vary_headers

from . import certificates

# (name, limit, window in seconds). Generous on purpose: behind the host's proxy many visitors can share one address (HG-070).
PAGE_MINUTE = ("page", 60, 60)  # any request to the page
MISS_HOUR = ("miss", 30, 3600)  # requests that found nothing: blunts a guesser

WORDS = {
    "ar": {
        "title": "التحقق من شهادة", "valid_h": "الشهادة دي سارية وصادرة فعلًا من المركز", "valid_p": "البيانات اللي تحت هي نفس المكتوب على الشهادة المطبوعة.",
        "revoked_h": "الشهادة دي ملغاة", "revoked_p": "المركز ألغى الشهادة دي، فمابقتش سارية. لو حضرتك محتاج تستفسر، تواصل مع المركز.",
        "number": "رقم الشهادة", "name": "اسم الطالب", "course": "الكورس", "period": "الفترة", "issued": "تاريخ الإصدار", "grade": "التقدير",
        "business": "الجهة المانحة", "status": "الحالة", "status_valid": "سارية", "status_revoked": "ملغية", "to": "إلى", "level": "المستوى",
        "dead_h": "مفيش شهادة بالرابط ده", "dead_p": "الرابط غير صحيح أو ناقص. لو حضرتك مسحت الرمز من الشهادة، جرّب تمسحه مرة تانية، أو تواصل مع المركز.",
        "busy_h": "عدد المحاولات كبير", "busy_p": "حضرتك جرّبت عدد كبير من المرات في وقت قصير. جرّب تاني بعد شوية، أو تواصل مع المركز.", "call": "اتصل بالمركز", "whatsapp": "واتساب",
    },
    "en": {
        "title": "Certificate verification", "valid_h": "This certificate is genuine and still stands", "valid_p": "The details below match what is printed on the certificate.",
        "revoked_h": "This certificate was revoked", "revoked_p": "The centre revoked this certificate, so it no longer stands. Please contact the centre with any questions.",
        "number": "Certificate number", "name": "Student", "course": "Course", "period": "Period", "issued": "Issued on", "grade": "Grade",
        "business": "Issued by", "status": "Status", "status_valid": "Valid", "status_revoked": "Revoked", "to": "to", "level": "Level",
        "dead_h": "No certificate at this link", "dead_p": "The link is not valid or is incomplete. Check the code printed on the certificate and scan it again, or contact the centre.",
        "busy_h": "Too many tries", "busy_p": "Please wait a while and try again, or contact the centre.", "call": "Call the centre", "whatsapp": "WhatsApp",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" else "ar"


def _private(response):
    """No caching, no indexing and no referrer leaving the page: the address is the certificate's key."""

    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"  # as the share and lab pages: the stricter "no referrer" makes browsers post "Origin: null"
    patch_vary_headers(response, ("Accept",))
    return response


def _address(request):
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def _key(bucket, who):
    name, _, window = bucket
    return f"education-certificate:{name}:{who}:{int(timezone.now().timestamp()) // window}"


def _count(bucket, who):
    """Take one slot; the new count (atomic in the cache, so two requests never read the same number)."""

    key, ttl = _key(bucket, who), bucket[2] * 2
    if cache.add(key, 1, ttl):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired between the two calls
        cache.add(key, 1, ttl)
        return 1


def _company(lang):
    from printing.company import company_details
    from reports.aging import whatsapp_number

    company = company_details()
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "company": company, "phone": company["phone"],
            "whatsapp": whatsapp_number(company["phone"]), "vw": WORDS[lang]}


def _plain(request, lang, busy=False):
    """The one page for every link that must show nothing; it knows no certificate, only the installation's own identity."""

    return _private(render(request, "education/certificate_verify.html", {**_company(lang), "state": "busy" if busy else "dead"}, status=429 if busy else 404))


def page(request, token):
    if request.method not in ("GET", "HEAD"):
        return _private(HttpResponseNotAllowed(["GET", "HEAD"]))
    lang = _lang(request)
    address = _address(request)
    if _count(PAGE_MINUTE, address) > PAGE_MINUTE[1]:
        return _plain(request, lang, busy=True)
    certificate = certificates.read_token(token)
    if certificate is None:
        if _count(MISS_HOUR, address) > MISS_HOUR[1]:
            return _plain(request, lang, busy=True)
        return _plain(request, lang)
    name, level = certificates.course_title(certificate)
    first, last = certificates.period(certificate)
    business = certificates.business(certificate, lang)
    context = {**_company(lang), "state": "revoked" if certificate.revoked else "valid", "certificate": certificate, "student_name": certificate.enrollment.student.name,
               "course": name, "level": level, "first": first, "last": last, "business": business,
               "switch_url": f"{request.path}?lang={'ar' if lang == 'en' else 'en'}"}
    return _private(render(request, "education/certificate_verify.html", context))
