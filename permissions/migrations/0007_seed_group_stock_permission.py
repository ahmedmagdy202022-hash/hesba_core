from django.db import migrations

# ENT-001 (HG-031): seeing stock across every entity of the group — quantities
# only. Granted to every role that can already see stock, and to the cashier so
# a sale can say "not here, but the factory has 40". Cost still needs
# inventory.view_cost.
CODE = "inventory.view_group_stock"


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    RolePermission = apps.get_model("permissions", "RolePermission")
    permission, _ = Permission.objects.update_or_create(code=CODE, defaults={
        "name_ar": "عرض المخزون في كل الكيانات", "name_en": "View stock across all entities", "module": "inventory",
        "is_report_permission": False, "is_sensitive_finance": False, "active": True,
    })
    Role = apps.get_model("permissions", "Role")
    holders = set(RolePermission.objects.filter(permission__code="inventory.view_stock", allow=True).values_list("role_id", flat=True))
    holders |= set(Role.objects.filter(code="cashier").values_list("pk", flat=True))
    for role_id in holders:
        RolePermission.objects.update_or_create(role_id=role_id, permission=permission, defaults={"allow": True})


def unseed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    RolePermission = apps.get_model("permissions", "RolePermission")
    RolePermission.objects.filter(permission__code=CODE).delete()
    Permission.objects.filter(code=CODE).delete()


class Migration(migrations.Migration):
    dependencies = [("permissions", "0006_seed_expense_permissions")]

    operations = [migrations.RunPython(seed, unseed)]
