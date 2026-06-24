from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.urls import reverse


CHECKPOINT_CODE = "093_FOUNDATION_FIRST_UI_NAVIGATION_MAP"
DASHBOARD_CHECKPOINT_CODE = "094_FOUNDATION_DASHBOARD_SNAPSHOT"
REPORTS_CHECKPOINT_CODE = "096_FOUNDATION_READ_ONLY_REPORT_HUB"


def _admin_changelist(app_label, model_name):
    return reverse(f"admin:{app_label}_{model_name}_changelist")


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
        "الخزن تتحرك بالمبلغ المدفوع فعليًا فقط.",
        "المخزون يتحرك من خلال حركات مخزون قابلة للتتبع.",
        "التقارير قراءة فقط وليست مكان إدخال بيانات.",
    ]


def _shared_template_context():
    return {
        "business_cycle": _business_cycle(),
        "protected_rules": _protected_rules(),
        "admin_index_url": reverse("admin:index"),
        "dashboard_url": reverse("dashboard_snapshot"),
        "reports_url": reverse("report_hub"),
        "status_url": reverse("status_counts_report"),
        "logout_url": reverse("logout"),
    }


@login_required(login_url="login")
def home(request):
    sections = [
        {"title": "١) البيانات الأساسية", "description": "تجهيز الموردين والعملاء والأصناف والمواقع والخزن قبل أي حركة.", "items": [
            {"label": "الموردين", "url": _admin_changelist("master_data", "supplier"), "note": "طرف الشراء فقط"},
            {"label": "العملاء", "url": _admin_changelist("master_data", "customer"), "note": "طرف البيع فقط"},
            {"label": "الأصناف", "url": _admin_changelist("master_data", "item"), "note": "كود / اسم / تكلفة محمية"},
            {"label": "المخازن / المواقع", "url": _admin_changelist("master_data", "location"), "note": "المخزون = صنف + موقع"},
            {"label": "الخزن", "url": _admin_changelist("cashboxes", "cashbox"), "note": "تتأثر بالمدفوع فقط"},
        ]},
        {"title": "٢) الشراء من المورد", "description": "فاتورة شراء متعددة السطور تزود المخزون وتثبت مستحق المورد فقط بالمتبقي.", "items": [
            {"label": "فواتير الشراء", "url": _admin_changelist("purchases", "purchaseinvoice"), "note": "Header"},
            {"label": "سطور الشراء", "url": _admin_changelist("purchases", "purchaseline"), "note": "Multi-line"},
            {"label": "مدفوعات الموردين", "url": _admin_changelist("purchases", "supplierpayment"), "note": "تقلل مستحق المورد"},
        ]},
        {"title": "٣) المخزون حسب الموقع", "description": "أي زيادة أو نقص مخزون لازم يظهر كحركة قابلة للتتبع.", "items": [
            {"label": "حركات المخزون", "url": _admin_changelist("inventory", "stockmovement"), "note": "شراء / بيع / تحويل / تسوية"},
            {"label": "تقرير المخزون", "url": reverse("report_hub"), "note": "قراءة فقط من الحركات"},
        ]},
        {"title": "٤) البيع للعميل", "description": "فاتورة بيع متعددة السطور تخصم المخزون وتثبت مديونية العميل فقط بالمتبقي.", "items": [
            {"label": "فواتير البيع", "url": _admin_changelist("sales", "salesinvoice"), "note": "Header"},
            {"label": "سطور البيع", "url": _admin_changelist("sales", "salesline"), "note": "تكلفة وربح محميين"},
            {"label": "مدفوعات العملاء", "url": _admin_changelist("sales", "customerpayment"), "note": "تقلل مديونية العميل"},
        ]},
        {"title": "٥) الخزنة والتقارير", "description": "الخزنة تتأثر بالمبلغ المدفوع فعليًا فقط، والتقارير قراءة فقط.", "items": [
            {"label": "حركات الخزن", "url": _admin_changelist("cashboxes", "cashboxmovement"), "note": "Cash in / Cash out"},
            {"label": "Dashboard", "url": reverse("dashboard_snapshot"), "note": "ملخص قراءة فقط"},
            {"label": "Reports", "url": reverse("report_hub"), "note": "مركز التقارير"},
            {"label": "Status", "url": reverse("status_counts_report"), "note": "أعداد آمنة"},
        ]},
    ]
    context = _shared_template_context()
    context.update({"checkpoint_code": CHECKPOINT_CODE, "page_title": "خريطة تشغيل أول شاشة UI", "page_description": "شاشة بسيطة وآمنة للتنقل داخل حِسْبَة.", "sections": sections, "footer_note": "Navigation Map فقط. الإدخال الفعلي من Admin مؤقتًا."})
    return render(request, "reports/home.html", context)


@login_required(login_url="login")
def dashboard_snapshot(request):
    sections = [
        {"title": "١) حالة دورة العمل", "description": "الدورة الأساسية جاهزة كمسار واحد قابل للتوسع.", "items": [
            {"label": "الدورة الكاملة", "url": reverse("report_hub"), "note": "مورد → شراء → مخزون → بيع → عميل → خزنة → تقارير"},
            {"label": "حالة الإدخال", "url": reverse("home"), "note": "Admin مؤقتًا حتى شاشة Transaction آمنة"},
        ]},
        {"title": "٢) KPIs آمنة لاحقًا", "description": "تجهيز أماكن الأرقام بدون عرض ربح أو تكلفة قبل الصلاحيات.", "items": [
            {"label": "عدد الموردين", "url": reverse("status_counts_report"), "note": "قراءة فقط"},
            {"label": "عدد العملاء", "url": reverse("status_counts_report"), "note": "قراءة فقط"},
            {"label": "حالة المخزون", "url": reverse("status_counts_report"), "note": "حسب الصنف والموقع"},
            {"label": "حالة الخزن", "url": reverse("status_counts_report"), "note": "بالمدفوع فعليًا فقط"},
        ]},
        {"title": "٣) حماية الأرقام الحساسة", "description": "التكلفة والربح لا يظهروا قبل صلاحيات حقيقية.", "items": [
            {"label": "الربح", "url": reverse("report_hub"), "note": "Owner فقط لاحقًا"},
            {"label": "التكلفة", "url": reverse("report_hub"), "note": "محمية من Cashier"},
        ]},
        {"title": "٤) الخطوة الجاية", "description": "ربط أرقام قراءة فقط بعد ثبات reports/views والمايجريشن.", "items": [
            {"label": "تقارير Read-only", "url": reverse("report_hub"), "note": "قبل أي شاشة إدخال جديدة"},
            {"label": "تقرير Status", "url": reverse("status_counts_report"), "note": "أعداد فعلية غير حساسة"},
        ]},
    ]
    context = _shared_template_context()
    context.update({"checkpoint_code": DASHBOARD_CHECKPOINT_CODE, "page_title": "Dashboard Snapshot قراءة فقط", "page_description": "أول لقطة داشبورد آمنة بعد تسجيل الدخول.", "sections": sections, "footer_note": "Read-only Snapshot فقط. الربح والتكلفة تظهر بعد صلاحيات حقيقية."})
    return render(request, "reports/home.html", context)


@login_required(login_url="login")
def report_hub(request):
    sections = [
        {"title": "١) تقارير الأطراف", "description": "أرصدة العملاء والموردين تأتي من الفواتير والمدفوعات والمرتجعات فقط.", "items": [
            {"label": "Customer Report", "url": _admin_changelist("sales", "customerledgerentry"), "note": "مبيعات + مدفوعات عملاء فقط"},
            {"label": "Supplier Report", "url": _admin_changelist("purchases", "supplierledgerentry"), "note": "مشتريات + مدفوعات موردين فقط"},
        ]},
        {"title": "٢) تقارير الفواتير", "description": "الفواتير Header + Lines، والحسابات لا تتغير من التقرير.", "items": [
            {"label": "Sales Report", "url": _admin_changelist("sales", "salesinvoice"), "note": "مبيعات مدفوعة / جزئية / آجلة"},
            {"label": "Purchase Report", "url": _admin_changelist("purchases", "purchaseinvoice"), "note": "مشتريات مدفوعة / جزئية / آجلة"},
            {"label": "Status Counts", "url": reverse("status_counts_report"), "note": "أعداد فعلية غير حساسة"},
        ]},
        {"title": "٣) تقارير التشغيل", "description": "المخزون والخزن مبنيين على حركات فعلية قابلة للتتبع.", "items": [
            {"label": "Inventory Report", "url": _admin_changelist("inventory", "stockmovement"), "note": "Item + Location"},
            {"label": "Cashbox Report", "url": _admin_changelist("cashboxes", "cashboxmovement"), "note": "Paid_Now فقط"},
        ]},
        {"title": "٤) تقارير الربح المحمية", "description": "الربح = المبيعات - تكلفة البضاعة المباعة، ويظهر بعد صلاحيات حقيقية.", "items": [
            {"label": "Profit Report", "url": "#protected", "note": "Owner permission required"},
        ]},
    ]
    context = _shared_template_context()
    context.update({"checkpoint_code": REPORTS_CHECKPOINT_CODE, "page_title": "مركز التقارير قراءة فقط", "page_description": "تقارير لا تعدل أرصدة ولا تنشئ حركات.", "sections": sections, "footer_note": "التقارير Read-only فقط."})
    return render(request, "reports/home.html", context)
