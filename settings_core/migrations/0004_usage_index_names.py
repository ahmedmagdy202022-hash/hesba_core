from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("settings_core", "0003_usage_status_options")]

    operations = [
        migrations.RemoveIndex(
            model_name="usagestatussnapshot",
            name="settings_us_status_2c9952_idx",
        ),
        migrations.RemoveIndex(
            model_name="usagestatussnapshot",
            name="settings_us_created_36d15b_idx",
        ),
        migrations.AddIndex(
            model_name="usagestatussnapshot",
            index=models.Index(fields=["status_level"], name="settings_co_status__04f34e_idx"),
        ),
        migrations.AddIndex(
            model_name="usagestatussnapshot",
            index=models.Index(fields=["created_at"], name="settings_co_created_0ddac57_idx"),
        ),
    ]
