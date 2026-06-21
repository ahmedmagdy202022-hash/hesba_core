from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0001_initial"),
        ("sales", "0002_salesline"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="sales_invoice",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="stock_movements",
                to="sales.salesinvoice",
            ),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="sales_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="stock_movements",
                to="sales.salesline",
            ),
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["sales_invoice"], name="inventory_s_sales_i_1f6c33_idx"),
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["sales_line"], name="inventory_s_sales_l_675df7_idx"),
        ),
    ]
