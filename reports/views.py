from decimal import Decimal

from django.db.models import Sum
from django.shortcuts import render
from django.urls import reverse

from cashboxes.models import Cashbox, CashboxDirection, CashboxMovement
from inventory.models import StockMovement
from master_data.models import Customer, Item, Location, Supplier
from purchases.models import PurchaseInvoice, PurchaseLine, SupplierLedgerEntry, SupplierPayment
from sales.models import CustomerLedgerEntry, CustomerPayment, SalesInvoice, SalesLine


CHECKPOINT_CODE = "107_WORKING_ERP_OWNER_DASHBOARD"
DASHBOARD_CHECKPOINT_CODE = "107_WORKING_ERP_OWNER_DASHBOARD"
REPORTS_CHECKPOINT_CODE = "107_WORKING_ERP_REPORT_CENTER"


def _admin_changelist(app_label, model_name):
    return reverse(f"admin:{app_label}_{model_name}_changelist")


def _admin_add(app_label, model_name):
    return reverse(f"admin:{app_label}_{model_name}_add")


def _business_cycle():
    return [
        "Supplier",
        "Purchase Invoice",
        "Inventory by Location",
        "Sales Invoice",
        "Customer",
        "Cashbox",
        "Reports",
    ]


def _protected_rules():
    return [
        "المبيعات لا تنشئ مستحقات للموردين.",
        "المشتريات لا تنشئ مديونية للعملاء.",
        "الخزن تتحرك بالمبلغ المدفوع فعليا فقط.",
        "المخزون يتحرك من خلال حركات مخزون قابلة للتتبع.",
        "التقارير قراءة فقط وليست مكان إدخال بيانات.",
        "التصنيع والمقاولات يستخدمان نفس القاعدة: تكلفة محمية، مشروع أو أمر تشغيل، وحركة قابلة للتتبع.",
    ]


def _money(value):
    value = value or Decimal("0")
    return f"{value:,.2f} EGP"


def _count(model):
    try:
        return model.objects.count()
    except Exception:
        return 0


def _sum(model, field, **filters):
    try:
        qs = model.objects.filter(**filters) if filters else model.objects.all()
        return qs.aggregate(total=Sum(field))["total"] or Decimal("0")
    except Exception:
        return Decimal("0")


def _owner_allowed(request):
    user = getattr(request, "user", None)
    return bool(user and user.is_authenticated and (user.is_superuser or user.is_staff))


def _shared_template_context():
    return {
        "business_cycle": _business_cycle(),
        "protected_rules": _protected_rules(),
        "admin_index_url": reverse("admin:index"),
        "dashboard_url": reverse("dashboard_snapshot"),
        "reports_url": reverse("report_hub"),
        "status_url": reverse("status_counts_report"),
    }


def _quick_actions():
    return [
        {"title": "فاتورة بيع", "note": "بيع متعدد السطور", "url": _admin_add("sales", "salesinvoice"), "icon": "بيع"},
        {"title": "فاتورة شراء", "note": "شراء من مورد", "url": _admin_add("purchases", "purchaseinvoice"), "icon": "شراء"},
        {"title": "عميل جديد", "note": "اتصال وواتساب", "url": _admin_add("master_data", "customer"), "icon": "عميل"},
        {"title": "مورد جديد", "note": "حساب مورد مستقل", "url": _admin_add("master_data", "supplier"), "icon": "مورد"},
        {"title": "صنف جديد", "note": "باركود ومخزون", "url": _admin_add("master_data", "item"), "icon": "صنف"},
        {"title": "حركة خزنة", "note": "مدفوع فعلي فقط", "url": _admin_add("cashboxes", "cashboxmovement"), "icon": "خزنة"},
    ]


def _owner_kpis(request):
    protected = not _owner_allowed(request)
    sales_total = _sum(SalesInvoice, "total_amount")
    purchases_total = _sum(PurchaseInvoice, "total_amount")
    cash_in = _sum(CashboxMovement, "amount", direction=CashboxDirection.IN)
    cash_out = _sum(CashboxMovement, "amount", direction=CashboxDirection.OUT)
    profit = _sum(SalesLine, "line_profit_amount")
    return [
        {"label": "إجمالي المبيعات", "value": _money(sales_total), "note": "من فواتير البيع", "sensitive": False},
        {"label": "إجمالي المشتريات", "value": _money(purchases_total), "note": "من فواتير الشراء", "sensitive": False},
        {"label": "صافي حركة الخزن", "value": _money(cash_in - cash_out), "note": "مدفوع فعلي فقط", "sensitive": False},
        {"label": "صافي الربح", "value": "محمي" if protected else _money(profit), "note": "Sales - COGS", "sensitive": True},
        {"label": "العملاء", "value": str(_count(Customer)), "note": "كروت اتصال وواتساب", "sensitive": False},
        {"label": "الموردين", "value": str(_count(Supplier)), "note": "لا يتأثرون بالمبيعات", "sensitive": False},
        {"label": "الأصناف", "value": str(_count(Item)), "note": "Stock tracked / Services", "sensitive": False},
        {"label": "حركات المخزون", "value": str(_count(StockMovement)), "note": "Item + Location", "sensitive": False},
    ]


def _editions():
    return [
        {"title": "Store Edition", "note": "محلات ومخازن: أصناف، باركود، مواقع، تحويلات، حد أدنى للمخزون.", "chips": ["Items", "Barcode", "Stock", "Locations"]},
        {"title": "Services & Telecom", "note": "الخدمة كصنف، تكلفة مخفية، بيع سريع، خزنة، عميل ومورد.", "chips": ["Services", "Balance", "Cashbox", "Profit lock"]},
        {"title": "Construction", "note": "مشروع كمركز تكلفة، مشتريات موقع، عهد، مقاول باطن، مستخلصات لاحقا.", "chips": ["Projects", "Site costs", "Subcontractors", "Extracts"]},
        {"title": "Industrial", "note": "وصفات تصنيع، خامات، هالك، تشغيل تحت التنفيذ، منتج تام لاحقا.", "chips": ["Recipe", "Raw material", "WIP", "Finished goods"]},
    ]


def _automations():
    return [
        {"title": "تنبيه مخزون منخفض", "note": "من Min Stock حسب Item + Location."},
        {"title": "تحصيل عميل", "note": "من Customer ledger فقط بدون لمس الموردين."},
        {"title": "سداد مورد", "note": "من Supplier ledger فقط بدون لمس العملاء."},
        {"title": "إقفال فترة", "note": "شهري أو ربع سنوي مع Audit log."},
        {"title": "Usage Status", "note": "Green, Yellow, Orange, Red لحماية تكلفة التشغيل."},
        {"title": "مراجعة ربح محمية", "note": "تظهر للمالك فقط بعد الصلاحيات."},
    ]


def _sections():
    return [
        {"title": "١) البيانات الأساسية", "description": "تجهيز الموردين والعملاء والأصناف والمواقع والخزن قبل أي حركة.", "items": [
            {"label": "الموردين", "url": _admin_changelist("master_data", "supplier"), "note": "شراء ومدفوعات فقط"},
            {"label": "العملاء", "url": _admin_changelist("master_data", "customer"), "note": "بيع ومدفوعات فقط"},
            {"label": "الأصناف", "url": _admin_changelist("master_data", "item"), "note": "منتج أو خدمة أو رصيد"},
            {"label": "المواقع", "url": _admin_changelist("master_data", "location"), "note": "مخزن أو فرع أو موقع"},
            {"label": "الخزن", "url": _admin_changelist("cashboxes", "cashbox"), "note": "حركة نقدية فعلية"},
        ]},
        {"title": "٢) دورة الشراء", "description": "شراء متعدد السطور يزيد المخزون ويثبت مستحق المورد بالمتبقي فقط.", "items": [
            {"label": "فواتير الشراء", "url": _admin_changelist("purchases", "purchaseinvoice"), "note": str(_count(PurchaseInvoice))},
            {"label": "سطور الشراء", "url": _admin_changelist("purchases", "purchaseline"), "note": str(_count(PurchaseLine))},
            {"label": "مدفوعات الموردين", "url": _admin_changelist("purchases", "supplierpayment"), "note": str(_count(SupplierPayment))},
            {"label": "دفتر المورد", "url": _admin_changelist("purchases", "supplierledgerentry"), "note": str(_count(SupplierLedgerEntry))},
        ]},
        {"title": "٣) دورة البيع", "description": "بيع متعدد السطور يخصم المخزون ويثبت مديونية العميل بالمتبقي فقط.", "items": [
            {"label": "فواتير البيع", "url": _admin_changelist("sales", "salesinvoice"), "note": str(_count(SalesInvoice))},
            {"label": "سطور البيع", "url": _admin_changelist("sales", "salesline"), "note": str(_count(SalesLine))},
            {"label": "مدفوعات العملاء", "url": _admin_changelist("sales", "customerpayment"), "note": str(_count(CustomerPayment))},
            {"label": "دفتر العميل", "url": _admin_changelist("sales", "customerledgerentry"), "note": str(_count(CustomerLedgerEntry))},
        ]},
        {"title": "٤) التشغيل والتقارير", "description": "مخزون وخزن وتقارير قراءة فقط لقرار المالك.", "items": [
            {"label": "حركات المخزون", "url": _admin_changelist("inventory", "stockmovement"), "note": str(_count(StockMovement))},
            {"label": "حركات الخزن", "url": _admin_changelist("cashboxes", "cashboxmovement"), "note": str(_count(CashboxMovement))},
            {"label": "Dashboard", "url": reverse("dashboard_snapshot"), "note": "Owner view"},
            {"label": "Status", "url": reverse("status_counts_report"), "note": "Safe counts"},
        ]},
    ]


def _page_context(request, page_title, page_description, checkpoint_code, footer_note):
    context = _shared_template_context()
    context.update({
        "checkpoint_code": checkpoint_code,
        "page_title": page_title,
        "page_description": page_description,
        "sections": _sections(),
        "footer_note": footer_note,
        "quick_actions": _quick_actions(),
        "owner_kpis": _owner_kpis(request),
        "editions": _editions(),
        "automations": _automations(),
    })
    return context


def home(request):
    return render(request, "reports/home.html", _page_context(
        request,
        "حِسْبَة ERP Control Center",
        "واجهة تشغيل فعلية تربط المورد، الشراء، المخزون، البيع، العميل، الخزنة، والتقارير مع تجهيز قطاعات المتاجر والخدمات والمقاولات والتصنيع.",
        CHECKPOINT_CODE,
        "هذه الشاشة لا تكسر القواعد الحسابية. كل إدخال يذهب لمساره الصحيح، والتقارير قراءة فقط.",
    ))


def dashboard_snapshot(request):
    return render(request, "reports/home.html", _page_context(
        request,
        "Dashboard المالك",
        "أرقام تشغيلية ومالية منظمة لاتخاذ القرار، مع حماية التكلفة والربح حسب الصلاحيات.",
        DASHBOARD_CHECKPOINT_CODE,
        "الداشبورد يقرأ من الجداول ولا ينشئ فواتير أو حركات.",
    ))


def report_hub(request):
    return render(request, "reports/home.html", _page_context(
        request,
        "مركز التقارير",
        "تقارير العملاء والموردين والمخزون والخزن والربح بصورة قراءة فقط.",
        REPORTS_CHECKPOINT_CODE,
        "التقارير لا تغير أرصدة، ولا تنشئ حركات، ولا تعرض الربح لغير المصرح له.",
    ))
