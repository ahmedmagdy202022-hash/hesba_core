from django.db import migrations


# HG-009: operating expenses. Recording one moves real cash (a direct-out
# cashbox operation), so it is sensitive finance and goes only to the roles
# that already move cash. The manager may read expenses but not post them.
PERMISSIONS = (
    ("cashboxes.view_expenses", "عرض المصروفات", "View expenses", ("owner", "manager", "accountant")),
    ("cashboxes.record_expenses", "تسجيل وإلغاء المصروفات", "Record and cancel expenses", ("owner", "accountant")),
)


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")

    for code, name_ar, name_en, role_codes in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(
            code=code,
            defaults={
                "name_ar": name_ar,
                "name_en": name_en,
                "module": "cashboxes",
                "is_report_permission": False,
                "is_sensitive_finance": True,
                "active": True,
            },
        )
        for role in Role.objects.filter(code__in=role_codes):
            RolePermission.objects.update_or_create(role=role, permission=permission, defaults={"allow": True})


def unseed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    RolePermission = apps.get_model("permissions", "RolePermission")
    codes = [row[0] for row in PERMISSIONS]
    RolePermission.objects.filter(permission__code__in=codes).delete()
    Permission.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):
    dependencies = [("permissions", "0005_seed_view_suppliers_permission")]

    operations = [migrations.RunPython(seed, unseed)]
