from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("closing", "0003_period_options_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClosingRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("run_number", models.PositiveIntegerField()),
                ("status", models.CharField(choices=[("draft", "Draft"), ("completed", "Completed"), ("cancelled", "Cancelled")], default="draft", max_length=20)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("reason", models.TextField(blank=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_closing_runs", to=settings.AUTH_USER_MODEL)),
                ("period", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="closing_runs", to="closing.period")),
            ],
            options={
                "ordering": ["-started_at", "-id"],
                "verbose_name": "Closing Run",
                "verbose_name_plural": "Closing Runs",
                "indexes": [models.Index(fields=["period", "status"], name="closing_clo_period__48829e_idx")],
            },
        ),
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
            options={
                "ordering": ["period", "summary_code"],
                "verbose_name": "Period Summary",
                "verbose_name_plural": "Period Summaries",
                "indexes": [models.Index(fields=["period", "summary_code"], name="closing_per_period__1d5a84_idx")],
            },
        ),
        migrations.AddConstraint(
            model_name="closingrun",
            constraint=models.UniqueConstraint(fields=("period", "run_number"), name="unique_closing_run_number"),
        ),
        migrations.AddConstraint(
            model_name="periodsummary",
            constraint=models.UniqueConstraint(fields=("period", "summary_code"), name="unique_period_summary_code"),
        ),
    ]
