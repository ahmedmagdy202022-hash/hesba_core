from django.shortcuts import render
from django.urls import reverse

from master_data.models import Customer, Item, Location, Supplier
from purchases.models import PurchaseInvoice
from sales.models import SalesInvoice


def status_counts_report(request):
    sections = [{
        "title": "Safe Status Counts",
        "description": "Read-only counts.",
        "items": [
            {"label": "Suppliers", "url": reverse("admin:master_data_supplier_changelist"), "note": str(Supplier.objects.count())},
            {"label": "Customers", "url": reverse("admin:master_data_customer_changelist"), "note": str(Customer.objects.count())},
            {"label": "Items", "url": reverse("admin:master_data_item_changelist"), "note": str(Item.objects.count())},
            {"label": "Locations", "url": reverse("admin:master_data_location_changelist"), "note": str(Location.objects.count())},
            {"label": "Purchase Invoices", "url": reverse("admin:purchases_purchaseinvoice_changelist"), "note": str(PurchaseInvoice.objects.count())},
            {"label": "Sales Invoices", "url": reverse("admin:sales_salesinvoice_changelist"), "note": str(SalesInvoice.objects.count())},
        ],
    }]
    return render(request, "reports/home.html", {
        "checkpoint_code": "099_FOUNDATION_SAFE_STATUS_COUNTS",
        "page_title": "Safe Status Counts",
        "page_description": "Read-only real counts.",
        "business_cycle": ["Supplier", "Purchase Invoice", "Inventory by Location", "Sales Invoice", "Customer", "Cashbox", "Reports"],
        "sections": sections,
        "protected_rules": ["Read-only counts.", "Finance values are protected."],
        "admin_index_url": reverse("admin:index"),
        "dashboard_url": reverse("dashboard_snapshot"),
        "reports_url": reverse("report_hub"),
        "footer_note": "Status counts only.",
    })
