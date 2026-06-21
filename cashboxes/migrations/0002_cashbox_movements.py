from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cashboxes", "0001_initial"),
        ("purchases", "0002_supplier_payments_and_ledger"),
        ("sales", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="CashboxMovement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("movement_date", models.DateField()),
                ("movement_type", models.CharField(choices=[("purchase_payment", "Purchase payment"), ("sales_receipt", "Sales receipt"), ("supplier_payment", "Supplier payment"), ("customer_payment", "Customer payment"), ("direct_in", "Direct in"), ("direct_out", "Direct out"), ("transfer_in", "Transfer in"), ("transfer_out", "Transfer out"), ("adjustment", "Adjustment")], max_length=40)),
                ("direction", models.CharField(choices=[("in", "In"), ("out", "Out")], max_length=10)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("cashbox", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="movements", to="cashboxes.cashbox")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_cashbox_movements", to=settings.AUTH_USER_MODEL)),
                ("customer_payment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cashbox_movements", to="sales.customerpayment")),
                ("purchase_invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cashbox_movements", to="purchases.purchaseinvoice")),
                ("sales_invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cashbox_movements", to="sales.salesinvoice")),
                ("supplier_payment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cashbox_movements", to="purchases.supplierpayment")),
            ],
            options={
                "ordering": ["-movement_date", "-id"],
                "verbose_name": "Cashbox Movement",
                "verbose_name_plural": "Cashbox Movements",
                "indexes": [
                    models.Index(fields=["cashbox", "movement_date"], name="cashboxes_c_cashbox_c24814_idx"),
                    models.Index(fields=["movement_type"], name="cashboxes_c_movemen_c3975c_idx"),
                    models.Index(fields=["purchase_invoice"], name="cashboxes_c_purchas_465a9c_idx"),
                    models.Index(fields=["sales_invoice"], name="cashboxes_c_sales_i_c908bb_idx"),
                    models.Index(fields=["supplier_payment"], name="cashboxes_c_supplie_5010be_idx"),
                    models.Index(fields=["customer_payment"], name="cashboxes_c_custome_02d3da_idx"),
                ],
            },
        ),
    ]
