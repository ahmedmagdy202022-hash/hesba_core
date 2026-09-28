"""HG-027: two cashiers cannot both sell the last unit (PostgreSQL only).

SQLite lets one writer in at a time, so the race cannot happen there and the
test is skipped; CI runs it in the django-tests-postgres job.
"""

import threading
import time
import unittest
from decimal import Decimal
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TransactionTestCase

from hesba_testing.factories import add_sales_line, make_customer, make_draft_sales_invoice, make_item, make_location, recalculate_invoice_totals, stock_in
from inventory import services as inventory_services
from inventory.services import get_item_location_stock_quantity, recalculate_item_average_cost
from sales.models import SalesInvoice, SalesInvoiceStatus
from sales.services import post_sales_invoice


@unittest.skipUnless(connection.vendor == "postgresql", "row locks only matter on PostgreSQL")
class LastUnitRaceTests(TransactionTestCase):
    def test_two_invoices_for_the_last_unit_post_one_and_refuse_the_other(self):
        location, item, customer = make_location(), make_item(), make_customer()
        # Open the month first, so the only thing standing between the two postings is the item lock.
        from closing.services import ensure_period_is_open
        from hesba_testing.factories import DEFAULT_DATE

        ensure_period_is_open(DEFAULT_DATE)
        stock_in(item, location, 1, "5.00")
        recalculate_item_average_cost(item)
        drafts = []
        for number in ("SI-RACE-A", "SI-RACE-B"):
            invoice = make_draft_sales_invoice(customer=customer, location=location, invoice_number=number)
            add_sales_line(invoice, item, quantity=1, unit_sale_price="30.00")
            recalculate_invoice_totals(invoice, paid_now="0.00")
            drafts.append(invoice.pk)

        real_read = inventory_services.get_item_location_stock_quantity

        def slow_read(*args, **kwargs):
            # Widen the window between reading the stock and writing the movement,
            # so without the item lock both callers would read 1 and both post.
            value = real_read(*args, **kwargs)
            time.sleep(0.4)
            return value

        start, outcomes = threading.Barrier(2), {}

        def post(pk):
            try:
                start.wait()
                post_sales_invoice(pk)
                outcomes[pk] = "posted"
            except ValidationError as exc:
                outcomes[pk] = "refused: " + " ".join(exc.messages)
            except Exception as exc:  # noqa: BLE001 - surfaced in the assertion message
                outcomes[pk] = f"error: {exc!r}"
            finally:
                connection.close()

        with mock.patch("sales.services.get_item_location_stock_quantity", side_effect=slow_read):
            threads = [threading.Thread(target=post, args=(pk,)) for pk in drafts]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        self.assertEqual(sorted(value.split(":")[0] for value in outcomes.values()), ["posted", "refused"], outcomes)
        self.assertTrue(any("Not enough stock" in value for value in outcomes.values()))
        self.assertEqual(get_item_location_stock_quantity(item, location), Decimal("0"))
        self.assertEqual(SalesInvoice.objects.filter(status=SalesInvoiceStatus.POSTED).count(), 1)


@unittest.skipUnless(connection.vendor == "postgresql", "row locks only matter on PostgreSQL")
class FirstEntryOfTheMonthRaceTests(TransactionTestCase):
    def test_two_first_entries_share_one_new_period(self):
        from closing.models import Period
        from closing.services import provision_period_for
        from django.db import transaction
        from hesba_testing.factories import DEFAULT_DATE

        start, results = threading.Barrier(2), []
        real_save = Period.save

        def slow_save(self, *args, **kwargs):
            real_save(self, *args, **kwargs)
            time.sleep(0.4)  # hold the new row uncommitted while the other caller inserts

        def open_month():
            try:
                start.wait()
                with transaction.atomic():
                    results.append(provision_period_for(DEFAULT_DATE).period_code)
            except Exception as exc:  # noqa: BLE001 - surfaced in the assertion message
                results.append(f"error: {exc!r}")
            finally:
                connection.close()

        with mock.patch.object(Period, "save", slow_save):
            threads = [threading.Thread(target=open_month) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

        self.assertEqual(results, [f"{DEFAULT_DATE:%Y-%m}"] * 2, results)
        self.assertEqual(Period.objects.count(), 1)
