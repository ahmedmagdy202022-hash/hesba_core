"""PRICE-001: price lists (retail / wholesale / special customer prices).

Nothing here changes how an invoice is posted: a price list only decides the
price the sales screens *suggest* for an item and a customer. The cashier can
still change it, and the invoice keeps whatever price was actually charged.
"""

from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class PriceList(models.Model):
    """A named set of selling prices, e.g. "Wholesale" or "VIP customers".

    ``adjust_percent`` prices every item the list does not name explicitly:
    -10 means 10% below the item's retail price, +5 means 5% above it.
    """

    code = models.CharField(max_length=40, unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    adjust_percent = models.DecimalField(
        max_digits=6, decimal_places=2, default=Decimal("0"),
        validators=[MinValueValidator(Decimal("-100")), MaxValueValidator(Decimal("1000"))],
    )
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    class Meta:
        ordering = ["name_ar"]

    def label(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else self.name_ar

    def __str__(self):
        return self.name_ar


class PriceListItem(models.Model):
    """An explicit price for one item on one list."""

    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name="items")
    item = models.ForeignKey("master_data.Item", on_delete=models.PROTECT, related_name="list_prices")
    price = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["price_list", "item"], name="pricing_one_price_per_item_per_list")]


class CustomerPriceList(models.Model):
    """Which list a customer buys on. No row: the retail price."""

    customer = models.OneToOneField("master_data.Customer", on_delete=models.CASCADE, related_name="price_list_link")
    price_list = models.ForeignKey(PriceList, on_delete=models.PROTECT, related_name="customer_links")
    updated_at = models.DateTimeField(auto_now=True)
