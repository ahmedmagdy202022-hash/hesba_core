# F1 (HG-098): the delivery date promised on a sales order.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0002_purchaseorder_purchaseorderline_orderreceiptline_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="salesorder",
            name="deliver_by",
            field=models.DateField(
                blank=True,
                help_text="F1 (HG-098): the delivery date promised to the customer; valid_until is only the quotation's validity.",
                null=True,
            ),
        ),
    ]
