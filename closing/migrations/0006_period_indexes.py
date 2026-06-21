from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("closing", "0005_postclosingadjustment")]

    operations = [
        migrations.AddIndex("period", models.Index(fields=["start_date", "end_date"], name="closing_period_dates_idx")),
        migrations.AddIndex("period", models.Index(fields=["status"], name="closing_period_status_idx")),
        migrations.AddIndex("period", models.Index(fields=["frequency"], name="closing_period_freq_idx")),
        migrations.AddIndex("closingrun", models.Index(fields=["period", "status"], name="closing_run_period_status_idx")),
        migrations.AddIndex("periodsummary", models.Index(fields=["period", "summary_code"], name="closing_summary_code_idx")),
    ]
