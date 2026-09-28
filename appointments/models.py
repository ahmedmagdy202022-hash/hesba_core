from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db import models


class AppointmentKind(models.TextChoices):
    APPOINTMENT = "appointment", "At the shop"
    VISIT = "visit", "Visit to the customer"


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
    status = models.CharField(max_length=20, choices=AppointmentStatus.choices, default=AppointmentStatus.BOOKED, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="appointment")
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
