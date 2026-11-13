"""E0: the education demo has something to show on every education screen (certificate, group content, makeup seats), and the seed moves no money.

How a demo is built here: the way the demo landing builds it (``config.demo_templates``: setup, then ``seed_sample_business``), with the clock frozen
at a Cairo wall-clock moment so no test depends on the hour or on Friday being a day off. Each test class below builds one demo for one moment.

The hard line comes first (``MoneyIsUntouched``): the demo is built with the new seed switched off, every figure the education screens and the
dashboard read is taken, the seed is applied under the same clock, and the same figures are taken again. Rows that legitimately change (classes,
marks, enrolments, groups) are asserted by their exact change; nothing that is money is allowed to differ.
"""

import re
from datetime import datetime, timedelta
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from cashboxes.models import CashboxMovement
from expenses.models import Expense
from inventory.models import StockMovement
from ledger.models import JournalEntry
from messaging.models import OutboxMessage
from sales.models import CustomerPayment, SalesInvoice

from . import attendance, billing, insights, pay
from .models import Attendance, ClassSession, Enrollment, FeeCharge, GroupPayTerm, Student, StudyGroup, TeacherPayment

CAIRO = ZoneInfo("Africa/Cairo")
PASSWORD = "Demo-pass-1"


def cairo(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=CAIRO)


#: The moments the demo must build at (none is on a night Egypt changes its clock). Each is a Cairo wall-clock time.
TUESDAY_MORNING = cairo(2026, 11, 10, 10, 40)
FRIDAY_AFTERNOON = cairo(2026, 11, 13, 15, 30)  # a day off for most groups
LATE_NIGHT = cairo(2026, 11, 11, 23, 59)
JUST_AFTER_MIDNIGHT = cairo(2026, 11, 12, 0, 10)
LAST_OF_THE_MONTH = cairo(2026, 12, 31, 14, 0)  # also the last day of a year: next month is January


def frozen(moment):
    return mock.patch.object(timezone, "now", return_value=moment)


MONEY_TABLES = (SalesInvoice, CashboxMovement, JournalEntry, TeacherPayment, FeeCharge, CustomerPayment, GroupPayTerm, OutboxMessage, StockMovement, Expense)
CLASS_TABLES = (Attendance, ClassSession, Enrollment, Student, StudyGroup)  # what a seed that adds a finished batch legitimately changes


def months_around(today):
    """The month before last, last month, this month and the next: every month the fees and pay screens can be pointed at."""

    this = today.replace(day=1)
    last = (this - timedelta(days=1)).replace(day=1)
    before_last = (last - timedelta(days=1)).replace(day=1)
    following = (this + timedelta(days=32)).replace(day=1)
    return [before_last, last, this, following]


def page_text(client, url):
    """The screen's own text (the body of ``<main>``) with the per-response CSRF values taken out."""

    response = client.get(url)
    assert response.status_code == 200, (url, response.status_code)
    body = response.content.decode()
    body = re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', 'name="csrfmiddlewaretoken" value="X"', body)
    main = body.split("<main", 1)[-1].split("</main>", 1)[0]
    return re.sub(r"\s+", " ", main)


def figures(client, today):
    """Everything the education money screens and the dashboard read, for every month around today, plus the row counts of every money table."""

    out = {"months": {}, "counts": {model.__name__: model.objects.count() for model in MONEY_TABLES}}
    for first in months_around(today):
        due = [(l["enrollment"].pk, l["quantity"], l["price"], l["discount"], l["amount"]) for l in billing.due_lines(first)]
        preview = [(r["payer"].pk, [(l["enrollment"].pk, l["amount"]) for l in r["lines"]], r["net"], r["tax"], r["total"]) for r in billing.preview(first)]
        teachers = [(r["teacher"].pk, r["due"], r["paid"], r["remaining"], [(g["group"].pk, g["units"], g["due"]) for g in r["groups"]]) for r in pay.teacher_rows(first)]
        rows, totals = insights.group_report(first)
        report = ([(r["group"].pk, r["students"], r["held"], r["rate"], r["billed"], r["teacher"], r["left"]) for r in rows], totals)
        billed = [(row["invoice"].pk, row["students"]) for row in billing.billed(first)]
        month = first.strftime("%Y-%m")
        pages = {name: page_text(client, f"/education/{name}/?month={month}&lang=ar") for name in ("fees", "teachers", "report")}
        out["months"][first] = {"due": due, "preview": preview, "teachers": teachers, "report": report, "billed": billed, "pages": pages}
    out["dues"] = [(row["party"].pk, row["total"]) for row in billing.dues(today)]
    out["dues_page"] = page_text(client, "/education/dues/?lang=ar")
    tiles, lists = insights.panel_tiles("ar", today)
    out["tiles"] = ([(t["key"], t["value"], t["sub"]) for t in tiles], lists)
    return out


def class_counts():
    return {model.__name__: model.objects.count() for model in CLASS_TABLES}


@override_settings(DEMO_MODE=True, DEMO_PASSWORD=PASSWORD, DEBUG=True)
class EducationDemoCase(TestCase):
    """One education demo of one sub-activity at one moment, built once for the class. ``before``/``after`` are the money figures."""

    sub_activity = "training"
    moment = TUESDAY_MORNING

    @classmethod
    def build(cls):
        """The demo landing's build: the client, the demo users, setup, and the sample business (what ``prepare_demo`` seeds)."""

        from config.demo_templates import complete_demo_setup, seed_sample_business

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password=PASSWORD, verbosity=0)
        call_command("seed_demo_users", password=PASSWORD, force=True, verbosity=0, stdout=StringIO())
        complete_demo_setup("education", cls.sub_activity)
        assert seed_sample_business(), cls.sub_activity

    @classmethod
    def setUpTestData(cls):
        with frozen(cls.moment):
            cls.build()
            cls.today = timezone.localdate()

    def setUp(self):
        self.assertTrue(self.client.login(username="owner", password=PASSWORD))
        clock = frozen(self.moment)
        clock.start()
        self.addCleanup(clock.stop)


# ---- the scaffold: the figures are real, and the comparison notices a change ----

class MoneyFiguresScaffold(EducationDemoCase):
    def test_the_figures_are_there_and_a_real_class_changes_them(self):
        """The negative control: one extra class held in a paid group moves the figures, so a comparison that says «identical» means something."""

        before = figures(self.client, self.today)
        this = self.today.replace(day=1)
        self.assertTrue(before["dues"], "the demo has parents who owe")
        self.assertTrue(before["months"][this]["billed"], "this month is billed")
        self.assertTrue(before["months"][this]["teachers"], "teachers have something owed")
        self.assertEqual(figures(self.client, self.today), before)  # taking the figures twice changes nothing
        owner = get_user_model().objects.get(username="owner")
        group = StudyGroup.objects.get(teacher_pay="session")  # the teacher is paid per class held
        held = ClassSession.objects.filter(group=group, status="held").latest("date")
        attendance.take(group, owner, held.date, {}, cancelled=True)  # one class called off
        self.assertNotEqual(figures(self.client, self.today)["months"], before["months"])
