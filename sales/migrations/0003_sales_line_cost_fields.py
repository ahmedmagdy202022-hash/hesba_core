from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("sales", "0002_salesline"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesline",
            name="unit_cost",
            field=models.DecimalField(decimal_places=4, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="salesline",
            name="line_cost_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
        migrations.AddField(
            model_name="salesline",
            name="line_profit_amount",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
    ]
