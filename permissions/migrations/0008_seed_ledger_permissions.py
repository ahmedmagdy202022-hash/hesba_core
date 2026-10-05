from django.db import migrations

# GL-001: the general ledger. Reading it is finance; changing the chart is the
# owner's and the accountant's.
PERMISSIONS = (
    ("accounting.view_ledger", "عرض الحسابات والقيود والقوائم المالية", "View accounts, journals and statements", ("owner", "manager", "accountant")),
    ("accounting.manage_accounts", "إدارة دليل الحسابات", "Manage the chart of accounts", ("owner", "accountant")),
)


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")
    for code, name_ar, name_en, roles in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(code=code, defaults={
            "name_ar": name_ar, "name_en": name_en, "module": "accounting", "is_report_permission": True,
            "is_sensitive_finance": True, "active": True})
        for role in Role.objects.filter(code__in=roles):
            RolePermission.objects.update_or_create(role=role, permission=permission, defaults={"allow": True})


def unseed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    RolePermission = apps.get_model("permissions", "RolePermission")
    codes = [row[0] for row in PERMISSIONS]
    RolePermission.objects.filter(permission__code__in=codes).delete()
    Permission.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):
    dependencies = [("permissions", "0007_seed_group_stock_permission")]

    operations = [migrations.RunPython(seed, unseed)]
