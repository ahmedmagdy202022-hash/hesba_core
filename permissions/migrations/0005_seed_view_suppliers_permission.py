from django.db import migrations


# HG-008: suppliers get their own view permission, so the cashier (who sells
# and collects) no longer sees supplier accounts and balances. Every other
# role that could see suppliers through master_data.view_master_data keeps
# seeing them.
CODE = "master_data.view_suppliers"
ROLE_CODES = ("owner", "manager", "stock_keeper", "accountant", "support")


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")

    permission, _ = Permission.objects.update_or_create(
        code=CODE,
        defaults={
            "name_ar": "عرض الموردين",
            "name_en": "View suppliers",
            "module": "master_data",
            "is_report_permission": False,
            "is_sensitive_finance": False,
            "active": True,
        },
    )
    for role in Role.objects.filter(code__in=ROLE_CODES):
        RolePermission.objects.update_or_create(role=role, permission=permission, defaults={"allow": True})


def unseed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    RolePermission = apps.get_model("permissions", "RolePermission")
    RolePermission.objects.filter(permission__code=CODE).delete()
    Permission.objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("permissions", "0004_seed_approved_financial_permissions")]

    operations = [migrations.RunPython(seed, unseed)]
