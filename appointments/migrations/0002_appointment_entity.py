# HG-074: an appointment carries the entity it was booked in (HG-034 gap).

import django.db.models.deletion
from django.db import migrations, models


def copy_entity_from_bill(apps, schema_editor):
    """A billed appointment belongs where its bill landed: the entity of its invoice's selling store.

    Unbilled rows stay NULL and belong to the main entity, as every record without one (HG-034)."""

    Appointment = apps.get_model("appointments", "Appointment")
    billed = Appointment.objects.filter(entity__isnull=True, invoice__isnull=False, invoice__selling_location__entity__isnull=False)
    for entity_id in billed.values_list("invoice__selling_location__entity_id", flat=True).distinct():
        Appointment.objects.filter(entity__isnull=True, invoice__selling_location__entity_id=entity_id).update(entity_id=entity_id)


class Migration(migrations.Migration):

    dependencies = [
        ("appointments", "0001_initial"),
        ("entities", "0001_initial"),
        ("master_data", "0002_location_entity"),
    ]

    operations = [
        migrations.AddField(
            model_name="appointment",
            name="entity",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="entities.entity",
            ),
        ),
        migrations.RunPython(copy_entity_from_bill, migrations.RunPython.noop),
    ]
