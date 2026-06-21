from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Period",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period_code", models.CharField(max_length=80, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("frequency", models.CharField(default="quarterly", max_length=20)),
                ("start_date", models.DateField()),
                ("end_date", models.DateField()),
                ("status", models.CharField(default="open", max_length=20)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["-start_date", "-id"]},
        ),
    ]
