from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("closing", "0001_period"),
    ]

    operations = [
        migrations.AddField("period", "closed_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("period", "closed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="closed_periods", to=settings.AUTH_USER_MODEL)),
        migrations.AddField("period", "reopened_at", models.DateTimeField(blank=True, null=True)),
        migrations.AddField("period", "reopened_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reopened_periods", to=settings.AUTH_USER_MODEL)),
        migrations.AddField("period", "reopen_reason", models.TextField(blank=True)),
    ]
