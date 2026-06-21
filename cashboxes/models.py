from django.db import models


class Cashbox(models.Model):
    """Money holder.

    Cashbox balances must be calculated from opening balance plus real cashbox
    movements only. Invoices later affect cashboxes only by the actual paid
    amount, never by invoice total or remaining due.
    """

    cashbox_code = models.CharField(max_length=80, unique=True)
    name_ar = models.CharField(max_length=255)
    name_en = models.CharField(max_length=255, blank=True)
    opening_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    currency = models.CharField(max_length=10, default="EGP")
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    import_batch_id = models.CharField(max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["cashbox_code"]
        verbose_name = "Cashbox"
        verbose_name_plural = "Cashboxes"

    def __str__(self):
        return f"{self.cashbox_code} - {self.name_ar}"
