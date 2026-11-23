"""UNITS-001: selling and buying an item in bigger units (carton, box, dozen...).

Stock, cost and every posted line stay in the item's base unit (``Item.unit``),
so posting, returns and reports are untouched: a line entered as "5 cartons"
is saved as 5 x factor base units at the equivalent base price (HG-018).
"""

from decimal import Decimal

from django.core.validators import MinValueValidator
from django.db import models


class ItemUnit(models.Model):
    item = models.ForeignKey("master_data.Item", on_delete=models.CASCADE, related_name="alt_units")
    name_ar = models.CharField(max_length=60)
    name_en = models.CharField(max_length=60, blank=True)
    factor = models.DecimalField(max_digits=12, decimal_places=3, validators=[MinValueValidator(Decimal("1.001"))],
                                 help_text="How many base units one of these holds, e.g. 12 pieces in a carton.")
    barcode = models.CharField(max_length=120, blank=True, db_index=True)
    sale_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    purchase_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(Decimal("0"))])
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["item_id", "factor"]
        constraints = [models.UniqueConstraint(fields=["item", "name_ar"], name="units_one_name_per_item")]

    def label(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else self.name_ar

    def default_sale_price(self):
        return self.sale_price if self.sale_price is not None else (self.item.default_sale_price * self.factor).quantize(Decimal("0.01"))

    def default_purchase_price(self):
        return self.purchase_price if self.purchase_price is not None else (self.item.default_purchase_price * self.factor).quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.name_ar} ({self.factor.normalize():f})"
