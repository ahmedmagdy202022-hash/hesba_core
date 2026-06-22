from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("inventory", "0002_stockmovement_sales_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="stockmovement",
            name="created_at",
            field=models.DateTimeField(auto_now_add=True),
        ),
    ]
