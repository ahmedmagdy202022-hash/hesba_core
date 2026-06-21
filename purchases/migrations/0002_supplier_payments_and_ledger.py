from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cashboxes", "0001_initial"),
        ("master_data", "0001_initial"),
        ("purchases", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payment_number", models.CharField(max_length=80, unique=True)),
                ("payment_date", models.DateField()),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("status", models.CharField(choices=[("posted", "Posted"), ("cancelled", "Cancelled")], default="posted", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("cashbox", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="supplier_payments", to="cashboxes.cashbox")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_supplier_payments", to=settings.AUTH_USER_MODEL)),
                ("supplier", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="supplier_payments", to="master_data.supplier")),
            ],
            options={"ordering": ["-payment_date", "-id"], "verbose_name": "Supplier Payment", "verbose_name_plural": "Supplier Payments"},
        ),
    ]
