from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("inventory", "0002_stock_movement_sales_fields")]

    operations = [
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["sales_invoice"], name="inventory_s_sales_i_523af4_idx"),
        ),
        migrations.AddIndex(
            model_name="stockmovement",
            index=models.Index(fields=["sales_line"], name="inventory_s_sales_l_56125b_idx"),
        ),
    ]
