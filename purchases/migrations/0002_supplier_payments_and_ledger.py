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
            options={
                "ordering": ["-payment_date", "-id"],
                "verbose_name": "Supplier Payment",
                "verbose_name_plural": "Supplier Payments",
                "indexes": [
                    models.Index(fields=["payment_number"], name="purchases_s_payment_9fd54a_idx"),
                    models.Index(fields=["payment_date"], name="purchases_s_payment_41eef4_idx"),
                    models.Index(fields=["supplier"], name="purchases_s_supplie_ce2a5c_idx"),
                    models.Index(fields=["cashbox"], name="purchases_s_cashbox_194222_idx"),
                    models.Index(fields=["status"], name="purchases_s_status_7cb5fe_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SupplierLedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entry_date", models.DateField()),
                ("entry_type", models.CharField(choices=[("purchase_due", "Purchase due"), ("supplier_payment", "Supplier payment"), ("purchase_return", "Purchase return"), ("opening_balance", "Opening balance"), ("adjustment", "Adjustment")], max_length=40)),
                ("due_increase", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("due_decrease", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_supplier_ledger_entries", to=settings.AUTH_USER_MODEL)),
                ("purchase_invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="supplier_ledger_entries", to="purchases.purchaseinvoice")),
                ("supplier", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="master_data.supplier")),
                ("supplier_payment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="supplier_ledger_entries", to="purchases.supplierpayment")),
            ],
            options={
                "ordering": ["-entry_date", "-id"],
                "verbose_name": "Supplier Ledger Entry",
                "verbose_name_plural": "Supplier Ledger Entries",
                "indexes": [
                    models.Index(fields=["supplier", "entry_date"], name="purchases_s_supplie_c7f1e1_idx"),
                    models.Index(fields=["entry_type"], name="purchases_s_entry_t_314878_idx"),
                    models.Index(fields=["purchase_invoice"], name="purchases_s_purchas_8f0f56_idx"),
                    models.Index(fields=["supplier_payment"], name="purchases_s_supplie_9babd4_idx"),
                ],
            },
        ),
    ]
