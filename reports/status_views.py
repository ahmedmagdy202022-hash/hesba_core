from django.shortcuts import render
from django.urls import reverse

from cashboxes.models import Cashbox, CashboxMovement
from inventory.models import StockMovement
from master_data.models import Customer, Item, Location, Supplier
from purchases.models import PurchaseInvoice, PurchaseLine, SupplierPayment
from sales.models import CustomerPayment, SalesInvoice, SalesLine


STATUS_CHECKPOINT_CODE = "100_FOUNDATION_EXPANDED_SAFE_STATUS_COUNTS"


def _admin(app_label, model_name):
    return reverse(f"admin:{app_label}_{model_name}_changelist")


def _item(label, app_label, model_name, count):
    return {"label": label, "url": _admin(app_label, model_name), "note": str(count)}


def status_counts_report(request):
    sections = [
        {"title": "١) بيانات أساسية", "description": "أعداد تشغيلية غير مالية.", "items": [
            _item("Suppliers", "master_data", "supplier", Supplier.objects.count()),
            _item("Customers", "master_data", "customer", Customer.objects.count()),
            _item("Items", "master_data", "item", Item.objects.count()),
            _item("Locations", "master_data", "location", Location.objects.count()),
            _item("Cashboxes", "cashboxes", "cashbox", Cashbox.objects.count()),
        ]},
        {"title": "٢) فواتير متعددة السطور", "description": "أعداد فواتير وسطور فقط.", "items": [
            _item("Purchase Invoices", "purchases", "purchaseinvoice", PurchaseInvoice.objects.count()),
            _item("Purchase Lines", "purchases", "purchaseline", PurchaseLine.objects.count()),
            _item("Sales Invoices", "sales", "salesinvoice", SalesInvoice.objects.count()),
            _item("Sales Lines", "sales", "salesline", SalesLine.objects.count()),
        ]},
        {"title": "٣) مدفوعات وحركات", "description": "أعداد فقط بدون مبلغ أو رصيد.", "items": [
            _item("Supplier Payments", "purchases", "supplierpayment", SupplierPayment.objects.count()),
            _item("Customer Payments", "sales", "customerpayment", CustomerPayment.objects.count()),
            _item("Stock Movements", "inventory", "stockmovement", StockMovement.objects.count()),
            _item("Cashbox Movements", "cashboxes", "cashboxmovement", CashboxMovement.objects.count()),
        ]},
        {"title": "٤) حماية التقرير", "description": "هذه الشاشة تقرأ أعداد فقط.", "items": [
            {"label": "No money totals", "url": "#protected", "note": "مفيش مبالغ"},
            {"label": "No balances", "url": "#protected", "note": "مفيش أرصدة"},
            {"label": "No cost", "url": "#protected", "note": "التكلفة محمية"},
            {"label": "No profit", "url": "#protected", "note": "الربح محمي"},
        ]},
    ]
    return render(request, "reports/status_counts.html", {
        "checkpoint_code": STATUS_CHECKPOINT_CODE,
        "page_title": "تقرير حالة آمن موسّع",
        "page_description": "تقرير قراءة فقط بأعداد فعلية غير حساسة: بدون مبالغ أو أرصدة أو تكلفة أو ربح.",
        "sections": sections,
        "protected_rules": ["التقرير قراءة فقط.", "لا يعرض مبالغ أو أرصدة مالية.", "لا يعرض تكلفة أو ربح.", "لا ينشئ فواتير أو حركات مخزون أو حركات خزنة."],
        "admin_index_url": reverse("admin:index"),
        "dashboard_url": reverse("dashboard_snapshot"),
        "reports_url": reverse("report_hub"),
        "status_url": reverse("status_counts_report"),
        "footer_note": "هذه الشاشة Status Counts فقط. الهدف قياس وجود البيانات بدون كشف finance حساس.",
    })
