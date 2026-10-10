"""EDU-001: writing students, courses, groups and enrolments, and who may.

Nothing here moves money: a course is billed through its own service item
by the ordinary sales engine (EDU-003 makes the monthly invoices), and every
payment is an ordinary customer payment on the payer's account.
"""

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission

from .models import Course, Enrollment, EnrollmentStatus, FeeBasis, Student, StudyGroup, Weekday

VIEW = "master_data.view_master_data"
MANAGE = "master_data.manage_parties"

MESSAGES = {
    "ar": {
        "name": "اكتب الاسم.", "payer": "اختار مين اللي بيدفع، أو اكتب اسم ولي الأمر، أو علّم إن الطالب بيدفع لنفسه.",
        "payer_name": "اكتب اسم ولي الأمر.", "date": "التاريخ مش مظبوط.", "money": "المبلغ لازم يكون رقم صفر أو أكبر.",
        "course": "اختار الكورس.", "group_name": "اكتب اسم المجموعة.", "days": "اختار أيام من الأسبوع.", "time": "الميعاد مش مظبوط.",
        "duration": "مدة الحصة لازم تكون بين 15 دقيقة و8 ساعات.", "capacity": "السعة لازم تكون رقم صفر أو أكبر.",
        "dates": "تاريخ النهاية لازم يكون بعد البداية.", "teacher_busy": "المدرس {teacher} عنده مجموعة «{group}» في نفس الوقت.",
        "room_busy": "القاعة {room} عليها مجموعة «{group}» في نفس الوقت.", "full": "المجموعة كاملة ({capacity} طالب).",
        "enrolled": "الطالب ده مسجّل في المجموعة دي بالفعل.", "discount": "الخصم لازم يكون بين 0 و100%.",
        "group_closed": "المجموعة دي مقفولة.", "student_inactive": "الطالب ده موقوف.", "stopped": "التسجيل ده متوقف بالفعل.",
        "before_start": "تاريخ الإيقاف لازم يكون بعد بداية التسجيل.",
    },
    "en": {
        "name": "Enter the name.", "payer": "Choose who pays, type the parent's name, or mark that the student pays for themselves.",
        "payer_name": "Enter the parent's name.", "date": "The date is not valid.", "money": "The amount must be zero or more.",
        "course": "Choose the course.", "group_name": "Enter the group's name.", "days": "Choose days of the week.", "time": "The time is not valid.",
        "duration": "A class lasts between 15 minutes and 8 hours.", "capacity": "Capacity must be zero or more.",
        "dates": "The end date must come after the start.", "teacher_busy": "{teacher} already teaches «{group}» at that time.",
        "room_busy": "Room {room} already has «{group}» at that time.", "full": "The group is full ({capacity} students).",
        "enrolled": "This student is already enrolled in this group.", "discount": "The discount must be between 0 and 100%.",
        "group_closed": "This group is closed.", "student_inactive": "This student is inactive.", "stopped": "This enrolment is already stopped.",
        "before_start": "The stop date must come after the enrolment started.",
    },
}

DAY_LABELS = {
    "ar": {"sat": "السبت", "sun": "الأحد", "mon": "الاتنين", "tue": "التلات", "wed": "الأربع", "thu": "الخميس", "fri": "الجمعة"},
    "en": {"sat": "Sat", "sun": "Sun", "mon": "Mon", "tue": "Tue", "wed": "Wed", "thu": "Thu", "fri": "Fri"},
}
BASIS_LABELS = {
    "ar": {"monthly": "شهري", "course": "الكورس كله", "session": "بالحصة"},
    "en": {"monthly": "Monthly", "course": "Whole course", "session": "Per session"},
}


def is_education_install():
    """An education installation, or (ENT-002) working in an education entity of a group."""

    from entities.current import effective_activity

    return effective_activity()[0] == "education"


def can_view(user):
    return user_has_permission(user, VIEW)


def can_manage(user):
    return user_has_permission(user, MANAGE)


def _audit(user, action, obj, after, event=AuditEventType.CREATE):
    AuditLog.objects.create(event_type=event, actor=user, module="education", action=action, object_type=f"education.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data={}, after_data=after)


def _text(data, key, limit):
    return (data.get(key) or "").strip()[:limit]


def _date(raw, words, required=False):
    if raw in (None, "") and not required:
        return None
    if hasattr(raw, "year"):
        return raw
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(words["date"])


def _money(raw, words, blank=None):
    if raw in (None, ""):
        return blank
    try:
        value = Decimal(str(raw).strip().replace(",", ""))
    except InvalidOperation:
        raise ValidationError(words["money"])
    if value < 0:
        raise ValidationError(words["money"])
    return value.quantize(Decimal("0.01"))


def _next(model, field, prefix):
    from config.numbering import next_in_series

    return next_in_series(model, field, prefix)


def _create_numbered(model, field, prefix, **values):
    """Create with the next free number; a number taken by a save at the same moment moves on to the next."""

    for attempt in range(5):
        number = _next(model, field, prefix)
        try:
            with transaction.atomic():
                return model.objects.create(**{field: number}, **values)
        except IntegrityError:
            if attempt == 4 or not model.objects.filter(**{field: number}).exists():
                raise


# ---- students ----

def _payer(data, name, phone, user, words):
    """The account the student's fees go on: a chosen customer, a new parent, or the student themselves."""

    from sales.pos_customers import quick_add_customer

    chosen = data.get("payer")
    if chosen is not None and not isinstance(chosen, str):
        return chosen
    if data.get("self_pays") in ("1", "on", True):
        return quick_add_customer(name, phone, user)[0]
    parent = _text(data, "payer_name", 255)
    if parent:
        return quick_add_customer(parent, _text(data, "payer_phone", 50), user)[0]
    raise ValidationError(words["payer"])


@transaction.atomic
def save_student(data, user, student=None, lang="ar"):
    words = MESSAGES[lang]
    name = _text(data, "name", 255)
    if not name:
        raise ValidationError(words["name"])
    phone = _text(data, "phone", 50)
    values = {
        "name": name, "phone": phone, "date_of_birth": _date(data.get("date_of_birth"), words), "stage": _text(data, "stage", 80),
        "school": _text(data, "school", 120), "relation": _text(data, "relation", 40), "notes": (data.get("notes") or "").strip(),
    }
    if student is None:
        values["payer"] = _payer(data, name, phone, user, words)
        student = _create_numbered(Student, "code", "ST-", created_by=user, **values)
        _audit(user, "create_student", student, {"code": student.code, "name": name, "payer": student.payer.customer_code})
        return student
    if data.get("payer") is not None and not isinstance(data.get("payer"), str):
        values["payer"] = data["payer"]
    if "active_present" in data:  # the edit form says the box was shown, so an unticked box means inactive
        values["active"] = data.get("active") in ("1", "on", True)
    for key, value in values.items():
        setattr(student, key, value)
    student.save()
    _audit(user, "edit_student", student, {"code": student.code, "name": name, "active": student.active}, AuditEventType.UPDATE)
    return student


# ---- courses and groups ----

def _course_item(name, fee):
    from master_data.models import Item

    return _create_numbered(Item, "item_code", "CRS-", item_name=name[:255], default_sale_price=fee, is_stock_tracked=False, unit="اشتراك")


@transaction.atomic
def save_course(data, user, course=None, lang="ar"):
    words = MESSAGES[lang]
    name = _text(data, "name", 160)
    if not name:
        raise ValidationError(words["name"])
    fee = _money(data.get("fee"), words, Decimal("0"))
    basis = data.get("basis") if data.get("basis") in FeeBasis.values else FeeBasis.MONTHLY
    description = _text(data, "description", 255)
    if course is None:
        # An existing service the business already sells can become the course; otherwise the course gets its own.
        item = data.get("item") if hasattr(data.get("item"), "item_code") and not hasattr(data.get("item"), "course") else _course_item(name, fee)
        course = Course.objects.create(name=name, fee=fee, basis=basis, description=description, item=item)
        _audit(user, "create_course", course, {"name": name, "fee": str(fee), "basis": basis, "item": course.item.item_code})
        return course
    course.name, course.fee, course.basis, course.description = name, fee, basis, description
    course.active = data.get("active") in ("1", "on", True) if "active_present" in data else course.active
    course.save()
    course.item.item_name, course.item.default_sale_price = name[:255], fee  # the invoice line reads the course's own name and price
    course.item.save(update_fields=["item_name", "default_sale_price", "updated_at"])
    _audit(user, "edit_course", course, {"name": name, "fee": str(fee), "basis": basis}, AuditEventType.UPDATE)
    return course


def _time(raw, words):
    if raw in (None, ""):
        return None
    if hasattr(raw, "hour"):
        return raw
    try:
        return datetime.strptime(str(raw).strip()[:5], "%H:%M").time()
    except ValueError:
        raise ValidationError(words["time"])


def _span(group):
    start = datetime.combine(timezone.localdate(), group.start_time)
    return start, start + timedelta(minutes=group.duration_minutes)


def clash(group, lang="ar"):
    """The first active group sharing a day and an overlapping time with this one's teacher or room, as a message."""

    words = MESSAGES[lang]
    if not group.start_time or not group.weekdays:
        return ""
    start, end = _span(group)
    others = StudyGroup.objects.filter(active=True, start_time__isnull=False).exclude(pk=group.pk).select_related("teacher")
    for other in others:
        if not set(other.weekdays) & set(group.weekdays):
            continue
        other_start, other_end = _span(other)
        if not (start < other_end and other_start < end):
            continue
        if group.teacher_id and other.teacher_id == group.teacher_id:
            return words["teacher_busy"].format(teacher=group.teacher.name, group=other.name)
        if group.room and other.room.strip().lower() == group.room.strip().lower():
            return words["room_busy"].format(room=group.room, group=other.name)
    return ""


@transaction.atomic
def save_group(data, user, group=None, lang="ar"):
    words = MESSAGES[lang]
    course = data.get("course")
    if not isinstance(course, Course):
        raise ValidationError(words["course"])
    name = _text(data, "name", 160)
    if not name:
        raise ValidationError(words["group_name"])
    raw_days = data.getlist("days") if hasattr(data, "getlist") else data.get("days") or []
    if isinstance(raw_days, str):
        raw_days = raw_days.split(",")
    days = [day for day in Weekday.values if day in raw_days]
    try:
        capacity = int(str(data.get("capacity") or "0").strip())
        duration = int(str(data.get("duration_minutes") or "60").strip())
    except ValueError:
        raise ValidationError(words["capacity"])
    if capacity < 0:
        raise ValidationError(words["capacity"])
    if not 15 <= duration <= 480:
        raise ValidationError(words["duration"])
    starts_on, ends_on = _date(data.get("starts_on"), words), _date(data.get("ends_on"), words)
    if starts_on and ends_on and ends_on < starts_on:
        raise ValidationError(words["dates"])
    teacher = data.get("teacher")
    if isinstance(teacher, str) or not teacher:
        teacher = None
    values = {
        "course": course, "name": name, "teacher": teacher, "room": _text(data, "room", 60), "capacity": capacity, "days": ",".join(days),
        "start_time": _time(data.get("start_time"), words), "duration_minutes": duration, "fee": _money(data.get("fee"), words),
        "starts_on": starts_on, "ends_on": ends_on,
    }
    creating = group is None
    if creating:
        group = StudyGroup(**values)
    else:
        for key, value in values.items():
            setattr(group, key, value)
        if "active_present" in data:
            group.active = data.get("active") in ("1", "on", True)
    problem = clash(group, lang) if group.active else ""
    if problem:
        raise ValidationError(problem)
    if creating:
        values.pop("course")
        group = _create_numbered(StudyGroup, "code", "GR-", course=course, **values)
    else:
        group.save()
    _audit(user, "create_group" if creating else "edit_group", group,
           {"code": group.code, "course": course.name, "name": name, "days": group.days, "teacher": teacher.name if teacher else ""},
           AuditEventType.CREATE if creating else AuditEventType.UPDATE)
    return group


def active_count(group):
    return group.enrollments.filter(status=EnrollmentStatus.ACTIVE).count()


@transaction.atomic
def enroll(student, group, user, *, start_date=None, discount_percent="0", notes="", lang="ar"):
    words = MESSAGES[lang]
    group = StudyGroup.objects.select_for_update().get(pk=group.pk)  # two enrolments at once never overfill it
    if not group.active:
        raise ValidationError(words["group_closed"])
    if not student.active:
        raise ValidationError(words["student_inactive"])
    if group.enrollments.filter(student=student, status=EnrollmentStatus.ACTIVE).exists():
        raise ValidationError(words["enrolled"])
    if group.capacity and active_count(group) >= group.capacity:
        raise ValidationError(words["full"].format(capacity=group.capacity))
    try:
        discount = Decimal(str(discount_percent or "0").strip())
    except InvalidOperation:
        raise ValidationError(words["discount"])
    if not Decimal("0") <= discount <= Decimal("100"):
        raise ValidationError(words["discount"])
    enrollment = Enrollment.objects.create(student=student, group=group, start_date=_date(start_date, words) or timezone.localdate(),
                                           discount_percent=discount, notes=(notes or "").strip()[:255], created_by=user)
    _audit(user, "enroll_student", enrollment, {"student": student.code, "group": group.code, "discount": str(discount)})
    return enrollment


@transaction.atomic
def stop(enrollment, user, *, end_date=None, lang="ar"):
    words = MESSAGES[lang]
    enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
    if enrollment.status != EnrollmentStatus.ACTIVE:
        raise ValidationError(words["stopped"])
    day = _date(end_date, words) or timezone.localdate()
    if day < enrollment.start_date:
        raise ValidationError(words["before_start"])
    enrollment.status, enrollment.end_date = EnrollmentStatus.STOPPED, day
    enrollment.save(update_fields=["status", "end_date"])
    _audit(user, "stop_enrollment", enrollment, {"student": enrollment.student.code, "group": enrollment.group.code, "end": day.isoformat()},
           AuditEventType.UPDATE)
    return enrollment


def monthly_fee(enrollment):
    """What this enrolment costs a month after its discount (for the monthly invoices, EDU-003)."""

    from config.money import money_round

    fee = enrollment.group.effective_fee
    return money_round(fee * (Decimal("100") - enrollment.discount_percent) / Decimal("100"))


def schedule_text(group, lang="ar"):
    days = "، ".join(DAY_LABELS[lang][day] for day in group.weekdays) if lang == "ar" else ", ".join(DAY_LABELS[lang][day] for day in group.weekdays)
    when = group.start_time.strftime("%H:%M") if group.start_time else ""
    return " · ".join(part for part in (days, when) if part)
