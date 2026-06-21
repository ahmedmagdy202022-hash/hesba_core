from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("closing", "0004_periodsummary"),
    ]

    operations = [
        migrations.CreateModel(
            name="PostClosingAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("adjustment_number", models.CharField(max_length=80, unique=True)),
                ("adjustment_date", models.DateField()),
                ("status", models.CharField(default="draft", max_length=20)),
                ("reason", models.TextField()),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_post_closing_adjustments", to=settings.AUTH_USER_MODEL)),
                ("related_closed_period", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="post_closing_adjustments", to="closing.period")),
            ],
            options={"ordering": ["-adjustment_date", "-id"]},
        ),
    ]
