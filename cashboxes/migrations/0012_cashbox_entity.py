import django.db.models.deletion
from django.db import migrations, models


def assign(apps, schema_editor):
    Entity = apps.get_model("entities", "Entity")
    Cashbox = apps.get_model("cashboxes", "Cashbox")
    main = Entity.objects.filter(is_main=True).first()
    if main is not None:
        Cashbox.objects.filter(entity__isnull=True).update(entity=main)


class Migration(migrations.Migration):
    dependencies = [("cashboxes", "0011_cashboxmovement_sales_return_and_more"), ("entities", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="cashbox",
            name="entity",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="cashboxes", to="entities.entity"),
        ),
        migrations.RunPython(assign, migrations.RunPython.noop),
    ]
