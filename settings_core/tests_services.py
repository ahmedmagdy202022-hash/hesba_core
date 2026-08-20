from django.test import TestCase

from cashboxes.models import CashboxDirection
from hesba_testing.factories import (
    make_cashbox,
    make_cashbox_movement,
    make_customer,
    make_item,
    make_location,
    make_supplier,
    stock_in,
)
from settings_core.models import UsageStatusLevel, UsageStatusSnapshot
from settings_core.services import (
    DEFAULT_RECOMMENDATIONS,
    GREEN_LIMIT,
    ORANGE_LIMIT,
    YELLOW_LIMIT,
    build_usage_warnings,
    calculate_total_rows,
    collect_usage_metrics,
    create_usage_status_snapshot,
    evaluate_usage_status,
)


class CollectUsageMetricsTests(TestCase):
    def test_metrics_are_zero_on_an_empty_database(self):
        self.assertEqual(
            collect_usage_metrics(),
            {
                "active_items_count": 0,
                "active_customers_count": 0,
                "active_suppliers_count": 0,
                "stock_movements_count": 0,
                "cashbox_movements_count": 0,
                "sales_invoices_count": 0,
                "purchase_invoices_count": 0,
            },
        )

    def test_metrics_count_existing_rows(self):
        location = make_location()
        item = make_item()
        make_customer()
        make_supplier()
        stock_in(item, location, "5")
        make_cashbox_movement(make_cashbox(), CashboxDirection.IN, "10.00")

        metrics = collect_usage_metrics()

        self.assertEqual(metrics["active_items_count"], 1)
        self.assertEqual(metrics["active_customers_count"], 1)
        self.assertEqual(metrics["active_suppliers_count"], 1)
        self.assertEqual(metrics["stock_movements_count"], 1)
        self.assertEqual(metrics["cashbox_movements_count"], 1)

    def test_inactive_master_data_is_excluded(self):
        make_item(item_code="ITEM-OFF", active=False)
        make_customer(customer_code="CUST-OFF", active=False)
        make_supplier(supplier_code="SUP-OFF", active=False)

        metrics = collect_usage_metrics()

        self.assertEqual(metrics["active_items_count"], 0)
        self.assertEqual(metrics["active_customers_count"], 0)
        self.assertEqual(metrics["active_suppliers_count"], 0)


class CalculateTotalRowsTests(TestCase):
    def test_total_is_the_sum_of_metric_values(self):
        self.assertEqual(calculate_total_rows({"a": 3, "b": 4, "c": 0}), 7)

    def test_total_of_no_metrics_is_zero(self):
        self.assertEqual(calculate_total_rows({}), 0)


class EvaluateUsageStatusTests(TestCase):
    """Threshold boundaries: each limit is inclusive of the higher level."""

    def test_zero_rows_is_green(self):
        self.assertEqual(evaluate_usage_status(0), UsageStatusLevel.GREEN)

    def test_just_below_green_limit_is_green(self):
        self.assertEqual(evaluate_usage_status(GREEN_LIMIT - 1), UsageStatusLevel.GREEN)

    def test_green_limit_exactly_is_yellow(self):
        self.assertEqual(evaluate_usage_status(GREEN_LIMIT), UsageStatusLevel.YELLOW)

    def test_just_below_yellow_limit_is_yellow(self):
        self.assertEqual(evaluate_usage_status(YELLOW_LIMIT - 1), UsageStatusLevel.YELLOW)

    def test_yellow_limit_exactly_is_orange(self):
        self.assertEqual(evaluate_usage_status(YELLOW_LIMIT), UsageStatusLevel.ORANGE)

    def test_just_below_orange_limit_is_orange(self):
        self.assertEqual(evaluate_usage_status(ORANGE_LIMIT - 1), UsageStatusLevel.ORANGE)

    def test_orange_limit_exactly_is_red(self):
        self.assertEqual(evaluate_usage_status(ORANGE_LIMIT), UsageStatusLevel.RED)

    def test_far_above_orange_limit_is_red(self):
        self.assertEqual(evaluate_usage_status(ORANGE_LIMIT * 10), UsageStatusLevel.RED)

    def test_limits_are_ordered(self):
        self.assertLess(GREEN_LIMIT, YELLOW_LIMIT)
        self.assertLess(YELLOW_LIMIT, ORANGE_LIMIT)


class BuildUsageWarningsTests(TestCase):
    def test_every_level_returns_exactly_one_warning(self):
        for level in UsageStatusLevel.values:
            with self.subTest(level=level):
                self.assertEqual(len(build_usage_warnings(level)), 1)

    def test_each_level_has_its_own_message(self):
        messages = {
            level: build_usage_warnings(level)[0] for level in UsageStatusLevel.values
        }

        self.assertEqual(len(set(messages.values())), len(UsageStatusLevel.values))

    def test_green_reports_normal_usage(self):
        self.assertEqual(
            build_usage_warnings(UsageStatusLevel.GREEN), ["Usage is normal."]
        )

    def test_an_unknown_level_falls_back_to_the_action_warning(self):
        self.assertEqual(build_usage_warnings("not-a-level"), ["Usage needs action."])


class CreateUsageStatusSnapshotTests(TestCase):
    def test_snapshot_is_persisted(self):
        snapshot = create_usage_status_snapshot()

        self.assertEqual(UsageStatusSnapshot.objects.count(), 1)
        self.assertEqual(UsageStatusSnapshot.objects.get(), snapshot)

    def test_empty_database_snapshot_is_green_with_zero_rows(self):
        snapshot = create_usage_status_snapshot()

        self.assertEqual(snapshot.status_level, UsageStatusLevel.GREEN)
        self.assertEqual(snapshot.total_rows, 0)
        self.assertEqual(snapshot.warnings, ["Usage is normal."])

    def test_snapshot_stores_the_default_recommendations(self):
        snapshot = create_usage_status_snapshot()

        self.assertEqual(snapshot.recommendations, DEFAULT_RECOMMENDATIONS)

    def test_snapshot_records_each_metric_and_their_total(self):
        location = make_location()
        item = make_item()
        make_customer()
        stock_in(item, location, "5")

        snapshot = create_usage_status_snapshot()

        self.assertEqual(snapshot.active_items_count, 1)
        self.assertEqual(snapshot.active_customers_count, 1)
        self.assertEqual(snapshot.stock_movements_count, 1)
        self.assertEqual(snapshot.total_rows, 3)

    def test_successive_snapshots_accumulate(self):
        create_usage_status_snapshot()
        create_usage_status_snapshot()

        self.assertEqual(UsageStatusSnapshot.objects.count(), 2)
