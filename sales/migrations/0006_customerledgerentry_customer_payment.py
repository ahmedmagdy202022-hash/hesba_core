from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0005_customerpayment"),
    ]

    operations = [
        migrations.AddField(
            model_name="customerledgerentry",
            name="customer_payment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="customer_ledger_entries",
                to="sales.customerpayment",
            ),
        ),
        migrations.AddIndex(
            model_name="customerledgerentry",
            index=models.Index(fields=["customer_payment"], name="sales_ledg_cust_pay_idx"),
        ),
    ]
