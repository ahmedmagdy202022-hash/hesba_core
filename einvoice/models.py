"""EINV-001: what the Egyptian Tax Authority's e-invoice needs beyond Hesba's data.

New tables only; customers and items are not altered. The issuer (the shop
itself) lives in SystemSetting rows, like the other company details.
"""

from django.db import models


class ReceiverType(models.TextChoices):
    BUSINESS = "B", "Business in Egypt"
    PERSON = "P", "Natural person"
    FOREIGNER = "F", "Foreigner"


class ReceiverProfile(models.Model):
    """The customer as the e-invoice's receiver."""

    customer = models.OneToOneField("master_data.Customer", on_delete=models.CASCADE, related_name="einvoice_profile")
    receiver_type = models.CharField(max_length=1, choices=ReceiverType.choices, default=ReceiverType.PERSON)
    tax_id = models.CharField(max_length=30, blank=True, help_text="Tax registration number (B), national ID (P) or passport / foreign ID (F).")
    country = models.CharField(max_length=2, default="EG")
    governate = models.CharField(max_length=100, blank=True)
    region_city = models.CharField(max_length=100, blank=True)
    street = models.CharField(max_length=200, blank=True)
    building_number = models.CharField(max_length=50, blank=True)
    updated_at = models.DateTimeField(auto_now=True)


class ItemCodeType(models.TextChoices):
    EGS = "EGS", "EGS (internal code registered with ETA)"
    GS1 = "GS1", "GS1 (international barcode)"


class ItemCode(models.Model):
    """The item's registered code and unit on the e-invoice."""

    item = models.OneToOneField("master_data.Item", on_delete=models.CASCADE, related_name="einvoice_code")
    code_type = models.CharField(max_length=3, choices=ItemCodeType.choices, default=ItemCodeType.EGS)
    code = models.CharField(max_length=100)
    unit_type = models.CharField(max_length=10, default="EA")
    updated_at = models.DateTimeField(auto_now=True)
