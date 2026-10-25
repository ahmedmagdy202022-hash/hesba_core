"""T6 (MARKET-PARITY): loyalty points (HG-058, additive tables only).

Points are a ledger per customer: a posted invoice earns them, a redemption spends them, and a cancellation or a
return takes them back with rows of its own (append-only). Spending points only fills the invoice's own discount
before its draft is saved; posting uses the amounts it is given, as for a discount typed by hand. No money moves.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models


class LoyaltySettings(models.Model):
    """The one set of rules (row 1): how points are earned, what one is worth, and when they run out."""

    #: Every this many pounds on a posted invoice earn one point.
    amount_per_point = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("10.00"))
    #: What one point takes off an invoice.
    point_value = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.10"))
    #: The fewest points spent at a time.
    min_points = models.PositiveIntegerField(default=100)
    #: Points run out this many months after they were earned; 0 keeps them for good.
    expiry_months = models.PositiveIntegerField(default=0)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def get(cls):
        return cls.objects.get_or_create(pk=1)[0]


class EntryKind(models.TextChoices):
    EARN = "earn", "Earned"
    SPEND = "spend", "Spent"
    BACK = "back", "Taken back"


class PointsEntry(models.Model):
    customer = models.ForeignKey("master_data.Customer", on_delete=models.PROTECT, related_name="points")
    kind = models.CharField(max_length=6, choices=EntryKind.choices)
    #: Signed: earned points are positive, spent ones negative, and a row taking points back carries its own sign.
    points = models.IntegerField()
    entry_date = models.DateField()
    #: Earned points only: the day after which what is left of them no longer counts.
    expires_on = models.DateField(null=True, blank=True)
    invoice = models.ForeignKey("sales.SalesInvoice", on_delete=models.PROTECT, null=True, blank=True, related_name="points")
    sales_return = models.ForeignKey("sales.SalesReturn", on_delete=models.PROTECT, null=True, blank=True, related_name="points")
    #: A "taken back" row: the earned or spent row it takes from (or gives back to).
    reverses = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="backs")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-entry_date", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["invoice", "kind"], condition=models.Q(kind__in=["earn", "spend"]), name="loyalty_one_earn_and_spend_per_invoice"),
        ]


class InvoiceRedemption(models.Model):
    """The points an invoice spends, written with its draft; the spending itself is booked when it is posted."""

    invoice = models.OneToOneField("sales.SalesInvoice", on_delete=models.CASCADE, related_name="points_redemption")
    points = models.PositiveIntegerField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
