from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL)]

    operations = [
        migrations.CreateModel(
            name="ImportBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("batch_code", models.CharField(max_length=80, unique=True)),
                ("target_type", models.CharField(max_length=40)),
                ("status", models.CharField(default="draft", max_length=20)),
                ("source_file_name", models.CharField(blank=True, max_length=255)),
                ("go_live_date", models.DateField(blank=True, null=True)),
                ("total_rows", models.PositiveIntegerField(default=0)),
                ("valid_rows", models.PositiveIntegerField(default=0)),
                ("invalid_rows", models.PositiveIntegerField(default=0)),
                ("imported_rows", models.PositiveIntegerField(default=0)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="created_import_batches", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at", "-id"]},
        ),
        migrations.CreateModel(
            name="ImportRaw",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("row_number", models.PositiveIntegerField()),
                ("raw_data", models.JSONField(default=dict)),
                ("row_status", models.CharField(default="pending", max_length=20)),
                ("validation_errors", models.JSONField(blank=True, default=list)),
                ("target_model", models.CharField(blank=True, max_length=120)),
                ("target_object_id", models.CharField(blank=True, max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="raw_rows", to="imports.importbatch")),
            ],
            options={"ordering": ["batch", "row_number"]},
        ),
        migrations.CreateModel(
            name="ImportReview",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("review_status", models.CharField(default="pending", max_length=20)),
                ("corrected_data", models.JSONField(blank=True, default=dict)),
                ("notes", models.TextField(blank=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("batch", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="review_rows", to="imports.importbatch")),
                ("raw_row", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="reviews", to="imports.importraw")),
                ("reviewed_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="reviewed_import_rows", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["batch", "raw_row_id", "id"]},
        ),
        migrations.AddConstraint("importraw", models.UniqueConstraint(fields=("batch", "row_number"), name="unique_import_raw_row")),
        migrations.AddIndex("importbatch", models.Index(fields=["batch_code"], name="imports_bat_batch_c_2c84dd_idx")),
        migrations.AddIndex("importbatch", models.Index(fields=["target_type", "status"], name="imports_bat_target__9cd011_idx")),
        migrations.AddIndex("importbatch", models.Index(fields=["go_live_date"], name="imports_bat_go_live_537a3e_idx")),
        migrations.AddIndex("importraw", models.Index(fields=["batch", "row_status"], name="imports_raw_batch_i_5395f3_idx")),
        migrations.AddIndex("importraw", models.Index(fields=["target_model", "target_object_id"], name="imports_raw_target__fa5a65_idx")),
        migrations.AddIndex("importreview", models.Index(fields=["batch", "review_status"], name="imports_rev_batch_i_5c7f87_idx")),
        migrations.AddIndex("importreview", models.Index(fields=["raw_row"], name="imports_rev_raw_row_02f66e_idx")),
    ]
