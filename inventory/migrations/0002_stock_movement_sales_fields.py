from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("inventory", "0001_initial"), ("sales", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="sales_invoice",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="stock_movements", to="sales.salesinvoice"),
        ),
        migrations.AddField(
            model_name="stockmovement",
            name="sales_line",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="stock_movements", to="sales.salesline"),
        ),
    ]
