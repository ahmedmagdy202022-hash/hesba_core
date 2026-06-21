from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("cashboxes", "0001_initial"),
        ("master_data", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PurchaseInvoice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("invoice_number", models.CharField(max_length=80, unique=True)),
                ("invoice_date", models.DateField()),
                (
                    "status",
                    models.CharField(
                        choices=[("draft", "Draft"), ("posted", "Posted"), ("cancelled", "Cancelled")],
                        default="draft",
                        max_length=20,
                    ),
                ),
                (
                    "payment_status",
                    models.CharField(
                        choices=[("credit", "Credit"), ("partial", "Partial"), ("paid", "Paid")],
                        default="credit",
                        max_length=20,
                    ),
                ),
                ("subtotal", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("discount_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("tax_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("total_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("paid_now", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("remaining_due", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "cashbox",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="purchase_payments",
                        to="cashboxes.cashbox",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="created_purchase_invoices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "receiving_location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="purchase_receipts",
                        to="master_data.location",
                    ),
                ),
                (
                    "supplier",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="purchase_invoices",
                        to="master_data.supplier",
                    ),
                ),
            ],
            options={
                "verbose_name": "Purchase Invoice",
                "verbose_name_plural": "Purchase Invoices",
                "ordering": ["-invoice_date", "-id"],
                "indexes": [
                    models.Index(fields=["invoice_number"], name="purchases_p_invoice_8b5be9_idx"),
                    models.Index(fields=["invoice_date"], name="purchases_p_invoice_b1f6aa_idx"),
                    models.Index(fields=["supplier"], name="purchases_p_supplier_574e15_idx"),
                    models.Index(fields=["status", "payment_status"], name="purchases_p_status_6308f7_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="PurchaseLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("line_number", models.PositiveIntegerField()),
                ("description", models.CharField(blank=True, max_length=255)),
                ("quantity", models.DecimalField(decimal_places=3, max_digits=14)),
                ("unit_purchase_price", models.DecimalField(decimal_places=2, max_digits=14)),
                ("line_discount_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("line_total_amount", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "invoice",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="purchases.purchaseinvoice",
                    ),
                ),
                (
                    "item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="purchase_lines",
                        to="master_data.item",
                    ),
                ),
            ],
            options={
                "verbose_name": "Purchase Line",
                "verbose_name_plural": "Purchase Lines",
                "ordering": ["invoice", "line_number"],
                "indexes": [
                    models.Index(fields=["invoice", "line_number"], name="purchases_p_invoice_c17104_idx"),
                    models.Index(fields=["item"], name="purchases_p_item_id_4759ec_idx"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="purchaseline",
            constraint=models.UniqueConstraint(fields=("invoice", "line_number"), name="unique_purchase_line_number"),
        ),
    ]
