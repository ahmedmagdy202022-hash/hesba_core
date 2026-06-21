from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("closing", "0004_closing_run_and_summary"),
    ]

    operations = [
        migrations.CreateModel(
            name="PostClosingAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("adjustment_number", models.CharField(max_length=80, unique=True)),
                ("adjustment_date", models.DateField()),
                ("status", models.CharField(choices=[("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")], default="draft", max_length=20)),
                ("reason", models.TextField()),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_post_closing_adjustments", to=settings.AUTH_USER_MODEL)),
                ("related_closed_period", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="post_closing_adjustments", to="closing.period")),
            ],
            options={
                "ordering": ["-adjustment_date", "-id"],
                "verbose_name": "Post Closing Adjustment",
                "verbose_name_plural": "Post Closing Adjustments",
                "indexes": [
                    models.Index(fields=["related_closed_period"], name="closing_pos_related_14f53f_idx"),
                    models.Index(fields=["status"], name="closing_pos_status_406372_idx"),
                    models.Index(fields=["adjustment_date"], name="closing_pos_adjustm_7c3350_idx"),
                ],
            },
        ),
    ]
