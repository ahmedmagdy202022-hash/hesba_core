from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cashboxes", "0001_initial"),
        ("purchases", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CashboxMovement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("movement_date", models.DateField()),
                (
                    "movement_type",
                    models.CharField(
                        choices=[
                            ("purchase_payment", "Purchase payment"),
                            ("supplier_payment", "Supplier payment"),
                            ("customer_payment", "Customer payment"),
                            ("direct_in", "Direct in"),
                            ("direct_out", "Direct out"),
                            ("transfer_in", "Transfer in"),
                            ("transfer_out", "Transfer out"),
                            ("adjustment", "Adjustment"),
                        ],
                        max_length=40,
                    ),
                ),
                ("direction", models.CharField(choices=[("in", "In"), ("out", "Out")], max_length=10)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "cashbox",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="movements",
                        to="cashboxes.cashbox",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_cashbox_movements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "purchase_invoice",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="cashbox_movements",
                        to="purchases.purchaseinvoice",
                    ),
                ),
            ],
            options={
                "verbose_name": "Cashbox Movement",
                "verbose_name_plural": "Cashbox Movements",
                "ordering": ["-movement_date", "-id"],
                "indexes": [
                    models.Index(fields=["cashbox", "movement_date"], name="cashboxes_c_cashbox_f4d390_idx"),
                    models.Index(fields=["movement_type"], name="cashboxes_c_movemen_79f653_idx"),
                    models.Index(fields=["purchase_invoice"], name="cashboxes_c_purchase_7b01da_idx"),
                ],
            },
        ),
    ]
