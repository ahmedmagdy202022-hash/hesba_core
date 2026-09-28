from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Employee(models.Model):
    """STAFF-001: someone who does the work: a technician, doctor, stylist, teacher.

    Separate from a login account on purpose: many technicians never sign in.
    When one does, ``user`` links the two so their own appointments can be
    shown to them.
    """

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=255)
    title = models.CharField(max_length=120, blank=True, help_text="Job title shown to customers, e.g. technician, doctor.")
    phone = models.CharField(max_length=50, blank=True)
    commission_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))],
        help_text="Share of the posted sales of this employee's completed work, for the performance report.",
    )
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="employee")
    active = models.BooleanField(default=True)
    notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name
