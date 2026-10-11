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
