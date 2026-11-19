"""HG-140 (Manufacturing F8): a production worker, and one narrow permission for the floor screen.

``manufacturing.record_stage`` lets its holder see the floor screen and record the stages of production orders. The new
role «عامل إنتاج» holds it, ``inventory.view_stock`` and, by HG-031's rule (whoever sees stock sees the group's
quantities, never a cost without the cost permission), ``inventory.view_group_stock``; nothing else: no cost, no sales,
no cash, and no general stock adjustment.

The owner and the manager are given the new permission too, as HG-048 and HG-084 did with theirs: nobody hands out a
permission they do not hold (FB-ROLES), so without it the owner could neither copy the ready role nor tick this
permission in a role of their own. It lets them do nothing new: both already hold ``inventory.adjust_stock``, which opens
the floor screen and every action on it. No other existing role is given it and none loses a grant.

The role code is not limited to a list of choices since ``0011_role_code_custom``, so this is a seed only (HG-048's
``0010`` also altered the column's choices, which no longer exist).
"""

from django.db import migrations

ROLE = ("production_worker", "عامل إنتاج", "Production worker",
        "يشوف شغله على شاشة العامل ويسجّل بداية ونهاية مراحل أوامر الإنتاج والسليم والمعيب، ويشوف كميات المخزون؛ من غير تكلفة ولا بيع ولا خزنة ولا تسوية مخزون.")

#: code, Arabic, English, module
PERMISSION = ("manufacturing.record_stage", "تسجيل مراحل أوامر الإنتاج من شاشة العامل", "Record production order stages from the floor screen", "inventory")

#: Existing permissions the new role also holds.
EXISTING = ("inventory.view_stock", "inventory.view_group_stock")

#: Existing roles that also hold the new permission, so that they can hand it out (see the note above).
ALSO_GRANTED = ("owner", "manager")


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
    for holder in Role.objects.filter(code__in=ALSO_GRANTED, is_system_role=True):
        RolePermission.objects.update_or_create(role=holder, permission=permission, defaults={"allow": True})


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
