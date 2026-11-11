"""E1: the student's QR card, and attendance by scan.

"Today" comes from ``timezone.localdate()`` and every moment is built from it and passed to ``scan.mark`` as ``now``; no clock is patched.
The money edge is pinned with explicit amounts: per-class fees bill the present and late marks, so a student who was never scanned must
never be marked present by closing the class from the sheet.
"""

import re
from datetime import datetime, time, timedelta
from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import make_location
from permissions.models import RoleCode

from . import attendance, billing, scan, services
from .models import Attendance, ClassSession, Enrollment
from .tests import EducationSetup, person

TODAY = timezone.localdate()
EVERY_DAY = "sun,mon,tue,wed,thu,fri,sat"
RADIO = re.compile(r'<input type="radio" name="p_(\d+)" value="(\w+)" checked>')
VERSION = re.compile(r'name="sheet_version" value="([^"]*)"')


def at(day, hour, minute, second=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute, second)))


class ScanSetup(EducationSetup):
    """A per-class group at 50.00 meeting every day at 16:00 for 90 minutes, and three children with a parent each."""

    def setUp(self):
        super().setUp()
        make_location(location_code="CENTER", is_default=True)  # where the fee invoices are recorded, as every install has
        self.client.force_login(self.owner)
        review = services.save_course({"name": "حصص مراجعة", "fee": "50", "basis": "session"}, self.owner)
        self.review = services.save_group({"course": review, "name": "مراجعة يومية", "teacher": self.teacher, "days": EVERY_DAY, "start_time": time(16),
                                           "duration_minutes": "90", "capacity": "0"}, self.owner)
        self.a, self.b, self.c = (self.kid("آدم", "01010000001"), self.kid("بسمة", "01010000002"), self.kid("جاد", "01010000003"))

    def kid(self, name, phone, group=None, start=None):
        student = services.save_student({"name": name, "payer_name": f"ولي أمر {name}", "payer_phone": phone}, self.owner)
        services.enroll(student, group or self.review, self.owner, start_date=start or TODAY - timedelta(days=60))
        return student

    def marks(self, day=TODAY, group=None):
        session = ClassSession.objects.get(group=group or self.review, date=day)
        return dict(session.marks.values_list("student__name", "presence"))

    def sheet_url(self, day):
        return reverse("education:attendance", args=[self.review.pk]) + f"?date={day.isoformat()}"

    def close_from_the_sheet(self, day):
        """What a teacher does: open the sheet, change nothing, press save. Posts exactly what the page showed as chosen."""

        form = self.open_sheet(day)
        response = self.client.post(reverse("education:attendance", args=[self.review.pk]), {"date": day.isoformat(), "lang": "ar", **form})
        self.assertEqual(response.status_code, 302)
        return {key: value for key, value in form.items() if key.startswith("p_")}

    def open_sheet(self, day):
        """The form fields exactly as the open page holds them: every checked radio and the sheet's version."""

        html = self.client.get(self.sheet_url(day)).content.decode()
        form = {f"p_{pk}": value for pk, value in RADIO.findall(html)}
        self.assertEqual(len(form), 3)
        form["sheet_version"] = VERSION.search(html).group(1)
        return form

    def save(self, day, form):
        return self.client.post(reverse("education:attendance", args=[self.review.pk]), {"date": day.isoformat(), "lang": "ar", **form})


class ScanRulesTests(ScanSetup):
    def test_a_scan_marks_present_and_writes_an_audit_row(self):
        result = scan.mark("st-00001 ", self.owner, at(TODAY, 15, 45))  # typed or scanned: case and spaces do not matter
        self.assertEqual((result.status, result.presence, result.at, result.student, result.group), ("ok", "present", "15:45", self.a, self.review))
        self.assertEqual(self.marks(), {"آدم": "present"})
        audit = AuditLog.objects.get(action="scan_attendance")
        self.assertEqual((audit.actor, audit.after_data["student"], audit.after_data["presence"], audit.after_data["session_started"]), (self.owner, "ST-00001", "present", True))

    def test_exactly_ten_minutes_is_present_and_one_second_more_is_late(self):
        self.assertEqual(scan.mark(self.a.code, self.owner, at(TODAY, 16, 10, 0)).presence, "present")
        self.assertEqual(scan.mark(self.b.code, self.owner, at(TODAY, 16, 10, 1)).presence, "late")
        self.assertEqual(self.marks(), {"آدم": "present", "بسمة": "late"})
        self.assertEqual(scan.LATE_MINUTES, 10)

    def test_the_window_is_thirty_minutes_before_the_start_until_the_class_ends(self):
        for hour, minute, expected in ((15, 29, "no_class"), (15, 30, "ok"), (17, 30, "ok"), (17, 31, "no_class")):
            with self.subTest(at=f"{hour}:{minute}"):
                ClassSession.objects.all().delete()
                self.assertEqual(scan.mark(self.a.code, self.owner, at(TODAY, hour, minute)).status, expected)
        outside = scan.mark(self.a.code, self.owner, at(TODAY, 9, 0))
        self.assertEqual(outside.groups, [self.review])  # it lists the student's classes today
        self.assertFalse(ClassSession.objects.exists())  # and creates nothing

    def test_a_rescan_never_makes_present_late_and_an_absent_student_becomes_late(self):
        scan.mark(self.a.code, self.owner, at(TODAY, 16, 5))
        again = scan.mark(self.a.code, self.owner, at(TODAY, 16, 40))  # 40 minutes in: still present, never downgraded
        self.assertEqual((again.status, again.presence, again.changed), ("again", "present", False))
        self.assertEqual(AuditLog.objects.filter(action="scan_attendance").count(), 1)  # nothing changed, nothing logged
        # The teacher closes the class before the other two arrive: they are absent on the sheet, then scan in.
        self.close_from_the_sheet(TODAY)
        self.assertEqual(self.marks(), {"آدم": "present", "بسمة": "absent", "جاد": "absent"})
        come = scan.mark(self.b.code, self.owner, at(TODAY, 16, 12))  # even inside the grace minutes: they were closed absent, so late
        self.assertEqual((come.status, come.presence, come.was_absent), ("ok", "late", True))
        self.assertEqual(scan.mark(self.b.code, self.owner, at(TODAY, 16, 13)).presence, "late")  # and a second scan keeps it
        self.assertEqual(self.marks()["بسمة"], "late")
        audit = AuditLog.objects.filter(action="scan_attendance").latest("pk")
        self.assertEqual((audit.before_data["presence"], audit.after_data["presence"]), ("absent", "late"))

    def test_an_excused_mark_is_the_teachers_and_a_scan_leaves_it(self):
        attendance.take(self.review, self.owner, TODAY, {str(self.a.pk): "excused"})
        result = scan.mark(self.a.code, self.owner, at(TODAY, 16, 2))
        self.assertEqual((result.status, result.presence), ("again", "excused"))
        self.assertEqual(self.marks()["آدم"], "excused")

    def test_a_cancelled_class_takes_no_scan(self):
        attendance.take(self.review, self.owner, TODAY, {}, cancelled=True)
        result = scan.mark(self.a.code, self.owner, at(TODAY, 16, 1))
        self.assertEqual(result.status, "cancelled")
        self.assertFalse(Attendance.objects.exists())

    def test_the_scan_takes_the_same_lock_as_take(self):
        # SQLite drops FOR UPDATE, so the proof is that both go through the one helper, on the same group.
        with mock.patch.object(attendance, "lock_group", wraps=attendance.lock_group) as lock:
            attendance.take(self.review, self.owner, TODAY, {})
            scan.mark(self.a.code, self.owner, at(TODAY, 16, 1))
        self.assertEqual([call.args[0].pk for call in lock.call_args_list], [self.review.pk, self.review.pk])

    def test_unknown_and_inactive_and_empty_codes(self):
        self.assertEqual(scan.mark("ST-99999", self.owner, at(TODAY, 16, 1)).status, "not_found")
        self.assertEqual(scan.mark("  ", self.owner, at(TODAY, 16, 1)).status, "not_found")
        services.save_student({"name": "آدم", "payer": self.a.payer, "active_present": "1"}, self.owner, self.a)
        self.a.refresh_from_db()
        self.assertFalse(self.a.active)
        self.assertEqual(scan.mark(self.a.code, self.owner, at(TODAY, 16, 1)).status, "inactive")
        self.assertFalse(Attendance.objects.exists())

    def test_a_student_with_no_class_today_and_a_group_with_no_start_time(self):
        weekday = attendance.weekday(TODAY)
        other_day = next(day for day in EVERY_DAY.split(",") if day != weekday)
        only_other = services.save_group({"course": self.review.course, "name": "يوم تاني", "days": other_day, "start_time": time(10)}, self.owner)
        loner = self.kid("وحيد", "01010000004", only_other)
        result = scan.mark(loner.code, self.owner, at(TODAY, 10, 0))
        self.assertEqual((result.status, result.groups), ("no_class", []))
        # A group without a start time is open all day and never late.
        open_group = services.save_group({"course": self.review.course, "name": "مفتوحة", "days": EVERY_DAY}, self.owner)
        walker = self.kid("حر", "01010000005", open_group)
        self.assertEqual(scan.mark(walker.code, self.owner, at(TODAY, 21, 0)).presence, "present")

    def test_two_classes_in_the_window_ask_which_and_the_answer_is_checked(self):
        second = services.save_group({"course": self.review.course, "name": "مراجعة تانية", "days": EVERY_DAY, "start_time": time(16, 30), "duration_minutes": "60"},
                                     self.owner)
        services.enroll(self.a, second, self.owner, start_date=TODAY - timedelta(days=30))
        result = scan.mark(self.a.code, self.owner, at(TODAY, 16, 35))
        self.assertEqual((result.status, {group.pk for group in result.choices}), ("choose", {self.review.pk, second.pk}))
        self.assertFalse(Attendance.objects.exists())  # nothing is written until they answer
        chosen = scan.mark(self.a.code, self.owner, at(TODAY, 16, 35), group=second.pk)
        self.assertEqual((chosen.status, chosen.group, chosen.presence), ("ok", second, "present"))  # 5 minutes into the second class
        self.assertEqual(scan.mark(self.b.code, self.owner, at(TODAY, 16, 35), group=second.pk).status, "refused")  # not in that class


class ScanWhoTests(ScanSetup):
    def test_a_code_from_another_entity_is_not_found(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import main_entity

        branch = Entity.objects.create(code="E-EDU", name_ar="فرع التجمع", activity_slug="education", sub_activity_slug="tutoring_center")
        with working_in(branch):
            theirs = services.save_student({"name": "طالب الفرع", "self_pays": "1"}, self.owner)
        with working_in(main_entity()):
            self.assertEqual(scan.mark(theirs.code, self.owner, at(TODAY, 16, 1)).status, "not_found")
            self.assertEqual(scan.mark(theirs.code, self.owner, at(TODAY, 16, 1)).message, scan.mark("ST-99999", self.owner, at(TODAY, 16, 1)).message)
        with working_in(branch):
            self.assertEqual(scan.mark(self.a.code, self.owner, at(TODAY, 16, 1)).status, "not_found")

    def teacher_user(self, employee=None, username="edu_scan_teacher"):
        user = person(RoleCode.CASHIER, username)
        (employee or self.teacher).user = user
        (employee or self.teacher).save()
        return user

    def test_a_teacher_scans_their_own_students_and_is_refused_the_rest(self):
        from staff.services import save_employee

        mine = self.teacher_user()
        outsider = save_employee({"name": "أ. نادية"}, self.owner)
        theirs = services.save_group({"course": self.review.course, "name": "مجموعة نادية", "teacher": outsider, "days": EVERY_DAY, "start_time": time(16), "room": "قاعة 9"},
                                     self.owner)
        other_kid = self.kid("غريب", "01010000006", theirs)
        self.assertEqual(scan.mark(self.a.code, mine, at(TODAY, 16, 3)).status, "ok")
        refused = scan.mark(other_kid.code, mine, at(TODAY, 16, 3))
        self.assertEqual((refused.status, refused.student), ("refused", None))  # the answer names nobody
        self.assertNotIn("غريب", refused.message)
        self.assertEqual(self.marks(group=self.review), {"آدم": "present"})
        self.assertFalse(ClassSession.objects.filter(group=theirs).exists())
        # The manager scans the same child without trouble.
        self.assertEqual(scan.mark(other_kid.code, self.owner, at(TODAY, 16, 3)).status, "ok")

    def test_someone_who_may_not_take_attendance_cannot_scan(self):
        cashier = person(RoleCode.CASHIER, "edu_scan_cashier")
        self.assertFalse(attendance.can_scan(cashier))
        with self.assertRaises(PermissionDenied):
            scan.mark(self.a.code, cashier, at(TODAY, 16, 1))
        self.assertFalse(Attendance.objects.exists())


class SheetAfterScanTests(ScanSetup):
    def test_a_brand_new_session_still_starts_with_everyone_present(self):
        html = self.client.get(self.sheet_url(TODAY)).content.decode()
        self.assertEqual(sorted(value for _, value in RADIO.findall(html)), ["present"] * 3)
        self.assertNotIn("data-not-scanned", html)
        self.assertNotIn("data-unscanned-note", html)
        # and saving it is exactly what it always was: everyone present
        self.close_from_the_sheet(TODAY)
        self.assertEqual(self.marks(), {"آدم": "present", "بسمة": "present", "جاد": "present"})

    def test_after_a_scan_run_the_sheet_shows_the_unscanned_as_absent_and_saving_keeps_them_absent(self):
        scan.mark(self.a.code, self.owner, at(TODAY, 16, 2))
        page = self.client.get(self.sheet_url(TODAY))
        html = page.content.decode()
        self.assertEqual({pk: value for pk, value in RADIO.findall(html)}, {str(self.a.pk): "present", str(self.b.pk): "absent", str(self.c.pk): "absent"})
        self.assertContains(page, "data-not-scanned", count=2)
        self.assertContains(page, "ما اتمسحش")
        self.assertContains(page, "data-unscanned-note")
        self.assertContains(self.client.get(self.sheet_url(TODAY) + "&lang=en"), "Not scanned")
        self.assertContains(self.client.get(reverse("education:today")), 'data-not-scanned-count')
        self.close_from_the_sheet(TODAY)
        self.assertEqual(self.marks(), {"آدم": "present", "بسمة": "absent", "جاد": "absent"})
        # after saving, every mark exists: nothing is tagged any more
        self.assertNotContains(self.client.get(self.sheet_url(TODAY)), "data-not-scanned")

    def test_a_cancelled_class_reopened_keeps_the_old_everyone_present_start(self):
        attendance.take(self.review, self.owner, TODAY, {}, cancelled=True)
        html = self.client.get(self.sheet_url(TODAY)).content.decode()
        self.assertEqual(sorted(value for _, value in RADIO.findall(html)), ["present"] * 3)
        self.assertNotIn("data-not-scanned", html)


class ScanBillingTests(ScanSetup):
    def test_per_class_fees_follow_the_scans_not_the_close(self):
        first = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)  # last month: four held classes can never be in the future
        for student in (self.a, self.b, self.c):
            Enrollment.objects.filter(student=student).update(start_date=first)
        days = [first + timedelta(days=n) for n in range(4)]
        for number, day in enumerate(days):
            self.assertEqual(scan.mark(self.a.code, self.owner, at(day, 16, 4)).presence, "present")  # A: scanned present, four times
            if number < 2:
                self.assertEqual(scan.mark(self.b.code, self.owner, at(day, 16, 25)).presence, "late")  # B: scanned late twice, not at all twice
            posted = self.close_from_the_sheet(day)  # the teacher closes each class from the sheet
            self.assertEqual(posted[f"p_{self.c.pk}"], "absent")  # C was never scanned: absent, not present
            self.assertEqual(posted[f"p_{self.b.pk}"], "late" if number < 2 else "absent")
        self.assertEqual(sorted(Attendance.objects.filter(student=self.b).values_list("presence", flat=True)), ["absent", "absent", "late", "late"])
        self.assertFalse(Attendance.objects.filter(student=self.c, presence__in=("present", "late")).exists())
        lines = {line["enrollment"].student_id: line for line in billing.due_lines(first, self.a.payer) + billing.due_lines(first, self.b.payer)
                 + billing.due_lines(first, self.c.payer)}
        self.assertEqual((lines[self.a.pk]["quantity"], lines[self.a.pk]["amount"]), (D("4"), D("200.00")))
        self.assertEqual((lines[self.b.pk]["quantity"], lines[self.b.pk]["amount"]), (D("2"), D("100.00")))
        self.assertNotIn(self.c.pk, lines)
        drafts = billing.bill_month(first, self.owner)
        by_payer = {invoice.customer_id: invoice.total_amount for invoice in drafts}
        self.assertEqual(by_payer, {self.a.payer_id: D("200.00"), self.b.payer_id: D("100.00")})
        self.assertEqual(len(drafts), 2)
        self.assertNotIn(self.c.payer_id, by_payer)


class StaleSheetTests(ScanSetup):
    """A sheet is saved only if the class is still as the page showed it; otherwise it comes back from the database and nothing is written."""

    def test_a_sheet_opened_before_any_scan_cannot_overwrite_the_scans_and_the_bill_follows_the_scans(self):
        first = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=1)
        day = first + timedelta(days=2)
        for student in (self.a, self.b, self.c):
            Enrollment.objects.filter(student=student).update(start_date=first)
        stale = self.open_sheet(day)  # no class yet: everyone shows present, version "none"
        self.assertEqual((stale["sheet_version"], set(value for key, value in stale.items() if key.startswith("p_"))), ("none", {"present"}))
        scan.mark(self.a.code, self.owner, at(day, 16, 3))
        scan.mark(self.b.code, self.owner, at(day, 16, 4))
        response = self.save(day, stale)  # the teacher saves the old page
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "اتغيّر الحضور بعد ما فتحت الكشف")
        self.assertEqual(self.marks(day), {"آدم": "present", "بسمة": "present"})  # the two scans survive and nobody else is marked
        self.assertEqual(AuditLog.objects.filter(action="take_attendance").count(), 0)
        # The page that came back is the database's: the third student is absent and tagged. Saving that one works.
        fresh = self.open_sheet(day)
        self.assertEqual(fresh[f"p_{self.c.pk}"], "absent")
        self.assertEqual(self.save(day, fresh).status_code, 302)
        self.assertEqual(self.marks(day), {"آدم": "present", "بسمة": "present", "جاد": "absent"})
        drafts = billing.bill_month(first, self.owner)
        self.assertEqual({invoice.customer_id: invoice.total_amount for invoice in drafts}, {self.a.payer_id: D("50.00"), self.b.payer_id: D("50.00")})

    def test_a_scan_after_the_sheet_was_opened_survives_a_stale_save(self):
        attendance.take(self.review, self.owner, TODAY, {str(self.c.pk): "absent"})  # the teacher marked everyone, c absent
        stale = self.open_sheet(TODAY)
        self.assertEqual(stale[f"p_{self.c.pk}"], "absent")
        self.assertEqual(scan.mark(self.c.code, self.owner, at(TODAY, 16, 30)).presence, "late")  # the late-comer arrives
        response = self.save(TODAY, stale)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "اتغيّر الحضور")
        self.assertEqual(self.marks()["جاد"], "late")
        self.assertEqual(self.save(TODAY, self.open_sheet(TODAY)).status_code, 302)  # the re-rendered sheet saves, late kept
        self.assertEqual(self.marks()["جاد"], "late")

    def test_an_unchanged_sheet_saves_as_always_and_a_note_changed_meanwhile_is_also_stale(self):
        scan.mark(self.a.code, self.owner, at(TODAY, 16, 2))
        form = self.open_sheet(TODAY)
        self.assertEqual(self.save(TODAY, form).status_code, 302)  # nothing changed between render and save
        self.assertEqual(self.marks(), {"آدم": "present", "بسمة": "absent", "جاد": "absent"})
        old = self.open_sheet(TODAY)
        attendance.take(self.review, self.owner, TODAY, {f"note_{self.a.pk}": "جه بدري", str(self.b.pk): "absent", str(self.c.pk): "absent"})
        self.assertEqual(self.save(TODAY, old).status_code, 200)  # another save (here a note) since the page was opened
        self.assertEqual(Attendance.objects.get(student=self.a).note, "جه بدري")

    def test_a_save_with_no_version_is_stale_when_a_class_exists_and_fresh_when_none_does(self):
        form = self.open_sheet(TODAY)
        form.pop("sheet_version")
        self.assertEqual(self.save(TODAY, form).status_code, 302)  # no class yet: saves as it always did
        self.assertEqual(self.marks(), {"آدم": "present", "بسمة": "present", "جاد": "present"})
        refused = self.save(TODAY, {**form, f"p_{self.b.pk}": "absent"})  # a class now exists: an old tab or a crafted post is refused
        self.assertEqual(refused.status_code, 200)
        self.assertEqual(self.marks()["بسمة"], "present")
        blank = self.save(TODAY, {**form, "sheet_version": "", f"p_{self.b.pk}": "absent"})
        self.assertEqual((blank.status_code, self.marks()["بسمة"]), (200, "present"))

    def test_the_message_is_in_both_languages_and_the_page_keeps_nothing_from_the_stale_form(self):
        stale = self.open_sheet(TODAY)
        scan.mark(self.a.code, self.owner, at(TODAY, 16, 2))
        stale[f"p_{self.b.pk}"] = "excused"
        url = reverse("education:attendance", args=[self.review.pk])
        en = self.client.post(url, {"date": TODAY.isoformat(), "lang": "en", **stale})
        self.assertContains(en, "Attendance changed after you opened this sheet (students were scanned). Check the sheet and save again.")
        html = en.content.decode()
        self.assertEqual({pk: value for pk, value in RADIO.findall(html)}[str(self.b.pk)], "absent")  # the database's view, not the posted "excused"
        self.assertEqual(self.marks(), {"آدم": "present"})
        self.assertContains(self.client.post(url, {"date": TODAY.isoformat(), "lang": "ar", **stale}), "راجع الكشف واحفظ تاني")


class OnlyScannedClassesChangeTests(ScanSetup):
    """The absent fill belongs to classes a scan has touched; a centre that never scans sees no change at all."""

    def test_a_back_dated_enrolment_shows_present_until_a_scan_has_touched_the_class(self):
        attendance.take(self.review, self.owner, TODAY, {str(self.b.pk): "absent"})  # taken by the teacher, never scanned
        joiner = self.kid("منضم متأخر", "01010000007")  # enrolled afterwards, back-dated: on the roster, no mark
        page = self.client.get(self.sheet_url(TODAY))
        self.assertEqual({pk: value for pk, value in RADIO.findall(page.content.decode())}[str(joiner.pk)], "present")  # as before this branch
        self.assertNotContains(page, "data-not-scanned")
        self.assertNotContains(self.client.get(reverse("education:today")), "data-not-scanned-count")
        # now the late-comer is scanned: the class is a scanned one, and the joiner (no mark, never scanned) shows absent and tagged
        self.assertEqual(scan.mark(self.b.code, self.owner, at(TODAY, 16, 20)).presence, "late")
        page = self.client.get(self.sheet_url(TODAY))
        self.assertEqual({pk: value for pk, value in RADIO.findall(page.content.decode())}[str(joiner.pk)], "absent")
        self.assertContains(page, "data-not-scanned", count=1)
        self.assertContains(self.client.get(reverse("education:today")), "data-not-scanned-count")
        self.assertEqual(scan.touched([ClassSession.objects.get().pk]), {ClassSession.objects.get().pk})
