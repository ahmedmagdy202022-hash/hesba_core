from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("settings_core", "0002_usage_status_snapshot")]

    operations = [
        migrations.AlterModelOptions(
            name="usagestatussnapshot",
            options={
                "ordering": ["-created_at", "-id"],
                "verbose_name": "Usage Status Snapshot",
                "verbose_name_plural": "Usage Status Snapshots",
            },
        ),
        migrations.AlterField(
            model_name="usagestatussnapshot",
            name="status_level",
            field=models.CharField(
                choices=[("green", "Green"), ("yellow", "Yellow"), ("orange", "Orange"), ("red", "Red")],
                default="green",
                max_length=20,
            ),
        ),
    ]
