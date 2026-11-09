"""HG-071: two tills spending the same card at once: one wins, the other is refused (PostgreSQL only).

SQLite lets one writer in at a time, so the race cannot happen there and the test is skipped; CI runs it in the
django-tests-postgres job.
"""

import threading
import time
import unittest
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode


@unittest.skipUnless(connection.vendor == "postgresql", "row locks only matter on PostgreSQL")
class TwoTillsOneCardTests(TransactionTestCase):
    def test_two_redemptions_racing_on_one_card_spend_it_once(self):
        from closing.services import ensure_period_is_open
        from reports.tests_dashboard import prepared_client
        from sales.models import CustomerPayment
        from sales.services import create_sales_draft, post_sales_invoice
        from settings_core.capabilities import set_capability_enabled
        from settlements.models import InvoiceSettlement

        from . import services

        profile = prepared_client("commercial", "retail", "customers,items_services,sales_operations,cashboxes,reports")
        owner = make_user(username="race_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        set_capability_enabled(profile, "gift_cards", True, user=owner)
        ensure_period_is_open(timezone.localdate())  # so the only contention left is the card's lock
        location = make_location(location_code="SHOP", is_selling_location=True, is_default=True)
        cash = make_cashbox(cashbox_code="CASH", is_default=True)
        customer = make_customer(customer_code="C-RACE")
        service = make_item(item_code="SRV-RACE", is_stock_tracked=False)
        card = services.sell(Decimal("300.00"), cash, owner)
        invoices = []
        for number in ("SI-RACE-A", "SI-RACE-B"):
            draft = create_sales_draft({"invoice_number": number, "invoice_date": timezone.localdate(), "customer": customer, "selling_location": location,
                                        "cashbox": None, "discount_amount": Decimal("0"), "tax_amount": Decimal("0"), "paid_now": Decimal("0")},
                                       [{"item": service, "quantity": Decimal("1"), "unit_sale_price": Decimal("300.00"), "line_discount_amount": Decimal("0"),
                                         "description": ""}], owner)
            post_sales_invoice(draft.pk, owner)
            invoices.append(draft)
        services.gift_box(card.entity)  # made already by the sale

        real_balance = services.balance

        def slow_balance(*args, **kwargs):
            # Widen the window between reading the balance and writing the payment: without the card's lock both
            # tills would read 300 and both spend it.
            value = real_balance(*args, **kwargs)
            time.sleep(0.4)
            return value

        start, outcomes = threading.Barrier(2), {}

        def spend(invoice):
            try:
                start.wait()
                services.redeem(card, invoice, Decimal("300.00"), owner, lang="en")
                outcomes[invoice.pk] = "redeemed"
            except ValidationError as exc:
                outcomes[invoice.pk] = "refused: " + " ".join(exc.messages)
            except Exception as exc:  # noqa: BLE001 - surfaced in the assertion message
                outcomes[invoice.pk] = f"error: {exc!r}"
            finally:
                connection.close()

        with mock.patch.object(services, "balance", side_effect=slow_balance):
            threads = [threading.Thread(target=spend, args=(invoice,)) for invoice in invoices]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        self.assertEqual(sorted(value.split(":")[0] for value in outcomes.values()), ["redeemed", "refused"], outcomes)
        self.assertTrue(any("balance is not enough" in value for value in outcomes.values()), outcomes)
        self.assertEqual(real_balance(card), Decimal("0.00"))
        self.assertEqual(CustomerPayment.objects.filter(payment_number__startswith="GCR-", status="posted").count(), 1)
        self.assertEqual(InvoiceSettlement.objects.filter(source="gift_card").count(), 1)
