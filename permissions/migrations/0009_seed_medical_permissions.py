from django.db import migrations

# MED-001: patient files are medical secrets. Reading and writing them is the
# owner's and the manager's by role; doctors (users linked to an employee)
# get it from that link. Reception (cashier) books and bills but does not read.
PERMISSIONS = (
    ("medical.view_records", "عرض الملفات الطبية للمرضى", "View patient medical files", ("owner", "manager")),
    ("medical.write_records", "تسجيل الكشوفات وتعديل الملف الطبي", "Record visits and edit medical files", ("owner", "manager")),
)


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")
    for code, name_ar, name_en, roles in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(code=code, defaults={
            "name_ar": name_ar, "name_en": name_en, "module": "medical", "is_report_permission": False,
            "is_sensitive_finance": False, "active": True})
        for role in Role.objects.filter(code__in=roles):
            RolePermission.objects.update_or_create(role=role, permission=permission, defaults={"allow": True})


def unseed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    RolePermission = apps.get_model("permissions", "RolePermission")
    codes = [row[0] for row in PERMISSIONS]
    RolePermission.objects.filter(permission__code__in=codes).delete()
    Permission.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):
    dependencies = [("permissions", "0008_seed_ledger_permissions")]

    operations = [migrations.RunPython(seed, unseed)]
