"""VARIANT-001: one model (style) sold in several sizes and colours.

Every size/colour combination is an ordinary ``master_data.Item`` with its own
code, barcode, stock and cost, so selling, buying, stock and reports treat it
like any other item (HG-020). ``VariantGroup`` only ties the combinations
together so they can be created, priced and viewed as one grid.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class VariantGroup(models.Model):
    code = models.CharField(max_length=40, unique=True)
    name = models.CharField(max_length=255)
    category = models.ForeignKey("master_data.Category", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    unit = models.CharField(max_length=50, default="unit")
    default_sale_price = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(Decimal("0"))])
    default_purchase_price = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=[MinValueValidator(Decimal("0"))])
    sizes = models.JSONField(default=list, blank=True)
    colors = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Variant(models.Model):
    group = models.ForeignKey(VariantGroup, on_delete=models.CASCADE, related_name="variants")
    item = models.OneToOneField("master_data.Item", on_delete=models.PROTECT, related_name="variant")
    size = models.CharField(max_length=80, blank=True)
    color = models.CharField(max_length=80, blank=True)

    class Meta:
        ordering = ["group_id", "pk"]
        constraints = [models.UniqueConstraint(fields=["group", "size", "color"], name="variants_one_item_per_combination")]

    def __str__(self):
        return f"{self.group.code} {self.size} {self.color}".strip()
