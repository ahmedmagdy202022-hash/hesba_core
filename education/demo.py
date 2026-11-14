"""The education demo's own seeds, kept out of the big demo seed so later education tasks do not all edit the same block.

E2 exams (``seed_exams``), then E0's ``seed_showcase``: a finished batch with a certificate (E6), group content (E9) and makeup seats (E8). Every
one goes through the module that owns the rule, as a manager's click would, so each audit row, number and check is the real one. None of it
touches money: the finished batch is free and has no teacher, a seat is never attendance, content is a title and an address.

No seed here makes a guardian link, a pay link, a scan card or a message: a portal link is shown once when it is made, and a pay link is money.
Days are always picked from a group's own class days and from the clock, never fixed, so the demo builds at any hour of any day.
"""

from datetime import date, datetime, timedelta

from django.utils import timezone

from . import attendance, certificates, content, exams, makeup, nursery, services
from .models import Attendance, ContentKind, Enrollment, EnrollmentStatus, GroupContent, MakeupSeat, Presence, SeatStatus, StudyGroup


def seed_exams(groups, owner):
    """Two exams in the first group, with marks: a published monthly exam and an unpublished quiz, one student absent from the quiz.

    Marks go through ``exams.record_marks`` like a teacher's would. Nothing for a nursery (it has no exams)."""

    if nursery.is_nursery() or not groups:
        return []
    today = timezone.localdate()
    group = groups[0]
    plan = (("امتحان الشهر", "monthly", 9, "50", (46, 41, 33, 38, 27)), ("كويز الأسبوع", "quiz", 3, "20", (18, 15, None, 12.5, 9)))
    made = []
    for title, kind, ago, out_of, scores in plan:
        exam = exams.save_exam(group, {"title": title, "kind": kind, "held_on": today - timedelta(days=ago), "out_of": out_of}, owner)
        roster = attendance.roster_on(group, exam.held_on)
        entries = {}
        for row, score in zip(roster, scores):
            entries[row.student_id] = {"absent": True, "note": "غايب بعذر"} if score is None else {"score": str(score)}
        exams.record_marks(exam, entries, owner)
        made.append(exam)
    exams.set_published(made[0], True, owner)  # the monthly exam is released; the quiz is still the teacher's
    return made


# ---- E0: what a visitor finds on the certificate, group-content and makeup screens ----

#: What a centre shares with its first group, by kind of centre: (video, file, online class, a draft kept hidden), plain titles that fit the place.
CONTENT_TITLES = {
    "training": ("تسجيل المحاضرة الأولى من الدورة", "ملف تمارين الأسبوع الأول (PDF)", "حصة المراجعة المباشرة عبر الإنترنت", "مسودة اختبار نهاية الدورة"),
    "languages": ("تسجيل درس المحادثة الأول", "ملف مفردات الوحدة الأولى (PDF)", "حصة محادثة مباشرة عبر الإنترنت", "مسودة اختبار نهاية الوحدة"),
    "nursery": ("تسجيل حفل نهاية الفصل الدراسي", "قائمة الوجبات الأسبوعية (PDF)", "اجتماع أولياء الأمور عن بُعد", "مسودة جدول الأنشطة للأسبوع القادم"),
    "tutoring": ("شرح الوحدة الأولى (تسجيل الحصة)", "مذكرة المراجعة الأسبوعية (PDF)", "حصة مراجعة مباشرة قبل الامتحان", "مسودة نموذج امتحان تجريبي"),
}
#: Examples on well-known hosts that are obviously placeholders (they pass ``content.clean_url``).
CONTENT_URLS = ("https://www.youtube.com/watch?v=EXAMPLE", "https://drive.google.com/file/d/EXAMPLE/view", "https://meet.google.com/abc-defg-hij",
                "https://drive.google.com/file/d/EXAMPLE-DRAFT/view")
ONLINE_NOTE = "الدخول قبل الموعد بخمس دقائق."
BATCH_NAME = "دفعة المنح التدريبية"
BATCH_DAYS = "sun,tue"
BATCH_WEEKS = 4
SEAT_NOTE = "تعويض حصة فائتة في المجموعة الأصلية."


def _kind_of_centre():
    from entities.current import effective_activity

    sub = (effective_activity() or ("", ""))[1]
    return sub if sub in CONTENT_TITLES else "tutoring"


def next_class_day(group, after, within=14):
    """The first day from ``after`` (inclusive) on which the group meets, within ``within`` days; None when it has none."""

    return next((day for day in (after + timedelta(days=n) for n in range(within)) if attendance.meets_on(group, day)), None)


def seed_showcase(groups, students, owner):
    """E0: a finished batch with one certificate, content in the first group, and makeup seats. Returns what it made, by name (empty parts left out)."""

    made = {}
    for name, seed in (("certificate", seed_certificate), ("content", seed_content), ("makeup", seed_makeup)):
        result = seed(groups, students, owner)
        if result:
            made[name] = result
    return made


def seed_content(groups, students, owner):
    """E9: in the first group, a recorded video, a file, an online class a few days ahead and a draft that stays hidden from guardians.

    The online class is on a day the group meets, at its start time, at least two days ahead, so no hour of today can put it in the past."""

    if not groups or GroupContent.objects.filter(group=groups[0]).exists():
        return []
    group = groups[0]
    video, file, online, draft = CONTENT_TITLES[_kind_of_centre()]
    day = next_class_day(group, timezone.localdate() + timedelta(days=2))
    starts_at = f"{day:%Y-%m-%d}T{group.start_time:%H:%M}" if day and group.start_time else (f"{day:%Y-%m-%d}T18:00" if day else "")
    items = [content.save_content(group, {"kind": ContentKind.VIDEO, "title": video, "url": CONTENT_URLS[0]}, owner),
             content.save_content(group, {"kind": ContentKind.FILE, "title": file, "url": CONTENT_URLS[1]}, owner),
             content.save_content(group, {"kind": ContentKind.ONLINE, "title": online, "url": CONTENT_URLS[2], "starts_at": starts_at, "note": ONLINE_NOTE}, owner)]
    hidden = content.save_content(group, {"kind": ContentKind.FILE, "title": draft, "url": CONTENT_URLS[3]}, owner)
    content.set_published(hidden, False, owner)
    return items + [hidden]


def _pair(groups):
    """(home, target): the first group and another running group of the same course, which are what a makeup class needs; None without them."""

    home = groups[0]
    target = next((group for group in groups[1:] if group.course_id == home.course_id and group.active and group.capacity), None)
    return (home, target) if target else None


def _absentee(roster, home, before=None):
    """The first student of ``roster`` with a class of ``home`` they were marked absent in (before ``before``, if given), and that day: the class a
    makeup seat makes up. Nobody absent: the first student and no day (the seat's «missed class» is optional)."""

    for student in roster:
        rows = Attendance.objects.filter(student=student, session__group=home, presence=Presence.ABSENT)
        if before is not None:
            rows = rows.filter(session__date__lt=before)
        mark = rows.select_related("session").order_by("-session__date").first()
        if mark:
            return student, mark.session.date
    return roster[0], None


def seed_makeup(groups, students, owner):
    """E8: one seat booked a few days ahead for a student of the first group in the second, and one already attended in the past week.

    The past one is booked with ``today=`` set to its own class day (the service's own parameter, since a real centre books it that day) and then
    marked attended; a seat never writes attendance, so the register, the fees and the teacher's pay are exactly as they were."""

    pair = _pair(groups) if groups else None
    if pair is None or MakeupSeat.objects.filter(group=pair[1]).exists():
        return []
    home, target = pair
    in_target = Enrollment.objects.filter(group=target, status=EnrollmentStatus.ACTIVE).values("student_id")
    roster = [row.student for row in Enrollment.objects.filter(group=home, status=EnrollmentStatus.ACTIVE).exclude(student_id__in=in_target)
              .select_related("student").order_by("student_id")]
    if len(roster) < 2:
        return []
    today = timezone.localdate()
    seats = []
    ahead = next_class_day(target, today + timedelta(days=2))
    if ahead:
        student, missed = _absentee(roster, home)
        seats.append(makeup.book(student, home, target, ahead, owner, missed_on=missed, note=SEAT_NOTE))
        roster = [other for other in roster if other.pk != student.pk]
    past = next((today - timedelta(days=n) for n in range(1, 8) if attendance.meets_on(target, today - timedelta(days=n))), None)
    if past:
        student, missed = _absentee(roster, home, before=past)
        seat = makeup.book(student, home, target, past, owner, missed_on=missed, note=SEAT_NOTE, today=past)
        seats.append(makeup.mark(seat, SeatStatus.ATTENDED, owner))
    return seats


def seed_certificate(groups, students, owner):
    """E6: a free batch that ended before last month, two students on it, and a certificate issued to the one with the best attendance.

    Only where certificates exist (training and language centres). Why a finished batch and not a stopped enrolment: the batch is free and has no
    teacher, so it adds no fee and no pay, and it ended more than a month ago, so no month's report or the dashboard's 30-day attendance reads it.
    Its classes are taken through ``attendance.take`` like any teacher's, its enrolments are stopped at the end of its term (so both are ready for a
    certificate), and the batch is closed. The guardian still owes this month's fees, so the manager acknowledges that warning, as the screen asks."""

    if not groups or not certificates.enabled() or len(students) < 2 or StudyGroup.objects.filter(name=BATCH_NAME).exists():
        return None
    today = timezone.localdate()
    level = StudyGroup.objects.values_list("level", flat=True).get(pk=groups[0].pk)  # read fresh: a language centre's levels were set after the groups were made
    this_month = today.replace(day=1)
    ends_on = min((this_month - timedelta(days=1)).replace(day=1) - timedelta(days=1), today - timedelta(days=30))
    starts_on = ends_on - timedelta(weeks=BATCH_WEEKS) + timedelta(days=1)
    data = {"course": groups[0].course, "name": BATCH_NAME, "days": BATCH_DAYS, "capacity": "0", "duration_minutes": "120", "fee": "0",
            "starts_on": starts_on, "ends_on": ends_on,
            "level": str(level) if level else ""}
    batch = services.save_group(data, owner)
    best, other = students[-1], students[-2]
    rows = {student: services.enroll(student, batch, owner, start_date=starts_on) for student in (best, other)}
    classes = [starts_on + timedelta(days=n) for n in range((ends_on - starts_on).days + 1) if attendance.meets_on(batch, starts_on + timedelta(days=n))]
    for number, day in enumerate(classes):
        marks = {}
        if number == 2:
            marks[str(other.pk)] = Presence.ABSENT  # one class missed by the second student: still well above the 75% a certificate warns under
        elif number == 5:
            marks[str(other.pk)] = Presence.LATE
        attendance.take(batch, owner, day, marks)
    for enrollment in rows.values():
        services.stop(enrollment, owner, end_date=ends_on)
    services.save_group({**data, "active_present": "1"}, owner, batch)  # a finished batch is closed: nobody can be enrolled in it any more
    certificate, created = certificates.issue(rows[best], owner, grade_text="امتياز", acknowledged=True)
    return certificate if created else None


# ---- education video findings: a believable demo (names, ages, mornings and finished classes) ----

#: Ages (years) of the seven demo students, in the order the seed creates them, by kind of centre: a nursery's children are 3 to 5, a tutoring
#: centre's pupils are of the third preparatory year (14 to 15), and training and language centres teach adults. Brothers and sisters differ.
AGES = {
    "nursery": (4, 3, 5, 4, 3, 5, 4),
    "tutoring_center": (15, 14, 14, 15, 14, 15, 14),
    "private_tutor": (15, 14, 14, 15, 14, 15, 14),
    "training": (24, 21, 27, 22, 30, 25, 28),
    "languages": (24, 21, 27, 22, 30, 25, 28),
}
#: Birthdays (month, day), never the 29th of February, so the age is exact on every day of the year.
BIRTHDAYS = ((3, 14), (9, 2), (6, 21), (11, 5), (1, 17), (7, 30), (12, 9))


def birth_date(sub_activity, n, today):
    """The birth date of the n-th demo student of a centre: a whole number of years old today, as old as the centre's students are."""

    ages = AGES.get(sub_activity) or AGES["tutoring_center"]
    month, day = BIRTHDAYS[n % len(BIRTHDAYS)]
    years = ages[n % len(ages)] + (1 if (month, day) > (today.month, today.day) else 0)
    return date(today.year - years, month, day)


#: The other parent of each nursery family, by the payer's phone: a person with their own full name (the child's second name is the father's).
OTHER_PARENT = {
    "01001234501": ("سمر إبراهيم", "الأم"),
    "01101234502": ("خالد عبد الغفار", "الأب"),
    "01201234503": ("داليا حسين", "الأم"),
    "01501234504": ("أحمد صلاح", "الأب"),
    "01001234505": ("رانيا عبد الحليم", "الأم"),
}
FALLBACK_PICKUP = ("فاطمة السيد", "الجدة")


def other_pickup_person(student, n):
    """(full name, relation) of the second person allowed to pick the child up."""

    return OTHER_PARENT.get(student.payer.phone, FALLBACK_PICKUP)


#: Local arrival times of the morning's children, in order (07:20 to 08:40).
ARRIVALS = ((7, 20), (7, 35), (7, 50), (8, 5), (8, 20), (8, 40))


def arrival_time(n, count, now):
    """When the n-th of ``count`` arriving children was checked in this morning: the n-th slot of 07:20 to 08:40 once the whole morning has passed.
    Built earlier than that, the arrivals are spread evenly over the stretch up to now (the last 80 minutes, never before today's midnight), so
    they are always in the past, always today, and still not one minute for everybody."""

    local = timezone.localtime(now)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = [midnight.replace(hour=hour, minute=minute) for hour, minute in ARRIVALS]
    if len(slots) >= count and slots[count - 1] <= now:
        return slots[n]
    first = max(midnight, min(slots[0], now - timedelta(minutes=80)))
    return (first + (now - first) * n / max(count - 1, 1)).replace(second=0, microsecond=0)
