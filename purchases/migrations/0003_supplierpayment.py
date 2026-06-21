from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cashboxes", "0002_cashboxmovement"),
        ("master_data", "0001_initial"),
        ("purchases", "0002_supplierledgerentry"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payment_number", models.CharField(max_length=80, unique=True)),
                ("payment_date", models.DateField()),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                (
                    "status",
                    models.CharField(
                        choices=[("posted", "Posted"), ("cancelled", "Cancelled")],
                        default="posted",
                        max_length=20,
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cashbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="supplier_payments",
                        to="cashboxes.cashbox",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_supplier_payments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="supplier_payments",
                        to="master_data.supplier",
                    ),
                ),
            ],
            options={
                "verbose_name": "Supplier Payment",
                "verbose_name_plural": "Supplier Payments",
                "ordering": ["-payment_date", "-id"],
                "indexes": [
                    models.Index(fields=["payment_number"], name="purchases_s_payment_9c31a0_idx"),
                    models.Index(fields=["payment_date"], name="purchases_s_payment_6f2f10_idx"),
                    models.Index(fields=["supplier"], name="purchases_s_supplier_a9bf19_idx"),
                    models.Index(fields=["cashbox"], name="purchases_s_cashbox_e86b6b_idx"),
                    models.Index(fields=["status"], name="purchases_s_status_e5c0c4_idx"),
                ],
            },
        ),
        migrations.AddField(
            model_name="supplierledgerentry",
            name="supplier_payment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="supplier_ledger_entries",
                to="purchases.supplierpayment",
            ),
        ),
        migrations.AddIndex(
            model_name="supplierledgerentry",
            index=models.Index(fields=["supplier_payment"], name="purchases_s_supplier_64cc03_idx"),
        ),
    ]
