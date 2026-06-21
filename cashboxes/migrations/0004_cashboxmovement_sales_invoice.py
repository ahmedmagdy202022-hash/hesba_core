from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("cashboxes", "0003_cashboxmovement_supplier_payment"),
        ("sales", "0002_salesline"),
    ]

    operations = [
        migrations.AddField(
            model_name="cashboxmovement",
            name="sales_invoice",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cashbox_movements",
                to="sales.salesinvoice",
            ),
        ),
        migrations.AddIndex(
            model_name="cashboxmovement",
            index=models.Index(fields=["sales_invoice"], name="cashboxes_c_sales_i_3fdf45_idx"),
        ),
    ]
