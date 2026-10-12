# MED-009: insured bills for dental steps and physio packages too (HG-042 note).

import django.db.models.deletion
from django.db import migrations, models


def fill_patients(apps, schema_editor):
    """Bills made before MED-009 are all appointments': their patient is the appointment's."""

    InsuredBill = apps.get_model("medical", "InsuredBill")
    if InsuredBill._meta.db_table not in schema_editor.connection.introspection.table_names():
        return  # a demo restart replays this on today's tables
    for bill in InsuredBill.objects.filter(patient__isnull=True, appointment__isnull=False).select_related("appointment"):
        InsuredBill.objects.filter(pk=bill.pk).update(patient_id=bill.appointment.customer_id)


class Migration(migrations.Migration):

    dependencies = [
        ("appointments", "0001_initial"),
        ("master_data", "0003_sales_rep"),
        ("medical", "0008_remove_session_packages"),
    ]

    operations = [
        migrations.AddField(
            model_name="insuredbill",
            name="description",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="insuredbill",
            name="kind",
            field=models.CharField(
                choices=[
                    ("visit", "Appointment"),
                    ("dental", "Dental steps"),
                    ("package", "Session package"),
                ],
                default="visit",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="insuredbill",
            name="patient",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="insured_bills",
                to="master_data.customer",
            ),
        ),
        migrations.AlterField(
            model_name="insuredbill",
            name="appointment",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="insured_bill",
                to="appointments.appointment",
            ),
        ),
        migrations.RunPython(fill_patients, migrations.RunPython.noop),
    ]
