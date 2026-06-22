from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Category",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category_code", models.CharField(max_length=50, unique=True)),
                ("name_ar", models.CharField(max_length=255)),
                ("name_en", models.CharField(blank=True, max_length=255)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("parent", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="children", to="master_data.category")),
            ],
            options={"verbose_name": "Category", "verbose_name_plural": "Categories", "ordering": ["category_code"]},
        ),
        migrations.CreateModel(
            name="Location",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("location_code", models.CharField(max_length=50, unique=True)),
                ("name_ar", models.CharField(max_length=255)),
                ("name_en", models.CharField(blank=True, max_length=255)),
                ("description", models.TextField(blank=True)),
                ("is_default", models.BooleanField(default=False)),
                ("is_receiving_location", models.BooleanField(default=True)),
                ("is_selling_location", models.BooleanField(default=True)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"verbose_name": "Location", "verbose_name_plural": "Locations", "ordering": ["location_code"]},
        ),
        migrations.CreateModel(
            name="Customer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("customer_code", models.CharField(max_length=80, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("phone", models.CharField(blank=True, max_length=50)),
                ("whatsapp", models.CharField(blank=True, max_length=50)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("address", models.TextField(blank=True)),
                ("opening_balance", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("credit_limit", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("notes", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
                ("import_batch_id", models.CharField(blank=True, max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Customer",
                "verbose_name_plural": "Customers",
                "ordering": ["customer_code"],
                "indexes": [
                    models.Index(fields=["customer_code"], name="master_data_custome_42768b_idx"),
                    models.Index(fields=["name"], name="master_data_name_f1bbdf_idx"),
                    models.Index(fields=["phone"], name="master_data_phone_4ef5a0_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Supplier",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("supplier_code", models.CharField(max_length=80, unique=True)),
                ("name", models.CharField(max_length=255)),
                ("phone", models.CharField(blank=True, max_length=50)),
                ("whatsapp", models.CharField(blank=True, max_length=50)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("address", models.TextField(blank=True)),
                ("opening_balance", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("notes", models.TextField(blank=True)),
                ("active", models.BooleanField(default=True)),
                ("import_batch_id", models.CharField(blank=True, max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Supplier",
                "verbose_name_plural": "Suppliers",
                "ordering": ["supplier_code"],
                "indexes": [
                    models.Index(fields=["supplier_code"], name="master_data_supplie_7f04d4_idx"),
                    models.Index(fields=["name"], name="master_data_name_790ad9_idx"),
                    models.Index(fields=["phone"], name="master_data_phone_7d880a_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Item",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("item_code", models.CharField(max_length=80, unique=True)),
                ("barcode", models.CharField(blank=True, db_index=True, max_length=120)),
                ("item_name", models.CharField(max_length=255)),
                ("size", models.CharField(blank=True, max_length=80)),
                ("color", models.CharField(blank=True, max_length=80)),
                ("unit", models.CharField(default="unit", max_length=50)),
                ("default_sale_price", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("default_purchase_price", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("average_cost", models.DecimalField(decimal_places=4, default=0, max_digits=14)),
                ("min_stock", models.DecimalField(decimal_places=3, default=0, max_digits=14)),
                ("is_stock_tracked", models.BooleanField(default=True)),
                ("active", models.BooleanField(default=True)),
                ("import_batch_id", models.CharField(blank=True, max_length=120)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("category", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="items", to="master_data.category")),
            ],
            options={
                "verbose_name": "Item",
                "verbose_name_plural": "Items",
                "ordering": ["item_code"],
                "indexes": [
                    models.Index(fields=["item_code"], name="master_data_item_co_b06c78_idx"),
                    models.Index(fields=["item_name"], name="master_data_item_na_1f224c_idx"),
                    models.Index(fields=["barcode"], name="master_data_barcode_b4770c_idx"),
                ],
            },
        ),
    ]
