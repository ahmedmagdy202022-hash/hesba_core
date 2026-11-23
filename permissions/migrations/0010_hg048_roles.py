"""HG-048: a waiter (restaurants) and a site engineer (contracting), and five narrow permissions.

The existing roles keep every grant they had; owner and manager also get the new
permissions (they could already do all of it through broader ones), and the
cashier gets taking orders and making missing plates at payment.
"""

from django.db import migrations, models

ROLES = (
    ("waiter", "الويتر", "Waiter", "يفتح الطاولات والطلبات ويبعت للمطبخ وينقل الطاولة، من غير ما يحاسب أو يشوف الفواتير والخزنة والتكلفة."),
    ("site_engineer", "مهندس الموقع", "Site engineer",
     "يشوف المشاريع والمقايسة، يسجل الكميات المنفذة في مستخلص مسودة، ويصرف الخامات للموقع؛ من غير ترحيل أو تحصيل أو ربح."),
)

#: code, Arabic, English, module, roles
PERMISSIONS = (
    ("restaurant.take_orders", "أخذ الطلبات على الطاولات وإرسالها للمطبخ", "Take table orders and send them to the kitchen", "restaurant",
     ("owner", "manager", "cashier", "waiter")),
    ("restaurant.prepare_at_payment", "تجهيز الأطباق الناقصة من خاماتها عند الحساب", "Make missing plates from their ingredients at payment", "restaurant",
     ("owner", "manager", "cashier")),
    ("projects.view_projects", "عرض المشاريع والمقايسة والمستخلصات", "View projects, the bill of quantities and certificates", "projects",
     ("owner", "manager", "site_engineer")),
    ("projects.record_progress", "تسجيل الكميات المنفذة في مستخلص مسودة", "Record quantities done on a draft certificate", "projects",
     ("owner", "manager", "site_engineer")),
    ("projects.issue_to_site", "صرف خامات من المخزن للموقع", "Issue materials from stock to the site", "projects",
     ("owner", "manager", "site_engineer")),
)

#: Existing permissions the new roles also hold.
EXISTING = {"site_engineer": ("inventory.view_stock", "inventory.view_group_stock")}  # HG-031: whoever sees stock sees the group's quantities


def seed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")
    for code, name_ar, name_en, description in ROLES:
        Role.objects.update_or_create(code=code, defaults={"name_ar": name_ar, "name_en": name_en, "description": description,
                                                           "is_system_role": True, "active": True})
    for code, name_ar, name_en, module, roles in PERMISSIONS:
        permission, _ = Permission.objects.update_or_create(code=code, defaults={
            "name_ar": name_ar, "name_en": name_en, "module": module, "is_report_permission": False,
            "is_sensitive_finance": False, "active": True})
        for role in Role.objects.filter(code__in=roles):
            RolePermission.objects.update_or_create(role=role, permission=permission, defaults={"allow": True})
    for role_code, codes in EXISTING.items():
        role = Role.objects.get(code=role_code)
        for permission in Permission.objects.filter(code__in=codes):
            RolePermission.objects.update_or_create(role=role, permission=permission, defaults={"allow": True})


def unseed(apps, schema_editor):
    Permission = apps.get_model("permissions", "Permission")
    Role = apps.get_model("permissions", "Role")
    RolePermission = apps.get_model("permissions", "RolePermission")
    codes = [row[0] for row in PERMISSIONS]
    RolePermission.objects.filter(permission__code__in=codes).delete()
    Permission.objects.filter(code__in=codes).delete()
    Role.objects.filter(code__in=[row[0] for row in ROLES], is_system_role=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("permissions", "0009_seed_medical_permissions"),
    ]

    operations = [
        migrations.AlterField(
            model_name="role",
            name="code",
            field=models.CharField(
                choices=[
                    ("owner", "Owner"),
                    ("manager", "Manager"),
                    ("cashier", "Cashier"),
                    ("stock_keeper", "Stock Keeper"),
                    ("accountant", "Accountant"),
                    ("support", "Support"),
                    ("waiter", "Waiter"),
                    ("site_engineer", "Site engineer"),
                ],
                max_length=40,
                unique=True,
            ),
        ),
        migrations.RunPython(seed, unseed),
    ]
