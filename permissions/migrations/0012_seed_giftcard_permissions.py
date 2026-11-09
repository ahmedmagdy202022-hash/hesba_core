from django.db import migrations

# S6 (HG-071): selling and spending a gift card is the till's work (owner, manager, cashier); writing off a card's
# unspent rest, or reversing a card's sale or write-off, is a manager's. Neither gives ``cashboxes.move_cash``.
PERMISSIONS = (
    ("giftcards.sell", "بيع كروت الهدايا والدفع بيها", "Sell gift cards and pay with them", ("owner", "manager", "cashier"), False),
    ("giftcards.breakage", "شطب باقي كارت هدية وعكس بيعه أو شطبه", "Write off a gift card's rest, and reverse a card's sale or write-off", ("owner", "manager"), True),
)


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")
    for code, name_ar, name_en, roles, sensitive in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(code=code, defaults={
            "name_ar": name_ar, "name_en": name_en, "module": "giftcards", "is_report_permission": False,
            "is_sensitive_finance": sensitive, "active": True})
        for role in Role.objects.filter(code__in=roles):
            RolePermission.objects.update_or_create(role=role, permission=permission, defaults={"allow": True})


def unseed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    RolePermission = apps.get_model("permissions", "RolePermission")
    codes = [row[0] for row in PERMISSIONS]
    RolePermission.objects.filter(permission__code__in=codes).delete()
    Permission.objects.filter(code__in=codes).delete()


class Migration(migrations.Migration):
    dependencies = [("permissions", "0011_role_code_custom")]

    operations = [migrations.RunPython(seed, unseed)]
