from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Cashbox",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("cashbox_code", models.CharField(max_length=80, unique=True)),
                ("name_ar", models.CharField(max_length=255)),
                ("name_en", models.CharField(blank=True, max_length=255)),
                ("opening_balance", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("currency", models.CharField(default="EGP", max_length=10)),
                ("is_default", models.BooleanField(default=False)),
                ("active", models.BooleanField(default=True)),
                ("notes", models.TextField(blank=True)),
                ("import_batch_id", models.CharField(blank=True, max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Cashbox",
                "verbose_name_plural": "Cashboxes",
                "ordering": ["cashbox_code"],
            },
        ),
    ]
