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
