# Restaurants R5a (HG-115): a table's QR key (random, unique, filled for every existing table) and TableRequest, what a guest asks for from it.

import secrets

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import restaurant.models


def fill_keys(apps, schema_editor):
    """Every existing table gets its own random key (a default evaluated once would give them all the same one)."""

    Table = apps.get_model("restaurant", "DiningTable")
    for table in Table.objects.filter(qr_key__isnull=True):
        table.qr_key = secrets.token_urlsafe(16)
        table.save(update_fields=["qr_key"])


class Migration(migrations.Migration):

    dependencies = [
        ("entities", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("restaurant", "0008_combos"),
    ]

    operations = [
        migrations.AddField(
            model_name="diningtable",
            name="qr_key",
            field=models.CharField(max_length=40, null=True, editable=False),
        ),
        migrations.RunPython(fill_keys, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="diningtable",
            name="qr_key",
            field=models.CharField(default=restaurant.models.new_qr_key, editable=False, max_length=40, unique=True),
        ),
        migrations.CreateModel(
            name="TableRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("kind", models.CharField(choices=[("order", "Order"), ("call_waiter", "Call the waiter"), ("bill", "Bring the bill")], default="order", max_length=20)),
                ("items", models.JSONField(blank=True, default=list)),
                ("note", models.CharField(blank=True, max_length=120)),
                ("status", models.CharField(choices=[("new", "New"), ("accepted", "Accepted"), ("declined", "Declined")], default="new", max_length=20)),
                ("handled_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("entity", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="table_requests", to="entities.entity")),
                ("handled_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="handled_table_requests", to=settings.AUTH_USER_MODEL)),
                ("order", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="table_requests", to="restaurant.order")),
                ("table", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="requests", to="restaurant.diningtable")),
            ],
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.AddIndex(
            model_name="tablerequest",
            index=models.Index(fields=["table", "status"], name="restaurant_req_table_status"),
        ),
    ]
