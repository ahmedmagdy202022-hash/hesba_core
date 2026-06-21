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
                ("status", models.CharField(choices=[("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")], default="draft", max_length=20)),
                ("payment_status", models.CharField(choices=[("credit", "Credit"), ("partial", "Partial"), ("paid", "Paid")], default="credit", max_length=20)),
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
            options={
                "verbose_name": "Sales Invoice",
                "verbose_name_plural": "Sales Invoices",
                "ordering": ["-invoice_date", "-id"],
                "indexes": [
                    models.Index(fields=["invoice_number"], name="sales_sales_invoice_b577c1_idx"),
                    models.Index(fields=["invoice_date"], name="sales_sales_invoice_dc35b7_idx"),
                    models.Index(fields=["customer"], name="sales_sales_custome_5940c2_idx"),
                    models.Index(fields=["status", "payment_status"], name="sales_sales_status_90a722_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="CustomerPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("payment_number", models.CharField(max_length=80, unique=True)),
                ("payment_date", models.DateField()),
                ("amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("status", models.CharField(choices=[("posted", "Posted"), ("cancelled", "Cancelled")], default="posted", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("cashbox", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="customer_payments", to="cashboxes.cashbox")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_customer_payments", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="customer_payments", to="master_data.customer")),
            ],
            options={
                "verbose_name": "Customer Payment",
                "verbose_name_plural": "Customer Payments",
                "ordering": ["-payment_date", "-id"],
                "indexes": [
                    models.Index(fields=["payment_number"], name="sales_custo_payment_168a93_idx"),
                    models.Index(fields=["payment_date"], name="sales_custo_payment_4f7115_idx"),
                    models.Index(fields=["customer"], name="sales_custo_custome_09ba92_idx"),
                    models.Index(fields=["cashbox"], name="sales_custo_cashbox_db3d37_idx"),
                    models.Index(fields=["status"], name="sales_custo_status_bef9f9_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SalesLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("line_number", models.PositiveIntegerField()),
                ("description", models.CharField(blank=True, max_length=255)),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("unit_sale_price", models.DecimalField(decimal_places=2, max_digits=14)),
                ("line_discount_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("line_total_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("unit_cost", models.DecimalField(decimal_places=4, default=0, max_digits=14)),
                ("line_cost_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("line_profit_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("invoice", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="sales.salesinvoice")),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sales_lines", to="master_data.item")),
            ],
            options={
                "verbose_name": "Sales Line",
                "verbose_name_plural": "Sales Lines",
                "ordering": ["invoice", "line_number"],
                "indexes": [
                    models.Index(fields=["invoice", "line_number"], name="sales_sales_invoice_9d942c_idx"),
                    models.Index(fields=["item"], name="sales_sales_item_id_9f4cce_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="CustomerLedgerEntry",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("entry_date", models.DateField()),
                ("entry_type", models.CharField(choices=[("sales_due", "Sales due"), ("customer_payment", "Customer payment"), ("sales_return", "Sales return"), ("opening_balance", "Opening balance"), ("adjustment", "Adjustment")], max_length=40)),
                ("due_increase", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("due_decrease", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("description", models.CharField(blank=True, max_length=255)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_customer_ledger_entries", to=settings.AUTH_USER_MODEL)),
                ("customer", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="ledger_entries", to="master_data.customer")),
                ("customer_payment", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="customer_ledger_entries", to="sales.customerpayment")),
                ("sales_invoice", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="customer_ledger_entries", to="sales.salesinvoice")),
            ],
            options={
                "verbose_name": "Customer Ledger Entry",
                "verbose_name_plural": "Customer Ledger Entries",
                "ordering": ["-entry_date", "-id"],
                "indexes": [
                    models.Index(fields=["customer", "entry_date"], name="sales_custo_custome_c2f095_idx"),
                    models.Index(fields=["entry_type"], name="sales_custo_entry_t_c1c4eb_idx"),
                    models.Index(fields=["sales_invoice"], name="sales_custo_sales_i_1e5aab_idx"),
                    models.Index(fields=["customer_payment"], name="sales_custo_custome_215063_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="salesline",
            constraint=models.UniqueConstraint(fields=("invoice", "line_number"), name="unique_sales_line_number"),
        ),
    ]
