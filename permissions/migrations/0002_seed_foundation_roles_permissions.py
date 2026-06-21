from django.db import migrations


PERMISSIONS = [
    ("settings.view_settings", "عرض الإعدادات", "View settings", "settings", False, False),
    ("settings.manage_settings", "إدارة الإعدادات", "Manage settings", "settings", False, True),
    ("permissions.manage_roles", "إدارة الأدوار والصلاحيات", "Manage roles and permissions", "settings", False, True),
    ("settings.manage_support_access", "إدارة صلاحية الدعم المؤقت", "Manage temporary support access", "settings", False, True),
    ("master_data.view_master_data", "عرض البيانات الأساسية", "View master data", "master_data", False, False),
    ("master_data.manage_items", "إدارة الأصناف والخدمات", "Manage items and services", "master_data", False, False),
    ("master_data.manage_parties", "إدارة العملاء والموردين", "Manage customers and suppliers", "master_data", False, False),
    ("master_data.manage_locations", "إدارة المواقع والمخازن", "Manage locations", "master_data", False, False),
    ("inventory.view_stock", "عرض المخزون", "View stock", "inventory", False, False),
    ("inventory.manage_movements", "إدارة حركات المخزون", "Manage stock movements", "inventory", False, False),
    ("inventory.transfer_stock", "تحويل مخزون بين المواقع", "Transfer stock", "inventory", False, False),
    ("inventory.adjust_stock", "تسوية المخزون", "Adjust stock", "inventory", False, True),
    ("inventory.view_cost", "عرض تكلفة المخزون", "View inventory cost", "inventory", False, True),
    ("purchases.create_purchase_invoice", "إنشاء فاتورة شراء", "Create purchase invoice", "purchases", False, False),
    ("purchases.view_purchase_invoices", "عرض فواتير الشراء", "View purchase invoices", "purchases", False, False),
    ("purchases.pay_supplier", "تسجيل سداد لمورد", "Record supplier payment", "purchases", False, True),
    ("purchases.return_purchase", "تسجيل مرتجع شراء", "Record purchase return", "purchases", False, False),
    ("sales.create_sales_invoice", "إنشاء فاتورة بيع", "Create sales invoice", "sales", False, False),
    ("sales.view_sales_invoices", "عرض فواتير البيع", "View sales invoices", "sales", False, False),
    ("sales.receive_customer_payment", "تسجيل تحصيل من عميل", "Record customer payment", "sales", False, False),
    ("sales.return_sale", "تسجيل مرتجع بيع", "Record sales return", "sales", False, False),
    ("cashboxes.view_cashboxes", "عرض الخزن", "View cashboxes", "cashboxes", False, False),
    ("cashboxes.move_cash", "تسجيل حركة خزنة فعلية", "Record real cash movement", "cashboxes", False, True),
    ("cashboxes.view_finance", "عرض التفاصيل المالية للخزن", "View cashbox finance details", "cashboxes", False, True),
    ("reports.view_sales_report", "عرض تقرير المبيعات", "View sales report", "reports", True, False),
    ("reports.view_purchase_report", "عرض تقرير المشتريات", "View purchase report", "reports", True, False),
    ("reports.view_inventory_report", "عرض تقرير المخزون", "View inventory report", "reports", True, False),
    ("reports.view_customer_report", "عرض تقرير العملاء", "View customer report", "reports", True, False),
    ("reports.view_supplier_report", "عرض تقرير الموردين", "View supplier report", "reports", True, True),
    ("reports.view_cashbox_report", "عرض تقرير الخزن", "View cashbox report", "reports", True, True),
    ("reports.view_profit_report", "عرض تقرير الأرباح", "View profit report", "reports", True, True),
    ("reports.export_reports", "تصدير التقارير", "Export reports", "reports", True, True),
    ("closing.run_closing", "تنفيذ إقفال فترة", "Run period closing", "closing", False, True),
    ("closing.reopen_period", "إعادة فتح فترة مقفولة", "Reopen closed period", "closing", False, True),
    ("audit.view_audit_log", "عرض سجل المراجعة", "View audit log", "audit", True, True),
    ("imports.run_import", "تنفيذ استيراد بيانات", "Run data import", "imports", False, True),
    ("barcode.print_labels", "طباعة باركود", "Print barcode labels", "barcode", False, False),
]

ROLES = [
    ("owner", "المالك", "Owner", "يرى ويدير كل شيء داخل قاعدة بيانات العميل."),
    ("manager", "المدير", "Manager", "يدير التشغيل اليومي بدون صلاحيات المالك الحساسة."),
    ("cashier", "الكاشير", "Cashier", "يسجل البيع والتحصيل فقط بدون تكلفة أو ربح أو تقارير مالية حساسة."),
    ("stock_keeper", "أمين المخزن", "Stock Keeper", "يدير الأصناف والمخزون والتحويلات بدون ربح أو خزن مالية."),
    ("accountant", "المحاسب", "Accountant", "يتابع الخزن والعملاء والموردين والتقارير المالية بدون صلاحية ربح افتراضية."),
    ("support", "الدعم المؤقت", "Support", "صلاحية محدودة ومؤقتة عند منح العميل إذن دعم."),
]

ROLE_PERMISSIONS = {
    "owner": [code for code, *_ in PERMISSIONS],
    "manager": [
        "settings.view_settings",
        "master_data.view_master_data",
        "master_data.manage_items",
        "master_data.manage_parties",
        "master_data.manage_locations",
        "inventory.view_stock",
        "inventory.manage_movements",
        "inventory.transfer_stock",
        "inventory.adjust_stock",
        "purchases.create_purchase_invoice",
        "purchases.view_purchase_invoices",
        "purchases.return_purchase",
        "sales.create_sales_invoice",
        "sales.view_sales_invoices",
        "sales.receive_customer_payment",
        "sales.return_sale",
        "cashboxes.view_cashboxes",
        "reports.view_sales_report",
        "reports.view_purchase_report",
        "reports.view_inventory_report",
        "reports.view_customer_report",
        "barcode.print_labels",
    ],
    "cashier": [
        "master_data.view_master_data",
        "sales.create_sales_invoice",
        "sales.view_sales_invoices",
        "sales.receive_customer_payment",
        "cashboxes.view_cashboxes",
        "reports.view_sales_report",
    ],
    "stock_keeper": [
        "master_data.view_master_data",
        "master_data.manage_items",
        "master_data.manage_locations",
        "inventory.view_stock",
        "inventory.manage_movements",
        "inventory.transfer_stock",
        "inventory.adjust_stock",
        "purchases.create_purchase_invoice",
        "purchases.view_purchase_invoices",
        "reports.view_inventory_report",
        "barcode.print_labels",
    ],
    "accountant": [
        "master_data.view_master_data",
        "master_data.manage_parties",
        "purchases.view_purchase_invoices",
        "purchases.pay_supplier",
        "sales.view_sales_invoices",
        "sales.receive_customer_payment",
        "cashboxes.view_cashboxes",
        "cashboxes.move_cash",
        "cashboxes.view_finance",
        "reports.view_sales_report",
        "reports.view_purchase_report",
        "reports.view_customer_report",
        "reports.view_supplier_report",
        "reports.view_cashbox_report",
        "reports.export_reports",
    ],
    "support": [
        "settings.view_settings",
        "master_data.view_master_data",
        "reports.view_inventory_report",
    ],
}


def seed_permissions(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")

    permission_by_code = {}
    for code, name_ar, name_en, module, is_report_permission, is_sensitive_finance in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "name_ar": name_ar,
                "name_en": name_en,
                "module": module,
                "is_report_permission": is_report_permission,
                "is_sensitive_finance": is_sensitive_finance,
                "active": True,
            },
        )
        permission_by_code[code] = permission

    for code, name_ar, name_en, description in ROLES:
        role, _ = Role.objects.update_or_create(
            code=code,
            defaults={
                "name_ar": name_ar,
                "name_en": name_en,
                "description": description,
                "is_system_role": True,
                "active": True,
            },
        )
        for permission_code in ROLE_PERMISSIONS[code]:
            RolePermission.objects.update_or_create(
                role=role,
                permission=permission_by_code[permission_code],
                defaults={"allow": True},
            )


def unseed_permissions(apps, schema_editor):
    RolePermission = apps.get_model("permissions", "RolePermission")
    Role = apps.get_model("permissions", "Role")
    Permission = apps.get_model("permissions", "Permission")

    role_codes = [code for code, *_ in ROLES]
    permission_codes = [code for code, *_ in PERMISSIONS]
    RolePermission.objects.filter(role__code__in=role_codes, permission__code__in=permission_codes).delete()
    Role.objects.filter(code__in=role_codes, is_system_role=True).delete()
    Permission.objects.filter(code__in=permission_codes).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("permissions", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_permissions, unseed_permissions),
    ]
