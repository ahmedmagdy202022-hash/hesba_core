"""E0: the education demo has something to show on every education screen (certificate, group content, makeup seats), and the seed moves no money.

How a demo is built here: the way the demo landing builds it (``config.demo_templates``: setup, then ``seed_sample_business``), with the clock frozen
at a Cairo wall-clock moment so no test depends on the hour or on Friday being a day off. Each test class below builds one demo for one moment.

The hard line comes first (``MoneyIsUntouched``): the demo is built with the new seed switched off, every figure the education screens and the
dashboard read is taken, the seed is applied under the same clock, and the same figures are taken again. Rows that legitimately change (classes,
marks, enrolments, groups) are asserted by their exact change; nothing that is money is allowed to differ.
"""

import re
from datetime import datetime, timedelta
from decimal import Decimal as D
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from expenses.models import Expense
from inventory.models import StockMovement
from ledger.models import JournalEntry
from messaging.models import OutboxMessage
from payments.models import PaymentLink
from sales.models import CustomerPayment, SalesInvoice

from . import attendance, billing, certificates, content, insights, makeup, pay, portal, services
from . import demo as education_demo
from .models import (Attendance, Certificate, ClassSession, Enrollment, FeeCharge, GroupContent, GroupPayTerm, GuardianLink, MakeupSeat, Student, StudyGroup,
                     TeacherPayment)

CAIRO = ZoneInfo("Africa/Cairo")
PASSWORD = "Demo-pass-1"
CONTENT_TITLES = education_demo.CONTENT_TITLES


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

    out = {"months": {}, "counts": {model.__name__: model.objects.count() for model in MONEY_TABLES},
           "audit_outside_education": AuditLog.objects.exclude(module="education").count()}
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


def element(html, pattern):
    """The one element a test reads: the first match of ``pattern``, or a failure that says which."""

    found = re.search(pattern, html, re.S)
    assert found is not None, pattern
    return found.group(0)


@override_settings(DEMO_MODE=True, DEMO_PASSWORD=PASSWORD, DEBUG=True)
class EducationDemoCase(TestCase):
    """One education demo of one sub-activity at one moment, built once for the class.

    The demo is built with ``seed_showcase`` switched off (``patch_seed``), the money figures are taken (``before``), the seed is applied to the same
    groups and students under the same clock, and the figures are taken again (``after``). ``wired`` classes build with the seed switched on instead,
    to prove ``prepare_demo`` really calls it."""

    sub_activity = "training"
    moment = TUESDAY_MORNING
    patch_seed = True

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
            cls.today = timezone.localdate()
            if not cls.patch_seed:
                cls.build()
                cls.has_certificates = certificates.enabled()
                return
            with mock.patch.object(education_demo, "seed_showcase") as switched_off:
                cls.build()
            assert switched_off.call_count == 1, "prepare_demo must call the seed exactly once"
            cls.seed_args = switched_off.call_args.args
            cls.has_certificates = certificates.enabled()
            client = Client()
            assert client.login(username="owner", password=PASSWORD)
            cls.before, cls.classes_before = figures(client, cls.today), class_counts()
            cls.made = education_demo.seed_showcase(*cls.seed_args)
            cls.after, cls.classes_after = figures(client, cls.today), class_counts()

    def setUp(self):
        clock = frozen(self.moment)
        clock.start()
        self.addCleanup(clock.stop)
        self.assertTrue(self.client.login(username="owner", password=PASSWORD))  # under the frozen clock, or a session made «now» is expired at a later moment
        self.owner = get_user_model().objects.get(username="owner")

    def first_group(self):
        return StudyGroup.objects.order_by("pk").first()

    def get(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()


# ---- the hard line: no money, stock or pay figure moves ----

def guardian_answer(student):
    """What a guardian types to open the portal: the child's birth date (the demo's students all have one), else the last 4 digits of the phone."""

    born = student.date_of_birth
    if born is not None:
        return {"day": str(born.day), "month": str(born.month), "year": str(born.year)}
    return {"answer": re.sub(r"\D", "", student.payer.phone)[-4:]}


def own_article(page, name):
    """One child's block of the guardian's page (the page holds every child of the family)."""

    return next(each for each in re.findall(r'<article class="gp-child".*?</article>', page, re.S) if f"<bdi>{name}</bdi>" in each.split("</h2>")[0])


class MoneyIsUntouched:
    def test_no_education_figure_the_dashboard_or_a_money_table_moves(self):
        before_last = months_around(self.today)[0]
        for first, was in self.before["months"].items():
            now = self.after["months"][first]
            for key in ("due", "preview", "teachers", "billed"):
                self.assertEqual(now[key], was[key], (first, key))  # fees due and previewed, fees billed, what each teacher is owed
            self.assertEqual((now["pages"]["fees"], now["pages"]["teachers"]), (was["pages"]["fees"], was["pages"]["teachers"]), first)  # and the screens' own text
            rows_was, totals_was = was["report"]
            rows_now, totals_now = now["report"]
            money = ("billed", "teacher", "left")
            if first != before_last:
                self.assertEqual((now["report"], now["pages"]["report"]), (was["report"], was["pages"]["report"]), first)
                continue
            # The one month the finished batch ran in: its report gains that group's row (no fee billed, no teacher), every other row is as it was.
            batch = StudyGroup.objects.filter(name=education_demo.BATCH_NAME).first()
            added = [row for row in rows_now if row not in rows_was]
            self.assertEqual([row[0] for row in added] if batch else [], [batch.pk] if batch else [])
            self.assertEqual([row for row in rows_now if row in rows_was], rows_was)
            self.assertTrue(all(row[4:] == (D("0"), D("0"), D("0")) for row in added))
            self.assertEqual({key: totals_now[key] for key in money}, {key: totals_was[key] for key in money})
        self.assertEqual((self.after["dues"], self.after["dues_page"], self.after["tiles"]), (self.before["dues"], self.before["dues_page"], self.before["tiles"]))
        self.assertEqual(self.after["counts"], self.before["counts"])  # not one invoice, payment, cash or stock movement, journal entry, fee charge, pay row or message
        self.assertEqual(self.after["audit_outside_education"], self.before["audit_outside_education"])

    def test_only_the_finished_batch_adds_classes_and_the_seats_add_none(self):
        added = {name: self.classes_after[name] - self.classes_before[name] for name in self.classes_after}
        if not self.has_certificates:
            self.assertEqual(added, {name: 0 for name in added})  # a seat is never attendance and a centre without certificates has no batch
            return
        held = ClassSession.objects.filter(group__name=education_demo.BATCH_NAME).count()
        self.assertGreaterEqual(held, 6)
        self.assertEqual(added, {"Attendance": 2 * held, "ClassSession": held, "Enrollment": 2, "Student": 0, "StudyGroup": 1})


# ---- the negative control: the comparison above does notice a change ----

class NegativeControl:
    def test_one_class_called_off_moves_the_figures(self):
        """If this did not move them, «identical» above would mean nothing."""

        before = figures(self.client, self.today)
        self.assertTrue(before["dues"], "the demo has parents who owe")
        self.assertTrue(before["months"][self.today.replace(day=1)]["teachers"], "teachers have something owed")
        self.assertEqual(figures(self.client, self.today), before)  # taking the figures twice changes nothing
        group = StudyGroup.objects.get(teacher_pay="session")  # the teacher is paid per class held
        held = ClassSession.objects.filter(group=group, status="held").latest("date")
        attendance.take(group, self.owner, held.date, {}, cancelled=True)
        self.assertNotEqual(figures(self.client, self.today)["months"], before["months"])


# ---- E9: group content ----

class ContentChecks:
    def items(self):
        return {item.kind + ("" if item.published else "-hidden"): item for item in GroupContent.objects.filter(group=self.first_group())}

    def test_the_first_group_has_a_video_a_file_an_online_class_and_a_hidden_draft(self):
        items = self.items()
        self.assertEqual(sorted(items), ["file", "file-hidden", "online", "video"])
        self.assertEqual(GroupContent.objects.count(), 4)  # only the first group has any
        for item in items.values():
            self.assertTrue(item.active)
            self.assertEqual(content.clean_url(item.url), item.url)  # every address passes the same check a typed one does
            self.assertEqual(AuditLog.objects.filter(action="save_group_content", object_id=str(item.pk)).count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="hide_group_content").count(), 1)
        self.assertEqual({item.url for item in items.values()}, set(education_demo.CONTENT_URLS))
        self.assertTrue(all(url.startswith("https://") for url in education_demo.CONTENT_URLS))
        self.assertEqual(items["video"].title, CONTENT_TITLES[self.titles_key][0])

    def test_the_online_class_is_a_few_days_ahead_on_a_day_the_group_meets_at_its_start_time(self):
        group, online = self.first_group(), self.items()["online"]
        local = timezone.localtime(online.starts_at)
        self.assertGreaterEqual(local.date(), self.today + timedelta(days=2))
        self.assertLessEqual(local.date(), self.today + timedelta(days=9))
        self.assertTrue(attendance.meets_on(group, local.date()), local)
        self.assertEqual(local.time(), group.start_time)
        self.assertGreater(online.starts_at, self.moment)  # in the future whatever the hour of the build
        self.assertEqual(online.note, education_demo.ONLINE_NOTE)

    def test_a_guardian_reads_three_items_online_class_first_and_never_the_hidden_one(self):
        group = self.first_group()
        kids = [row.student for row in Enrollment.objects.filter(group=group, status="active").select_related("student").order_by("student_id")]
        self.assertTrue(kids)
        shown = content.for_children(kids, services.scoped(StudyGroup.objects), now=self.moment)
        titles = CONTENT_TITLES[self.titles_key]
        for kid in kids:
            self.assertEqual([row["title"] for row in shown[kid.pk]], [titles[2], titles[1], titles[0]], kid.name)  # the online class first, then newest first
            self.assertNotIn(titles[3], [row["title"] for row in shown[kid.pk]])
            self.assertTrue(all(row["url"].startswith("https://") for row in shown[kid.pk]))

    def test_the_group_page_lists_the_four_with_the_hidden_one_marked(self):
        group = self.first_group()
        card = element(self.get(reverse("education:group", args=[group.pk]) + "?lang=ar"), r"<section[^>]*data-content-card.*?</section>")
        titles = CONTENT_TITLES[self.titles_key]
        rows = {pk: element(card, rf'<tr data-content="{pk}".*?</tr>') for pk in GroupContent.objects.filter(group=group).values_list("pk", flat=True)}
        self.assertEqual(len(rows), 4)
        by_title = {title: next(row for row in rows.values() if title in row) for title in titles}
        for title in titles[:3]:
            self.assertIn('data-published="1"', by_title[title], title)
        self.assertIn('data-published="0"', by_title[titles[3]])
        self.assertEqual(card.count('data-published="0"'), 1)
        self.assertIn('data-kind="online"', by_title[titles[2]])
        self.assertIn("16:00", by_title[titles[2]])  # the online class shows its own time
        self.assertIn(education_demo.ONLINE_NOTE, by_title[titles[2]])
        self.assertNotIn("لسه مفيش محتوى", card)
        self.assertTrue(re.search(r'<a href="https://www\.youtube\.com/watch\?v=EXAMPLE"[^>]*data-content-link>', by_title[titles[0]]))

    def test_the_guardians_portal_shows_the_three_and_not_the_draft(self):
        """Not a seed: the test makes the link itself (a link is shown once and has no place in a demo), then opens the public page as a guardian."""

        group = self.first_group()
        student = Enrollment.objects.filter(group=group, status="active").select_related("student").order_by("student_id").first().student
        with override_settings(PUBLIC_BASE_URL="https://demo.example.test"):
            made = portal.make_link(student, self.owner)
        path = made["url"].split("https://demo.example.test", 1)[1]
        guardian = Client()
        guardian.post(path, {**guardian_answer(student), "lang": "ar"})
        page = guardian.get(path).content.decode()
        card = element(own_article(page, student.name), r'<section class="cl-card" data-section="content">.*?</section>')
        titles = CONTENT_TITLES[self.titles_key]
        for title in titles[:3]:
            self.assertIn(title, card)
        self.assertNotIn(titles[3], card)
        self.assertEqual(card.count("https://"), 3)  # the three addresses, and no fourth
        self.assertEqual(GuardianLink.objects.count(), 1)  # the one this test made


# ---- E8: makeup seats ----

class MakeupChecks:
    def seats(self):
        return list(MakeupSeat.objects.select_related("student", "home_group", "group").order_by("day"))

    def test_one_seat_is_booked_ahead_and_one_was_attended_this_week(self):
        past, ahead = self.seats()
        self.assertEqual((past.status, ahead.status), ("attended", "booked"))
        self.assertEqual(past.group, ahead.group)
        self.assertEqual(past.home_group, ahead.home_group)
        self.assertNotEqual(past.group, past.home_group)
        self.assertEqual(past.group.course_id, past.home_group.course_id)
        self.assertNotEqual(past.student, ahead.student)
        self.assertTrue(past.missed_on is None or past.missed_on < past.day)
        self.assertTrue(self.today - timedelta(days=7) <= past.day <= self.today - timedelta(days=1), past.day)
        self.assertTrue(self.today + timedelta(days=2) <= ahead.day <= self.today + timedelta(days=9), ahead.day)
        for seat in (past, ahead):
            self.assertTrue(attendance.meets_on(seat.group, seat.day), seat.day)  # a day the group really meets
            self.assertTrue(attendance.meets_on(seat.home_group, seat.missed_on) if seat.missed_on else True)
            self.assertEqual(seat.note, education_demo.SEAT_NOTE)
            self.assertEqual(AuditLog.objects.filter(action="book_makeup_seat", object_id=str(seat.pk)).count(), 1)
        self.assertEqual(AuditLog.objects.filter(action="attend_makeup_seat").count(), 1)
        for seat in (past, ahead):
            self.assertTrue(seat.missed_on is None or seat.missed_on < self.today)  # the class missed is in the past, one of the home group's days

    def test_a_seat_is_not_attendance_and_the_booked_one_counts_against_the_room(self):
        past, ahead = self.seats()
        for seat in (past, ahead):
            self.assertFalse(Attendance.objects.filter(student=seat.student, session__group=seat.group).exists())  # the guest is on no register of the target group
        room = ahead.group
        self.assertTrue(room.capacity)
        enrolled = services.active_count(room)
        self.assertEqual(makeup.free_seats(room, ahead.day), room.capacity - enrolled - 1)  # the booked seat takes a place
        self.assertEqual(makeup.taken(room, ahead.day), 1)
        other_day = next(day for day in (ahead.day + timedelta(days=n) for n in range(1, 10)) if attendance.meets_on(room, day))
        self.assertEqual(makeup.free_seats(room, other_day), room.capacity - enrolled)  # another class of the group is untouched
        self.assertEqual(makeup.free_seats(room, past.day), room.capacity - enrolled - 1)  # an attended seat holds its place too

    def test_the_students_page_shows_the_seats_with_their_status(self):
        past, ahead = self.seats()
        for seat, label_class in ((ahead, "booked"), (past, "attended")):
            card = element(self.get(reverse("education:student", args=[seat.student.pk]) + "?lang=ar"), r"<section[^>]*data-makeup-card.*?</section>")
            row = element(card, rf'<li[^>]*data-makeup="{seat.pk}".*?</li>')
            self.assertIn(f'data-makeup-status="{label_class}"', row)
            self.assertIn(seat.group.name, row)
            self.assertIn(seat.home_group.name, row)
            self.assertNotIn("data-makeup-none", card)
        self.assertIn("data-makeup-cancel", element(self.get(reverse("education:student", args=[ahead.student.pk])), rf'<li[^>]*data-makeup="{ahead.pk}".*?</li>'))
        self.assertNotIn("data-makeup-cancel", element(self.get(reverse("education:student", args=[past.student.pk])), rf'<li[^>]*data-makeup="{past.pk}".*?</li>'))

    def test_the_target_groups_register_lists_the_guest_on_the_day(self):
        for seat in self.seats():
            html = self.get(reverse("education:attendance", args=[seat.group.pk]) + f"?lang=ar&date={seat.day.isoformat()}")
            guests = element(html, r"<section[^>]*data-makeup-guests.*?</section>")
            row = element(guests, rf'<li[^>]*data-guest="{seat.pk}".*?</li>')
            self.assertIn(f'data-guest-status="{seat.status}"', row)
            self.assertIn(seat.student.name, row)

    def test_the_guardians_portal_lists_both_seats_and_never_the_note(self):
        past, ahead = self.seats()
        with override_settings(PUBLIC_BASE_URL="https://demo.example.test"):
            made = portal.make_link(ahead.student, self.owner)
        path = made["url"].split("https://demo.example.test", 1)[1]
        guardian = Client()
        guardian.post(path, {**guardian_answer(ahead.student), "lang": "ar"})
        page = guardian.get(path).content.decode()
        card = element(own_article(page, ahead.student.name), r'<section[^>]*data-section="makeup".*?</section>')
        self.assertIn(ahead.group.name, card)
        self.assertNotIn(education_demo.SEAT_NOTE, card)  # the note is the centre's


# ---- E6: the certificate ----

class CertificateChecks:
    def test_one_live_certificate_for_a_student_who_finished_a_free_closed_batch(self):
        certificate = Certificate.objects.get()
        enrollment = certificate.enrollment
        batch = enrollment.group
        self.assertEqual(batch.name, education_demo.BATCH_NAME)
        self.assertEqual((certificate.revoked, certificate.grade_text, certificate.issued_on), (False, "امتياز", self.today))
        self.assertTrue(certificate.number.startswith(f"CRT-{self.today.year}-"))
        self.assertEqual((batch.effective_fee, batch.teacher_id, batch.teacher_pay, batch.active), (0, None, "none", False))
        this_month = self.today.replace(day=1)
        self.assertLess(batch.ends_on, (this_month - timedelta(days=1)).replace(day=1))  # ended before last month began
        self.assertLessEqual(batch.ends_on, self.today - timedelta(days=30))  # and outside the dashboard's 30-day attendance window
        held = ClassSession.objects.filter(group=batch, status="held").count()
        self.assertEqual(certificate.hours, D(held) * 2)  # two-hour classes
        self.assertEqual(certificate.attendance_rate, 100)
        self.assertEqual((enrollment.status, enrollment.end_date), ("stopped", batch.ends_on))
        log = AuditLog.objects.get(action="issue_certificate")
        self.assertEqual(log.after_data["student"], enrollment.student.code)
        self.assertTrue(log.after_data["warned"])  # this month's fees are open for every family: the manager acknowledged the warning

    def test_the_second_student_is_ready_for_a_certificate_with_good_attendance(self):
        queue = certificates.ready()
        self.assertEqual(len(queue), 1)
        waiting = queue[0]
        self.assertNotEqual(waiting.student, Certificate.objects.get().enrollment.student)
        measured = certificates.measure(waiting, self.today)
        self.assertGreaterEqual(measured["rate"], certificates.ATTENDANCE_WARN)
        self.assertEqual(measured["held"], ClassSession.objects.filter(group=waiting.group, status="held").count())

    def test_the_certificates_screen_lists_it_and_the_sheet_and_the_public_page_open(self):
        certificate = Certificate.objects.select_related("enrollment__student").get()
        page = self.get(reverse("education:certificates") + "?lang=ar")
        table = element(page, r"<table[^>]*data-certificates.*?</table>")
        row = element(table, rf'<tr data-certificate="{certificate.pk}" data-state="live".*?</tr>')
        self.assertIn(certificate.number, row)
        self.assertIn(certificate.enrollment.student.name, row)
        self.assertIn("امتياز", row)
        self.assertEqual(table.count("data-certificate="), 1)
        self.assertTrue(re.search(r":\s*1\s*</a>", element(page, r"<a[^>]*data-ready-count>.*?</a>")))  # one more finished student waits for theirs
        sheet = self.get(reverse("education:certificate", args=[certificate.pk]) + "?lang=ar")
        self.assertIn(certificate.number, element(sheet, r"<dd data-cert-number>.*?</dd>"))
        self.assertEqual(Client().get(certificates.verify_path(certificate)).status_code, 200)  # the QR's page needs no login
        issue = self.get(reverse("education:certificate_issue") + "?lang=ar")
        self.assertIn(certificates.ready()[0].student.name, issue)


class NoCertificates:
    def test_a_centre_without_certificates_has_no_batch_and_no_certificate(self):
        self.assertFalse(self.has_certificates)
        self.assertEqual((Certificate.objects.count(), StudyGroup.objects.filter(name=education_demo.BATCH_NAME).count()), (0, 0))
        self.assertEqual(self.client.get(reverse("education:certificates")).status_code, 404)  # a gate of the sub-activity, as before


# ---- the seed is run once, and a second run adds nothing ----

class Idempotent:
    def test_running_the_seed_again_adds_nothing(self):
        counts = {model.__name__: model.objects.count() for model in (Certificate, GroupContent, MakeupSeat, StudyGroup, Enrollment, ClassSession, Attendance, AuditLog)}
        self.assertEqual(education_demo.seed_showcase(*self.seed_args), {})
        self.assertEqual({model.__name__: model.objects.count() for model in (Certificate, GroupContent, MakeupSeat, StudyGroup, Enrollment, ClassSession, Attendance, AuditLog)}, counts)

    def test_building_the_sample_business_a_second_time_does_nothing(self):
        from config.demo_templates import seed_sample_business

        counts = (Certificate.objects.count(), GroupContent.objects.count(), MakeupSeat.objects.count(), StudyGroup.objects.count(), AuditLog.objects.count())
        self.assertFalse(seed_sample_business())  # the demo's own sentinel: a shop with posted sales is left alone
        self.assertEqual((Certificate.objects.count(), GroupContent.objects.count(), MakeupSeat.objects.count(), StudyGroup.objects.count(), AuditLog.objects.count()), counts)


# ---- E3, E4a, E1, E5: nothing is created, and every screen opens on the seeded data ----

class ScreensOnSeededData:
    def test_the_seed_made_no_link_token_card_message_or_payment(self):
        self.assertEqual((GuardianLink.objects.count(), PaymentLink.objects.count(), OutboxMessage.objects.count()), (0, 0, 0))
        self.assertEqual(self.after["counts"]["CustomerPayment"], self.before["counts"]["CustomerPayment"])

    def test_every_education_screen_opens(self):
        group = self.first_group()
        seat = MakeupSeat.objects.order_by("day").first()
        urls = [reverse(f"education:{name}") for name in ("students", "groups", "courses", "today", "scan", "fees", "dues", "teachers", "report", "student_new", "group_new")]
        urls += [reverse("messaging:outbox")]
        if self.has_certificates:
            urls += [reverse("education:certificates"), reverse("education:certificate_issue")]
        urls += [reverse("education:cards") + f"?group={group.pk}", reverse("education:today") + f"?date={seat.day.isoformat()}"]
        for other in StudyGroup.objects.all():
            urls += [reverse("education:group", args=[other.pk]), reverse("education:attendance", args=[other.pk]), reverse("education:exams", args=[other.pk])]
        urls += [reverse("education:content", args=[group.pk]), reverse("education:content_new", args=[group.pk])]
        for each in Student.objects.all():
            urls += [reverse("education:student", args=[each.pk]), reverse("education:report_card", args=[each.pk])]
        urls += [reverse("education:makeup_new", args=[seat.student.pk]) + f"?home={seat.home_group.pk}"]
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url + ("&" if "?" in url else "?") + "lang=ar").status_code, 200)



# ---- plain wording ----

class WordingTests(TestCase):
    def test_no_seeded_text_is_folksy_or_names_a_child(self):
        texts = [education_demo.BATCH_NAME, education_demo.SEAT_NOTE, education_demo.ONLINE_NOTE]
        for titles in CONTENT_TITLES.values():
            texts += list(titles)
        for text in texts:
            for word in ("ورشة", "الأسطى", "أسطى", "بتاع", "دوس", "لينك", "طفلك", "ابنك", "ابنتك", "ابنكم", "الحاج", "المعلم"):
                self.assertNotIn(word, text, text)
            self.assertFalse(re.search(r"[A-Za-z]{3,}", text.replace("PDF", "")), text)  # Arabic, with only «PDF» as a Latin word
        self.assertEqual(set(CONTENT_TITLES), {"training", "languages", "nursery", "tutoring"})
        for titles in CONTENT_TITLES.values():
            self.assertEqual(len(titles), 4)

    def test_every_example_address_passes_the_checks_a_typed_one_does(self):
        for url in education_demo.CONTENT_URLS:
            self.assertEqual(content.clean_url(url), url)


# ---- the demos: one per moment for the training centre, and one for every other kind of centre ----

class TrainingOnATuesdayMorning(MoneyIsUntouched, NegativeControl, ContentChecks, MakeupChecks, CertificateChecks, Idempotent, ScreensOnSeededData, EducationDemoCase):
    sub_activity, titles_key, moment = "training", "training", TUESDAY_MORNING


class TrainingOnAFridayAfternoon(MoneyIsUntouched, ContentChecks, MakeupChecks, CertificateChecks, Idempotent, EducationDemoCase):
    sub_activity, titles_key, moment = "training", "training", FRIDAY_AFTERNOON


class TrainingAtOneMinuteToMidnight(MoneyIsUntouched, ContentChecks, MakeupChecks, CertificateChecks, Idempotent, EducationDemoCase):
    sub_activity, titles_key, moment = "training", "training", LATE_NIGHT


class TrainingTenPastMidnight(MoneyIsUntouched, ContentChecks, MakeupChecks, CertificateChecks, Idempotent, EducationDemoCase):
    sub_activity, titles_key, moment = "training", "training", JUST_AFTER_MIDNIGHT


class TrainingOnTheLastDayOfTheMonth(MoneyIsUntouched, ContentChecks, MakeupChecks, CertificateChecks, Idempotent, EducationDemoCase):
    sub_activity, titles_key, moment = "training", "training", LAST_OF_THE_MONTH


class LanguageCentre(MoneyIsUntouched, ContentChecks, MakeupChecks, CertificateChecks, EducationDemoCase):
    sub_activity, titles_key = "languages", "languages"

    def test_the_finished_batch_teaches_the_level_the_first_group_does(self):
        batch = Certificate.objects.get().enrollment.group
        self.assertEqual(batch.level, self.first_group().level)
        self.assertEqual(batch.level, 1)


class Nursery(MoneyIsUntouched, ContentChecks, MakeupChecks, NoCertificates, ScreensOnSeededData, EducationDemoCase):
    sub_activity, titles_key = "nursery", "nursery"


class TutoringCentre(MoneyIsUntouched, ContentChecks, MakeupChecks, NoCertificates, EducationDemoCase):
    sub_activity, titles_key = "tutoring_center", "tutoring"


class WiredIntoTheBuild(EducationDemoCase):
    """The build itself (no switch-off) makes everything: ``prepare_demo`` calls the seed, and a second build changes nothing."""

    patch_seed = False
    titles_key = "training"

    def test_a_plain_build_has_the_certificate_the_content_and_the_seats(self):
        self.assertEqual((Certificate.objects.count(), GroupContent.objects.count(), MakeupSeat.objects.count()), (1, 4, 2))
        self.assertEqual(GroupContent.objects.filter(published=False).count(), 1)

    def test_a_second_build_adds_nothing(self):
        from config.demo_templates import seed_sample_business

        counts = {model.__name__: model.objects.count() for model in (Certificate, GroupContent, MakeupSeat, StudyGroup, ClassSession, Attendance, AuditLog, SalesInvoice)}
        self.assertFalse(seed_sample_business())
        self.assertEqual({model.__name__: model.objects.count() for model in (Certificate, GroupContent, MakeupSeat, StudyGroup, ClassSession, Attendance, AuditLog, SalesInvoice)}, counts)
