from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("purchases", "0001_initial"),
        ("master_data", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SupplierLedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entry_date", models.DateField()),
                (
                    "entry_type",
                    models.CharField(
                        choices=[
                            ("purchase_due", "Purchase due"),
                            ("supplier_payment", "Supplier payment"),
                            ("purchase_return", "Purchase return"),
                            ("opening_balance", "Opening balance"),
                            ("adjustment", "Adjustment"),
                        ],
                        max_length=40,
                    ),
                ),
                ("due_increase", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("due_decrease", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_supplier_ledger_entries",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "purchase_invoice",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="supplier_ledger_entries",
                        to="purchases.purchaseinvoice",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="ledger_entries",
                        to="master_data.supplier",
                    ),
                ),
            ],
            options={
                "verbose_name": "Supplier Ledger Entry",
                "verbose_name_plural": "Supplier Ledger Entries",
                "ordering": ["-entry_date", "-id"],
                "indexes": [
                    models.Index(fields=["supplier", "entry_date"], name="purchases_s_supplier_379c60_idx"),
                    models.Index(fields=["entry_type"], name="purchases_s_entry_t_2d5b56_idx"),
                    models.Index(fields=["purchase_invoice"], name="purchases_s_purchase_0e66da_idx"),
                ],
            },
        ),
    ]
