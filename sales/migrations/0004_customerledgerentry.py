from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("master_data", "0001_initial"),
        ("sales", "0003_sales_line_cost_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerLedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entry_date", models.DateField()),
                ("entry_type", models.CharField(max_length=40)),
                ("due_increase", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("due_decrease", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_customer_ledger_entries", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="master_data.customer")),
                ("sales_invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="customer_ledger_entries", to="sales.salesinvoice")),
            ],
            options={"ordering": ["-entry_date", "-id"]},
        ),
    ]
