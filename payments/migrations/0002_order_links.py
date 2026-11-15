"""HG-121 (Restaurants R5b, pay from the table): a payment link can be made for a restaurant order instead of an invoice.

``invoice`` becomes nullable, ``order`` (a restaurant ``Order``, nullable) and ``order_digest`` are added, a check says exactly
one of ``invoice`` and ``order`` is set, one live (``new``) link per order, and a ``review`` status for an order link the
gateway confirmed but nothing was posted for. Every existing link is an invoice link and stays exactly as it is.

It depends on ``restaurant``'s first migration only (where ``Order`` is made), not its latest, so taking ``restaurant`` back
to an earlier migration (its own migration tests do) never has to take this one back.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0001_initial"),
        ("restaurant", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="paymentlink",
            name="order",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payment_links",
                to="restaurant.order",
            ),
        ),
        migrations.AddField(
            model_name="paymentlink",
            name="order_digest",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="paymentlink",
            name="invoice",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="payment_links",
                to="sales.salesinvoice",
            ),
        ),
        migrations.AlterField(
            model_name="paymentlink",
            name="status",
            field=models.CharField(
                choices=[
                    ("new", "Waiting for payment"),
                    ("paid", "Paid"),
                    ("mismatch", "Paid, different amount"),
                    ("failed", "Failed"),
                    ("expired", "Expired"),
                    ("review", "Paid, needs review"),
                ],
                db_index=True,
                default="new",
                max_length=10,
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentlink",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status", "new")),
                fields=("order",),
                name="payment_link_one_live_per_order",
            ),
        ),
        migrations.AddConstraint(
            model_name="paymentlink",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(("invoice__isnull", False), ("order__isnull", True)),
                    models.Q(("invoice__isnull", True), ("order__isnull", False)),
                    _connector="OR",
                ),
                name="payment_link_invoice_or_order",
            ),
        ),
    ]
