from django.db import migrations

# FB-27: who may approve what the owner holds for a second person. The owner and the manager by role.
PERMISSIONS = (
    ("approvals.approve", "الموافقة على العمليات اللي محتاجة موافقة", "Approve the actions that need a second person", ("owner", "manager")),
)


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")
    for code, name_ar, name_en, roles in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(code=code, defaults={
            "name_ar": name_ar, "name_en": name_en, "module": "permissions", "is_report_permission": False,
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
    dependencies = [("approvals", "0001_initial"), ("permissions", "0011_role_code_custom")]

    operations = [migrations.RunPython(seed, unseed)]
