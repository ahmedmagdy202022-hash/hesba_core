"""HG-140 (Manufacturing F8): a production worker, and one narrow permission for the floor screen.

``manufacturing.record_stage`` lets its holder see the floor screen and record the stages of production orders. The new
role «عامل إنتاج» holds it and ``inventory.view_stock``, and nothing else: no cost, no sales, no cash, and no general
stock adjustment. No existing role is given the new permission and none loses a grant.

The role code is not limited to a list of choices since ``0011_role_code_custom``, so this is a seed only (HG-048's
``0010`` also altered the column's choices, which no longer exist).
"""

from django.db import migrations

ROLE = ("production_worker", "عامل إنتاج", "Production worker",
        "يشوف شغله على شاشة العامل ويسجّل بداية ونهاية مراحل أوامر الإنتاج والسليم والمعيب؛ من غير تكلفة ولا بيع ولا خزنة ولا تسوية مخزون.")

#: code, Arabic, English, module
PERMISSION = ("manufacturing.record_stage", "تسجيل مراحل أوامر الإنتاج من شاشة العامل", "Record production order stages from the floor screen", "inventory")

#: Existing permissions the new role also holds.
EXISTING = ("inventory.view_stock",)


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")
    code, name_ar, name_en, description = ROLE
    role, _ = Role.objects.update_or_create(code=code, defaults={"name_ar": name_ar, "name_en": name_en, "description": description,
                                                                 "is_system_role": True, "active": True})
    code, name_ar, name_en, module = PERMISSION
    permission, _ = Permission.objects.update_or_create(code=code, defaults={
        "name_ar": name_ar, "name_en": name_en, "module": module, "is_report_permission": False,
        "is_sensitive_finance": False, "active": True})
    RolePermission.objects.update_or_create(role=role, permission=permission, defaults={"allow": True})
    for existing in Permission.objects.filter(code__in=EXISTING):
        RolePermission.objects.update_or_create(role=role, permission=existing, defaults={"allow": True})


def unseed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")
    RolePermission.objects.filter(permission__code=PERMISSION[0]).delete()
    Permission.objects.filter(code=PERMISSION[0]).delete()
    Role.objects.filter(code=ROLE[0], is_system_role=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("permissions", "0012_seed_giftcard_permissions"),
    ]

    operations = [migrations.RunPython(seed, unseed)]
