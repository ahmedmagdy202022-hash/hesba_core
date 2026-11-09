"""TEST-002: the run-wide date pin (hesba_testing/clock.py), and the proof that a
run which crosses Cairo midnight no longer breaks the two tests seen failing on CI.

The "real" clock (``clock._clock``) is swapped for a fake one, so the crossing is
simulated: the run starts at 23:59:30 Cairo, and the test body runs at 00:00:30
the next day.  The same two tests are run with the pin off as a negative control.
"""

import importlib.util
import sys
from datetime import datetime, timedelta, timezone as utc
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
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

    def test_timezone_datetime_still_constructs_plain_datetimes(self):
        # attendance/tests.py builds ``timezone.datetime(2026, 1, 1)``.
        self.start_clock(START)
        built = timezone.datetime(2026, 1, 1)
        self.assertIs(type(built), datetime)
        self.assertEqual(built, datetime(2026, 1, 1))
        aware = timezone.datetime(2026, 1, 1, 9, 30, tzinfo=utc.utc)
        self.assertIs(type(aware), datetime)
        combined = timezone.datetime.combine(START.date(), START.timetz())
        self.assertIs(type(combined), datetime)
        self.assertEqual(combined, START)
        parsed = timezone.datetime.fromisoformat("2026-12-08T10:00:00+02:00")
        self.assertIs(type(parsed), datetime)
        self.assertEqual(parsed + timedelta(hours=1), datetime(2026, 12, 8, 11, 0, tzinfo=CAIRO))
        self.assertEqual(timezone.datetime.min, datetime.min)
        self.assertEqual(timezone.datetime.max, datetime.max)
        self.assertEqual(timezone.datetime.strptime("2026-01-02", "%Y-%m-%d"), datetime(2026, 1, 2))
        self.assertIs(type(timezone.now()), datetime)
        self.assertIsInstance(built, timezone.datetime)

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


def _fresh_copy_of(module_name, moment_before, case, pin):
    """Import a *fresh copy* of a test module while the (fake) clock reads
    ``moment_before``, so its import-time ``TODAY`` is computed exactly as it is
    when a CI run starts at that moment.  Needs no database."""

    spec = importlib.util.find_spec(module_name)
    package, _, leaf = module_name.rpartition(".")
    copy_name = f"{package}.midnight_copy_of_{leaf}"
    copy_spec = importlib.util.spec_from_file_location(copy_name, spec.origin)
    module = importlib.util.module_from_spec(copy_spec)
    sys.modules[copy_name] = module
    case.addCleanup(sys.modules.pop, copy_name, None)
    case.start_clock(moment_before, pin=pin)
    copy_spec.loader.exec_module(module)
    case.assertEqual(module.TODAY, moment_before.date())
    return module


class MidnightCrossingProofTests(ClockCase):
    """The two CI failures, reproduced at the service calls they hit.

    The test modules are only imported (for their ``TODAY``) and the services are
    called directly, so no other module's TestCase is nested inside this one.
    """

    def deliver_stamps_warranty_from_today(self, module):
        # workorders/services.py: ``warranty_until = timezone.localdate() + timedelta(days=warranty_days)``;
        # the test asserts it equals ``TODAY + timedelta(days=30)``.
        return timezone.localdate() + timedelta(days=30) == module.TODAY + timedelta(days=30)

    def tomorrow_is_refused_as_a_future_day(self, module):
        # education/attendance.py take(): ``if day > timezone.localdate(): raise ValidationError(future)``.
        from education import attendance

        try:
            with mock.patch.object(attendance, "can_take", return_value=True):
                attendance.take(mock.Mock(), mock.Mock(), module.TODAY + timedelta(days=1), {})
        except ValidationError as error:
            return "لسه مجاش" in str(error)
        except Exception:  # went past the day check, so tomorrow was accepted as not-future
            return False
        return False

    def test_with_the_pin_both_scenarios_hold_after_the_run_crosses_midnight(self):
        workorders = _fresh_copy_of("workorders.tests", START, self, pin=True)
        education = _fresh_copy_of("education.tests", START, self, pin=True)
        self.move_to(AFTER_MIDNIGHT)
        self.assertTrue(self.deliver_stamps_warranty_from_today(workorders))
        self.assertTrue(self.tomorrow_is_refused_as_a_future_day(education))

    def test_without_the_pin_both_scenarios_break_as_they_did_on_ci(self):
        workorders = _fresh_copy_of("workorders.tests", START, self, pin=False)
        education = _fresh_copy_of("education.tests", START, self, pin=False)
        self.move_to(AFTER_MIDNIGHT)
        self.assertFalse(self.deliver_stamps_warranty_from_today(workorders))
        self.assertFalse(self.tomorrow_is_refused_as_a_future_day(education))
