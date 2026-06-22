from django.shortcuts import render
from django.urls import reverse

from cashboxes.models import Cashbox, CashboxMovement
from inventory.models import StockMovement
from master_data.models import Customer, Item, Location, Supplier
from purchases.models import PurchaseInvoice, PurchaseLine, SupplierPayment
from sales.models import CustomerPayment, SalesInvoice, SalesLine


STATUS_CHECKPOINT_CODE = "100_FOUNDATION_EXPANDED_SAFE_STATUS_COUNTS"


def _admin_url(app_label, model_name):
    return reverse(f"admin:{app_label}_{model_name}_changelist")


def _count_item(label, app_label, model_name, count):
    return {
        "label": label,
        "url": _admin_url(app_label, model_name),
        "note": str(count),
    }


def status_counts_report(request):
    """Read-only non-sensitive status counts.

    This report intentionally shows counts only. It must not expose money,
    balances, costs, profit, or due values. It does not create or update data.
    """

    sections = [
        {
            "title": "١) بيانات أساسية",
            "description": "أعداد تشغيلية غير مالية.",
            "items": [
                _count_item("Suppliers", "master_data", "supplier", Supplier.objects.count()),
                _count_item("Customers", "master_data", "customer", Customer.objects.count()),
                _count_item("Items", "master_data", "item", Item.objects.count()),
                _count_item("Locations", "master_data", "location", Location.objects.count()),
                _count_item("Cashboxes", "cashboxes", "cashbox", Cashbox.objects.count()),
            ],
        },
        {
            "title": "٢) فواتير متعددة السطور",
            "description": "أعداد فواتير وسطور فقط، بدون إجماليات مالية.",
            "items": [
                _count_item("Purchase Invoices", "purchases", "purchaseinvoice", PurchaseInvoice.objects.count()),
                _count_item("Purchase Lines", "purchases", "purchaseline", PurchaseLine.objects.count()),
                _count_item("Sales Invoices", "sales", "salesinvoice", SalesInvoice.objects.count()),
                _count_item("Sales Lines", "sales", "salesline", SalesLine.objects.count()),
            ],
        },
        {
            "title": "٣) مدفوعات وحركات",
            "description": "أعداد فقط لتأكيد وجود الحركات، بدون مبلغ أو رصيد.",
            "items": [
                _count_item("Supplier Payments", "purchases", "supplierpayment", SupplierPayment.objects.count()),
                _count_item("Customer Payments", "sales", "customerpayment", CustomerPayment.objects.count()),
                _count_item("Stock Movements", "inventory", "stockmovement", StockMovement.objects.count()),
                _count_item("Cashbox Movements", "cashboxes", "cashboxmovement", CashboxMovement.objects.count()),
            ],
        },
        {
            "title": "٤) حماية التقرير",
            "description": "هذه الشاشة تقرأ أعداد فقط ولا تعرض finance حساس.",
            "items": [
                {"label": "No money totals", "url": "#reports", "note": "مفيش مبالغ"},
                {"label": "No balances", "url": "#reports", "note": "مفيش أرصدة"},
                {"label": "No cost", "url": "#reports", "note": "التكلفة محمية"},
                {"label": "No profit", "url": "#reports", "note": "الربح محمي"},
            ],
        },
    ]

    return render(
        request,
        "reports/home.html",
        {
            "checkpoint_code": STATUS_CHECKPOINT_CODE,
            "page_title": "تقرير حالة آمن موسّع",
            "page_description": "تقرير قراءة فقط بأعداد فعلية غير حساسة: بدون مبالغ أو أرصدة أو تكلفة أو ربح.",
            "business_cycle": [
                "Supplier",
                "Purchase Invoice",
                "Inventory by Location",
                "Sales Invoice",
                "Customer",
                "Cashbox",
                "Reports",
            ],
            "sections": sections,
            "protected_rules": [
                "التقرير قراءة فقط.",
                "لا يعرض مبالغ أو أرصدة مالية.",
                "لا يعرض تكلفة أو ربح.",
                "لا ينشئ فواتير أو حركات مخزون أو حركات خزنة.",
                "الأرقام الحساسة تظهر لاحقًا بعد صلاحيات حقيقية.",
            ],
            "admin_index_url": reverse("admin:index"),
            "dashboard_url": reverse("dashboard_snapshot"),
            "reports_url": reverse("report_hub"),
            "footer_note": "هذه الشاشة Status Counts فقط. الهدف قياس وجود البيانات بدون كشف finance حساس.",
        },
    )
