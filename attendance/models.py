"""T11 (MARKET-PARITY): staff attendance and leave (HG-063, additive tables only).

``WorkSchedule`` sits beside an employee: the working hours, the weekly days
off, the grace before a late arrival, the yearly leave and the number the
fingerprint device knows them by. ``AttendanceDay`` is one employee's day:
when they came and left, or that they were absent. ``LeaveRequest`` is a leave
asked for and approved or refused, and ``Holiday`` a public holiday. Nothing
here touches pay: the monthly sheet only counts days, late minutes and
overtime for whoever pays salaries (payroll is HG-060).
"""

from datetime import time

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class WorkSchedule(models.Model):
    employee = models.OneToOneField("staff.Employee", on_delete=models.CASCADE, related_name="schedule")
    start_time = models.TimeField(default=time(9, 0))
    end_time = models.TimeField(default=time(17, 0))
    #: Python weekdays (Monday 0 … Sunday 6), comma separated; Friday by default.
    days_off = models.CharField(max_length=20, default="4", blank=True)
    grace_minutes = models.PositiveSmallIntegerField(default=15, validators=[MaxValueValidator(240)])
    annual_leave_days = models.PositiveSmallIntegerField(default=21, validators=[MaxValueValidator(366)])
    device_id = models.CharField(max_length=30, blank=True, db_index=True, help_text="The number the fingerprint device knows this employee by.")
    updated_at = models.DateTimeField(auto_now=True)

    def off_weekdays(self):
        return {int(day) for day in self.days_off.split(",") if day.strip().isdigit() and int(day) <= 6}


class DayStatus(models.TextChoices):
    PRESENT = "present", "Present"
    ABSENT = "absent", "Absent"


class AttendanceDay(models.Model):
    employee = models.ForeignKey("staff.Employee", on_delete=models.CASCADE, related_name="attendance_days")
    date = models.DateField(db_index=True)
    status = models.CharField(max_length=10, choices=DayStatus.choices, default=DayStatus.PRESENT)
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    #: manual (the manager), self (the employee's own button) or device (the fingerprint file).
    source = models.CharField(max_length=10, default="manual")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "employee_id"]
        constraints = [models.UniqueConstraint(fields=["employee", "date"], name="attendance_one_day_per_employee")]


class LeaveKind(models.TextChoices):
    ANNUAL = "annual", "Annual"
    CASUAL = "casual", "Casual"
    SICK = "sick", "Sick"
    UNPAID = "unpaid", "Unpaid"
    OTHER = "other", "Other"


class LeaveStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    CANCELLED = "cancelled", "Cancelled"


class LeaveRequest(models.Model):
    employee = models.ForeignKey("staff.Employee", on_delete=models.CASCADE, related_name="leave_requests")
    kind = models.CharField(max_length=10, choices=LeaveKind.choices, default=LeaveKind.ANNUAL)
    start_date = models.DateField()
    end_date = models.DateField()
    #: Working days inside the range (days off and holidays left out), counted when it is asked for.
    days = models.PositiveSmallIntegerField(default=0, validators=[MinValueValidator(0)])
    status = models.CharField(max_length=10, choices=LeaveStatus.choices, default=LeaveStatus.PENDING, db_index=True)
    reason = models.CharField(max_length=255, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    decided_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-start_date", "-pk"]


class Holiday(models.Model):
    date = models.DateField(unique=True)
    name = models.CharField(max_length=120)

    class Meta:
        ordering = ["date"]
