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
        "entity": "الطالب والمجموعة والكورس لازم يكونوا في نفس الكيان.",
        "capacity_below": "في المجموعة {count} طالب دلوقتي؛ السعة مينفعش تقل عن كده. أوقف تسجيل بعضهم الأول.",
        "phone": "رقم التليفون قصير؛ اكتبه كامل (7 أرقام على الأقل).",
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
        "entity": "The student, the group and the course must belong to the same entity.",
        "capacity_below": "The group has {count} students now; capacity cannot go below that. Stop some enrolments first.",
        "phone": "The phone number is too short; enter it in full (at least 7 digits).",
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


def owner_entity():
    """The entity new records belong to: the one being worked in, else the main one."""

    from entities.current import current_entity
    from entities.services import main_entity

    return current_entity() or main_entity()


def _main_id():
    from entities.services import main_entity

    return main_entity().pk


def entity_id_of(record):
    return record.entity_id or _main_id()


def scoped(queryset):
    """Only the records of the entity being worked in (all of them for the whole group); unowned ones are the main entity's."""

    from django.db.models import Q

    from entities.current import current_entity

    queryset, entity = queryset.all(), current_entity()
    if entity is None:
        return queryset
    if entity.pk == _main_id():
        return queryset.filter(Q(entity=entity) | Q(entity__isnull=True))
    return queryset.filter(entity=entity)


def same_entity_as(queryset, record):
    """The records of ``record``'s own entity: what it may be paired with, even when the whole group is open."""

    from django.db.models import Q

    entity_id = entity_id_of(record)
    if entity_id == _main_id():
        return queryset.filter(Q(entity_id=entity_id) | Q(entity__isnull=True))
    return queryset.filter(entity_id=entity_id)


#: What an edit's audit records before and after (Codex on #185): a change of payer, room, fee or dates can be traced.
SNAPSHOT = {
    "Student": ("code", "name", "phone", "date_of_birth", "stage", "school", "relation", "notes", "payer", "active"),
    "Course": ("name", "fee", "basis", "description", "item", "active"),
    "StudyGroup": ("code", "course", "name", "teacher", "room", "capacity", "days", "start_time", "duration_minutes", "fee", "starts_on", "ends_on",
                   "teacher_pay", "teacher_rate", "active"),
}


def _snapshot(obj):
    out = {}
    for field in SNAPSHOT.get(type(obj).__name__, ()):
        if not hasattr(obj, field):
            continue
        value = getattr(obj, field)
        if hasattr(value, "pk"):  # a related record: its code, or its name
            value = getattr(value, "customer_code", None) or getattr(value, "item_code", None) or getattr(value, "code", None) or str(value)
        out[field] = value if value is None or isinstance(value, (bool, int, str)) else str(value)
    return out


def _audit(user, action, obj, after, event=AuditEventType.CREATE, before=None):
    if event == AuditEventType.UPDATE:
        after = {**after, **_snapshot(obj)}
    AuditLog.objects.create(event_type=event, actor=user, module="education", action=action, object_type=f"education.{type(obj).__name__}",
                            object_id=str(obj.pk), before_data=before or {}, after_data=after)


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


#: The fee fields hold 12 digits with 2 after the point.
MONEY_LIMIT = Decimal("10000000000")


def _plain_number(raw):
    """A typed amount as a plain number: Arabic digits and separators, "1,250.50", "1,250" and a decimal comma "400,50" (Codex on #185)."""

    import re

    text = str(raw).strip().translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,")).replace(" ", "")
    if "," in text and "." in text:
        return text.replace(",", "")  # 1,250.50: the comma groups thousands
    if re.fullmatch(r"\d{1,3}(,\d{3})+", text):
        return text.replace(",", "")  # 1,250 or 12,500,000: thousands
    return text.replace(",", ".")  # 400,50: a decimal comma


def _money(raw, words, blank=None):
    if raw in (None, ""):
        return blank
    try:
        value = Decimal(_plain_number(raw))
    except InvalidOperation:
        raise ValidationError(words["money"])
    if not value.is_finite() or value < 0 or value >= MONEY_LIMIT:  # "NaN" and "Infinity" parse but are not amounts
        raise ValidationError(words["money"])
    value = value.quantize(Decimal("0.01"))
    if value >= MONEY_LIMIT:  # 9999999999.999 rounds up past the limit
        raise ValidationError(words["money"])
    return value


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

    def add(who, number):
        try:
            return quick_add_customer(who, number, user)[0]
        except ValidationError as exc:  # its messages are codes ("name", "phone"), not words
            raise ValidationError(words["phone"] if "phone" in exc.messages else words["payer_name"])

    chosen = data.get("payer")
    if chosen is not None and not isinstance(chosen, str):
        return chosen
    if data.get("self_pays") in ("1", "on", True):
        return add(name, phone)
    parent = _text(data, "payer_name", 255)
    if parent:
        return add(parent, _text(data, "payer_phone", 50))
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
        student = _create_numbered(Student, "code", "ST-", created_by=user, entity=owner_entity(), **values)
        _audit(user, "create_student", student, {"code": student.code, "name": name, "payer": student.payer.customer_code})
        return student
    before = _snapshot(student)
    if data.get("payer") is not None and not isinstance(data.get("payer"), str):
        values["payer"] = data["payer"]
    if "active_present" in data:  # the edit form says the box was shown, so an unticked box means inactive
        values["active"] = data.get("active") in ("1", "on", True)
    for key, value in values.items():
        setattr(student, key, value)
    student.save()
    _audit(user, "edit_student", student, {"code": student.code, "name": name, "active": student.active}, AuditEventType.UPDATE, before)
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
        course = Course.objects.create(name=name, fee=fee, basis=basis, description=description, item=item, entity=owner_entity())
        _audit(user, "create_course", course, {"name": name, "fee": str(fee), "basis": basis, "item": course.item.item_code})
        return course
    before = _snapshot(course)
    course.name, course.fee, course.basis, course.description = name, fee, basis, description
    course.active = data.get("active") in ("1", "on", True) if "active_present" in data else course.active
    course.save()
    course.item.item_name, course.item.default_sale_price = name[:255], fee  # the invoice line reads the course's own name and price
    course.item.save(update_fields=["item_name", "default_sale_price", "updated_at"])
    _audit(user, "edit_course", course, {"name": name, "fee": str(fee), "basis": basis}, AuditEventType.UPDATE, before)
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


WEEKDAY_CODE = {5: "sat", 6: "sun", 0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri"}  # Python's weekday() → ours
PY_WEEKDAY = {code: number for number, code in WEEKDAY_CODE.items()}
DAY, WEEK = 1440, 7 * 1440


def _slots(group):
    """Each weekly meeting as minutes from Monday 00:00: [(python weekday, start, end)]; a late class runs past midnight."""

    start = group.start_time.hour * 60 + group.start_time.minute
    return [(PY_WEEKDAY[code], PY_WEEKDAY[code] * DAY + start, PY_WEEKDAY[code] * DAY + start + group.duration_minutes) for code in group.weekdays]


def _a_date_with(weekday, offset, group, other):
    """Whether some date D on ``weekday`` is in ``group``'s term while D + ``offset`` days is in ``other``'s."""

    def shifted(day):
        return day - timedelta(days=offset) if day else None

    lows = [day for day in (group.starts_on, shifted(other.starts_on)) if day]
    highs = [day for day in (group.ends_on, shifted(other.ends_on)) if day]
    first, last = (max(lows) if lows else None), (min(highs) if highs else None)
    if first and last and last < first:
        return False
    if not (first and last) or (last - first).days >= 6:
        return True  # a whole week or more in common: every weekday occurs
    return any((first + timedelta(days=n)).weekday() == weekday for n in range((last - first).days + 1))


def _overlap(group, other):
    """Whether the two groups are ever in class at the same moment, on real dates of both terms (Codex on #185)."""

    for weekday, start, end in _slots(group):
        for other_weekday, other_start, other_end in _slots(other):
            for wrap in (-WEEK, 0, WEEK):  # Friday night runs into Saturday morning
                if start < other_end + wrap and other_start + wrap < end:
                    offset = (other_weekday * DAY + wrap - weekday * DAY) // DAY
                    if _a_date_with(weekday, offset, group, other):
                        return True
    return False


def clash(group, lang="ar"):
    """The first active group in class at the same time as this one, with its teacher or in its room, as a message."""

    words = MESSAGES[lang]
    if not group.start_time or not group.weekdays:
        return ""
    others = StudyGroup.objects.filter(active=True, start_time__isnull=False).exclude(pk=group.pk).select_related("teacher")
    for other in others:
        same_teacher = bool(group.teacher_id) and other.teacher_id == group.teacher_id
        same_place = (other.entity_id or _main_id()) == (group.entity_id or _main_id())  # rooms belong to one entity's building
        same_room = bool(group.room) and same_place and other.room.strip().lower() == group.room.strip().lower()
        if not (same_teacher or same_room) or not other.weekdays or not _overlap(group, other):
            continue
        if same_teacher:
            return words["teacher_busy"].format(teacher=group.teacher.name, group=other.name)
        return words["room_busy"].format(room=group.room, group=other.name)
    return ""


def _lock_schedule(group):
    """Two saves at once for one teacher or one entity's rooms wait for each other, so both cannot pass the clash check."""

    from entities.models import Entity
    from staff.models import Employee

    if group.teacher_id:
        Employee.objects.select_for_update().filter(pk=group.teacher_id).exists()
    Entity.objects.select_for_update().filter(pk=group.entity_id or _main_id()).exists()


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
    if not 0 <= capacity <= 100000:  # the column is an integer; a school group is never bigger
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
    before = {} if creating else _snapshot(StudyGroup.objects.get(pk=group.pk))
    if creating:
        values["entity"] = owner_entity()
        group = StudyGroup(**values)
    else:
        for key, value in values.items():
            setattr(group, key, value)
        if "active_present" in data:
            group.active = data.get("active") in ("1", "on", True)
    if entity_id_of(course) != entity_id_of(group):
        raise ValidationError(words["entity"])
    if not creating:
        StudyGroup.objects.select_for_update().filter(pk=group.pk).exists()  # the lock enrol() takes: no seat is taken while the count is checked
    if not creating and capacity and capacity < active_count(group):
        raise ValidationError(words["capacity_below"].format(count=active_count(group)))  # stop some enrolments first
    if group.active:
        _lock_schedule(group)
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
           AuditEventType.CREATE if creating else AuditEventType.UPDATE, before)
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
    if entity_id_of(student) != entity_id_of(group):
        raise ValidationError(words["entity"])
    if group.enrollments.filter(student=student, status=EnrollmentStatus.ACTIVE).exists():
        raise ValidationError(words["enrolled"])
    if group.capacity and active_count(group) >= group.capacity:
        raise ValidationError(words["full"].format(capacity=group.capacity))
    try:
        discount = Decimal(str(discount_percent or "0").strip())
    except InvalidOperation:
        raise ValidationError(words["discount"])
    if not discount.is_finite() or not Decimal("0") <= discount <= Decimal("100"):
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
    """The enrolment's fee after its discount, per the course's basis: a month, a class or the whole course.

    Only monthly courses add up to a monthly total (``is_monthly``)."""

    from config.money import money_round

    fee = enrollment.group.effective_fee
    return money_round(fee * (Decimal("100") - enrollment.discount_percent) / Decimal("100"))


def is_monthly(enrollment):
    return enrollment.group.course.basis == FeeBasis.MONTHLY


def schedule_text(group, lang="ar"):
    days = "، ".join(DAY_LABELS[lang][day] for day in group.weekdays) if lang == "ar" else ", ".join(DAY_LABELS[lang][day] for day in group.weekdays)
    when = group.start_time.strftime("%H:%M") if group.start_time else ""
    return " · ".join(part for part in (days, when) if part)
