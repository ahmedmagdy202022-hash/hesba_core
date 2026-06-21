from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cashboxes", "0001_initial"),
        ("master_data", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SalesInvoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("invoice_number", models.CharField(max_length=80, unique=True)),
                ("invoice_date", models.DateField()),
                ("status", models.CharField(default="draft", max_length=20)),
                ("payment_status", models.CharField(default="credit", max_length=20)),
                ("subtotal", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("discount_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("tax_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("total_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("paid_now", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("remaining_due", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("cashbox", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="sales_receipts", to="cashboxes.cashbox")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_sales_invoices", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sales_invoices", to="master_data.customer")),
                ("selling_location", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sales_dispatches", to="master_data.location")),
            ],
            options={"ordering": ["-invoice_date", "-id"]},
        ),
    ]
