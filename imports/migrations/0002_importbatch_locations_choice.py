from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("imports", "0001_initial")]

    operations = [
        migrations.AlterField(
            model_name="importbatch",
            name="target_type",
            field=models.CharField(
                choices=[
                    ("items", "Items"),
                    ("categories", "Categories"),
                    ("locations", "Locations"),
                    ("stock", "Stock"),
                    ("customers", "Customers"),
                    ("suppliers", "Suppliers"),
                    ("cashboxes", "Cashboxes"),
                    ("users", "Users"),
                    ("opening_balances", "Opening balances"),
                ],
                max_length=40,
            ),
        ),
    ]
