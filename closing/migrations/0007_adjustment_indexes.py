from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("closing", "0006_period_indexes")]

    operations = [
        migrations.AddIndex("postclosingadjustment", models.Index(fields=["related_closed_period"], name="closing_adj_period_idx")),
        migrations.AddIndex("postclosingadjustment", models.Index(fields=["status"], name="closing_adj_status_idx")),
        migrations.AddIndex("postclosingadjustment", models.Index(fields=["adjustment_date"], name="closing_adj_date_idx")),
    ]
