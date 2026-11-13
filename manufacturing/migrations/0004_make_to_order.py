# F1 (HG-098): the sales order line a production order is made for.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("manufacturing", "0003_wip_conversion_scrap"),
        ("orders", "0003_deliver_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="productionorder",
            name="sales_order_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="production_orders",
                to="orders.salesorderline",
            ),
        ),
    ]
