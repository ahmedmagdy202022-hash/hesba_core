import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def seed_main(apps, schema_editor):
    Entity = apps.get_model("entities", "Entity")
    ClientProfile = apps.get_model("settings_core", "ClientProfile")
    profile = ClientProfile.objects.filter(is_active=True).order_by("pk").first()
    name = (profile.display_name if profile and profile.display_name else "") or "الكيان الرئيسي"
    Entity.objects.get_or_create(code="MAIN", defaults={"name_ar": name, "name_en": "Main entity", "is_main": True})


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("settings_core", "0006_clientprofile_activity_slug_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Entity",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.CharField(max_length=20, unique=True)),
                ("name_ar", models.CharField(max_length=255)),
                ("name_en", models.CharField(blank=True, max_length=255)),
                ("kind", models.CharField(choices=[("branch", "Branch"), ("company", "Separate company")], default="branch", max_length=20)),
                ("activity_slug", models.CharField(blank=True, max_length=40)),
                ("sub_activity_slug", models.CharField(blank=True, max_length=40)),
                ("tax_registration_number", models.CharField(blank=True, max_length=50)),
                ("document_prefix", models.CharField(blank=True, max_length=10)),
                ("is_main", models.BooleanField(default=False)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Entity", "verbose_name_plural": "Entities", "ordering": ["-is_main", "code"]},
        ),
        migrations.AddConstraint(
            model_name="entity",
            constraint=models.UniqueConstraint(condition=models.Q(("is_main", True)), fields=("is_main",), name="one_main_entity"),
        ),
        migrations.CreateModel(
            name="EntityMembership",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("is_default", models.BooleanField(default=False)),
                ("entity", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="memberships", to="entities.entity")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="entity_memberships", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name="entitymembership",
            constraint=models.UniqueConstraint(fields=("user", "entity"), name="unique_user_entity"),
        ),
        migrations.RunPython(seed_main, migrations.RunPython.noop),
    ]
