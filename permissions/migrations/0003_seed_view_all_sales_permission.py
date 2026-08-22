"""Separate seeing everyone's sales from seeing your own.

docs/dashboard_kpis.md gives a cashier "my sales today" and "my invoice count",
while a manager or accountant sees the whole business. The matrix had no way to
say that: every role that could read a sales report could read all of it, so the
only way to scope a cashier's figures was to name the role in the dashboard.

This adds the distinction as a permission, so the scoping rule stays a
permission question and the dashboard never has to ask who someone is.
"""

from django.db import migrations


PERMISSION = (
    "reports.view_all_sales_report",
    "عرض مبيعات كل المستخدمين",
    "View sales for all users",
    "reports",
    True,
    False,
)

# Everyone who already reads sales reports, except the cashier: a cashier keeps
# reports.view_sales_report and is therefore scoped to their own invoices.
GRANT_TO = ("owner", "manager", "accountant")


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")

    code, name_ar, name_en, module, is_report, is_sensitive = PERMISSION
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

    for role_code in GRANT_TO:
        role = Role.objects.filter(code=role_code).first()
        if role is None:
            continue
        RolePermission.objects.update_or_create(
            role=role, permission=permission, defaults={"allow": True}
        )


def unseed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    RolePermission = apps.get_model("permissions", "RolePermission")

    code = PERMISSION[0]
    RolePermission.objects.filter(permission__code=code).delete()
    Permission.objects.filter(code=code).delete()


class Migration(migrations.Migration):
    dependencies = [("permissions", "0002_seed_foundation_roles_permissions")]

    operations = [migrations.RunPython(seed, unseed)]
