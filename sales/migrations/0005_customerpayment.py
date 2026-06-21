from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cashboxes", "0004_cashboxmovement_sales_invoice"),
        ("master_data", "0001_initial"),
        ("sales", "0004_customerledgerentry"),
    ]

    operations = [
        migrations.CreateModel(
            name="CustomerPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payment_number", models.CharField(max_length=80, unique=True)),
                ("payment_date", models.DateField()),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("status", models.CharField(default="posted", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("cashbox", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="customer_payments", to="cashboxes.cashbox")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_customer_payments", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="customer_payments", to="master_data.customer")),
            ],
            options={"ordering": ["-payment_date", "-id"]},
        ),
    ]
