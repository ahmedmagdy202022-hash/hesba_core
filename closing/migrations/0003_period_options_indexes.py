from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("closing", "0002_period_extra_fields")]

    operations = [
        migrations.AlterModelOptions(
            name="period",
            options={"ordering": ["-start_date", "-id"], "verbose_name": "Period", "verbose_name_plural": "Periods"},
        ),
        migrations.AddIndex(
            model_name="period",
            index=models.Index(fields=["start_date", "end_date"], name="closing_per_start_d_2ea1e0_idx"),
        ),
        migrations.AddIndex(
            model_name="period",
            index=models.Index(fields=["status"], name="closing_per_status_e98afb_idx"),
        ),
        migrations.AddIndex(
            model_name="period",
            index=models.Index(fields=["frequency"], name="closing_per_frequen_056253_idx"),
        ),
    ]
