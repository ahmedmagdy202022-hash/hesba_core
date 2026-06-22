from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("cashboxes", "0005_cashboxmovement_customer_payment"),
    ]

    operations = [
        migrations.AlterField(
            model_name="cashboxmovement",
            name="movement_type",
            field=models.CharField(
                choices=[
                    ("purchase_payment", "Purchase payment"),
                    ("sales_receipt", "Sales receipt"),
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
        migrations.RenameIndex(
            model_name="cashboxmovement",
            old_name="cashboxes_c_cust_pay_idx",
            new_name="cashboxes_c_custome_02d3da_idx",
        ),
    ]
