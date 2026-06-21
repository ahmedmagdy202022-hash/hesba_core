from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("settings_core", "0001_initial")]

    operations = [
        migrations.CreateModel(
            name="UsageStatusSnapshot",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status_level", models.CharField(default="green", max_length=20)),
                ("total_rows", models.PositiveIntegerField(default=0)),
                ("active_items_count", models.PositiveIntegerField(default=0)),
                ("active_customers_count", models.PositiveIntegerField(default=0)),
                ("active_suppliers_count", models.PositiveIntegerField(default=0)),
                ("stock_movements_count", models.PositiveIntegerField(default=0)),
                ("cashbox_movements_count", models.PositiveIntegerField(default=0)),
                ("sales_invoices_count", models.PositiveIntegerField(default=0)),
                ("purchase_invoices_count", models.PositiveIntegerField(default=0)),
                ("warnings", models.JSONField(blank=True, default=list)),
                ("recommendations", models.JSONField(blank=True, default=list)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.AddIndex(
            model_name="usagestatussnapshot",
            index=models.Index(fields=["status_level"], name="settings_us_status_2c9952_idx"),
        ),
        migrations.AddIndex(
            model_name="usagestatussnapshot",
            index=models.Index(fields=["created_at"], name="settings_us_created_36d15b_idx"),
        ),
    ]
