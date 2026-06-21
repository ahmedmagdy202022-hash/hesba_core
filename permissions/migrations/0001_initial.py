from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Permission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=120, unique=True)),
                ("name_ar", models.CharField(max_length=255)),
                ("name_en", models.CharField(blank=True, max_length=255)),
                (
                    "module",
                    models.CharField(
                        choices=[
                            ("settings", "Settings"),
                            ("master_data", "Master data"),
                            ("inventory", "Inventory"),
                            ("purchases", "Purchases"),
                            ("sales", "Sales"),
                            ("cashboxes", "Cashboxes"),
                            ("reports", "Reports"),
                            ("closing", "Closing"),
                            ("audit", "Audit"),
                            ("imports", "Imports"),
                            ("barcode", "Barcode"),
                        ],
                        max_length=30,
                    ),
                ),
                ("description", models.TextField(blank=True)),
                ("is_report_permission", models.BooleanField(default=False)),
                ("is_sensitive_finance", models.BooleanField(default=False)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Permission",
                "verbose_name_plural": "Permissions",
                "ordering": ["module", "code"],
            },
        ),
        migrations.CreateModel(
            name="Role",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "code",
                    models.CharField(
                        choices=[
                            ("owner", "Owner"),
                            ("manager", "Manager"),
                            ("cashier", "Cashier"),
                            ("stock_keeper", "Stock Keeper"),
                            ("accountant", "Accountant"),
                            ("support", "Support"),
                        ],
                        max_length=40,
                        unique=True,
                    ),
                ),
                ("name_ar", models.CharField(max_length=255)),
                ("name_en", models.CharField(blank=True, max_length=255)),
                ("description", models.TextField(blank=True)),
                ("is_system_role", models.BooleanField(default=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Role",
                "verbose_name_plural": "Roles",
                "ordering": ["code"],
            },
        ),
        migrations.CreateModel(
            name="RolePermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("allow", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "permission",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="permissions.permission"),
                ),
                (
                    "role",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="permissions.role"),
                ),
            ],
            options={
                "verbose_name": "Role Permission",
                "verbose_name_plural": "Role Permissions",
            },
        ),
        migrations.AddField(
            model_name="role",
            name="permissions",
            field=models.ManyToManyField(blank=True, related_name="roles", through="permissions.RolePermission", to="permissions.permission"),
        ),
        migrations.AddConstraint(
            model_name="rolepermission",
            constraint=models.UniqueConstraint(fields=("role", "permission"), name="unique_role_permission"),
        ),
    ]
