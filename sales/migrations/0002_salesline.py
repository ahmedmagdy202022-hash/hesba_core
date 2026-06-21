from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("master_data", "0001_initial"),
        ("sales", "0001_salesinvoice"),
    ]

    operations = [
        migrations.CreateModel(
            name="SalesLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("line_number", models.PositiveIntegerField()),
                ("description", models.CharField(blank=True, max_length=255)),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("unit_sale_price", models.DecimalField(decimal_places=2, max_digits=14)),
                ("line_discount_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("line_total_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("invoice", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="lines", to="sales.salesinvoice")),
                ("item", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="sales_lines", to="master_data.item")),
            ],
            options={"ordering": ["invoice", "line_number"]},
        ),
        migrations.AddConstraint(
            model_name="salesline",
            constraint=models.UniqueConstraint(fields=("invoice", "line_number"), name="unique_sales_line_number"),
        ),
    ]
