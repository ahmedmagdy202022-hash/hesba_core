# Restaurants R12 (HG-108): tables and orders belong to an entity (branch); a table's name is unique within its entity.

import django.db.models.deletion
from django.db import migrations, models


def fill_entities(apps, schema_editor):
    """Every existing table and order gets an entity, so none is left NULL.

    - tables: the main entity (nothing recorded which branch a table stood in);
    - a paid order with an invoice: the entity of the invoice's selling store, where its sale and books already are
      (as ``appointments/0002`` did), the main one when that store has none;
    - every other order: its table's entity (the main one), else the main one.

    The main entity is the one ``entities/0001`` seeded; if it is missing and rows need it, it is made the same way."""

    Entity = apps.get_model("entities", "Entity")
    Table = apps.get_model("restaurant", "DiningTable")
    Order = apps.get_model("restaurant", "Order")
    if not Table.objects.exists() and not Order.objects.exists():
        return
    connection = schema_editor.connection
    if connection.vendor == "postgresql":
        # The updates below queue deferred foreign-key checks; checked at once, the ALTERs that follow in this same
        # migration do not meet "pending trigger events". Through the connection, not ``schema_editor.execute``: the demo
        # restart replays every data step with a stand-in editor that carries only ``connection`` (``config.demo_restart``).
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    main = Entity.objects.filter(is_main=True).first()
    if main is None:
        ClientProfile = apps.get_model("settings_core", "ClientProfile")
        profile = ClientProfile.objects.filter(is_active=True).order_by("pk").first()
        name = (profile.display_name if profile and profile.display_name else "") or "الكيان الرئيسي"
        main, _ = Entity.objects.get_or_create(code="MAIN", defaults={"name_ar": name, "name_en": "Main entity", "is_main": True})
    Table.objects.filter(entity__isnull=True).update(entity=main)
    billed = Order.objects.filter(entity__isnull=True, invoice__isnull=False, invoice__selling_location__entity__isnull=False)
    for entity_id in billed.values_list("invoice__selling_location__entity_id", flat=True).distinct():
        Order.objects.filter(entity__isnull=True, invoice__selling_location__entity_id=entity_id).update(entity_id=entity_id)
    Order.objects.filter(entity__isnull=True).update(entity=main)  # tables are all the main entity's now


class Migration(migrations.Migration):

    dependencies = [
        ("entities", "0001_initial"),
        ("master_data", "0002_location_entity"),
        ("restaurant", "0004_kitchen_stations"),
    ]

    operations = [
        migrations.AddField(
            model_name="diningtable",
            name="entity",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="dining_tables", to="entities.entity"),
        ),
        migrations.AddField(
            model_name="order",
            name="entity",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="restaurant_orders", to="entities.entity"),
        ),
        migrations.RunPython(fill_entities, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="diningtable",
            name="entity",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="dining_tables", to="entities.entity"),
        ),
        migrations.AlterField(
            model_name="order",
            name="entity",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="restaurant_orders", to="entities.entity"),
        ),
        migrations.AlterField(
            model_name="diningtable",
            name="name",
            field=models.CharField(max_length=40),
        ),
        migrations.AddConstraint(
            model_name="diningtable",
            constraint=models.UniqueConstraint(fields=("entity", "name"), name="restaurant_table_name_per_entity"),
        ),
    ]
