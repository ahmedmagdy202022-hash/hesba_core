"""ASSET-001: the shop's fixed assets and their straight-line depreciation.

An asset is bought once (optionally paid from a cashbox through the existing
direct cash-out operation) and then loses (cost - salvage) / useful months
of value every month from the month it is put in service, until it is fully
depreciated or disposed of. Depreciation is *computed* from these fields, not
posted: Hesba has no general ledger, and the profit report shows it as its
own line under net profit (HG-023). The figures that drive it cannot be edited
after creation; a wrong asset is cancelled and entered again.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class AssetCategory(models.TextChoices):
    VEHICLES = "vehicles", "Vehicles"
    EQUIPMENT = "equipment", "Equipment & machines"
    COMPUTERS = "computers", "Computers & devices"
    FURNITURE = "furniture", "Furniture & fit-out"
    BUILDINGS = "buildings", "Buildings"
    OTHER = "other", "Other"


class AssetStatus(models.TextChoices):
    ACTIVE = "active", "In use"
    DISPOSED = "disposed", "Disposed"
    CANCELLED = "cancelled", "Cancelled"


class FixedAsset(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=AssetCategory.choices, default=AssetCategory.EQUIPMENT)
    in_service_on = models.DateField()
    cost = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    salvage_value = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(Decimal("0"))])
    useful_months = models.PositiveSmallIntegerField()
    payment_operation = models.ForeignKey("cashboxes.CashboxOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    status = models.CharField(max_length=20, choices=AssetStatus.choices, default=AssetStatus.ACTIVE)
    disposed_on = models.DateField(null=True, blank=True)
    disposal_proceeds = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    disposal_operation = models.ForeignKey("cashboxes.CashboxOperation", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    notes = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"
