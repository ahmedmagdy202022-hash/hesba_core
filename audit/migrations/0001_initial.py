from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="AuditLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("login", "Login"),
                            ("logout", "Logout"),
                            ("create", "Create"),
                            ("update", "Update"),
                            ("delete", "Delete"),
                            ("export", "Export"),
                            ("import", "Import"),
                            ("permission_change", "Permission change"),
                            ("support_access", "Support access"),
                            ("closing", "Closing"),
                            ("reopening", "Reopening"),
                            ("adjustment", "Adjustment"),
                        ],
                        max_length=40,
                    ),
                ),
                ("module", models.CharField(max_length=80)),
                ("action", models.CharField(max_length=120)),
                ("object_type", models.CharField(blank=True, max_length=120)),
                ("object_id", models.CharField(blank=True, max_length=120)),
                ("before_data", models.JSONField(blank=True, null=True)),
                ("after_data", models.JSONField(blank=True, null=True)),
                ("reason", models.TextField(blank=True)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("user_agent", models.TextField(blank=True)),
                ("support_access_identifier", models.CharField(blank=True, max_length=150)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="audit_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Audit Log",
                "verbose_name_plural": "Audit Logs",
                "ordering": ["-created_at"],
                "indexes": [
                    models.Index(fields=["created_at"], name="audit_audit_created_87df99_idx"),
                    models.Index(fields=["event_type"], name="audit_audit_event_t_5faf23_idx"),
                    models.Index(fields=["module", "action"], name="audit_audit_module_4edb99_idx"),
                    models.Index(fields=["object_type", "object_id"], name="audit_audit_object__21cba8_idx"),
                ],
            },
        ),
    ]
