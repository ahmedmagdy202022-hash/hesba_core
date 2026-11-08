"""TEST-002: the run-wide date pin (hesba_testing/clock.py), and the proof that a
run which crosses Cairo midnight no longer breaks the two tests seen failing on CI.

The "real" clock (``clock._clock``) is swapped for a fake one, so the crossing is
simulated: the run starts at 23:59:30 Cairo, and the test body runs at 00:00:30
the next day.  The same two tests are run with the pin off as a negative control.
"""

import importlib.util
import io
import sys
import unittest
from datetime import datetime, timedelta, timezone as utc
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from . import clock

CAIRO = ZoneInfo("Africa/Cairo")
# December: Cairo is UTC+2 (no daylight saving), so 23:59:30 Cairo = 21:59:30 UTC.
START = datetime(2026, 12, 8, 23, 59, 30, tzinfo=CAIRO)
AFTER_MIDNIGHT = datetime(2026, 12, 9, 0, 0, 30, tzinfo=CAIRO)


class FakeClock:
    """Stands in for the OS clock; each reading is a microsecond after the last."""

    def __init__(self, moment):
        self.moment = moment.astimezone(utc.utc)

    def __call__(self, tz=None):
        self.moment += timedelta(microseconds=1)
        return self.moment.astimezone(tz) if tz else self.moment.replace(tzinfo=None)


class ClockCase(TestCase):
    """Swaps in the fake clock and puts the run's real pin back afterwards."""

    def start_clock(self, moment, pin=True):
        previous_anchor, previous_clock = clock.anchor_day(), clock._clock
        self.fake = FakeClock(moment)
        clock._clock = self.fake

        def restore():
            clock.uninstall()
            clock._clock = previous_clock
            if previous_anchor is not None:
                clock.install(anchor=previous_anchor)

        self.addCleanup(restore)
        return clock.install(pin=pin)

    def move_to(self, moment):
        self.fake.moment = moment.astimezone(utc.utc)


class PinnedClockTests(ClockCase):
    def test_after_midnight_the_date_stays_on_the_day_the_run_started(self):
        self.assertEqual(self.start_clock(START), START.date())
        self.move_to(AFTER_MIDNIGHT)
        self.assertEqual(timezone.localdate(), START.date())
        # 00:00:30 on the 8th in Cairo is still the evening of the 7th in UTC.
        self.assertLess(abs(timezone.now() - datetime(2026, 12, 7, 22, 0, 30, tzinfo=utc.utc)), timedelta(seconds=1))
        self.assertEqual(timezone.localtime().date(), START.date())

    def test_time_of_day_keeps_moving_forward_inside_the_pinned_day(self):
        self.start_clock(START)
        self.move_to(AFTER_MIDNIGHT)
        first, second = timezone.localtime(), timezone.localtime()
        self.assertLess(first, second)
        self.assertEqual((first.hour, first.minute), (0, 0))

    def test_several_days_later_is_shifted_back_by_whole_days(self):
        self.start_clock(START)
        self.move_to(START + timedelta(days=3, hours=1))
        self.assertEqual(timezone.localdate(), START.date())
        self.assertEqual(timezone.localtime().hour, 0)

    def test_nothing_moves_while_the_run_is_still_on_its_day(self):
        self.start_clock(START)
        self.move_to(START - timedelta(hours=5))
        self.assertEqual(timezone.localtime().hour, 18)

    def test_pin_off_is_the_plain_clock(self):
        self.start_clock(START, pin=False)
        self.move_to(AFTER_MIDNIGHT)
        self.assertEqual(timezone.localdate(), AFTER_MIDNIGHT.date())

    def test_captured_references_and_isinstance_still_work(self):
        from workorders.models import WorkOrder

        self.start_clock(START)
        self.move_to(AFTER_MIDNIGHT)
        # ``default=timezone.now`` holds the original function object.
        received = WorkOrder._meta.get_field("received_at").get_default()
        self.assertEqual(timezone.localtime(received).date(), START.date())
        self.assertIsInstance(received, timezone.datetime)
        self.assertIs(timezone.is_aware(received), True)

    def test_a_test_that_freezes_time_still_wins(self):
        self.start_clock(START)
        self.move_to(AFTER_MIDNIGHT)
        moment = datetime(2030, 1, 2, 3, 4, tzinfo=utc.utc)
        with mock.patch("django.utils.timezone.now", return_value=moment):
            self.assertEqual(timezone.now(), moment)
        with mock.patch("django.utils.timezone.localdate", return_value=moment.date()):
            self.assertEqual(timezone.localdate(), moment.date())
        self.assertEqual(timezone.localdate(), START.date())

    def test_the_runner_installs_the_pin_for_the_whole_run(self):
        # This very run is under HesbaTestRunner unless someone opted out.
        import os

        from django.conf import settings

        self.assertEqual(settings.TEST_RUNNER, "hesba_testing.runner.HesbaTestRunner")
        if not os.environ.get("HESBA_TEST_REAL_CLOCK"):
            self.assertIsNotNone(clock.anchor_day())


def _run_copy_of(module_name, test_name, moment_before, moment_after, pin, case):
    """Import a *fresh copy* of ``module_name`` (so its ``TODAY`` is computed now,
    at ``moment_before``), jump the clock to ``moment_after``, run one test."""

    spec = importlib.util.find_spec(module_name)
    package = module_name.rpartition(".")[0]
    copy_name = f"{package}.midnight_copy_of_{module_name.rpartition('.')[2]}"
    copy_spec = importlib.util.spec_from_file_location(copy_name, spec.origin)
    module = importlib.util.module_from_spec(copy_spec)
    sys.modules[copy_name] = module
    case.addCleanup(sys.modules.pop, copy_name, None)

    case.start_clock(moment_before, pin=pin)
    copy_spec.loader.exec_module(module)
    case.assertEqual(module.TODAY, moment_before.date())
    case.move_to(moment_after)
    suite = unittest.defaultTestLoader.loadTestsFromName(test_name, module)
    case.assertEqual(suite.countTestCases(), 1)
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=0).run(suite)
    return result, stream.getvalue()


class MidnightCrossingProofTests(ClockCase):
    WORKORDERS = ("workorders.tests", "WorkOrderCycleTests.test_delivery_is_one_ordinary_invoice_moving_the_exact_parts")
    EDUCATION = ("education.tests", "AttendanceTests.test_no_future_days_and_only_ones_own_groups_for_a_teacher")

    def run_both(self, pin):
        outcomes = []
        for module_name, test_name in (self.WORKORDERS, self.EDUCATION):
            outcomes.append(_run_copy_of(module_name, test_name, START, AFTER_MIDNIGHT, pin, self))
        return outcomes

    def test_with_the_pin_both_tests_pass_when_the_run_crosses_midnight(self):
        for result, output in self.run_both(pin=True):
            self.assertTrue(result.wasSuccessful(), output)

    def test_without_the_pin_the_same_two_tests_fail_as_they_did_on_ci(self):
        (workorders, workorders_output), (education, education_output) = self.run_both(pin=False)
        self.assertEqual(len(workorders.failures) + len(workorders.errors), 1, workorders_output)
        self.assertIn("warranty_until", workorders_output)
        self.assertEqual(len(education.failures) + len(education.errors), 1, education_output)
        self.assertIn("لسه مجاش", education_output)
