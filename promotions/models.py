"""T5 (MARKET-PARITY): promotions and coupons (HG-057, additive tables only).

A promotion only fills the discount field each sales line already has, before the draft is saved; posting keeps
using the line amounts it is given, exactly as for a discount typed by hand. ``InvoicePromotion`` remembers which
promotion gave an invoice its discount, for the promotions report.
"""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models


class PromotionKind(models.TextChoices):
    PERCENT = "percent", "% off"
    BUY_GET = "buy_get", "Buy X get Y"
    AMOUNT_OVER = "amount_over", "Amount off over a total"


class Promotion(models.Model):
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=12, choices=PromotionKind.choices)
    active = models.BooleanField(default=True)
    #: Only with this code typed at the till or on the invoice; empty means it applies by itself.
    coupon_code = models.CharField(max_length=40, blank=True, db_index=True)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    #: A happy hour: only between these times of day (both empty: all day).
    from_time = models.TimeField(null=True, blank=True)
    to_time = models.TimeField(null=True, blank=True)
    #: What it covers: one item, one section, or everything (both empty).
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    category = models.ForeignKey("master_data.Category", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    min_total = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    buy_quantity = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    get_quantity = models.PositiveIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-active", "name", "pk"]

    def __str__(self):
        return self.name


class InvoicePromotion(models.Model):
    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.CASCADE, related_name="promotion_use")
    promotion = models.ForeignKey(Promotion, on_delete=models.PROTECT, related_name="uses")
    discount = models.DecimalField(max_digits=14, decimal_places=2)
    coupon_code = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
