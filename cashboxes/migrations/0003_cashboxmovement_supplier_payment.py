from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("cashboxes", "0002_cashboxmovement"),
        ("purchases", "0003_supplierpayment"),
    ]

    operations = [
        migrations.AddField(
            model_name="cashboxmovement",
            name="supplier_payment",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="cashbox_movements",
                to="purchases.supplierpayment",
            ),
        ),
        migrations.AddIndex(
            model_name="cashboxmovement",
            index=models.Index(fields=["supplier_payment"], name="cashboxes_c_supplier_995d25_idx"),
        ),
    ]
