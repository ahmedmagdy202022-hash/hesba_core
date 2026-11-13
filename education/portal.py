"""E3 (HG-101): the guardian portal. A paying guardian opens one link, answers one question, and reads what the centre keeps about
their children: groups and times, the content the centre shared with those groups (E9, HG-104), attendance, the exam marks the centre **published**, nursery days,
and the fees still owed.

This is the education twin of ``medical.public`` (HG-085), defence for defence, with one table because a portal lives for weeks and the
centre must be able to switch it off at once (a custody dispute, a wrong number, a lost phone):

- ``GuardianLink`` is the key. The address carries a token signed with the SECRET_KEY and its own salt (never ``printing.share``'s, the
  lab's or the certificate's), holding the row's id, its random ``nonce`` and a keyed fingerprint of the payer's phone numbers. The row
  decides: revoked, past ``EDU_PORTAL_DAYS`` (from the row's ``created_at``, not only from the token's own timestamp), a wrong nonce,
  an archived payer, a changed phone, or no child left to show all give the one plain page. A new link revokes the payer's older ones
  (and the database allows one live link per payer), so «ابعت تاني» replaces, and one tap on «إلغاء الرابط» ends it.
- The second factor is the full birth date (day, month, year) of any of the payer's active children in the link's entity; only when none
  of them has a birth date, the last 4 digits of the payer's phone (which protect nothing if the link went to a wrong number: the centre
  is told so). Before the answer the page names only the centre. A birth year alone (about 15 plausible values) was judged too weak.
- A passed factor is remembered in this browser by a short signed cookie bound to this link (never the Django session). Answers are
  counted before they are compared (so N parallel answers cannot slip past the limit) and a right answer gives its slot back. The
  lifetime limit, 8 wrong answers in the link's whole life, is a column on the row (``wrong_answers``, raised by one atomic guarded
  UPDATE), so it survives a restart and is shared by every worker; the short windows, 5 per 15 minutes per link and 50 per hour per
  address, and the 60 page loads per minute per address are in Django's default cache (per process, as the booking and lab pages).
- Every failure shows the same page with the same status. Every response is ``no-store``, ``noindex`` and ``same-origin``.
- The portal only reads. A visit writes ``last_opened_at`` and an audit row with no actor and no address; an answer writes only the
  ``wrong_answers`` counter.

Entity: an anonymous request has no current entity, so every read below runs inside ``working_in(link.entity)``; the children, groups,
exams, shared group content, invoices and the dues figure are then the link's entity's alone.

What the portal never shows: the student's notes, school or birth date; the payer's phone; a pick-up person's ID number or phone; any
note on an attendance mark, an exam, a mark or a day record; teacher pay; another payer's child; an unpublished exam; a hidden or removed piece of group content, an old online class, and content of a group the child has left; the QR code
(a student code opens nothing here).
"""

import hashlib
import hmac
import json
import secrets
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import salted_hmac

from audit.models import AuditEventType, AuditLog
from entities.current import working_in

from . import attendance as register
from . import content as shared_content
from . import exams, services
from .models import Attendance, DayRecord, Enrollment, EnrollmentStatus, Exam, ExamMark, GuardianLink, Presence, SessionStatus, Student, StudyGroup

SALT = "hesba.education.portal.v1"
FINGERPRINT_SALT = "hesba.education.portal.fingerprint.v1"
PASS_SALT = "hesba.education.portal.pass.v1"
PASS_SECONDS = 30 * 60  # how long a passed second factor is remembered in this browser
COOKIE_PREFIX = "hs_edu_"
MIN_PHONE_DIGITS = 7  # fewer digits is not a phone number; its last 4 would be a guess, not a factor
ATTENDANCE_DAYS = 30
MARKS_DAYS = 365  # published exams of the last year make the report card
NURSERY_DAYS = 14
ZERO = Decimal("0.00")

# Wrong answers: per link is the real limit; per address is generous because behind the host's proxy many visitors can share one
# address (HG-070). (name, limit, window in seconds). TOKEN_LIFE lives in the row (``wrong_answers``), not in the cache: its window is None.
TOKEN_LIFE = ("tokenlife", 8, None)  # then the link is dead: «the centre sends a new one»
TOKEN_SHORT = ("token15", 5, 15 * 60)
ADDRESS_HOUR = ("ip", 50, 3600)
PAGE_MINUTE = ("page", 60, 60)  # any request to the page, per address: blunts a flood
DATE, PHONE = "date", "phone"
FIRST_YEAR = 1900  # a birth date older than this is a typo, not a date

# What a guardian reads. Everything here is held to the customer-voice rules (reports/tests_customer_voice.py): «رابط», «حضرتك»,
# «تواصل مع المركز», nouns for attendance (a boy and a girl read the same line), no Arabic line starts or ends with an English word.
WORDS = {
    "ar": {
        "sub": "بوابة ولي الأمر", "title": "بوابة ولي الأمر", "ask_h": "أهلاً بحضرتك",
        "ask_date": "عشان نتأكد إن حضرتك ولي الأمر، اكتب تاريخ ميلاد أحد الأبناء المسجّلين عندنا (اليوم والشهر والسنة بالأرقام).", "label_date": "تاريخ الميلاد",
        "label_day": "اليوم", "label_month": "الشهر", "label_year": "السنة",
        "ask_phone": "عشان نتأكد إن حضرتك ولي الأمر، اكتب آخر 4 أرقام من رقم الموبايل المسجّل عندنا.", "label_phone": "آخر 4 أرقام من الموبايل",
        "open": "افتح البوابة", "wrong": "البيانات دي مش مطابقة للي عندنا. اتأكد وجرّب تاني، أو تواصل مع المركز.",
        "format_date": "اكتب تاريخ ميلاد حقيقي: اليوم والشهر والسنة بالأرقام.", "format_phone": "اكتب آخر 4 أرقام من الموبايل.",
        "busy": "محاولات كتير متتالية. جرّب تاني بعد دقايق، أو تواصل مع المركز.",
        "private": "الصفحة دي خاصة بحضرتك وفيها بيانات الأبناء، فياريت ماتشاركش الرابط مع حد.",
        "dead_h": "الرابط ده مبقاش شغال", "dead": "ممكن تكون صلاحيته انتهت أو المركز وقّفه. تواصل مع المركز عشان نبعت لحضرتك رابط جديد.",
        "call": "اتصل بالمركز", "whatsapp": "واتساب", "contact_h": "لأي استفسار", "contact_p": "تواصل مع المركز في أي وقت.",
        "hello": "أهلاً بحضرتك", "hello_p": "ده كل اللي عند المركز عن الأبناء: المواعيد والحضور والدرجات المنشورة والمصروفات.",
        "print": "اطبع الصفحة أو احفظها", "children": "الأبناء", "child_n": "ابن أو ابنة",
        "groups_h": "المجموعات والمواعيد", "teacher": "المدرس", "no_groups": "مفيش مجموعات شغالة دلوقتي.",
        "attendance_h": "الحضور في آخر 30 يوم", "rate": "نسبة الحضور", "held": "حصص اتعملت", "absent_days": "أيام الغياب", "late_days": "أيام التأخير",
        "none_absent": "مفيش غياب ولا تأخير في الفترة دي.", "no_classes": "لسه مفيش حصص متسجّلة في الفترة دي.",
        "marks_h": "الدرجات وتقرير الأداء", "marks_note": "هنا الدرجات اللي المركز نشرها بس.", "mark": "الدرجة", "percent": "النسبة", "average": "المتوسط",
        "exam_absent": "غياب عن الامتحان", "no_marks": "لسه مفيش درجات منشورة.", "exam_kinds": {"quiz": "اختبار قصير", "monthly": "امتحان شهري", "midterm": "امتحان نصف الترم", "final": "امتحان نهائي", "other": "امتحان"},
        "content_h": "محتوى المجموعة", "content_note": "الروابط اللي المركز شاركها مع المجموعة. بتفتح في صفحة تانية، ولو رابط مش شغال تواصل مع المركز.",
        "no_content": "لسه مفيش محتوى مشترك.", "content_kinds": {"video": "فيديو مسجّل", "file": "ملف", "online": "حصة أونلاين"},
        "watch": "شاهد الفيديو", "open_file": "افتح الملف", "join": "ادخل الحصة", "class_at": "ميعاد الحصة",
        "nursery_h": "أيام الحضانة", "arrived": "وقت الوصول", "left": "وقت المغادرة", "picked": "الاستلام", "not_left": "—",
        "fees_h": "المصروفات", "owed": "إجمالي المستحق على الحساب", "open_invoices": "الفواتير المفتوحة", "invoice": "فاتورة رقم", "remaining": "الباقي",
        "pay": "ادفع", "none_owed": "مفيش مستحقات على الحساب. شكرًا لحضرتك.", "other_dues": "فيه كمان مستحقات تانية على الحساب. تواصل مع المركز لو حضرتك محتاج التفاصيل.",
        "pay_hint": "هتدفع على صفحة الدفع الآمنة، وبنسجّل الدفع أول ما يتأكد.", "for_students": "عن", "month": "شهر",
        "wa_text_date": "أهلاً بحضرتك، دي بوابة ولي الأمر من {company}. افتح الرابط واكتب تاريخ ميلاد أحد الأبناء. الرابط صالح لمدة {days} يوم:\n{url}",
        "wa_text_phone": "أهلاً بحضرتك، دي بوابة ولي الأمر من {company}. افتح الرابط واكتب آخر 4 أرقام من الموبايل المسجّل عندنا. الرابط صالح لمدة {days} يوم:\n{url}",
    },
    "en": {
        "sub": "Guardian portal", "title": "Guardian portal", "ask_h": "Welcome",
        "ask_date": "To make sure you are the guardian, type the birth date of one of the children registered with us (day, month and year, in digits).", "label_date": "Birth date",
        "label_day": "Day", "label_month": "Month", "label_year": "Year",
        "ask_phone": "To make sure you are the guardian, type the last 4 digits of the mobile number we have on file.", "label_phone": "The mobile's last 4 digits",
        "open": "Open the portal", "wrong": "That does not match the details we have. Check and try again, or contact the centre.",
        "format_date": "Type a real birth date: the day, the month and the year, in digits.", "format_phone": "Type the last 4 digits of the mobile.",
        "busy": "Too many tries in a row. Please wait a few minutes and try again, or contact the centre.",
        "private": "This page is private to you and shows your children's details. Please do not share the link.",
        "dead_h": "This link no longer works", "dead": "It may have expired or the centre switched it off. Contact the centre and we will send you a new link.",
        "call": "Call the centre", "whatsapp": "WhatsApp", "contact_h": "Need anything?", "contact_p": "Contact the centre any time.",
        "hello": "Welcome", "hello_p": "Everything the centre keeps about your children: schedules, attendance, published marks and fees.",
        "print": "Print or save this page", "children": "Children", "child_n": "child",
        "groups_h": "Groups and times", "teacher": "Teacher", "no_groups": "No active groups right now.",
        "attendance_h": "Attendance in the last 30 days", "rate": "Attendance rate", "held": "Classes held", "absent_days": "Days absent", "late_days": "Days late",
        "none_absent": "No absence or late arrival in this period.", "no_classes": "No classes recorded in this period yet.",
        "marks_h": "Marks and progress report", "marks_note": "Only the marks the centre has published are shown here.", "mark": "Mark", "percent": "Percent", "average": "Average",
        "exam_absent": "Did not sit the exam", "no_marks": "No published marks yet.", "exam_kinds": {"quiz": "Quiz", "monthly": "Monthly exam", "midterm": "Midterm", "final": "Final exam", "other": "Exam"},
        "content_h": "Group content", "content_note": "The links the centre shared with your child's groups. They open in another page; if a link does not work, contact the centre.",
        "no_content": "Nothing has been shared yet.", "content_kinds": {"video": "Recorded video", "file": "File", "online": "Online class"},
        "watch": "Watch the video", "open_file": "Open the file", "join": "Join the class", "class_at": "Class time",
        "nursery_h": "Nursery days", "arrived": "Arrived", "left": "Left", "picked": "Picked up by", "not_left": "—",
        "fees_h": "Fees", "owed": "Total due on the account", "open_invoices": "Open invoices", "invoice": "Invoice", "remaining": "Remaining",
        "pay": "Pay", "none_owed": "Nothing is due on the account. Thank you.", "other_dues": "There are other amounts due on the account. Contact the centre for the details.",
        "pay_hint": "You pay on the secure payment page, and we record the payment as soon as it is confirmed.", "for_students": "for", "month": "Month",
        "wa_text_date": "Hello, this is the guardian portal from {company}. Open the link and type the birth date of one of the children. The link works for {days} days:\n{url}",
        "wa_text_phone": "Hello, this is the guardian portal from {company}. Open the link and type the last 4 digits of the mobile number we have on file. The link works for {days} days:\n{url}",
    },
}

# What the centre's staff read when making a link (not shown to guardians).
MESSAGES = {
    "ar": {"forbidden": "بوابة ولي الأمر لإدارة المركز بس.", "inactive": "الطالب ده موقوف، فمفيش بوابة ليه.",
           "no_factor": "مينفعش تبعت البوابة: مفيش للأبناء تاريخ ميلاد، وموبايل ولي الأمر فاضي أو قصير. ولي الأمر لازم يكتب واحد منهم عشان يفتحها. ضيف تاريخ ميلاد أحد الأبناء أو رقم الموبايل.",
           "no_base_url": "عنوان الموقع (PUBLIC_BASE_URL) مش متظبط؛ ظبّطه الأول عشان الرابط يتبعت.",
           "phone_only": "مفيش للأبناء تاريخ ميلاد، فالرابط محمي بآخر 4 أرقام من موبايل ولي الأمر بس، ودي مش بتحمي لو الرقم نفسه غلط "
                         "(اللي هيستلم الرابط هو صاحب الرقم). الأحسن تضيف تاريخ ميلاد أحد الأبناء الأول.",
           "revoked": "الرابط ده اتلغى بالفعل.", "unknown": "الرابط ده مش موجود."},
    "en": {"forbidden": "The guardian portal is for the centre's managers only.", "inactive": "This student is inactive, so there is no portal for them.",
           "no_factor": "The portal cannot be sent: no child has a birth date, and the guardian's mobile is empty or too short. The guardian must type one of them to open it. Add a child's birth date or the mobile number.",
           "no_base_url": "The site address (PUBLIC_BASE_URL) is not set; set it first so the link can be sent.",
           "phone_only": "No child has a birth date, so the link is protected only by the last 4 digits of the guardian's mobile, which does not help "
                         "if the number itself is wrong (whoever receives the link owns that number). Better add a child's birth date first.",
           "revoked": "This link is already revoked.", "unknown": "This link does not exist."},
}


def _words(lang):
    return WORDS["en" if lang == "en" else "ar"]


def messages(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def days():
    return getattr(settings, "EDU_PORTAL_DAYS", 30)


def max_age():
    return days() * 86400


# ---- who the portal is for, and the second factor ----

def _digits(text):
    table = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    return "".join(ch for ch in str(text or "").translate(table) if ch in "0123456789")


def children(payer):
    """The payer's active children in the entity being worked in (callers wrap this in ``working_in``): name order, then id."""

    return list(services.scoped(Student.objects).filter(payer=payer, active=True).order_by("name", "pk"))


def factor(payer, kids):
    """(kind, [accepted answers]) or None. The full birth dates (ISO, ``YYYY-MM-DD``) of the children that have one; only when none has
    one, the last 4 digits of the payer's phone. A mix (one child with a date, one without) uses the dates alone: the phone is the weaker factor."""

    dates = sorted({kid.date_of_birth.isoformat() for kid in kids if kid.date_of_birth})
    if dates:
        return DATE, dates
    phone = _digits(payer.phone)
    if len(phone) >= MIN_PHONE_DIGITS:
        return PHONE, [phone[-4:]]
    return None


def parse_date(post):
    """The birth date typed in the three boxes (``day``, ``month``, ``year``; Arabic-Indic and Persian digits read) as an ISO string, or
    None when it is not a real calendar date or its year is outside 1900 to this year: a format error, never counted as a wrong answer."""

    day, month, year = (_digits(post.get(name)) for name in ("day", "month", "year"))
    if not (1 <= len(day) <= 2 and 1 <= len(month) <= 2 and len(year) == 4):
        return None
    try:
        found = date(int(year), int(month), int(day))
    except ValueError:
        return None
    return found.isoformat() if FIRST_YEAR <= found.year <= timezone.localdate().year else None


def _matches(found, answer):
    ok = False
    for wanted in found.answers:  # every candidate (every child's date) is compared: no early exit tells which one was close
        ok |= hmac.compare_digest(wanted, answer)
    return ok


def fingerprint(payer):
    """Keyed hash (never a plain one: the token is signed, not encrypted, and a phone number is guessable) of the payer's phone numbers.
    A corrected or changed number kills the old links; «ابعت تاني» makes a fresh one (plan §5: a change of the payer's phone kills the link)."""

    data = json.dumps([payer.pk, _digits(payer.phone), _digits(payer.whatsapp)])
    return salted_hmac(FINGERPRINT_SALT, data, algorithm="sha256").hexdigest()[:32]


# ---- the token ----

def make_token(link):
    return signing.dumps({"l": int(link.pk), "n": link.nonce, "f": fingerprint(link.payer)}, salt=SALT, compress=True)


def _path(token):
    return reverse("guardian_portal", args=[token])


def link_url(token, lang="ar"):
    """Built from settings.PUBLIC_BASE_URL only (the standing rule of ``printing.share``), never from the request's host; None without it."""

    base = getattr(settings, "PUBLIC_BASE_URL", "")
    if not base:
        return None
    return f"{base.rstrip('/')}{_path(token)}?lang={'en' if lang == 'en' else 'ar'}"


def ident(link):
    """What a link's counters and pass cookie are bound to: the row and its nonce, not the token's text (which carries a timestamp, so a
    second signing of the same link would otherwise start with clean counters)."""

    return hashlib.sha256(f"{link.pk}:{link.nonce}".encode()).hexdigest()


def expires_at(link):
    return link.created_at + timedelta(days=days())


def is_live(link, now=None):
    return link.revoked_at is None and (now or timezone.now()) < expires_at(link)


class Found:
    def __init__(self, link, kids, kind, answers):
        self.link, self.payer, self.children, self.kind, self.answers = link, link.payer, kids, kind, answers


def read_token(token):
    """What the link opens, or None for anything that must show nothing (one answer for every failure)."""

    try:
        data = signing.loads(token, salt=SALT, max_age=max_age())
    except signing.BadSignature:  # also SignatureExpired
        return None
    if not isinstance(data, dict):
        return None
    pk, nonce, mark = data.get("l"), data.get("n"), data.get("f")
    if not isinstance(pk, int) or not isinstance(nonce, str) or not isinstance(mark, str):
        return None
    link = GuardianLink.objects.select_related("payer", "entity").filter(pk=pk).first()
    if link is None or not hmac.compare_digest(link.nonce.encode(), nonce.encode()):
        return None
    if not is_live(link) or not link.payer.active:
        return None  # revoked, past its days, or the payer was archived
    if not hmac.compare_digest(fingerprint(link.payer).encode(), mark.encode()):
        return None  # the payer's phone changed since the link was made
    with working_in(link.entity):
        kids = children(link.payer)
    found = factor(link.payer, kids) if kids else None
    if found is None:
        return None  # no child left to show (custody moved), or nothing left the guardian could prove
    return Found(link, kids, *found)


# ---- throttling (the booking, payment and lab pages' pattern: Django's default cache, per process) ----

def _address(request):
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def _key(bucket, who):
    name, limit, window = bucket
    return f"education-portal:{name}:{who}:{int(timezone.now().timestamp()) // window}"


def _ttl(bucket):
    return bucket[2] * 2


def count(bucket, who):
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


def _slots(found, request):
    who = ident(found.link)
    return ((TOKEN_SHORT, who), (ADDRESS_HOUR, _address(request)))


def used_up(found):
    """True once the link had its lifetime of wrong answers (TOKEN_LIFE): it is dead for any new answer. Read from the row."""

    return GuardianLink.objects.filter(pk=found.link.pk, wrong_answers__gte=TOKEN_LIFE[1]).exists()


def _life_take(link):
    """One atomic guarded UPDATE: the answer's slot in the link's lifetime, or False when the limit is already reached. Shared by every worker."""

    return GuardianLink.objects.filter(pk=link.pk, wrong_answers__lt=TOKEN_LIFE[1]).update(wrong_answers=F("wrong_answers") + 1) == 1


def _life_give_back(link):
    GuardianLink.objects.filter(pk=link.pk, wrong_answers__gt=0).update(wrong_answers=F("wrong_answers") - 1)


def take(found, request):
    """Count before comparing: take a slot in the row's lifetime counter, then in every cache window, then refuse if any is over its limit.

    Returns "ok" (the slots are held: keep them for a wrong answer, ``give_back`` them for a right one), "dead" (the link's lifetime of
    wrong answers is used up) or "busy" (a window is full). A refused answer gives its slots back, so only answers that were actually
    compared are counted, and N answers at once can never compare more than the limit."""

    if not _life_take(found.link):
        return "dead"
    taken = []
    for bucket, who in _slots(found, request):
        number = count(bucket, who)
        taken.append((bucket, who))
        if number > bucket[1]:
            for held in taken:
                _give_back(*held)
            _life_give_back(found.link)
            return "busy"
    return "ok"


def give_back(found, request):
    _life_give_back(found.link)
    for bucket, who in _slots(found, request):
        _give_back(bucket, who)


# ---- the pass cookie ----

def cookie_name(found):
    return COOKIE_PREFIX + ident(found.link)[:16]


def passed(request, found):
    value = request.get_signed_cookie(cookie_name(found), default=None, salt=PASS_SALT, max_age=PASS_SECONDS)
    return value is not None and hmac.compare_digest(value.encode(), ident(found.link).encode())


# ---- the centre's side: making, listing and revoking links ----

def can_manage(user):
    return services.can_manage(user)


def scoped_links():
    """The links of the entity being worked in (every entity's when working in the whole group)."""

    from entities.current import current_entity

    links, entity = GuardianLink.objects.all(), current_entity()
    return links if entity is None else links.filter(entity=entity)


def _audit(user, action, link, event, after=None, before=None):
    AuditLog.objects.create(event_type=event, actor=user, module="education", action=action, object_type="education.GuardianLink",
                            object_id=str(link.pk), before_data=before, after_data=after or {})


def preview(student):
    """What a link made now for this student's payer would ask: {"kind", "children"} or {"kind": None}. Read only; nothing is made."""

    from entities.models import Entity

    entity = Entity.objects.filter(pk=services.entity_id_of(student)).first()
    with working_in(entity):
        kids = children(student.payer) if student.active else []
    found = factor(student.payer, kids) if kids else None
    return {"kind": found[0] if found else None, "children": len(kids)}


def make_link(student, user, lang="ar"):
    """The centre's «ابعت بوابة ولي الأمر»: {url, whatsapp, days, kind, warning}. A new link for the student's payer revokes their older ones.

    Whoever manages parties only (``master_data.manage_parties``). The link opens the payer's active children in the *student's* entity."""

    from entities.models import Entity
    from printing.company import company_details
    from printing.share import whatsapp_link

    words, staff = _words(lang), messages(lang)
    if not can_manage(user):
        raise PermissionDenied(staff["forbidden"])
    if not student.active:
        raise ValidationError(staff["inactive"])
    entity = Entity.objects.get(pk=services.entity_id_of(student))
    with transaction.atomic():
        from master_data.models import Customer

        payer = Customer.objects.select_for_update().get(pk=student.payer_id)  # one maker at a time per payer
        with working_in(entity):
            kids = children(payer)
        found = factor(payer, kids) if kids else None
        if found is None:
            raise ValidationError(staff["no_factor"])
        if not getattr(settings, "PUBLIC_BASE_URL", ""):
            raise ValidationError(staff["no_base_url"])
        now = timezone.now()
        for old in GuardianLink.objects.select_for_update().filter(payer=payer, revoked_at__isnull=True):
            old.revoked_at = now
            old.save(update_fields=["revoked_at"])
            _audit(user, "revoke_guardian_link", old, AuditEventType.UPDATE, {"revoked": True, "why": "replaced"}, {"revoked": False})
        link = GuardianLink.objects.create(payer=payer, entity=entity, created_by=user, nonce=secrets.token_urlsafe(32))
        _audit(user, "create_guardian_link", link, AuditEventType.CREATE,
               {"payer": payer.pk, "entity": entity.pk, "children": len(kids), "factor": found[0], "days": days()})
    url = link_url(make_token(link), lang)
    text = words[f"wa_text_{found[0]}"].format(company=company_details()["name"], days=days(), url=url)
    number = payer.whatsapp or payer.phone
    return {"url": url, "whatsapp": whatsapp_link(number, text), "days": days(), "kind": found[0], "link": link,
            "warning": staff["phone_only"] if found[0] == PHONE else ""}


def revoke(link, user, lang="ar"):
    """«إلغاء الرابط»: the link stops opening at once. The row stays, so the audit still names it."""

    staff = messages(lang)
    if not can_manage(user):
        raise PermissionDenied(staff["forbidden"])
    with transaction.atomic():
        row = GuardianLink.objects.select_for_update().filter(pk=link.pk).first()
        if row is None:
            raise ValidationError(staff["unknown"])
        if row.revoked_at is not None:
            raise ValidationError(staff["revoked"])
        row.revoked_at = timezone.now()
        row.save(update_fields=["revoked_at"])
        _audit(user, "revoke_guardian_link", row, AuditEventType.UPDATE, {"revoked": True, "why": "revoked"}, {"revoked": False})
    return row


def _open_links(payer):
    now = timezone.now()
    return [link for link in scoped_links().filter(payer=payer, revoked_at__isnull=True).select_related("entity") if is_live(link, now)]


def live_link_of(payer):
    """The payer's one link that still opens (not revoked, not past its days, answers left) in the entity being worked in, or None."""

    return next((link for link in _open_links(payer) if link.wrong_answers < TOKEN_LIFE[1]), None)


def spent_link_of(payer):
    """The payer's link that is not revoked or expired but has used all its wrong answers: it opens for nobody new. Or None."""

    return next((link for link in _open_links(payer) if link.wrong_answers >= TOKEN_LIFE[1]), None)


def record_open(found):
    """The only thing a guardian's visit writes: the link's last-opened time and an audit row with no actor, no address, no device."""

    GuardianLink.objects.filter(pk=found.link.pk).update(last_opened_at=timezone.now())
    AuditLog.objects.create(event_type=AuditEventType.EXPORT, actor=None, module="education", action="open_guardian_portal",
                            object_type="education.GuardianLink", object_id=str(found.link.pk), after_data={"link": found.link.pk, "payer": found.payer.pk})


# ---- what the guardian reads (every read inside ``working_in(link.entity)``; nothing is written) ----

def _groups(kid, scope, lang):
    rows = (Enrollment.objects.filter(student=kid, status=EnrollmentStatus.ACTIVE, group__in=scope, group__active=True)
            .select_related("group__course", "group__teacher").order_by("group__course__name", "group__name", "pk"))
    return [{"course": row.group.course.name, "name": row.group.name, "schedule": services.schedule_text(row.group, lang),
             "teacher": row.group.teacher.name if row.group.teacher_id else ""} for row in rows]


def _attendance(kid, scope, today):
    first = today - timedelta(days=ATTENDANCE_DAYS - 1)
    recent = Attendance.objects.filter(student=kid, session__date__range=(first, today), session__group__in=scope)
    held = recent.filter(session__status=SessionStatus.HELD)
    flagged = held.filter(presence__in=(Presence.ABSENT, Presence.LATE)).select_related("session__group").order_by("session__date", "pk")
    absent, late = [], []
    for mark in flagged:  # the date and the group only; never the mark's note (a reason is the centre's own)
        (absent if mark.presence == Presence.ABSENT else late).append({"date": mark.session.date, "group": mark.session.group.name})
    return {"rate": register.rate(recent), "held": held.count(), "absent": absent, "late": late, "first": first, "last": today}


def _marks(kid, scope, today, lang):
    """The published exams of the last year this child was on the roster of, with the report card's arithmetic (``exams.percent``, the
    half-up mean of the percentages of the exams sat). Same roster rule as ``exams._student_rows``; unpublished exams never enter."""

    own = Q(group__enrollments__student=kid, group__enrollments__start_date__lte=F("held_on")) & (
        Q(group__enrollments__end_date__isnull=True) | Q(group__enrollments__end_date__gte=F("held_on")))
    sat = Exam.objects.filter(group__in=scope).filter(own, published=True, held_on__gte=today - timedelta(days=MARKS_DAYS), held_on__lte=today)
    sat = list(sat.select_related("group__course").distinct().order_by("-held_on", "-pk"))
    marks = {mark.exam_id: mark for mark in ExamMark.objects.filter(student=kid, exam__in=sat)}
    kinds, rows, exact = _words(lang)["exam_kinds"], [], []
    for exam in sat:
        mark = marks.get(exam.pk)
        if mark is None or (mark.score is None and not mark.absent):
            continue  # not entered yet: nothing to show
        value = None if mark.absent else exams._exact(mark.score, exam.out_of)
        if value is not None:
            exact.append(value)
        rows.append({"title": exam.title, "kind": kinds.get(exam.kind, kinds["other"]), "date": exam.held_on, "group": exam.group.name, "absent": mark.absent,
                     "score": "" if mark.score is None else f"{mark.score.normalize():f}", "out_of": f"{exam.out_of.normalize():f}",
                     "percent": None if value is None else value.quantize(Decimal("0.1"), ROUND_HALF_UP)})
    average = (sum(exact) / len(exact)).quantize(Decimal("0.1"), ROUND_HALF_UP) if exact else None
    return {"rows": rows, "average": average}


def _nursery(kid, today):
    rows = DayRecord.objects.filter(student=kid, day__gte=today - timedelta(days=NURSERY_DAYS - 1), day__lte=today).select_related("picked_by").order_by("-day", "-pk")
    return [{"day": row.day, "arrived": timezone.localtime(row.arrived_at).strftime("%H:%M"),  # never the pick-up person's ID number or phone, nor the day's note
             "left": timezone.localtime(row.left_at).strftime("%H:%M") if row.left_at else "", "picked_by": row.picked_by.name if row.picked_by_id else ""} for row in rows]


def _fees(found, lang):
    """The payer's money in this entity: the dues figure (what the staff dues screen says), and the open posted fee invoices with what
    each still asks, spent against the account the way the pay-link board does (``paylinks.Board.load``): a desk payment lowers the
    account but not an invoice's own ``remaining_due``, so the account's balance is allocated newest invoice first."""

    from parties.services import balances
    from payments import services as pay
    from payments.models import LinkStatus, PaymentLink
    from printing.company import company_details
    from settlements import services as settlements
    from workorders.public import CURRENCY_WORDS

    from . import billing, paylinks
    from .models import FeeCharge

    payer = found.payer
    owed = next((row["total"] for row in billing.dues() if row["party"].pk == payer.pk), ZERO)
    invoices = list(paylinks.fee_invoices().filter(customer=payer, status="posted", remaining_due__gt=0).order_by("-invoice_date", "-pk"))
    applied = settlements.applied(invoices)
    room = max(balances("customer", [payer])[payer.pk], ZERO)
    kids = {kid.pk: kid.name for kid in found.children}
    charges = {}
    for charge in FeeCharge.objects.filter(invoice__in=invoices).select_related("enrollment__student").order_by("pk"):
        entry = charges.setdefault(charge.invoice_id, {"students": [], "month": charge.month})
        name = kids.get(charge.enrollment.student_id)  # this family's own children in this entity only
        if name and name not in entry["students"]:
            entry["students"].append(name)
    live = {}
    if pay.enabled():
        now = timezone.now()
        for link in PaymentLink.objects.filter(invoice__in=invoices, status=LinkStatus.NEW, expires_at__gt=now).select_related("invoice").order_by("pk"):
            if pay.is_payable(link, now):  # read only: no expiry sweep, nothing written
                live[link.invoice_id] = link
    rows = []
    for invoice in invoices:  # newest first
        outstanding = Decimal(invoice.remaining_due) - applied.get(invoice.pk, ZERO)
        ask = min(max(outstanding, ZERO), room)
        if outstanding > 0:
            room -= ask
        if ask <= 0:
            continue
        info = charges.get(invoice.pk, {"students": [], "month": None})
        link = live.get(invoice.pk)
        if link is not None and Decimal(link.amount) > ask:
            link = None  # stale, as on the pay-link board: it asks for more than this invoice still owes after a desk payment
        rows.append({"number": invoice.invoice_number, "date": invoice.invoice_date, "remaining": ask, "month_text": billing.month_label(info["month"], lang) if info["month"] else "", "students": info["students"],
                     "pay_url": f"{reverse('pay_page', args=[pay.sign_token(link)])}?lang={lang}" if link is not None else "", "pay_amount": link.amount if link is not None else None})
    rows.reverse()  # oldest first, the order the aging reads
    currency = company_details()["currency"]
    return {"owed": owed, "rows": rows, "has_pay": any(row["pay_url"] for row in rows), "other": owed > sum((row["remaining"] for row in rows), ZERO), "currency": CURRENCY_WORDS.get(currency, {}).get(lang, currency)}


def _shared(rows, lang):
    """The group content a child may read (``content.for_children`` decides what), with the words for its kind."""

    words = _words(lang)
    kinds = words["content_kinds"]
    return [{**row, "kind_label": kinds[row["kind"]], "button": words["join"] if row["kind"] == "online" else words["open_file"] if row["kind"] == "file" else words["watch"]} for row in rows]


def content(found, lang="ar", today=None, now=None):
    """Everything the guardian reads once the factor is passed: one block per active child, and the payer's fees."""

    today = today or timezone.localdate()
    with working_in(found.link.entity):
        scope = services.scoped(StudyGroup.objects)
        shared = shared_content.for_children(found.children, scope, now or timezone.now())  # reads only: two queries for every child together
        blocks = [{"student": kid, "groups": _groups(kid, scope, lang), "attendance": _attendance(kid, scope, today), "marks": _marks(kid, scope, today, lang),
                   "nursery": _nursery(kid, today), "content": _shared(shared[kid.pk], lang)} for kid in found.children]
        return {"children": blocks, "fees": _fees(found, lang)}
