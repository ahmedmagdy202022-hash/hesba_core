from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models


class AppointmentKind(models.TextChoices):
    APPOINTMENT = "appointment", "At the shop"
    VISIT = "visit", "Visit to the customer"
    #: M10 (HG-096): a consultation over a video call; the call itself runs on a meeting service outside Hesba.
    ONLINE = "online", "Online visit"


class AppointmentStatus(models.TextChoices):
    BOOKED = "booked", "Booked"
    CONFIRMED = "confirmed", "Confirmed"
    ARRIVED = "arrived", "In progress"
    DONE = "done", "Done"
    CANCELLED = "cancelled", "Cancelled"
    NO_SHOW = "no_show", "No show"


#: Statuses that still hold the employee's time.
HOLDS_TIME = (AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED, AppointmentStatus.DONE)


class Appointment(models.Model):
    """APPT-001: a booked slot (at the shop) or a visit (at the customer's address).

    It never moves money by itself. When the work is done it is billed by a
    draft sales invoice made through the ordinary sales services, and posting
    that invoice is what touches stock, the cashbox and the customer's account.
    """

    number = models.CharField(max_length=30, unique=True)
    kind = models.CharField(max_length=20, choices=AppointmentKind.choices, default=AppointmentKind.APPOINTMENT)
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="appointments")
    employee = models.ForeignKey("staff.Employee", on_delete=models.PROTECT, null=True, blank=True, related_name="appointments")
    service = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, null=True, blank=True, related_name="appointments")
    price = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0"))
    starts_at = models.DateTimeField(db_index=True)
    duration_minutes = models.PositiveIntegerField(default=30)
    address = models.CharField(max_length=255, blank=True)
    #: M10 (HG-096): the https link of an online visit's video call (made by Hesba from the clinic's meeting server, or pasted).
    #: Only the staff who may see the booking and, inside its window, the patient's own confirm page ever show it.
    call_url = models.URLField(max_length=600, blank=True, default="")
    status = models.CharField(max_length=20, choices=AppointmentStatus.choices, default=AppointmentStatus.BOOKED, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="appointment")
    # HG-074: the entity the appointment was booked in; its bill lands in that entity's store and books.
    # NULL (booked before HG-074) belongs to the main entity, as every record without one (HG-034).
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_appointments")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at", "id"]
        indexes = [models.Index(fields=["employee", "starts_at"])]

    @property
    def ends_at(self):
        return self.starts_at + timedelta(minutes=self.duration_minutes)

    def __str__(self):
        return self.number


# ---- S3 (HG-077): online booking and working hours. Additive tables only; nothing here moves money or stock. ----


class Weekday(models.IntegerChoices):
    """Python's numbering (``date.weekday()``): Monday is 0. Egypt's week starts on Saturday (5)."""

    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class BookingSettings(models.Model):
    """One per entity: whether its public booking page takes bookings, and the slot rules.

    ``public_key`` is the random part of the entity's public link (``/book/<key>/``); changing it retires the old link.
    """

    entity = models.OneToOneField("entities.Entity", on_delete=models.PROTECT, related_name="booking_settings")
    public_key = models.CharField(max_length=40, unique=True)
    accepting = models.BooleanField(default=False, help_text="The entity's public page takes bookings.")
    slot_minutes = models.PositiveSmallIntegerField(default=30)
    min_notice_minutes = models.PositiveIntegerField(default=60)
    max_days_ahead = models.PositiveSmallIntegerField(default=30)
    #: Ahmed's open question (S3): prices stay off the public page unless the owner shows them.
    show_prices = models.BooleanField(default=False)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"booking:{self.entity_id}"


class WorkingHours(models.Model):
    """One opening window per weekday. ``employee`` empty: the entity's opening hours; set: that employee's own hours
    there (always inside the entity's). A weekday with no entity row is closed. No overnight windows: closes > opens."""

    entity = models.ForeignKey("entities.Entity", on_delete=models.CASCADE, related_name="+")
    employee = models.ForeignKey("staff.Employee", on_delete=models.CASCADE, null=True, blank=True, related_name="+")
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    opens = models.TimeField()
    closes = models.TimeField()

    class Meta:
        ordering = ["entity", "employee", "weekday"]
        constraints = [
            models.UniqueConstraint(fields=["entity", "employee", "weekday"], name="appt_hours_one_per_employee_day"),
            # NULLs are distinct in a unique index: the entity's own row needs its own rule.
            models.UniqueConstraint(fields=["entity", "weekday"], condition=models.Q(employee__isnull=True), name="appt_hours_one_per_entity_day"),
            models.CheckConstraint(condition=models.Q(closes__gt=models.F("opens")), name="appt_hours_close_after_open"),
        ]


class BookableService(models.Model):
    """A service the entity's public page offers, with how long it takes (the appointment's duration)."""

    entity = models.ForeignKey("entities.Entity", on_delete=models.CASCADE, related_name="+")
    item = models.ForeignKey("master_data.Item", on_delete=models.CASCADE, related_name="+")
    duration_minutes = models.PositiveIntegerField(default=30)

    class Meta:
        ordering = ["entity", "item__item_name"]
        constraints = [models.UniqueConstraint(fields=["entity", "item"], name="appt_bookable_service_once")]


class BookingStaff(models.Model):
    """An employee who takes online bookings in this entity."""

    entity = models.ForeignKey("entities.Entity", on_delete=models.CASCADE, related_name="+")
    employee = models.ForeignKey("staff.Employee", on_delete=models.CASCADE, related_name="+")

    class Meta:
        ordering = ["entity", "employee__name"]
        constraints = [models.UniqueConstraint(fields=["entity", "employee"], name="appt_booking_staff_once")]
