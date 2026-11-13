"""EDU-001: students, their courses and study groups, and who is enrolled where.

A student is a person who studies; the account (fees, payments, statement)
belongs to whoever pays: a parent, or the student themselves. That payer is
an ordinary customer, so billing, collection and the customer's ledger stay
exactly where they are. A course is billed through its own service item, so
an invoice for it is an ordinary sales invoice.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Weekday(models.TextChoices):
    # The Egyptian week starts on Saturday.
    SAT = "sat", "Saturday"
    SUN = "sun", "Sunday"
    MON = "mon", "Monday"
    TUE = "tue", "Tuesday"
    WED = "wed", "Wednesday"
    THU = "thu", "Thursday"
    FRI = "fri", "Friday"


class FeeBasis(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    COURSE = "course", "Whole course"
    SESSION = "session", "Per session"


class Student(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=50, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    stage = models.CharField(max_length=80, blank=True, help_text="Grade, level or age group.")
    school = models.CharField(max_length=120, blank=True)
    payer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="students",
                              help_text="Who pays and holds the account: a parent, or the student.")
    relation = models.CharField(max_length=40, blank=True, help_text="The payer's relation to the student: father, mother, self…")
    notes = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    # Codex on #185 (HG-034): in a group, each education entity sees only its own students, courses and groups.
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return f"{self.code} {self.name}"


class Course(models.Model):
    name = models.CharField(max_length=160)
    basis = models.CharField(max_length=10, choices=FeeBasis.choices, default=FeeBasis.MONTHLY)
    fee = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))])
    item = models.OneToOneField("master_data.Item", on_delete=models.PROTECT, related_name="course",
                                help_text="The service line its invoices use.")
    description = models.CharField(max_length=255, blank=True)
    active = models.BooleanField(default=True)
    # Codex on #185 (HG-034): in a group, each education entity sees only its own students, courses and groups.
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name", "pk"]

    def __str__(self):
        return self.name


class TeacherPay(models.TextChoices):
    """EDU-004: how the group's teacher is paid."""

    NONE = "none", "Not paid per group"
    SESSION = "session", "Per class held"
    PERCENT = "percent", "Share of the group's fees"
    MONTHLY = "monthly", "Fixed per month"


class StudyGroup(models.Model):
    code = models.CharField(max_length=20, unique=True)
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="groups")
    name = models.CharField(max_length=160)
    teacher = models.ForeignKey("staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, related_name="study_groups")
    room = models.CharField(max_length=60, blank=True)
    capacity = models.PositiveIntegerField(default=0, help_text="0 means no limit.")
    days = models.CharField(max_length=40, blank=True, help_text="Comma-separated weekdays, e.g. sat,mon,wed.")
    start_time = models.TimeField(null=True, blank=True)
    duration_minutes = models.PositiveIntegerField(default=60)
    fee = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))],
                              help_text="Blank: the course's fee.")
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    #: EDU-006: the level this group teaches within its course (a language centre's level 1, 2, 3…); blank when the course has none.
    level = models.PositiveSmallIntegerField(null=True, blank=True)
    teacher_pay = models.CharField(max_length=10, choices=TeacherPay.choices, default=TeacherPay.NONE)
    teacher_rate = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0"), validators=[MinValueValidator(Decimal("0"))],
                                       help_text="Per class, per month, or a percentage of the fees, by teacher_pay.")
    active = models.BooleanField(default=True)
    # Codex on #185 (HG-034): in a group, each education entity sees only its own students, courses and groups.
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["course__name", "name", "pk"]

    def __str__(self):
        return f"{self.course} — {self.name}"

    @property
    def weekdays(self):
        return [day for day in (self.days or "").split(",") if day]

    @property
    def effective_fee(self):
        return self.fee if self.fee is not None else self.course.fee


class EnrollmentStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    STOPPED = "stopped", "Stopped"


class Enrollment(models.Model):
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="enrollments")
    group = models.ForeignKey(StudyGroup, on_delete=models.PROTECT, related_name="enrollments")
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("0"),
                                           validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
                                           help_text="Siblings, scholarships…")
    status = models.CharField(max_length=10, choices=EnrollmentStatus.choices, default=EnrollmentStatus.ACTIVE, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-pk"]
        constraints = [models.UniqueConstraint(fields=["student", "group"], condition=models.Q(status="active"),
                                               name="education_one_active_enrollment_per_group")]

    def __str__(self):
        return f"{self.student} @ {self.group}"


class SessionStatus(models.TextChoices):
    HELD = "held", "Held"
    CANCELLED = "cancelled", "Cancelled"


class ClassSession(models.Model):
    """EDU-002: one meeting of a group on one day, and who came."""

    group = models.ForeignKey(StudyGroup, on_delete=models.PROTECT, related_name="sessions")
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=10, choices=SessionStatus.choices, default=SessionStatus.HELD)
    topic = models.CharField(max_length=255, blank=True)
    taken_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    taken_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-pk"]
        constraints = [models.UniqueConstraint(fields=["group", "date"], name="education_one_session_per_group_day")]

    def __str__(self):
        return f"{self.group} {self.date}"


class Presence(models.TextChoices):
    PRESENT = "present", "Present"
    LATE = "late", "Late"
    ABSENT = "absent", "Absent"
    EXCUSED = "excused", "Excused"


class Attendance(models.Model):
    session = models.ForeignKey(ClassSession, on_delete=models.CASCADE, related_name="marks")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="attendance")
    presence = models.CharField(max_length=10, choices=Presence.choices, default=Presence.PRESENT)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["student__name", "pk"]
        constraints = [models.UniqueConstraint(fields=["session", "student"], name="education_one_mark_per_student_session")]


class FeeCharge(models.Model):
    """EDU-003: one enrolment billed for one month (or once, for a whole course), and the invoice line that billed it.

    Deleting the draft invoice deletes its charges, so the month can be billed
    again; a cancelled invoice is skipped when checking what is already billed.
    """

    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="charges")
    month = models.DateField(help_text="The first day of the month billed.")
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.CASCADE, related_name="fee_charges")
    quantity = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("1"), help_text="Months, or classes attended for per-class fees.")
    amount = models.DecimalField(max_digits=14, decimal_places=2)  # quantity × fee: as wide as an invoice line (Codex on #187)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-month", "pk"]
        indexes = [models.Index(fields=["enrollment", "month"])]


class TeacherPayment(models.Model):
    """EDU-004: money paid to a teacher for a month, made as an ordinary expense out of a cashbox.

    The expense carries the cash movement; cancelling it (on the expenses page)
    reverses the cash and the month is owed again.
    """

    teacher = models.ForeignKey("staff.Employee", on_delete=models.PROTECT, related_name="teacher_payments")
    month = models.DateField(help_text="The first day of the month paid for.")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    expense = models.OneToOneField("expenses.Expense", on_delete=models.PROTECT, related_name="teacher_payment")
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-month", "pk"]
        indexes = [models.Index(fields=["teacher", "month"])]


class GroupPayTerm(models.Model):
    """EDU-004 (Codex on #189): how a group's teacher was paid for a month, frozen when that month is first paid.

    Editing the group afterwards (another teacher, another rate) changes the months still to pay, never one
    already paid, so a payment cannot vanish from its month or be made twice for it.
    """

    group = models.ForeignKey(StudyGroup, on_delete=models.CASCADE, related_name="pay_terms")
    month = models.DateField()
    teacher = models.ForeignKey("staff.Employee", on_delete=models.PROTECT, related_name="+")
    teacher_pay = models.CharField(max_length=10, choices=TeacherPay.choices)
    teacher_rate = models.DecimalField(max_digits=12, decimal_places=2)
    due = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"),
                              help_text="What the month came to when it was last paid: it never owes less than that afterwards.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "month"], name="education_one_pay_term_per_group_month")]


class PickupPerson(models.Model):
    """EDU-005: someone a nursery hands a child to at the end of the day (a parent, a grandparent, the driver).

    The child leaves only with one of these; a person no longer allowed is switched off, never deleted, so
    the days they picked the child up still show who it was."""

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="pickup_people")
    name = models.CharField(max_length=120)
    relation = models.CharField(max_length=40, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    id_number = models.CharField(max_length=30, blank=True, help_text="National ID or another document, checked at the door.")
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-active", "name", "pk"]

    def __str__(self):
        return self.name


class DayRecord(models.Model):
    """EDU-005: a child's day at the nursery: when they arrived, when they left and who picked them up."""

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="day_records")
    day = models.DateField(db_index=True)
    arrived_at = models.DateTimeField()
    arrived_with = models.CharField(max_length=120, blank=True)
    left_at = models.DateTimeField(null=True, blank=True)
    picked_by = models.ForeignKey(PickupPerson, on_delete=models.PROTECT, null=True, blank=True, related_name="pickups")
    note = models.CharField(max_length=255, blank=True)
    checked_in_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    checked_out_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-day", "arrived_at", "pk"]
        constraints = [models.UniqueConstraint(fields=["student", "day"], name="education_one_day_record_per_child")]


class PlacementTest(models.Model):
    """EDU-006: a placement test: the level a student was placed at in a course, and the mark that put them there."""

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="placements")
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="placements")
    taken_on = models.DateField()
    score = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    out_of = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    level = models.PositiveSmallIntegerField()
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-taken_on", "-pk"]


class ExamKind(models.TextChoices):
    QUIZ = "quiz", "Quiz"
    MONTHLY = "monthly", "Monthly exam"
    MIDTERM = "midterm", "Midterm"
    FINAL = "final", "Final"
    OTHER = "other", "Other"


class Exam(models.Model):
    """E2: one paper sat by a study group on one day, out of a fixed mark. Its entity is the group's, as a class session's is.

    ``published`` is off until the centre releases it: a guardian (once a portal exists) sees published exams only."""

    group = models.ForeignKey(StudyGroup, on_delete=models.PROTECT, related_name="exams")
    title = models.CharField(max_length=160)
    kind = models.CharField(max_length=10, choices=ExamKind.choices, default=ExamKind.QUIZ)
    held_on = models.DateField(db_index=True)
    out_of = models.DecimalField(max_digits=7, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    published = models.BooleanField(default=False)
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-held_on", "-pk"]
        constraints = [models.CheckConstraint(condition=models.Q(out_of__gt=0), name="education_exam_out_of_positive")]

    def __str__(self):
        return f"{self.group} {self.title}"


class ExamMark(models.Model):
    """E2: what one student scored on one exam. A student with no row has not been entered yet; ``absent`` and a score never go together."""

    exam = models.ForeignKey(Exam, on_delete=models.CASCADE, related_name="marks")
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="exam_marks")
    score = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    absent = models.BooleanField(default=False)
    note = models.CharField(max_length=255, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    entered_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["student__name", "pk"]
        constraints = [
            models.UniqueConstraint(fields=["exam", "student"], name="education_one_mark_per_student_exam"),
            models.CheckConstraint(condition=models.Q(absent=False) | models.Q(score__isnull=True), name="education_exam_mark_absent_has_no_score"),
        ]


class Certificate(models.Model):
    """E6: a completion certificate for one enrolment: its number, what it measured when issued, and whether it still stands.

    What a printed sheet says is kept here (``hours``, ``attendance_rate`` and ``grade_text`` are frozen at issue, so a later correction
    of attendance never changes a certificate already handed out). A revoked certificate keeps its row, so its number still answers the
    verify page with «revoked». It has no entity of its own: it is its enrolment's group's, as an exam is."""

    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="certificates")
    number = models.CharField(max_length=24, unique=True, help_text="CRT-<year>-<running number>, shown on the sheet.")
    issued_on = models.DateField()
    hours = models.DecimalField(max_digits=6, decimal_places=2, default=Decimal("0"),
                                help_text="Classes held while the student was enrolled × the group's class length ÷ 60.")
    attendance_rate = models.PositiveSmallIntegerField(null=True, blank=True, help_text="Whole percent, as the attendance screens show it.")
    grade_text = models.CharField(max_length=40, blank=True, help_text="Printed as the grade; blank prints none.")
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    revoke_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-issued_on", "-pk"]
        constraints = [models.UniqueConstraint(fields=["enrollment"], condition=models.Q(revoked_at__isnull=True),
                                               name="education_one_live_certificate_per_enrollment")]

    def __str__(self):
        return self.number

    @property
    def revoked(self):
        return self.revoked_at is not None


class GuardianLink(models.Model):
    """E3: the guardian portal's key: one link for one paying guardian, in one entity, that the centre can switch off at once.

    The address carries a signed token (``education.portal.SALT``) holding this row's id and ``nonce``; the row decides whether it still
    opens anything. A new link for the same payer revokes the older ones (at most one live link per payer, enforced by the database), so a
    lost message or a change of custody is cured by sending again, or by revoking. The token and the nonce are never written anywhere else
    (not in an audit row, not in a message outbox). ``last_opened_at`` is the only thing a guardian's visit writes (an answer writes ``wrong_answers``).
    """

    payer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="guardian_links")
    nonce = models.CharField(max_length=64, unique=True)
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, related_name="+",
                               help_text="The portal shows this entity's children of the payer only.")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_opened_at = models.DateTimeField(null=True, blank=True)
    #: Wrong answers given in this link's whole life (the limit is ``portal.TOKEN_LIFE``): counted in the row, atomically, so it survives
    #: a restart and every worker sees it. Revoking or replacing a link never resets it; a new link is a new row and starts at 0.
    wrong_answers = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["payer"], condition=models.Q(revoked_at__isnull=True), name="education_one_live_guardian_link_per_payer")]

    def __str__(self):
        return f"guardian link {self.pk} for {self.payer_id}"

    @property
    def revoked(self):
        return self.revoked_at is not None


class ContentKind(models.TextChoices):
    VIDEO = "video", "Recorded video"
    FILE = "file", "File"
    ONLINE = "online", "Online class"


class GroupContent(models.Model):
    """E9 (HG-104): one thing a centre shares with one study group: a recorded video, a file, or the link of an online class.

    Hesba stores a title and an https address and nothing else: no file, no video, no player. The address is checked by
    ``education.content.clean_url`` (the only place that decides what is acceptable). Its entity is the group's, as an exam's is, so
    there is no entity column that could drift from the group's. ``published`` decides whether a guardian may see it; ``active`` is the
    soft removal: nothing here is ever deleted. ``starts_at`` is used by an online class only (when it starts)."""

    group = models.ForeignKey(StudyGroup, on_delete=models.PROTECT, related_name="contents")
    kind = models.CharField(max_length=10, choices=ContentKind.choices, default=ContentKind.VIDEO)
    title = models.CharField(max_length=160)
    url = models.CharField(max_length=500, help_text="An https address; see education.content.clean_url.")
    note = models.CharField(max_length=255, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    published = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        # the staff list and the guardian's list both filter a group's rows by active (and published)
        indexes = [models.Index(fields=["group", "active", "published"], name="edu_content_group_idx")]

    def __str__(self):
        return f"{self.group} {self.title}"


class SeatStatus(models.TextChoices):
    BOOKED = "booked", "Booked"
    ATTENDED = "attended", "Attended"
    MISSED = "missed", "Missed"
    CANCELLED = "cancelled", "Cancelled"


#: The seats that still hold a place: a cancelled one frees it. One definition for the unique rule, the capacity count and every list.
LIVE_SEATS = (SeatStatus.BOOKED, SeatStatus.ATTENDED, SeatStatus.MISSED)


class MakeupSeat(models.Model):
    """E8 (HG-105): a seat booked for one student in ONE class of another group of the same course, in place of a class of their own group.

    It is not attendance: no ``Attendance`` row, no ``ClassSession``, no fee and no pay figure ever reads this table. Its entity is the
    target group's, as an exam's is (no entity column that could drift). Nothing is deleted: «cancelled» frees the seat."""

    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="makeup_seats")
    home_group = models.ForeignKey(StudyGroup, on_delete=models.PROTECT, related_name="+", help_text="The group the student is enrolled in and missed.")
    missed_on = models.DateField(null=True, blank=True, help_text="The class day of the home group being made up.")
    group = models.ForeignKey(StudyGroup, on_delete=models.PROTECT, related_name="makeup_seats", help_text="The group whose class they attend.")
    day = models.DateField(db_index=True, help_text="The day of that class.")
    status = models.CharField(max_length=10, choices=SeatStatus.choices, default=SeatStatus.BOOKED)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-day", "-pk"]
        constraints = [models.UniqueConstraint(fields=["student", "group", "day"], condition=models.Q(status__in=[s.value for s in LIVE_SEATS]),
                                               name="education_one_live_makeup_seat_per_class")]
        indexes = [models.Index(fields=["group", "day"], name="edu_makeup_group_day_idx")]

    def __str__(self):
        return f"{self.student} @ {self.group} {self.day}"
