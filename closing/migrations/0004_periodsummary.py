from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("closing", "0003_closingrun")]

    operations = [
        migrations.CreateModel(
            name="PeriodSummary",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("summary_code", models.CharField(max_length=80)),
                ("summary_name", models.CharField(max_length=255)),
                ("amount", models.DecimalField(decimal_places=2, default=0, max_digits=16)),
                ("quantity", models.DecimalField(decimal_places=3, default=0, max_digits=16)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("closing_run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="summaries", to="closing.closingrun")),
                ("period", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="summaries", to="closing.period")),
            ],
            options={"ordering": ["period", "summary_code"]},
        ),
        migrations.AddConstraint("periodsummary", models.UniqueConstraint(fields=("period", "summary_code"), name="unique_period_summary_code")),
    ]
