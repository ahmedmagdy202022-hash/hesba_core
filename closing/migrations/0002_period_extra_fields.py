from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("closing", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="period",
            name="frequency",
            field=models.CharField(choices=[("monthly", "Monthly"), ("quarterly", "Quarterly"), ("semi_annual", "Semi annual"), ("annual", "Annual")], default="quarterly", max_length=20),
        ),
        migrations.AddField(
            model_name="period",
            name="status",
            field=models.CharField(choices=[("open", "Open"), ("closed", "Closed"), ("reopened", "Reopened")], default="open", max_length=20),
        ),
        migrations.AddField(model_name="period", name="closed_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="period", name="reopened_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="period", name="reopen_reason", field=models.TextField(blank=True)),
        migrations.AddField(model_name="period", name="notes", field=models.TextField(blank=True)),
        migrations.AddField(model_name="period", name="created_at", field=models.DateTimeField(auto_now_add=True, null=True)),
        migrations.AddField(model_name="period", name="updated_at", field=models.DateTimeField(auto_now=True, null=True)),
        migrations.AddField(
            model_name="period",
            name="closed_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="closed_periods", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name="period",
            name="reopened_by",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reopened_periods", to=settings.AUTH_USER_MODEL),
        ),
    ]
