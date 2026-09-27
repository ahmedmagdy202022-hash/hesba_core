"""TAX-001: value-added tax per item and per sales line (HG-015).

New tables only; the sales models are not altered. The invoice header keeps
carrying the total tax in ``SalesInvoice.tax_amount`` exactly as before, and
these rows say how that total splits across the lines, which rate each line
used, and how much tax each return gave back.
"""

from decimal import Decimal

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class TaxRate(models.Model):
    """A VAT rate the shop charges, e.g. 14% standard or 0% exempt.

    ``eta_type`` / ``eta_subtype`` are the Egyptian Tax Authority codes the
    e-invoice will need (T1 = VAT); they are stored now and checked against the
    authority's current code list when e-invoicing is built.
    """

    code = models.CharField(max_length=20, unique=True)
    name_ar = models.CharField(max_length=120)
    name_en = models.CharField(max_length=120, blank=True)
    rate = models.DecimalField(max_digits=5, decimal_places=2, validators=[MinValueValidator(Decimal("0")), MaxValueValidator(Decimal("100"))])
    eta_type = models.CharField(max_length=8, blank=True, default="T1")
    eta_subtype = models.CharField(max_length=8, blank=True)
    is_default = models.BooleanField(default=False)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_default", "code"]

    def label(self, lang="ar"):
        name = (self.name_en or self.name_ar) if lang == "en" else self.name_ar
        return f"{name} ({self.rate.normalize():f}%)"

    def __str__(self):
        return self.label()


class ItemTaxRate(models.Model):
    """The rate an item is sold at. No row: the default rate."""

    item = models.OneToOneField("master_data.Item", on_delete=models.CASCADE, related_name="tax_rate_link")
    tax_rate = models.ForeignKey(TaxRate, on_delete=models.PROTECT, related_name="items")


class SalesLineTax(models.Model):
    """The tax charged on one sales line, fixed when the draft is created."""

    line = models.OneToOneField("sales.SalesLine", on_delete=models.CASCADE, related_name="tax")
    tax_rate = models.ForeignKey(TaxRate, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    rate = models.DecimalField(max_digits=5, decimal_places=2)
    taxable_amount = models.DecimalField(max_digits=14, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2)


class SalesReturnLineTax(models.Model):
    """The part of a return line's refund that was tax given back."""

    return_line = models.OneToOneField("sales.SalesReturnLine", on_delete=models.CASCADE, related_name="tax")
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2)


class PurchaseLineTax(models.Model):
    """TAX-002: input VAT on one purchase line, recoverable, kept out of cost."""

    line = models.OneToOneField("purchases.PurchaseLine", on_delete=models.CASCADE, related_name="tax")
    tax_rate = models.ForeignKey(TaxRate, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    rate = models.DecimalField(max_digits=5, decimal_places=2)
    taxable_amount = models.DecimalField(max_digits=14, decimal_places=2)
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2)


class PurchaseReturnLineTax(models.Model):
    """TAX-002: the input VAT a purchase return takes back from the supplier."""

    return_line = models.OneToOneField("purchases.PurchaseReturnLine", on_delete=models.CASCADE, related_name="tax")
    tax_amount = models.DecimalField(max_digits=14, decimal_places=2)
