from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("cashboxes", "0004_cashboxmovement_sales_invoice"),
        ("sales", "0005_customerpayment"),
    ]

    operations = [
        migrations.AddField(
            model_name="cashboxmovement",
            name="customer_payment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cashbox_movements",
                to="sales.customerpayment",
            ),
        ),
        migrations.AddIndex(
            model_name="cashboxmovement",
            index=models.Index(fields=["customer_payment"], name="cashboxes_c_cust_pay_idx"),
        ),
    ]
