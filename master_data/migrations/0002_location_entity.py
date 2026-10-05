import django.db.models.deletion
from django.db import migrations, models


def assign(apps, schema_editor):
    Entity = apps.get_model("entities", "Entity")
    Location = apps.get_model("master_data", "Location")
    main = Entity.objects.filter(is_main=True).first()
    if main is not None:
        Location.objects.filter(entity__isnull=True).update(entity=main)


class Migration(migrations.Migration):
    dependencies = [("master_data", "0001_initial"), ("entities", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="location",
            name="entity",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="locations", to="entities.entity"),
        ),
        migrations.RunPython(assign, migrations.RunPython.noop),
    ]
