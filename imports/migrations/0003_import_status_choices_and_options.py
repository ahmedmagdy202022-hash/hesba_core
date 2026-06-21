from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("imports", "0002_importbatch_locations_choice")]

    operations = [
        migrations.AlterModelOptions(
            name="importbatch",
            options={
                "ordering": ["-created_at", "-id"],
                "verbose_name": "Import Batch",
                "verbose_name_plural": "Import Batches",
            },
        ),
        migrations.AlterModelOptions(
            name="importraw",
            options={
                "ordering": ["batch", "row_number"],
                "verbose_name": "Import Raw Row",
                "verbose_name_plural": "Import Raw Rows",
            },
        ),
        migrations.AlterModelOptions(
            name="importreview",
            options={
                "ordering": ["batch", "raw_row_id", "id"],
                "verbose_name": "Import Review Row",
                "verbose_name_plural": "Import Review Rows",
            },
        ),
        migrations.AlterField(
            model_name="importbatch",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("uploaded", "Uploaded"),
                    ("reviewing", "Reviewing"),
                    ("approved", "Approved"),
                    ("imported", "Imported"),
                    ("failed", "Failed"),
                    ("cancelled", "Cancelled"),
                ],
                default="draft",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="importraw",
            name="row_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("valid", "Valid"),
                    ("invalid", "Invalid"),
                    ("imported", "Imported"),
                    ("skipped", "Skipped"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="importreview",
            name="review_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("approved", "Approved"),
                    ("rejected", "Rejected"),
                    ("corrected", "Corrected"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
