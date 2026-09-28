"""PRINT-003: take the company snapshot when a document is posted (HG-025)."""

from django.db.models.signals import post_save

from .company import company_details
from .models import DocumentCompanySnapshot


LEGAL_FIELDS = ("name", "legal_name", "currency", "phone", "address", "tax_number", "commercial_register", "footer_note")


def _documents():
    from purchases.models import PurchaseInvoice, PurchaseReturn, SupplierPayment
    from sales.models import CustomerPayment, SalesInvoice, SalesReturn

    return {SalesInvoice: "sales_invoice", PurchaseInvoice: "purchase_invoice", SalesReturn: "sales_return",
            PurchaseReturn: "purchase_return", CustomerPayment: "customer_payment", SupplierPayment: "supplier_payment"}


def take_snapshot(kind, object_id):
    details = company_details()
    DocumentCompanySnapshot.objects.get_or_create(kind=kind, object_id=object_id, defaults={"data": {key: details.get(key, "") for key in LEGAL_FIELDS}})


def snapshot_for(kind, object_id):
    row = DocumentCompanySnapshot.objects.filter(kind=kind, object_id=object_id).values_list("data", flat=True).first()
    return row or None


def _on_save(sender, instance, **kwargs):
    if getattr(instance, "status", None) == "posted":
        take_snapshot(_documents()[sender], instance.pk)


def connect():
    for model, kind in _documents().items():
        post_save.connect(_on_save, sender=model, dispatch_uid=f"printing-snapshot-{kind}", weak=False)
