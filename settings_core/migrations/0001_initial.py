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
            name="ClientProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("client_code", models.CharField(max_length=50, unique=True)),
                ("legal_name", models.CharField(max_length=255)),
                ("display_name", models.CharField(max_length=255)),
                (
                    "activity_type",
                    models.CharField(
                        choices=[
                            ("store", "Store"),
                            ("services", "Services"),
                            ("telecom", "Telecom"),
                            ("contracting", "Contracting"),
                            ("mixed", "Mixed"),
                        ],
                        default="store",
                        max_length=30,
                    ),
                ),
                ("edition_code", models.CharField(default="HESBA_LITE_STORE_SERVICES", max_length=100)),
                ("default_currency", models.CharField(default="EGP", max_length=10)),
                ("default_language", models.CharField(default="ar", max_length=10)),
                ("timezone", models.CharField(default="Africa/Cairo", max_length=64)),
                ("fiscal_year_start_month", models.PositiveSmallIntegerField(default=1)),
                (
                    "default_closing_frequency",
                    models.CharField(
                        choices=[
                            ("monthly", "Monthly"),
                            ("quarterly", "Quarterly"),
                            ("semi_annual", "Semi-annual"),
                            ("annual", "Annual"),
                        ],
                        default="quarterly",
                        max_length=20,
                    ),
                ),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Client Profile",
                "verbose_name_plural": "Client Profiles",
            },
        ),
        migrations.CreateModel(
            name="FeatureFlag",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=120, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("enabled", models.BooleanField(default=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Feature Flag",
                "verbose_name_plural": "Feature Flags",
                "ordering": ["code"],
            },
        ),
        migrations.CreateModel(
            name="SystemSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key", models.CharField(max_length=120, unique=True)),
                ("value", models.TextField(blank=True)),
                (
                    "data_type",
                    models.CharField(
                        choices=[
                            ("string", "String"),
                            ("integer", "Integer"),
                            ("decimal", "Decimal"),
                            ("boolean", "Boolean"),
                            ("json", "JSON"),
                        ],
                        default="string",
                        max_length=20,
                    ),
                ),
                ("description", models.TextField(blank=True)),
                ("is_sensitive", models.BooleanField(default=False)),
                ("active", models.BooleanField(default=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "System Setting",
                "verbose_name_plural": "System Settings",
                "ordering": ["key"],
            },
        ),
        migrations.CreateModel(
            name="SupportAccessGrant",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("granted_to_identifier", models.CharField(max_length=150)),
                ("reason", models.TextField()),
                ("starts_at", models.DateTimeField()),
                ("expires_at", models.DateTimeField()),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "granted_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="support_access_grants_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Support Access Grant",
                "verbose_name_plural": "Support Access Grants",
                "ordering": ["-created_at"],
            },
        ),
    ]
