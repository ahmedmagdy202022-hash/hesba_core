"""Restaurants R2b (HG-110): two bills paid several ways at the same moment (PostgreSQL only).

Both tills read the same next number «POS-…-0001». One commits first; the other's invoice insert then fails on the unique
number, its whole attempt (draft, split payment, settlement) is rolled back, and ``checkout``'s retry takes «-0002». The
split payment's number comes from its invoice's, so it can never clash with the other bill's. SQLite lets one writer in at
a time, so the race cannot happen there and the test is skipped; CI runs it in the django-tests-postgres job.
"""

import threading
import time
import unittest
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TransactionTestCase
from django.utils import timezone


@unittest.skipUnless(connection.vendor == "postgresql", "row locks only matter on PostgreSQL")
class TwoSplitBillsAtOnceTests(TransactionTestCase):
    # A TransactionTestCase flushes every table when it ends, migration-seeded rows (roles, settings) included, so a
    # sibling that ran earlier in the same worker leaves this one without them. Restore the seed before each test.
    serialized_rollback = True

    def test_two_split_bills_at_once_both_post_with_their_own_numbers_and_payments(self):
        from cashboxes.services import get_cashbox_balance
        from closing.services import ensure_period_is_open
        from hesba_testing.factories import make_cashbox, make_item, make_location, make_seeded_role, make_user, make_user_profile
        from permissions.models import RoleCode
        from reports.tests_dashboard import prepared_client
        from sales import pos
        from sales.models import CustomerPayment, SalesInvoice
        from settlements.models import InvoiceSettlement

        prepared_client("commercial", "retail", "customers,items_services,sales_operations,cashboxes,reports")
        owner = make_user(username="split_race_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        ensure_period_is_open(timezone.localdate())
        location = make_location(location_code="SHOP-R", is_selling_location=True, is_default=True)
        cash = make_cashbox(cashbox_code="CASH-R", is_default=True)
        card = make_cashbox(cashbox_code="CARD-R")
        service = make_item(item_code="SRV-R", is_stock_tracked=False)
        walk_in = pos.walk_in_customer()

        real_next = pos._next_number

        def slow_next(day):
            # Both tills read the same next number before either writes it.
            number = real_next(day)
            time.sleep(0.4)
            return number

        start, outcomes = threading.Barrier(2), {}

        def ring(name):
            try:
                start.wait()
                invoice, _change = pos.checkout(
                    lines=[{"item": service, "quantity": Decimal("1"), "unit_sale_price": Decimal("270.00"), "line_discount_amount": Decimal("0"),
                            "description": ""}],
                    customer=walk_in, location=location, cashbox=cash, discount=Decimal("0"), tendered=Decimal("0"), user=owner,
                    tenders=[(cash, Decimal("150.00")), (card, Decimal("120.00"))])
                outcomes[name] = invoice.invoice_number
            except Exception as exc:  # noqa: BLE001 - surfaced in the assertion message
                outcomes[name] = f"error: {exc!r}"
            finally:
                connection.close()

        with mock.patch.object(pos, "_next_number", side_effect=slow_next):
            threads = [threading.Thread(target=ring, args=(name,)) for name in ("a", "b")]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        prefix = f"POS-{timezone.localdate():%Y%m%d}-"
        self.assertEqual(sorted(outcomes.values()), [f"{prefix}0001", f"{prefix}0002"], outcomes)
        self.assertEqual(SalesInvoice.objects.filter(status="posted").count(), 2)
        self.assertEqual(sorted(CustomerPayment.objects.values_list("payment_number", flat=True)), [f"{prefix}0001-T2", f"{prefix}0002-T2"])
        self.assertEqual(InvoiceSettlement.objects.filter(source="split").count(), 2)
        self.assertEqual((get_cashbox_balance(cash), get_cashbox_balance(card)), (Decimal("300.00"), Decimal("240.00")))
