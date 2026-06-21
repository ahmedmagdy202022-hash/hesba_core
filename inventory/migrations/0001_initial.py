from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("master_data", "0001_initial"),
        ("purchases", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="StockMovement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("movement_date", models.DateField()),
                (
                    "movement_type",
                    models.CharField(
                        choices=[
                            ("purchase_in", "Purchase in"),
                            ("sale_out", "Sale out"),
                            ("purchase_return_out", "Purchase return out"),
                            ("sale_return_in", "Sale return in"),
                            ("transfer_in", "Transfer in"),
                            ("transfer_out", "Transfer out"),
                            ("adjustment_in", "Adjustment in"),
                            ("adjustment_out", "Adjustment out"),
                            ("opening_stock", "Opening stock"),
                        ],
                        max_length=40,
                    ),
                ),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("unit_cost", models.DecimalField(decimal_places=4, default=0, max_digits=14)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_stock_movements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_movements",
                        to="master_data.item",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_movements",
                        to="master_data.location",
                    ),
                ),
                (
                    "purchase_invoice",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_movements",
                        to="purchases.purchaseinvoice",
                    ),
                ),
                (
                    "purchase_line",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="stock_movements",
                        to="purchases.purchaseline",
                    ),
                ),
            ],
            options={
                "verbose_name": "Stock Movement",
                "verbose_name_plural": "Stock Movements",
                "ordering": ["-movement_date", "-id"],
                "indexes": [
                    models.Index(fields=["item", "location"], name="inventory_s_item_id_5e61d0_idx"),
                    models.Index(fields=["movement_date"], name="inventory_s_movemen_17d3c7_idx"),
                    models.Index(fields=["movement_type"], name="inventory_s_movemen_34b4d0_idx"),
                    models.Index(fields=["purchase_invoice"], name="inventory_s_purchase_0ad8b4_idx"),
                    models.Index(fields=["purchase_line"], name="inventory_s_purchase_81ba9a_idx"),
                ],
            },
        ),
    ]
