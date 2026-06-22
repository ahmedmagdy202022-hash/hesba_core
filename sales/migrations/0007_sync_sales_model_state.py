from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0006_customerledgerentry_customer_payment"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="salesinvoice",
            options={
                "ordering": ["-invoice_date", "-id"],
                "verbose_name": "Sales Invoice",
                "verbose_name_plural": "Sales Invoices",
            },
        ),
        migrations.AlterModelOptions(
            name="salesline",
            options={
                "ordering": ["invoice", "line_number"],
                "verbose_name": "Sales Line",
                "verbose_name_plural": "Sales Lines",
            },
        ),
        migrations.AlterModelOptions(
            name="customerpayment",
            options={
                "ordering": ["-payment_date", "-id"],
                "verbose_name": "Customer Payment",
                "verbose_name_plural": "Customer Payments",
            },
        ),
        migrations.AlterModelOptions(
            name="customerledgerentry",
            options={
                "ordering": ["-entry_date", "-id"],
                "verbose_name": "Customer Ledger Entry",
                "verbose_name_plural": "Customer Ledger Entries",
            },
        ),
        migrations.AlterField(
            model_name="salesinvoice",
            name="status",
            field=models.CharField(
                choices=[("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")],
                default="draft",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="salesinvoice",
            name="payment_status",
            field=models.CharField(
                choices=[("credit", "Credit"), ("partial", "Partial"), ("paid", "Paid")],
                default="credit",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="customerpayment",
            name="status",
            field=models.CharField(
                choices=[("posted", "Posted"), ("cancelled", "Cancelled")],
                default="posted",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="customerledgerentry",
            name="entry_type",
            field=models.CharField(
                choices=[
                    ("sales_due", "Sales due"),
                    ("customer_payment", "Customer payment"),
                    ("sales_return", "Sales return"),
                    ("opening_balance", "Opening balance"),
                    ("adjustment", "Adjustment"),
                ],
                max_length=40,
            ),
        ),
        migrations.AddIndex(
            model_name="salesinvoice",
            index=models.Index(fields=["invoice_number"], name="sales_sales_invoice_b577c1_idx"),
        ),
        migrations.AddIndex(
            model_name="salesinvoice",
            index=models.Index(fields=["invoice_date"], name="sales_sales_invoice_dc35b7_idx"),
        ),
        migrations.AddIndex(
            model_name="salesinvoice",
            index=models.Index(fields=["customer"], name="sales_sales_custome_5940c2_idx"),
        ),
        migrations.AddIndex(
            model_name="salesinvoice",
            index=models.Index(fields=["status", "payment_status"], name="sales_sales_status_90a722_idx"),
        ),
        migrations.AddIndex(
            model_name="salesline",
            index=models.Index(fields=["invoice", "line_number"], name="sales_sales_invoice_9d942c_idx"),
        ),
        migrations.AddIndex(
            model_name="salesline",
            index=models.Index(fields=["item"], name="sales_sales_item_id_9f4cce_idx"),
        ),
        migrations.AddIndex(
            model_name="customerpayment",
            index=models.Index(fields=["payment_number"], name="sales_custo_payment_168a93_idx"),
        ),
        migrations.AddIndex(
            model_name="customerpayment",
            index=models.Index(fields=["payment_date"], name="sales_custo_payment_4f7115_idx"),
        ),
        migrations.AddIndex(
            model_name="customerpayment",
            index=models.Index(fields=["customer"], name="sales_custo_custome_09ba92_idx"),
        ),
        migrations.AddIndex(
            model_name="customerpayment",
            index=models.Index(fields=["cashbox"], name="sales_custo_cashbox_db3d37_idx"),
        ),
        migrations.AddIndex(
            model_name="customerpayment",
            index=models.Index(fields=["status"], name="sales_custo_status_bef9f9_idx"),
        ),
        migrations.AddIndex(
            model_name="customerledgerentry",
            index=models.Index(fields=["customer", "entry_date"], name="sales_custo_custome_c2f095_idx"),
        ),
        migrations.AddIndex(
            model_name="customerledgerentry",
            index=models.Index(fields=["entry_type"], name="sales_custo_entry_t_c1c4eb_idx"),
        ),
        migrations.AddIndex(
            model_name="customerledgerentry",
            index=models.Index(fields=["sales_invoice"], name="sales_custo_sales_i_1e5aab_idx"),
        ),
        migrations.RenameIndex(
            model_name="customerledgerentry",
            old_name="sales_ledg_cust_pay_idx",
            new_name="sales_custo_custome_215063_idx",
        ),
    ]
