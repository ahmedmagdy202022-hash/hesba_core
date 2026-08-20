from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from closing.models import (
    ClosingRun,
    ClosingRunStatus,
    Period,
    PeriodStatus,
    PeriodSummary,
    PostClosingAdjustment,
    PostClosingAdjustmentStatus,
)
from closing.services import (
    build_period_summary_payload,
    cancel_post_closing_adjustment,
    complete_period_closing,
    create_post_closing_adjustment,
    ensure_period_is_open,
    get_period_for_date,
    post_closing_adjustment,
    reopen_period,
)
from hesba_testing.factories import (
    make_cashbox,
    make_customer,
    make_draft_sales_invoice,
    make_supplier,
    make_user,
    posted_invoice_ready,
)
from sales.services import post_sales_invoice


JANUARY = (date(2026, 1, 1), date(2026, 1, 31))
FEBRUARY = (date(2026, 2, 1), date(2026, 2, 28))

SUMMARY_CODES = [
    "sales_total",
    "purchase_total",
    "profit_total",
    "stock_value_total",
    "customer_balance_total",
    "supplier_balance_total",
    "cashbox_balance_total",
]


def make_period(period_code="2026-01", start_date=None, end_date=None, **kwargs):
    start_date = start_date or JANUARY[0]
    end_date = end_date or JANUARY[1]
    defaults = {"name": f"Period {period_code}", "status": PeriodStatus.OPEN}
    defaults.update(kwargs)
    return Period.objects.create(
        period_code=period_code,
        start_date=start_date,
        end_date=end_date,
        **defaults,
    )


def make_closed_period(period_code="2025-12", **kwargs):
    return make_period(
        period_code=period_code,
        start_date=date(2025, 12, 1),
        end_date=date(2025, 12, 31),
        status=PeriodStatus.CLOSED,
        closed_at=timezone.now(),
        **kwargs,
    )


class GetPeriodForDateTests(TestCase):
    def test_no_period_returns_none(self):
        self.assertIsNone(get_period_for_date(date(2026, 1, 15)))

    def test_a_date_inside_the_period_matches(self):
        period = make_period()

        self.assertEqual(get_period_for_date(date(2026, 1, 15)), period)

    def test_the_start_date_is_inside_the_period(self):
        period = make_period()

        self.assertEqual(get_period_for_date(JANUARY[0]), period)

    def test_the_end_date_is_inside_the_period(self):
        period = make_period()

        self.assertEqual(get_period_for_date(JANUARY[1]), period)

    def test_a_date_outside_every_period_returns_none(self):
        make_period()

        self.assertIsNone(get_period_for_date(date(2026, 2, 15)))

    def test_overlapping_periods_return_the_later_start(self):
        make_period(period_code="WIDE", start_date=date(2026, 1, 1), end_date=date(2026, 3, 31))
        later = make_period(
            period_code="NARROW", start_date=date(2026, 2, 1), end_date=date(2026, 2, 28)
        )

        self.assertEqual(get_period_for_date(date(2026, 2, 15)), later)


class EnsurePeriodIsOpenTests(TestCase):
    def test_missing_period_is_rejected(self):
        with self.assertRaises(ValidationError):
            ensure_period_is_open(date(2026, 1, 15))

    def test_an_open_period_is_returned(self):
        period = make_period()

        self.assertEqual(ensure_period_is_open(date(2026, 1, 15)), period)

    def test_a_closed_period_is_rejected(self):
        make_period(status=PeriodStatus.CLOSED, closed_at=timezone.now())

        with self.assertRaises(ValidationError):
            ensure_period_is_open(date(2026, 1, 15))

    def test_a_reopened_period_is_rejected(self):
        make_period(status=PeriodStatus.REOPENED, reopen_reason="correction")

        with self.assertRaises(ValidationError):
            ensure_period_is_open(date(2026, 1, 15))


class BuildPeriodSummaryPayloadTests(TestCase):
    def test_payload_has_one_row_per_summary_code(self):
        payload = build_period_summary_payload(make_period())

        self.assertEqual([row["code"] for row in payload], SUMMARY_CODES)

    def test_every_row_carries_a_name_and_amount(self):
        for row in build_period_summary_payload(make_period()):
            with self.subTest(code=row["code"]):
                self.assertTrue(row["name"])
                self.assertIsInstance(row["amount"], Decimal)
                self.assertEqual(row["quantity"], Decimal("0"))

    def test_an_empty_period_totals_zero(self):
        payload = build_period_summary_payload(make_period())

        self.assertEqual({row["amount"] for row in payload}, {Decimal("0")})

    def test_posted_sales_inside_the_period_are_counted(self):
        period = make_period()
        invoice, _, _, _ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)

        payload = {row["code"]: row["amount"] for row in build_period_summary_payload(period)}

        self.assertEqual(payload["sales_total"], Decimal("60.00"))
        self.assertEqual(payload["profit_total"], Decimal("50.00"))

    def test_draft_sales_are_not_counted(self):
        period = make_period()
        posted_invoice_ready()

        payload = {row["code"]: row["amount"] for row in build_period_summary_payload(period)}

        self.assertEqual(payload["sales_total"], Decimal("0"))

    def test_sales_outside_the_period_are_not_counted(self):
        period = make_period(period_code="2026-02", start_date=FEBRUARY[0], end_date=FEBRUARY[1])
        invoice, _, _, _ = posted_invoice_ready()
        post_sales_invoice(invoice.pk)

        payload = {row["code"]: row["amount"] for row in build_period_summary_payload(period)}

        self.assertEqual(payload["sales_total"], Decimal("0"))

    def test_customer_and_supplier_balances_are_totalled(self):
        period = make_period()
        make_customer(opening_balance=Decimal("30.00"))
        make_supplier(opening_balance=Decimal("70.00"))

        payload = {row["code"]: row["amount"] for row in build_period_summary_payload(period)}

        self.assertEqual(payload["customer_balance_total"], Decimal("30.00"))
        self.assertEqual(payload["supplier_balance_total"], Decimal("70.00"))

    def test_cashbox_balances_are_totalled(self):
        period = make_period()
        make_cashbox(opening_balance=Decimal("500.00"))

        payload = {row["code"]: row["amount"] for row in build_period_summary_payload(period)}

        self.assertEqual(payload["cashbox_balance_total"], Decimal("500.00"))


class CompletePeriodClosingTests(TestCase):
    def test_closing_an_open_period_creates_the_first_run(self):
        period = make_period()

        run = complete_period_closing(period.pk)

        self.assertEqual(run.run_number, 1)
        self.assertEqual(run.status, ClosingRunStatus.COMPLETED)
        self.assertIsNotNone(run.completed_at)

    def test_closing_marks_the_period_closed(self):
        period = make_period()

        complete_period_closing(period.pk)
        period.refresh_from_db()

        self.assertEqual(period.status, PeriodStatus.CLOSED)
        self.assertIsNotNone(period.closed_at)

    def test_closing_records_who_closed_the_period(self):
        period = make_period()
        user = make_user()

        complete_period_closing(period.pk, user=user)
        period.refresh_from_db()

        self.assertEqual(period.closed_by_id, user.pk)

    def test_closing_writes_one_summary_row_per_code(self):
        period = make_period()

        run = complete_period_closing(period.pk)

        summaries = PeriodSummary.objects.filter(closing_run=run)
        self.assertEqual(summaries.count(), len(SUMMARY_CODES))
        self.assertEqual(
            sorted(summaries.values_list("summary_code", flat=True)),
            sorted(SUMMARY_CODES),
        )

    def test_closing_an_already_closed_period_is_rejected(self):
        period = make_period()
        complete_period_closing(period.pk)

        with self.assertRaises(ValidationError):
            complete_period_closing(period.pk)

    def test_reclosing_a_reopened_period_currently_fails(self):
        """KNOWN DEFECT: reopening then closing again always crashes.

        PeriodSummary's unique constraint is on (period, summary_code) even
        though each row also carries closing_run, so a second run cannot write
        its summaries and the whole closing rolls back. Both _next_run_number()
        and reopen_period() exist to support re-closing, so the constraint
        contradicts the intended design rather than enforcing it.

        This test documents today's behaviour. Invert it when the constraint is
        fixed to include closing_run.
        """
        period = make_period()
        complete_period_closing(period.pk)
        reopen_period(period.pk, reason="late invoice")

        with self.assertRaises(IntegrityError):
            complete_period_closing(period.pk)

        period.refresh_from_db()
        self.assertEqual(period.status, PeriodStatus.REOPENED)
        self.assertEqual(ClosingRun.objects.count(), 1)

    def test_closing_writes_an_audit_log(self):
        period = make_period()

        complete_period_closing(period.pk, reason="month end")

        log = AuditLog.objects.get(action="complete_period_closing")
        self.assertEqual(log.event_type, AuditEventType.CLOSING)
        self.assertEqual(log.module, "closing")
        self.assertEqual(log.reason, "month end")
        self.assertEqual(log.after_data["status"], PeriodStatus.CLOSED)
        self.assertEqual(log.after_data["run_number"], 1)

    def test_the_reason_is_stored_on_the_run(self):
        period = make_period()

        run = complete_period_closing(period.pk, reason="month end")

        self.assertEqual(run.reason, "month end")


class ReopenPeriodTests(TestCase):
    def setUp(self):
        super().setUp()
        self.period = make_period()
        complete_period_closing(self.period.pk)

    def test_reopening_without_a_reason_is_rejected(self):
        with self.assertRaises(ValidationError):
            reopen_period(self.period.pk, reason="")

    def test_reopening_sets_the_reopened_status_and_reason(self):
        reopened = reopen_period(self.period.pk, reason="late invoice")

        self.assertEqual(reopened.status, PeriodStatus.REOPENED)
        self.assertEqual(reopened.reopen_reason, "late invoice")
        self.assertIsNotNone(reopened.reopened_at)

    def test_reopening_records_who_reopened_the_period(self):
        user = make_user()

        reopened = reopen_period(self.period.pk, user=user, reason="late invoice")

        self.assertEqual(reopened.reopened_by_id, user.pk)

    def test_reopening_an_open_period_is_rejected(self):
        open_period = make_period(period_code="2026-02", start_date=FEBRUARY[0], end_date=FEBRUARY[1])

        with self.assertRaises(ValidationError):
            reopen_period(open_period.pk, reason="why not")

    def test_reopening_twice_is_rejected(self):
        reopen_period(self.period.pk, reason="late invoice")

        with self.assertRaises(ValidationError):
            reopen_period(self.period.pk, reason="again")

    def test_reopening_writes_an_audit_log(self):
        reopen_period(self.period.pk, reason="late invoice")

        log = AuditLog.objects.get(action="reopen_period")
        self.assertEqual(log.event_type, AuditEventType.REOPENING)
        self.assertEqual(log.reason, "late invoice")


class CreatePostClosingAdjustmentTests(TestCase):
    def setUp(self):
        super().setUp()
        self.closed = make_closed_period()
        self.open_period = make_period()

    def create(self, **kwargs):
        defaults = {
            "adjustment_number": "ADJ-001",
            "related_closed_period": self.closed,
            "adjustment_date": date(2026, 1, 15),
            "reason": "missed expense",
        }
        defaults.update(kwargs)
        return create_post_closing_adjustment(**defaults)

    def test_adjustment_is_created_as_a_draft(self):
        adjustment = self.create()

        self.assertEqual(PostClosingAdjustment.objects.count(), 1)
        self.assertEqual(adjustment.status, PostClosingAdjustmentStatus.DRAFT)
        self.assertEqual(adjustment.related_closed_period_id, self.closed.pk)

    def test_referencing_an_open_period_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.create(related_closed_period=self.open_period)

    def test_an_adjustment_date_in_no_period_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.create(adjustment_date=date(2027, 6, 1))

    def test_an_adjustment_date_in_a_closed_period_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.create(adjustment_date=date(2025, 12, 15))

    def test_creating_writes_an_audit_log(self):
        adjustment = self.create()

        log = AuditLog.objects.get(action="create_post_closing_adjustment")
        self.assertEqual(log.event_type, AuditEventType.ADJUSTMENT)
        self.assertEqual(log.object_id, str(adjustment.pk))
        self.assertEqual(log.reason, "missed expense")

    def test_the_acting_user_is_recorded(self):
        user = make_user()

        adjustment = self.create(user=user)

        self.assertEqual(adjustment.created_by_id, user.pk)


class PostClosingAdjustmentTests(TestCase):
    def setUp(self):
        super().setUp()
        self.closed = make_closed_period()
        self.open_period = make_period()
        self.adjustment = create_post_closing_adjustment(
            adjustment_number="ADJ-001",
            related_closed_period=self.closed,
            adjustment_date=date(2026, 1, 15),
            reason="missed expense",
        )

    def test_posting_sets_the_posted_status(self):
        posted = post_closing_adjustment(self.adjustment.pk)

        self.assertEqual(posted.status, PostClosingAdjustmentStatus.POSTED)

    def test_posting_twice_is_rejected(self):
        post_closing_adjustment(self.adjustment.pk)

        with self.assertRaises(ValidationError):
            post_closing_adjustment(self.adjustment.pk)

    def test_posting_is_rejected_once_the_related_period_is_reopened(self):
        reopen_period(self.closed.pk, reason="correction")

        with self.assertRaises(ValidationError):
            post_closing_adjustment(self.adjustment.pk)

    def test_posting_is_rejected_when_the_target_period_is_closed(self):
        complete_period_closing(self.open_period.pk)

        with self.assertRaises(ValidationError):
            post_closing_adjustment(self.adjustment.pk)

    def test_posting_writes_an_audit_log(self):
        post_closing_adjustment(self.adjustment.pk)

        log = AuditLog.objects.get(action="post_closing_adjustment")
        self.assertEqual(log.after_data["status"], PostClosingAdjustmentStatus.POSTED)


class CancelPostClosingAdjustmentTests(TestCase):
    def setUp(self):
        super().setUp()
        self.closed = make_closed_period()
        self.open_period = make_period()
        self.adjustment = create_post_closing_adjustment(
            adjustment_number="ADJ-001",
            related_closed_period=self.closed,
            adjustment_date=date(2026, 1, 15),
            reason="missed expense",
        )

    def test_cancelling_a_draft_is_rejected(self):
        with self.assertRaises(ValidationError):
            cancel_post_closing_adjustment(self.adjustment.pk, reason="mistake")

    def test_cancelling_without_a_reason_is_rejected(self):
        post_closing_adjustment(self.adjustment.pk)

        with self.assertRaises(ValidationError):
            cancel_post_closing_adjustment(self.adjustment.pk, reason="")

    def test_cancelling_sets_the_cancelled_status(self):
        post_closing_adjustment(self.adjustment.pk)

        cancelled = cancel_post_closing_adjustment(self.adjustment.pk, reason="mistake")

        self.assertEqual(cancelled.status, PostClosingAdjustmentStatus.CANCELLED)

    def test_cancelling_twice_is_rejected(self):
        post_closing_adjustment(self.adjustment.pk)
        cancel_post_closing_adjustment(self.adjustment.pk, reason="mistake")

        with self.assertRaises(ValidationError):
            cancel_post_closing_adjustment(self.adjustment.pk, reason="again")

    def test_cancelling_writes_an_audit_log_with_the_reason(self):
        post_closing_adjustment(self.adjustment.pk)

        cancel_post_closing_adjustment(self.adjustment.pk, reason="mistake")

        log = AuditLog.objects.get(action="cancel_post_closing_adjustment")
        self.assertEqual(log.reason, "mistake")
