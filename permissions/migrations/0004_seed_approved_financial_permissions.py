from django.db import migrations


PERMISSIONS = (
    (
        "cashboxes.manage_cashboxes",
        "إدارة بيانات الخزن الأساسية",
        "Manage cashbox master data",
        "cashboxes",
        False,
        True,
        ("owner", "manager"),
    ),
    (
        "master_data.adjust_opening_balances",
        "تسوية الأرصدة الافتتاحية بعد الاستخدام",
        "Adjust opening balances after use",
        "master_data",
        False,
        True,
        ("owner", "accountant"),
    ),
)


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")

    for code, name_ar, name_en, module, is_report, is_sensitive, role_codes in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "name_ar": name_ar,
                "name_en": name_en,
                "module": module,
                "is_report_permission": is_report,
                "is_sensitive_finance": is_sensitive,
                "active": True,
            },
        )
        for role in Role.objects.filter(code__in=role_codes):
            RolePermission.objects.update_or_create(
                role=role,
                permission=permission,
                defaults={"allow": True},
            )


def unseed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    RolePermission = apps.get_model("permissions", "RolePermission")
    codes = [row[0] for row in PERMISSIONS]
    RolePermission.objects.filter(permission__code__in=codes).delete()
    Permission.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):
    dependencies = [("permissions", "0003_seed_view_all_sales_permission")]

    operations = [migrations.RunPython(seed, unseed)]
