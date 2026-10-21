"""T10 (MARKET-PARITY): the pharmacy pack (HG-062, additive tables only).

``DrugInfo`` adds what a pharmacy knows about an item: its active ingredient,
strength and form, the maker, and whether it is sold on prescription only.
Items with the same active ingredient are each other's alternatives; nothing is
stored for that, it is read from ``ingredient_key``.

A ``Prescription`` is the doctor's paper as the pharmacy receives it: the
patient, the doctor, and each drug with its dosage. It is dispensed on the
ordinary sales invoice form (``/sales/new/?prescription=<pk>``), so stock,
expiry batches, the cashbox and posting work exactly as on any sale, and
``PrescriptionInvoice`` only remembers which invoice dispensed it. A chronic
prescription (``refill_days``) is dispensed again every so many days.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class DosageForm(models.TextChoices):
    TABLET = "tablet", "Tablet"
    CAPSULE = "capsule", "Capsule"
    SYRUP = "syrup", "Syrup"
    SUSPENSION = "suspension", "Suspension"
    INJECTION = "injection", "Injection"
    DROPS = "drops", "Drops"
    CREAM = "cream", "Cream or ointment"
    SPRAY = "spray", "Spray"
    SACHET = "sachet", "Sachet"
    SUPPOSITORY = "suppository", "Suppository"
    OTHER = "other", "Other"


class DrugInfo(models.Model):
    item = models.OneToOneField("master_data.Item", on_delete=models.CASCADE, related_name="drug")
    active_ingredient = models.CharField(max_length=255, help_text="As the box says it, e.g. “Paracetamol” or “Amoxicillin + Clavulanic acid”.")
    ingredient_key = models.CharField(max_length=255, db_index=True, help_text="The ingredient reduced for matching alternatives.")
    strength = models.CharField(max_length=60, blank=True)
    strength_key = models.CharField(max_length=60, blank=True)
    form = models.CharField(max_length=12, choices=DosageForm.choices, blank=True)
    manufacturer = models.CharField(max_length=120, blank=True)
    prescription_only = models.BooleanField(default=False)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["ingredient_key", "strength_key"]

    def __str__(self):
        return f"{self.active_ingredient} {self.strength}".strip()


class PrescriptionStatus(models.TextChoices):
    ACTIVE = "active", "Active"
    CANCELLED = "cancelled", "Cancelled"


class Prescription(models.Model):
    number = models.CharField(max_length=20, unique=True, help_text="RX-…")
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="prescriptions",
                                 help_text="The patient's account, when the sale goes on one; empty for a cash sale.")
    patient_name = models.CharField(max_length=120)
    patient_phone = models.CharField(max_length=30, blank=True)
    doctor_name = models.CharField(max_length=120, blank=True)
    issued_on = models.DateField(default=timezone.localdate)
    refill_days = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1)],
                                                   help_text="A chronic prescription is dispensed again every this many days.")
    status = models.CharField(max_length=10, choices=PrescriptionStatus.choices, default=PrescriptionStatus.ACTIVE, db_index=True)
    notes = models.CharField(max_length=255, blank=True)
    entity = models.ForeignKey("entities.Entity", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_on", "-pk"]

    def __str__(self):
        return self.number


class PrescriptionLine(models.Model):
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name="lines")
    prescribed_name = models.CharField(max_length=255, help_text="What the doctor wrote.")
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                             help_text="What the pharmacy dispenses; empty until one is chosen.")
    quantity = models.DecimalField(max_digits=12, decimal_places=3, default=1, validators=[MinValueValidator(Decimal("0.001"))])
    dosage = models.CharField(max_length=255, blank=True)
    substituted = models.BooleanField(default=False, help_text="The pharmacist dispenses an alternative to what was written.")

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return self.prescribed_name


class PrescriptionInvoice(models.Model):
    """One sales invoice that dispensed a prescription (once, or once per refill)."""

    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name="dispenses")
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.CASCADE, related_name="prescription_link")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]
