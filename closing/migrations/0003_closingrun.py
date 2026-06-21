from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("closing", "0002_period_audit_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="ClosingRun",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("run_number", models.PositiveIntegerField()),
                ("status", models.CharField(default="draft", max_length=20)),
                ("started_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("reason", models.TextField(blank=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_closing_runs", to=settings.AUTH_USER_MODEL)),
                ("period", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="closing_runs", to="closing.period")),
            ],
            options={"ordering": ["-started_at", "-id"]},
        ),
        migrations.AddConstraint("closingrun", models.UniqueConstraint(fields=("period", "run_number"), name="unique_closing_run_number")),
    ]
