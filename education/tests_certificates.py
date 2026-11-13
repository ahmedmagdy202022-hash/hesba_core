"""E6: the completion certificate: the rules in ``education.certificates``, the staff screens, the printed sheet, and the public verify page.

Every day is built from ``timezone.localdate()``; no clock is patched. A certificate moves no money: counts of invoices, cashbox
movements, journal entries, stock movements and outbox rows are pinned around the whole feature. Marker strings (names, a school, a
note, a revoke reason) are asserted on the page, never short digit strings: a public page holds a random token.
"""

import pathlib
import re
from datetime import date, time, timedelta
from decimal import Decimal as D
from unittest import mock

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import SimpleTestCase, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from inventory.models import StockMovement
from ledger.models import JournalEntry
from messaging.models import OutboxMessage
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice

from . import attendance, certificates, exams, services
from .models import Certificate, Enrollment
from .tests import CENTER, person

TODAY = timezone.localdate()
EVERY_DAY = "sun,mon,tue,wed,thu,fri,sat"
BASE = "https://hesba.example"
GUARDIAN, SCHOOL, NOTE, STAGE = "علي محمود الجارحي", "مدرسة النور الخاصة", "ملاحظة داخلية للمركز فقط", "تالتة ثانوي"
GROUP_NAME, TEACHER_NAME, REASON = "مجموعة السبت المسائية", "أ. منى الشاذلي", "سبب إلغاء داخلي رقم تسعمية"
BORN = date(2010, 3, 17)


def day(n):
    return TODAY - timedelta(days=n)


class CertSetup(TestCase):
    """A training centre: «إنجليزي» level 2, a group that met every day for the last ten days and whose term ended yesterday, and Sara in it."""

    install = "training"

    def setUp(self):
        cache.clear()
        prepared_client("education", self.install, CENTER)
        from staff.services import save_employee

        self.owner = person(RoleCode.OWNER, "cert_owner")
        self.cashier = person(RoleCode.CASHIER, "cert_cashier")
        self.teacher_user = person(RoleCode.CASHIER, "cert_teacher")
        self.teacher = save_employee({"name": TEACHER_NAME}, self.owner)
        self.teacher.user = self.teacher_user
        self.teacher.save()
        self.english = services.save_course({"name": "اللغة الإنجليزية", "fee": "600", "basis": "monthly"}, self.owner)
        self.group = self.make_group(GROUP_NAME, ends=day(1), teacher=True)
        self.sara = self.student("سارة علي", "01001234501", own_phone="01234567890")
        self.enrollment = services.enroll(self.sara, self.group, self.owner, start_date=day(30))

    def make_group(self, name, ends=None, starts=None, level="2", teacher=False):
        return services.save_group({"course": self.english, "name": name, "teacher": self.teacher if teacher else None, "days": EVERY_DAY, "start_time": time(17),
                                    "duration_minutes": "90", "capacity": "0", "level": level, "starts_on": starts or day(30), "ends_on": ends,
                                    "room": f"قاعة {name}"[:60]}, self.owner)

    def student(self, name, payer_phone, own_phone=""):
        return services.save_student({"name": name, "payer_name": GUARDIAN if payer_phone.endswith("501") else f"ولي أمر {name}", "payer_phone": payer_phone,
                                      "phone": own_phone, "school": SCHOOL, "notes": NOTE, "stage": STAGE, "date_of_birth": BORN.isoformat()}, self.owner)

    def hold(self, group=None, days=range(10, 0, -1), absent=(), late=(), student=None):
        """Take attendance on each of ``days`` (days ago): ``absent`` and ``late`` are the days the student was marked so."""

        student = student or self.sara
        for n in days:
            mark = "absent" if n in absent else "late" if n in late else "present"
            attendance.take(group or self.group, self.owner, day(n), {str(student.pk): mark})

    def issue(self, **kwargs):
        return certificates.issue(self.enrollment, self.owner, **kwargs)[0]

    def final_exam(self, score, published=True, kind="final", out_of="20", ago=3, title="الامتحان النهائي"):
        exam = exams.save_exam(self.group, {"title": title, "kind": kind, "held_on": day(ago), "out_of": out_of}, self.owner)
        exams.record_marks(exam, {self.sara.pk: {"score": score}} if score is not None else {self.sara.pk: {"absent": True}}, self.owner)
        if published:
            exams.set_published(exam, True, self.owner)
        return exam

    def counts(self):
        return (SalesInvoice.objects.count(), CashboxMovement.objects.count(), JournalEntry.objects.count(), StockMovement.objects.count(), OutboxMessage.objects.count())


# ---- the rules ----

class MeasureTests(CertSetup):
    def test_ten_classes_of_ninety_minutes_make_fifteen_hours_and_nine_attended_make_ninety_percent(self):
        self.hold(absent={4})
        measured = certificates.measure(self.enrollment)
        self.assertEqual((measured["held"], measured["hours"], measured["rate"]), (10, D("15.00"), 90))
        self.assertEqual((measured["first"], measured["last"]), (day(30), day(1)))
        certificate = self.issue()
        self.assertEqual((certificate.hours, certificate.attendance_rate, certificate.number, certificate.issued_on, certificate.issued_by, certificate.grade_text),
                         (D("15.00"), 90, f"CRT-{TODAY.year}-00001", TODAY, self.owner, ""))
        self.assertFalse(certificate.revoked)

    def test_late_counts_as_attended_and_a_cancelled_class_is_not_held(self):
        self.hold(late={5, 6}, absent={2})
        attendance.take(self.group, self.owner, day(11), {}, cancelled=True)  # a cancelled class: no marks, not held
        measured = certificates.measure(self.enrollment)
        self.assertEqual((measured["held"], measured["hours"], measured["rate"]), (10, D("15.00"), 90))  # 7 present + 2 late of 10

    def test_an_odd_class_length_is_quantised_half_up(self):
        group = self.make_group("نص ساعة وكسر", ends=day(1))
        group.duration_minutes = 50
        group.save()
        student = self.student("كريم", "01001234502")
        enrollment = services.enroll(student, group, self.owner, start_date=day(30))
        self.hold(group, days=range(3, 0, -1), student=student)
        self.assertEqual(certificates.measure(enrollment)["hours"], D("2.50"))  # 3 classes × 50 min = 150 min

    def test_the_span_starts_when_the_student_enrolled_and_not_before_the_group(self):
        self.hold()
        late_joiner = self.student("مالك", "01001234503")
        enrollment = services.enroll(late_joiner, self.group, self.owner, start_date=day(5))
        measured = certificates.measure(enrollment)
        self.assertEqual((measured["first"], measured["held"], measured["hours"]), (day(5), 5, D("7.50")))  # classes of day 5 to day 1
        early = self.student("هدى", "01001234504")
        enrollment = services.enroll(early, self.group, self.owner, start_date=day(90))
        self.assertEqual(certificates.measure(enrollment)["first"], day(30))  # the group started 30 days ago

    def test_a_stopped_enrolment_ends_at_its_stop_day_even_while_the_group_runs_on(self):
        group = self.make_group("إنجليزي 3", ends=TODAY + timedelta(days=20))
        student = self.student("ياسر", "01001234505")
        enrollment = services.enroll(student, group, self.owner, start_date=day(30))
        self.hold(group, student=student)
        with self.assertRaisesMessage(ValidationError, "لسه مكمّل"):
            certificates.issue(enrollment, self.owner)  # still enrolled, term not over
        services.stop(enrollment, self.owner, end_date=day(4))
        enrollment.refresh_from_db()
        certificate = certificates.issue(enrollment, self.owner)[0]
        self.assertEqual((certificate.hours, certificates.period(certificate)), (D("10.50"), (day(30), day(4))))  # 7 classes: days 10 to 4

    def test_the_last_day_of_the_term_counts_as_ended(self):
        group = self.make_group("بتخلص النهارده", ends=TODAY)
        student = self.student("نور", "01001234506")
        enrollment = services.enroll(student, group, self.owner, start_date=day(30))
        self.hold(group, days=range(3, -1, -1), student=student)  # up to and including today
        self.assertTrue(certificates.finished(enrollment))
        self.assertEqual(certificates.issue(enrollment, self.owner)[0].hours, D("6.00"))


class RefusalTests(CertSetup):
    def test_a_student_still_enrolled_in_a_running_group_is_refused_with_the_exact_message(self):
        group = self.make_group("لسه شغالة", ends=TODAY + timedelta(days=5))
        student = self.student("ليلى", "01001234507")
        enrollment = services.enroll(student, group, self.owner, start_date=day(30))
        self.hold(group, student=student)
        expected = "الطالب لسه مكمّل في المجموعة ومدتها ما خلصتش، فمينفعش تتصدر له شهادة دلوقتي. أوقف تسجيله أو استنى لحد ما تخلص."
        with self.assertRaises(ValidationError) as caught:
            certificates.issue(enrollment, self.owner)
        self.assertEqual(caught.exception.messages, [expected])
        with self.assertRaises(ValidationError) as caught:
            certificates.issue(enrollment, self.owner, lang="en")
        self.assertEqual(caught.exception.messages, ["The student is still enrolled and the group's term has not ended, so no certificate can be issued yet. Stop the enrolment, or wait until the term ends."])
        self.assertFalse(Certificate.objects.exists())

    def test_a_finished_enrolment_with_no_class_held_is_refused_with_the_exact_message(self):
        with self.assertRaises(ValidationError) as caught:
            certificates.issue(self.enrollment, self.owner)  # the term ended but nothing was ever recorded
        self.assertEqual(caught.exception.messages, ["مفيش حصة اتعملت للطالب في المجموعة دي، فمفيش حاجة تتشهد بيها."])
        self.assertFalse(Certificate.objects.exists())

    def test_the_grade_is_at_most_forty_characters(self):
        self.hold()
        with self.assertRaisesMessage(ValidationError, "التقدير أطول من 40 حرف"):
            self.issue(grade_text="ي" * 41)
        self.assertEqual(self.issue(grade_text="ي" * 40).grade_text, "ي" * 40)

    def test_only_managers_issue_and_revoke(self):
        self.hold()
        for user in (self.cashier, self.teacher_user):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                certificates.issue(self.enrollment, user)
        self.assertFalse(Certificate.objects.exists())
        certificate = self.issue()
        for user in (self.cashier, self.teacher_user):
            with self.subTest(user=user.username), self.assertRaises(PermissionDenied):
                certificates.revoke(certificate, "سبب", user)
        self.assertFalse(Certificate.objects.get().revoked)

    def test_an_unknown_enrolment_is_refused(self):
        stranger = Enrollment(pk=999999)
        with self.assertRaisesMessage(ValidationError, "مش موجود"):
            certificates.issue(stranger, self.owner)


class WarningTests(CertSetup):
    def test_attendance_under_seventy_five_warns_and_needs_a_second_step(self):
        self.hold(absent={1, 2, 3})  # 7 of 10 = 70%
        with self.assertRaises(certificates.NeedsConfirmation) as caught:
            self.issue()
        self.assertEqual(caught.exception.messages, ["نسبة حضور الطالب 70%، وده أقل من 75%.",
                                                     "فيه تنبيه فوق. لو متأكد إنك عايز تصدر الشهادة، علّم «أصدرها برضو» واضغط إصدار."])
        self.assertFalse(Certificate.objects.exists())  # nothing written by the refused first step
        certificate = self.issue(acknowledged=True)
        self.assertEqual(certificate.attendance_rate, 70)
        self.assertEqual(AuditLog.objects.get(action="issue_certificate").after_data["warned"], True)

    def test_exactly_seventy_five_percent_does_not_warn(self):
        self.hold(days=range(4, 0, -1), absent={1})  # 3 of 4
        self.assertEqual(certificates.measure(self.enrollment)["rate"], 75)
        self.assertEqual(certificates.preview(self.enrollment, self.owner)["warnings"], [])
        self.assertEqual(self.issue().attendance_rate, 75)

    def test_a_guardian_who_owes_warns_and_a_guardian_who_does_not_does_not(self):
        from hesba_testing.factories import make_location

        from . import billing

        make_location(location_code="CENTER", is_default=True)
        self.hold()
        self.assertEqual(certificates.preview(self.enrollment, self.owner)["warnings"], [])
        billing.bill_month(TODAY.replace(day=1), self.owner, post=True)  # 600.00 on the guardian's account
        self.assertTrue(billing.dues())
        with self.assertRaises(certificates.NeedsConfirmation) as caught:
            self.issue()
        self.assertEqual(caught.exception.messages[0], "على ولي الأمر مبلغ مستحق لسه ماتدفعش.")
        self.assertFalse(Certificate.objects.exists())
        self.assertEqual(self.issue(acknowledged=True).hours, D("15.00"))
        self.assertEqual(self.counts()[0], 1)  # issuing did not add an invoice: the one fee invoice is still the only one


class GradeTests(CertSetup):
    def setUp(self):
        super().setUp()
        self.hold()

    def grade(self):
        return certificates.suggested_grade(self.enrollment, self.owner)

    def test_only_a_published_final_exam_with_a_score_is_offered(self):
        exam = self.final_exam("17", published=False)
        self.assertEqual(self.grade(), "")  # an unpublished mark is the centre's own: it never reaches a public page
        self.assertEqual(self.issue().grade_text, "")
        Certificate.objects.all().delete()
        exams.set_published(exam, True, self.owner)
        self.assertEqual(self.grade(), "85.0%")  # 17 of 20
        self.assertEqual(self.issue().grade_text, "85.0%")

    def test_a_quiz_or_an_absence_or_an_unscored_final_offers_nothing(self):
        self.final_exam("19", kind="quiz", title="كويز")
        self.assertEqual(self.grade(), "")
        absent = self.final_exam(None, title="نهائي غاب عنه")
        self.assertEqual(self.grade(), "")
        absent.marks.all().delete()
        self.assertEqual(self.grade(), "")

    def test_the_newest_final_wins_and_the_percentage_is_exact(self):
        self.final_exam("10", ago=8, title="نهائي قديم")
        self.final_exam("7.5", out_of="10", ago=2, title="نهائي أحدث")
        self.assertEqual(self.grade(), "75.0%")

    def test_what_the_manager_types_is_what_is_printed(self):
        self.final_exam("17")
        self.assertEqual(self.issue(grade_text="امتياز").grade_text, "امتياز")  # typed over the suggestion
        Certificate.objects.all().delete()
        self.assertEqual(self.issue(grade_text="").grade_text, "")  # an empty box means no grade is printed


class IssueRevokeTests(CertSetup):
    def setUp(self):
        super().setUp()
        self.hold()

    def test_issuing_twice_makes_one_certificate_and_one_audit_row(self):
        first, created = certificates.issue(self.enrollment, self.owner)
        second, again = certificates.issue(self.enrollment, self.owner, grade_text="تقدير تاني")
        self.assertEqual((created, again, first.pk), (True, False, second.pk))
        self.assertEqual((Certificate.objects.count(), second.grade_text, AuditLog.objects.filter(action="issue_certificate").count()), (1, "", 1))

    def test_two_certificates_get_distinct_numbers_and_a_taken_number_is_skipped(self):
        first = self.issue()
        other = self.student("كريم", "01001234508")
        enrollment = services.enroll(other, self.group, self.owner, start_date=day(30))
        self.hold(student=other, days=[])  # nothing to record: the same ten classes already hold both students
        taken = first.number
        with mock.patch("config.numbering.next_in_series", side_effect=[taken, f"CRT-{TODAY.year}-00002"]):
            second = certificates.issue(enrollment, self.owner)[0]
        self.assertEqual((first.number, second.number), (f"CRT-{TODAY.year}-00001", f"CRT-{TODAY.year}-00002"))
        self.assertEqual(Certificate.objects.values_list("number", flat=True).distinct().count(), 2)

    def test_the_audit_row_holds_what_was_printed(self):
        certificate = self.issue(grade_text="جيد جدًا")
        log = AuditLog.objects.get(action="issue_certificate")
        self.assertEqual((log.object_id, log.actor, log.after_data["number"], log.after_data["student_name"], log.after_data["course"], log.after_data["hours"],
                          log.after_data["attendance_rate"], log.after_data["grade"], log.after_data["warned"]),
                         (str(certificate.pk), self.owner, certificate.number, "سارة علي", "اللغة الإنجليزية", "15.00", 100, "جيد جدًا", False))

    def test_revoking_keeps_the_row_needs_a_reason_and_happens_once(self):
        certificate = self.issue()
        for reason in ("", "  ", "لا"):
            with self.subTest(reason=reason), self.assertRaisesMessage(ValidationError, "اكتب سبب الإلغاء"):
                certificates.revoke(certificate, reason, self.owner)
        revoked = certificates.revoke(certificate, f"  {REASON}  ", self.owner)
        self.assertEqual((revoked.pk, revoked.revoked, revoked.revoke_reason, revoked.revoked_by), (certificate.pk, True, REASON, self.owner))
        self.assertIsNotNone(revoked.revoked_at)
        self.assertEqual(Certificate.objects.count(), 1)  # the row is kept
        with self.assertRaisesMessage(ValidationError, "ملغية بالفعل"):
            certificates.revoke(certificate, "تاني", self.owner)
        log = AuditLog.objects.get(action="revoke_certificate")
        self.assertEqual((log.after_data["number"], log.after_data["reason"], log.before_data), (certificate.number, REASON, {"revoked": False}))

    def test_a_revoked_enrolment_can_be_certified_again_with_a_new_number(self):
        first = self.issue()
        certificates.revoke(first, "اتصدرت بالغلط", self.owner)
        self.assertIsNone(certificates.live_for(self.enrollment))
        second, created = certificates.issue(self.enrollment, self.owner)
        self.assertTrue(created)
        self.assertEqual((second.number, Certificate.objects.count(), certificates.live_for(self.enrollment).pk), (f"CRT-{TODAY.year}-00002", 2, second.pk))
        self.assertTrue(Certificate.objects.get(pk=first.pk).revoked)  # the old number still says revoked

    def test_the_database_allows_one_live_certificate_per_enrolment(self):
        self.issue()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Certificate.objects.create(enrollment=self.enrollment, number="CRT-X-1", issued_on=TODAY, issued_by=self.owner)

    def test_the_ready_queue_holds_finished_enrolments_with_no_live_certificate(self):
        self.assertEqual([row.pk for row in certificates.ready()], [self.enrollment.pk])
        running = self.make_group("لسه شغالة", ends=TODAY + timedelta(days=9))
        services.enroll(self.student("منة", "01001234509"), running, self.owner, start_date=day(10))
        self.assertEqual([row.pk for row in certificates.ready()], [self.enrollment.pk])  # a running group's student is not ready
        certificate = self.issue()
        self.assertEqual(certificates.ready(), [])
        certificates.revoke(certificate, "غلط", self.owner)
        self.assertEqual([row.pk for row in certificates.ready()], [self.enrollment.pk])  # a revoked one is ready again
        self.assertEqual(certificates.ready(group=running), [])

    def test_a_group_of_another_entity_is_not_seen(self):
        from entities.current import working_in
        from entities.models import Entity

        certificate = self.issue()
        branch = Entity.objects.create(code="E-CERT", name_ar="فرع", activity_slug="education", sub_activity_slug="training")
        with working_in(branch):
            self.assertEqual(list(certificates.scoped()), [])
            self.assertEqual(certificates.ready(), [])
            with self.assertRaisesMessage(ValidationError, "مش موجود"):
                certificates.issue(self.enrollment, self.owner)
        self.assertEqual(list(certificates.scoped()), [certificate])

    def test_the_whole_feature_moves_no_money_and_sends_no_message(self):
        before = self.counts()
        certificate = self.issue(grade_text="ممتاز")
        certificates.issue(self.enrollment, self.owner)
        certificates.preview(self.enrollment, self.owner)
        certificates.verify_url(certificate)
        certificates.revoke(certificate, "سبب", self.owner)
        self.assertEqual(self.counts(), before)
        source = (pathlib.Path(settings.BASE_DIR) / "education/certificates.py").read_text(encoding="utf-8")
        for word in ("post_sales_invoice", "record_expense", "OutboxMessage", "wa.me", "bill_month", "messaging", "record_marks", "attendance.take", "set_published"):
            self.assertNotIn(word, source)


class OtherInstallTests(CertSetup):
    install = "tutoring_center"

    def test_a_tutoring_centre_has_no_certificates(self):
        self.assertFalse(certificates.enabled())
        with self.assertRaisesMessage(ValidationError, "للمراكز التدريبية ومراكز اللغات بس"):
            certificates.issue(self.enrollment, self.owner)
        self.client.force_login(self.owner)
        for url in (reverse("education:certificates"), reverse("education:certificate_issue"), reverse("education:certificate", args=[1])):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertNotContains(self.client.get(reverse("education:students")), "data-certificates-tab")


class LanguagesInstallTests(CertSetup):
    install = "languages"

    def test_a_language_centre_has_them_too(self):
        self.assertTrue(certificates.enabled())
        self.hold()
        self.assertEqual(self.issue().hours, D("15.00"))


# ---- the token and the address ----

class TokenTests(CertSetup):
    def setUp(self):
        super().setUp()
        self.hold()
        self.certificate = self.issue()

    def test_a_token_reads_back_to_its_certificate_and_is_not_the_running_number(self):
        token = certificates.make_token(self.certificate)
        self.assertEqual(certificates.read_token(token).pk, self.certificate.pk)
        self.assertNotIn(self.certificate.number, token)
        other = self.student("كريم", "01001234510")
        second = certificates.issue(services.enroll(other, self.group, self.owner, start_date=day(30)), self.owner)[0]
        self.assertNotEqual(token, certificates.make_token(second))
        # the number that was printed is not a key: it opens nothing
        self.assertEqual(self.client.get(f"/verify/c/{self.certificate.number}/").status_code, 404)

    def test_forged_altered_foreign_and_mismatched_tokens_read_as_nothing(self):
        token = certificates.make_token(self.certificate)
        flipped = token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1]
        for bad in ("", "abc", token[:-5], flipped, f"{token}x", token.upper()):
            with self.subTest(token=bad[:12]):
                self.assertIsNone(certificates.read_token(bad))
        from printing.share import make_token as share_token

        self.assertIsNone(certificates.read_token(share_token("sales_invoice", self.certificate.pk)))  # another salt
        self.assertIsNone(certificates.read_token(signing.dumps({"c": self.certificate.pk, "n": "CRT-1-00001"}, salt=certificates.SALT)))  # right salt, wrong number
        self.assertIsNone(certificates.read_token(signing.dumps({"c": 987654, "n": self.certificate.number}, salt=certificates.SALT)))  # no such row
        self.assertIsNone(certificates.read_token(signing.dumps({"c": "1", "n": 5}, salt=certificates.SALT)))
        self.assertIsNone(certificates.read_token(signing.dumps([1, 2], salt=certificates.SALT)))

    def test_a_token_never_expires(self):
        token = certificates.make_token(self.certificate)
        import time as real_time

        with mock.patch("django.core.signing.time.time", return_value=real_time.time() + 3650 * 86400):
            self.assertEqual(certificates.read_token(token).pk, self.certificate.pk)

    def test_a_share_token_does_not_open_the_verify_page_nor_this_one_a_share_page(self):
        from printing.share import make_token as share_token

        self.assertEqual(self.client.get(reverse("certificate_verify", args=[share_token("sales_invoice", 1)])).status_code, 404)
        self.assertEqual(self.client.get(reverse("share_document", args=[certificates.make_token(self.certificate)])).status_code, 404)

    @override_settings(PUBLIC_BASE_URL=BASE + "/")
    def test_the_address_is_built_from_the_public_base_url_only(self):
        url = certificates.verify_url(self.certificate, "en")
        self.assertTrue(url.startswith(f"{BASE}/verify/c/"))
        self.assertTrue(url.endswith("/?lang=en"))
        self.assertEqual(certificates.read_token(url.split("/verify/c/")[1].split("/")[0]).pk, self.certificate.pk)
        with override_settings(PUBLIC_BASE_URL=""):
            self.assertIsNone(certificates.verify_url(self.certificate))


# ---- the public page ----

def frozen_minute():
    """The throttle counts per wall-clock minute and hour: hold the clock it reads still, so a test that crosses a boundary cannot fail by chance."""

    class Instant:
        def timestamp(self):
            return 1_800_000_000.0

    return mock.patch("education.public.timezone", mock.Mock(now=lambda: Instant()))


HEADERS = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "same-origin"}


class PublicPageTests(CertSetup):
    def setUp(self):
        super().setUp()
        self.hold()
        self.final_exam("17")
        self.certificate = self.issue()
        self.url = reverse("certificate_verify", args=[certificates.make_token(self.certificate)])

    def assertPrivate(self, response):
        for header, value in HEADERS.items():
            self.assertEqual(response[header], value, header)

    def test_a_valid_certificate_says_who_what_when_and_by_whom_to_anyone_without_an_account(self):
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertPrivate(page)
        html = page.content.decode()
        self.assertIn('data-state="valid"', html)
        self.assertIn('data-banner="valid"', html)
        for marker, expected in (("data-verify-number", self.certificate.number), ("data-verify-name", "سارة علي")):
            self.assertRegex(html, rf"{marker}[^>]*>{re.escape(expected)}</bdi>")
        self.assertRegex(html, r"data-verify-business[^>]*><bdi>Demo Store</bdi>")
        self.assertRegex(html, r"data-verify-course[^>]*><bdi>اللغة الإنجليزية</bdi> · المستوى <bdi>2</bdi>")
        self.assertRegex(html, r"data-verify-grade[^>]*><bdi>85\.0%</bdi>")
        self.assertRegex(html, r"data-verify-status[^>]*>سارية<")
        first, last = day(30), day(1)
        self.assertRegex(html, rf"data-verify-period[^>]*><bdi>{first.day} \S+ {first.year}</bdi> إلى <bdi>{last.day} \S+ {last.year}</bdi>")
        self.assertRegex(html, rf"data-verify-issued[^>]*><bdi>{TODAY.day} \S+ {TODAY.year}</bdi>")

    def test_the_english_page(self):
        html = self.client.get(self.url + "?lang=en").content.decode()
        self.assertIn('dir="ltr"', html)
        for text in ("This certificate is genuine and still stands", "Certificate number", "Student", "Issued by", "Valid"):
            self.assertIn(text, html)
        self.assertRegex(html, r"data-verify-course[^>]*><bdi>اللغة الإنجليزية</bdi> · Level <bdi>2</bdi>")

    def test_a_certificate_without_a_grade_prints_none(self):
        Certificate.objects.filter(pk=self.certificate.pk).update(grade_text="")
        html = self.client.get(self.url).content.decode()
        self.assertNotIn("data-verify-grade", html)
        self.assertNotIn("التقدير", html)

    def test_a_revoked_certificate_says_revoked_and_never_why(self):
        certificates.revoke(self.certificate, REASON, self.owner)
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertPrivate(page)
        html = page.content.decode()
        self.assertIn('data-state="revoked"', html)
        self.assertIn('data-banner="revoked"', html)
        self.assertRegex(html, r"data-verify-status[^>]*>ملغية<")
        self.assertRegex(html, rf"data-verify-number[^>]*>{re.escape(self.certificate.number)}<")  # the number still answers
        self.assertNotIn("data-banner=\"valid\"", html)
        for forbidden in (REASON, "تسعمية", "cert_owner"):
            self.assertNotIn(forbidden, html)
        self.assertIn("The centre revoked this certificate", self.client.get(self.url + "?lang=en").content.decode())

    def test_nothing_beyond_the_certificate_is_ever_shown(self):
        """Marker strings for everything the certificate does not print: guardian, phones, school, notes, stage, birth date, group, teacher, hours, rate."""

        for url in (self.url, self.url + "?lang=en"):
            html = self.client.get(url).content.decode()
            for marker in (GUARDIAN, SCHOOL, NOTE, STAGE, GROUP_NAME, TEACHER_NAME, "01001234501", "01234567890", "2010-03-17", "17 مارس 2010", "March 17, 2010",
                           "17 March 2010", "data-verify-hours", "data-verify-rate", "نسبة الحضور", "عدد الساعات", "Attendance", "Hours", "ولي الأمر", "المدرس", "قاعة"):
                self.assertNotIn(marker, html, marker)
            self.assertNotIn("الامتحان النهائي", html)  # the exam's title is not the certificate's

    def test_every_dead_link_is_the_same_plain_page_with_no_data(self):
        token = certificates.make_token(self.certificate)
        from printing.share import make_token as share_token

        bad = ["abc", token[:-4], token[:-2] + ("A" if token[-2] != "A" else "B") + token[-1], share_token("sales_invoice", self.certificate.pk),
               signing.dumps({"c": self.certificate.pk, "n": "CRT-1-00001"}, salt=certificates.SALT), signing.dumps({"c": 99999, "n": self.certificate.number}, salt=certificates.SALT)]
        bodies = set()
        for value in bad:
            page = self.client.get(reverse("certificate_verify", args=[value]))
            self.assertEqual(page.status_code, 404)
            self.assertPrivate(page)
            html = page.content.decode()
            self.assertIn("data-certificate-dead", html)
            for marker in ("سارة علي", self.certificate.number, "اللغة الإنجليزية", "data-verify-"):
                self.assertNotIn(marker, html)
            bodies.add(html)
        self.assertEqual(len(bodies), 1)  # forged, altered, foreign, wrong number and unknown row cannot be told apart

    def test_get_and_head_only_and_the_errors_are_private_too(self):
        self.assertEqual(self.client.head(self.url).status_code, 200)
        for method in (self.client.post, self.client.put, self.client.delete):
            response = method(self.url)
            self.assertEqual(response.status_code, 405)
            self.assertPrivate(response)

    def test_a_flood_from_one_address_gets_the_plain_page_with_429_and_another_address_is_unaffected(self):
        with frozen_minute():
            self.flood()

    def flood(self):
        for _ in range(60):
            self.assertEqual(self.client.get(self.url, REMOTE_ADDR="10.0.0.9").status_code, 200)
        flooded = self.client.get(self.url, REMOTE_ADDR="10.0.0.9")
        self.assertEqual(flooded.status_code, 429)
        self.assertPrivate(flooded)
        self.assertIn("data-certificate-dead", flooded.content.decode())
        self.assertNotIn("سارة علي", flooded.content.decode())
        self.assertEqual(self.client.get(self.url, REMOTE_ADDR="10.0.0.10").status_code, 200)

    def test_a_guesser_is_stopped_after_thirty_misses_in_an_hour(self):
        with frozen_minute():
            self.guess()

    def guess(self):
        miss = reverse("certificate_verify", args=["nothing-here"])
        for _ in range(30):
            self.assertEqual(self.client.get(miss, REMOTE_ADDR="10.0.0.20").status_code, 404)
        self.assertEqual(self.client.get(miss, REMOTE_ADDR="10.0.0.20").status_code, 429)
        self.assertEqual(self.client.get(self.url, REMOTE_ADDR="10.0.0.20").status_code, 200)  # a real certificate is still read, only the guessing is stopped

    def test_the_issuing_business_is_the_groups_own_entity_when_there_are_several(self):
        from entities.models import Entity

        self.assertIn(">Demo Store<", self.client.get(self.url).content.decode())  # one entity: the company's name
        branch = Entity.objects.create(code="E-MAADI", name_ar="فرع المعادي", name_en="Maadi branch", activity_slug="education", sub_activity_slug="training")
        self.group.entity = branch
        self.group.save()
        self.assertGreater(Entity.objects.filter(active=True).count(), 1)
        self.assertRegex(self.client.get(self.url).content.decode(), r"data-verify-business[^>]*><bdi>فرع المعادي</bdi>")
        self.assertRegex(self.client.get(self.url + "?lang=en").content.decode(), r"data-verify-business[^>]*><bdi>Maadi branch</bdi>")

    def test_the_page_changes_nothing(self):
        before, logs = self.counts(), AuditLog.objects.count()
        self.client.get(self.url)
        certificates.revoke(self.certificate, "سبب", self.owner)
        self.client.get(self.url)
        self.assertEqual(self.counts(), before)
        self.assertEqual(AuditLog.objects.count(), logs + 1)  # only the revoke itself wrote an audit row; opening the page writes none

    def test_the_route_is_public_and_is_not_a_share_kind(self):
        from printing import share

        self.assertNotIn("certificate", share.KINDS)
        self.assertEqual(self.client.get(self.url).status_code, 200)  # no login redirect


# ---- the staff screens ----

@override_settings(PUBLIC_BASE_URL=BASE)
class ScreenTests(CertSetup):
    def setUp(self):
        super().setUp()
        self.hold(absent={4})
        self.final_exam("18", title="النهائي")
        self.client.force_login(self.owner)

    def as_user(self, user):
        self.client.force_login(user)
        return self.client

    def issue_via_screen(self, **extra):
        return self.client.post(reverse("education:certificate_issue"), {"lang": "ar", "enrollment": self.enrollment.pk, **extra})

    def test_the_tab_is_there_for_a_manager_of_a_training_centre_only(self):
        self.assertContains(self.client.get(reverse("education:students")), "data-certificates-tab")
        page = self.as_user(self.cashier).get(reverse("education:students"))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-certificates-tab")

    def test_every_screen_and_post_refuses_who_may_not_manage_students(self):
        certificate = self.issue()
        urls = (reverse("education:certificates"), reverse("education:certificate_issue"), reverse("education:certificate", args=[certificate.pk]))
        for user in (self.cashier, self.teacher_user):
            self.as_user(user)
            with self.subTest(user=user.username):
                for url in urls:
                    self.assertEqual(self.client.get(url).status_code, 403, url)
                self.assertEqual(self.client.post(reverse("education:certificate_revoke", args=[certificate.pk]), {"reason": "سبب كافي"}).status_code, 403)
                self.assertEqual(self.issue_via_screen().status_code, 403)
        self.assertFalse(Certificate.objects.get().revoked)
        self.client.logout()
        for url in urls:
            self.assertEqual(self.client.get(url).status_code, 302)
            self.assertIn("/login/", self.client.get(url)["Location"])

    def test_the_issue_screen_starts_from_the_finished_students_and_filters_by_group(self):
        page = self.client.get(reverse("education:certificate_issue"))
        self.assertContains(page, f'data-ready-enrollment="{self.enrollment.pk}"')
        self.assertContains(page, "سارة علي")
        other = self.make_group("مجموعة تانية", ends=day(1), level="3")
        filtered = self.client.get(reverse("education:certificate_issue") + f"?group={other.pk}")
        self.assertNotContains(filtered, f'data-ready-enrollment="{self.enrollment.pk}"')
        self.assertContains(filtered, "data-no-ready")
        self.assertContains(self.client.get(reverse("education:certificate_issue") + "?lang=en"), "Issue")

    def test_the_preview_says_what_will_be_printed_with_the_grade_offered(self):
        html = self.client.get(reverse("education:certificate_issue") + f"?enrollment={self.enrollment.pk}").content.decode()
        self.assertIn('data-held>10<', html)
        self.assertRegex(html, r"data-hours><bdi dir=\"ltr\">15</bdi>")
        self.assertRegex(html, r"data-rate><bdi dir=\"ltr\">90%</bdi>")
        self.assertRegex(html, r'data-grade[^>]*>|value="90%"|value="85\.0%"|value="90\.0%"')
        self.assertIn('value="90.0%"', html)  # 18 of 20, published
        self.assertIn("data-issue-form", html)
        self.assertNotIn("data-warnings", html)
        self.assertNotIn("data-refusal", html)

    def test_a_student_who_does_not_meet_the_conditions_is_refused_on_the_screen_with_the_exact_message(self):
        group = self.make_group("لسه شغالة", ends=TODAY + timedelta(days=5))
        enrollment = services.enroll(self.student("ليلى", "01001234507"), group, self.owner, start_date=day(30))
        page = self.client.get(reverse("education:certificate_issue") + f"?enrollment={enrollment.pk}")
        self.assertContains(page, "data-refusal")
        self.assertContains(page, "الطالب لسه مكمّل في المجموعة ومدتها ما خلصتش، فمينفعش تتصدر له شهادة دلوقتي. أوقف تسجيله أو استنى لحد ما تخلص.")
        self.assertNotContains(page, "data-issue-form")
        posted = self.client.post(reverse("education:certificate_issue"), {"lang": "ar", "enrollment": enrollment.pk})
        self.assertContains(posted, "الطالب لسه مكمّل", status_code=200)
        self.assertFalse(Certificate.objects.exists())

    def test_issuing_from_the_screen_makes_one_certificate_and_a_second_click_shows_it_again(self):
        response = self.issue_via_screen(grade_text="90.0%")
        certificate = Certificate.objects.get()
        self.assertRedirects(response, f"{reverse('education:certificate', args=[certificate.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertEqual((certificate.hours, certificate.attendance_rate, certificate.grade_text), (D("15.00"), 90, "90.0%"))
        again = self.issue_via_screen(grade_text="ممتاز")
        self.assertEqual(again.status_code, 302)
        self.assertEqual((Certificate.objects.count(), Certificate.objects.get().grade_text), (1, "90.0%"))
        page = self.client.get(again["Location"])
        self.assertContains(page, "للتسجيل ده شهادة سارية بالفعل")
        self.assertContains(self.client.get(reverse("education:certificate_issue") + f"?enrollment={self.enrollment.pk}"), "data-live")

    def test_a_warning_stops_the_first_post_and_the_second_with_the_box_ticked_issues(self):
        group = self.make_group("ضعيف", ends=day(1))
        weak = self.student("هاني", "01001234511")
        enrollment = services.enroll(weak, group, self.owner, start_date=day(30))
        self.hold(group, student=weak, absent={1, 2, 3})
        form = {"lang": "ar", "enrollment": enrollment.pk, "grade_text": ""}
        first = self.client.post(reverse("education:certificate_issue"), form)
        self.assertEqual(first.status_code, 200)
        self.assertContains(first, "data-warnings")
        self.assertContains(first, "نسبة حضور الطالب 70%، وده أقل من 75%.")
        self.assertContains(first, "أصدرها برضو")
        self.assertFalse(Certificate.objects.filter(enrollment=enrollment).exists())
        second = self.client.post(reverse("education:certificate_issue"), {**form, "confirm": "1"})
        self.assertEqual(second.status_code, 302)
        self.assertEqual(Certificate.objects.get(enrollment=enrollment).attendance_rate, 70)

    def test_the_list_shows_number_student_course_date_grade_and_status_and_filters(self):
        live = self.issue(grade_text="ممتاز")
        group = self.make_group("تانية", ends=day(1))
        enrollment = services.enroll(self.student("كريم", "01001234512"), group, self.owner, start_date=day(30))
        self.hold(group, student=enrollment.student)
        gone = certificates.issue(enrollment, self.owner)[0]
        certificates.revoke(gone, "غلط", self.owner)
        page = self.client.get(reverse("education:certificates"))
        html = page.content.decode()
        self.assertIn(f'data-certificate="{live.pk}" data-state="live"', html)
        self.assertIn(f'data-certificate="{gone.pk}" data-state="revoked"', html)
        self.assertContains(page, "ممتاز")
        self.assertContains(page, "data-list-rows")
        self.assertNotContains(page, "data-label=")
        only = self.client.get(reverse("education:certificates") + "?status=revoked").content.decode()
        self.assertIn(f'data-certificate="{gone.pk}"', only)
        self.assertNotIn(f'data-certificate="{live.pk}"', only)
        by_name = self.client.get(reverse("education:certificates") + "?q=سارة").content.decode()
        self.assertIn(f'data-certificate="{live.pk}"', by_name)
        self.assertNotIn(f'data-certificate="{gone.pk}"', by_name)
        by_number = self.client.get(reverse("education:certificates") + f"?q={gone.number}").content.decode()
        self.assertIn(f'data-certificate="{gone.pk}"', by_number)
        self.assertContains(self.client.get(reverse("education:certificates") + "?lang=en"), "Completion certificates")

    def test_the_printed_certificate_in_arabic(self):
        certificate = self.issue(grade_text="90.0%")
        page = self.client.get(reverse("education:certificate", args=[certificate.pk]))
        html = page.content.decode()
        self.assertEqual(page.status_code, 200)
        self.assertIn('dir="rtl"', html)
        self.assertIn("size:A4 landscape", self.static("hesba/css/education_certificate.css"))
        for marker, expected in (("data-cert-business", "Demo Store"), ("data-cert-title", "شهادة إتمام"), ("data-cert-name", "سارة علي"), ("data-cert-course", "اللغة الإنجليزية"),
                                 ("data-cert-level", "المستوى <bdi>2</bdi>"), ("data-cert-grade", "<bdi>90.0%</bdi>")):
            self.assertRegex(html, rf"{marker}[^>]*>{re.escape(expected)}<")
        self.assertRegex(html, r"data-cert-hours[^>]*><bdi dir=\"ltr\">15</bdi> ساعة")
        self.assertRegex(html, rf"data-cert-number[^>]*><bdi dir=\"ltr\">{re.escape(certificate.number)}</bdi>")
        self.assertRegex(html, r"data-cert-period[^>]*>من <bdi>\d+ \S+ \d+</bdi> إلى <bdi>\d+ \S+ \d+</bdi>")
        self.assertIn('data-state="live"', html)
        self.assertNotIn("data-cert-void", html)
        qr = re.search(r'data-qr-text="([^"]+)"', html).group(1)
        self.assertTrue(qr.startswith(f"{BASE}/verify/c/"))
        self.assertNotIn("testserver", html)  # the address comes from PUBLIC_BASE_URL, never the request's host
        token = qr.split("/verify/c/")[1].split("/")[0]
        self.assertEqual(certificates.read_token(token).pk, certificate.pk)
        self.assertEqual(self.client.get(f"/verify/c/{token}/").status_code, 200)  # the code on the paper opens the page that says it is genuine
        self.assertIn("data-revoke", html)
        self.assertEqual(self.client.post(reverse("education:certificate", args=[certificate.pk])).status_code, 405)

    def test_the_printed_certificate_in_english_has_the_same_facts(self):
        certificate = self.issue(grade_text="90.0%")
        html = self.client.get(reverse("education:certificate", args=[certificate.pk]) + "?lang=en").content.decode()
        self.assertIn('dir="ltr"', html)
        for text in ("Certificate of Completion", "This certificate is awarded to", "in recognition of completing", "Certificate no.", "Scan the code to verify this certificate", "Centre management"):
            self.assertIn(text, html)
        self.assertRegex(html, r"data-cert-hours[^>]*><bdi dir=\"ltr\">15</bdi> hours")
        self.assertRegex(html, r"data-cert-period[^>]*>from <bdi>")
        self.assertIn("data-lang-switch", html)

    def test_a_revoked_certificate_prints_a_revoked_mark_and_shows_the_reason_to_staff_only(self):
        certificate = self.issue()
        response = self.client.post(reverse("education:certificate_revoke", args=[certificate.pk]), {"lang": "ar", "reason": REASON})
        self.assertRedirects(response, f"{reverse('education:certificate', args=[certificate.pk])}?lang=ar", fetch_redirect_response=False)
        html = self.client.get(response["Location"]).content.decode()
        self.assertIn("الشهادة اتلغت.", html)  # the flash message, read once
        self.assertIn('data-state="revoked"', html)
        self.assertIn("data-cert-void", html)
        self.assertRegex(html, rf"data-revoke-reason[^>]*>{REASON}<")  # the manager sees it on the staff block...
        self.assertNotIn("data-revoke-btn", html)
        self.assertNotIn(REASON, self.client.get(reverse("certificate_verify", args=[certificates.make_token(certificate)])).content.decode())  # ...the public never does

    def test_revoking_without_a_reason_is_refused_and_the_certificate_stands(self):
        certificate = self.issue()
        response = self.client.post(reverse("education:certificate_revoke", args=[certificate.pk]), {"lang": "ar", "reason": " "})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Certificate.objects.get().revoked)
        self.assertContains(self.client.get(response["Location"]), "اكتب سبب الإلغاء")
        self.assertEqual(self.client.get(reverse("education:certificate_revoke", args=[certificate.pk])).status_code, 405)

    @override_settings(PUBLIC_BASE_URL="")
    def test_without_a_public_address_the_sheet_prints_no_code_and_says_why_to_the_manager(self):
        certificate = self.issue()
        html = self.client.get(reverse("education:certificate", args=[certificate.pk])).content.decode()
        self.assertNotIn("data-qr-text=", html)
        self.assertNotIn("data-cert-verify", html)
        self.assertIn("data-no-base-url", html)
        self.assertIn("PUBLIC_BASE_URL", html)

    def test_another_entitys_certificate_enrolment_and_group_are_not_found(self):
        from entities.models import Entity

        certificate = self.issue()
        self.assertEqual(self.client.get(reverse("education:certificate", args=[certificate.pk])).status_code, 200)  # the positive control
        branch = Entity.objects.create(code="E-CERT3", name_ar="فرع", activity_slug="education", sub_activity_slug="training")
        session = self.client.session
        session["hesba_entity"] = branch.pk
        session.save()
        self.assertEqual(self.client.get(reverse("education:certificate", args=[certificate.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("education:certificate_revoke", args=[certificate.pk]), {"reason": "سبب كافي"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("education:certificate_issue") + f"?enrollment={self.enrollment.pk}").status_code, 404)
        self.assertEqual(self.issue_via_screen().status_code, 404)
        self.assertNotContains(self.client.get(reverse("education:certificates")), f'data-certificate="{certificate.pk}"')
        self.assertNotContains(self.client.get(reverse("education:certificate_issue")), f'data-ready-enrollment="{self.enrollment.pk}"')
        self.assertFalse(Certificate.objects.get().revoked)
        for unknown in (reverse("education:certificate", args=[999999]), reverse("education:certificate_issue") + "?enrollment=999999"):
            self.assertEqual(self.client.get(unknown).status_code, 404)

    def test_the_screens_move_no_money(self):
        before = self.counts()
        certificate = self.issue()
        self.client.get(reverse("education:certificates"))
        self.client.get(reverse("education:certificate_issue"))
        self.client.get(reverse("education:certificate", args=[certificate.pk]))
        self.client.post(reverse("education:certificate_revoke", args=[certificate.pk]), {"reason": "سبب كافي"})
        self.assertEqual(self.counts(), before)

    def static(self, path):
        with open(finders.find(path), encoding="utf-8") as handle:
            return handle.read()


# ---- assets and wording ----

class AssetTests(SimpleTestCase):
    TEMPLATES = ("certificates.html", "certificate_issue.html", "certificate.html", "certificate_verify.html")
    SOURCES = ("certificates.py", "public.py", "views_certificates.py")

    def read(self, path):
        with open(finders.find(path), encoding="utf-8") as handle:
            return handle.read()

    def test_the_stylesheet_uses_defined_tokens_and_no_colour_literals(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", self.read("hesba/css/tokens.css")))
        css = self.read("hesba/css/education_certificate.css")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css), [])
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertTrue(used)
        self.assertEqual(sorted(used - defined), [])
        self.assertNotRegex(css, r"font-weight:\s*[89]00")
        self.assertIn("@page{size:A4 landscape", css)
        self.assertIn("var(--hs-control-height)", css)  # the buttons keep the 44 px touch target

    def test_the_templates_carry_no_cell_labels_no_literal_colours_and_no_iso_dates(self):
        root = pathlib.Path(settings.BASE_DIR) / "templates/education"
        for name in self.TEMPLATES:
            text = (root / name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertNotRegex(text, r"<td[^>]*data-label=")
                self.assertNotRegex(text, r"#[0-9a-fA-F]{6}\b|style=\"[^\"]*(color|background)")
                self.assertNotIn('|date:"Y-m-d"', text)  # a date a person reads is in words
        self.assertIn("partials/back_arrow.html", (root / "certificate.html").read_text(encoding="utf-8"))

    def test_the_wording_keeps_to_the_premium_tone(self):
        root = pathlib.Path(settings.BASE_DIR)
        texts = [(root / "education" / name).read_text(encoding="utf-8") for name in self.SOURCES]
        texts += [(root / "templates/education" / name).read_text(encoding="utf-8") for name in self.TEMPLATES]
        for text in texts:
            for word in ("لينك", "بتاع", "ورشة", "الورشة", "أسطى", "اسطى", "صنايعي"):
                self.assertNotIn(word, text)

    def test_what_a_student_or_a_parent_reads_passes_the_customer_voice_rules(self):
        """The verify page, its messages and the printed sheet keep to the rules of ``reports.tests_customer_voice``: never «المحل», «لينك», «بتاع»,
        «ليك / بيك / معاك», or the misspelt «معاد». That test lists the templates it scans; these are scanned here with the same patterns."""

        from reports.tests_customer_voice import _problems

        from . import public, views_certificates

        sheet = ("sheet_title", "lead", "for", "sheet_hours", "hours_unit", "sheet_number", "sheet_issued", "scan", "sign", "void", "period", "grade", "level", "from", "to")
        texts = [(f"public.WORDS[{key}]", text) for key, text in public.WORDS["ar"].items()]
        texts += [(f"views_certificates.WORDS[{key}]", views_certificates.WORDS["ar"][key]) for key in sheet]
        root = pathlib.Path(settings.BASE_DIR) / "templates/education"
        texts += [(name, (root / name).read_text(encoding="utf-8")) for name in ("certificate_verify.html", "certificate.html")]
        for where, text in texts:
            with self.subTest(where=where):
                self.assertEqual(_problems(text), [])
        for key in ("dead_p", "busy_p", "revoked_p"):
            self.assertIn("حضرتك", public.WORDS["ar"][key])  # what a parent reads when the link does not open speaks to «حضرتك»

    def test_no_arabic_line_of_the_sheet_starts_or_ends_with_a_latin_word(self):
        """The number and every name sit in their own element (a ``dd`` or ``bdi``); no Arabic sentence is built around a Latin token."""

        text = (pathlib.Path(settings.BASE_DIR) / "templates/education/certificate.html").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"\{\{ cw\.(sheet_number|number)[^}]*\}\}[:：]?\s*(<bdi|\{\{ certificate\.number)")
        for words in (pathlib.Path(settings.BASE_DIR) / "education/views_certificates.py", pathlib.Path(settings.BASE_DIR) / "education/public.py"):
            source = words.read_text(encoding="utf-8")
            for line in re.findall(r'"[^"\n]*[؀-ۿ][^"\n]*"', source):
                inner = line.strip('"').strip().rstrip(".،؛:!؟)»«( ")  # a full stop or a bracket after a Latin word is still the word at the end
                self.assertFalse(re.match(r"^[A-Za-z]", inner), line)  # an Arabic string never starts with a Latin letter
                self.assertFalse(re.search(r"[A-Za-z]$", inner), line)  # ...nor ends with one


# ---- the migration ----

class MigrationTests(TransactionTestCase):
    """0009 adds the one table after 0008 and leaves the students, groups and attendance already there as they were."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_depends_on_0008_and_adds_one_table_beside_existing_rows(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("education", "0009_certificates")]
        self.assertIn(("education", "0008_exams_and_marks"), node.dependencies)
        self.assertEqual([type(op).__name__ for op in node.operations], ["CreateModel"])
        executor.migrate([("education", "0008_exams_and_marks")])
        old = executor.loader.project_state([n for n in executor.loader.graph.leaf_nodes() if n[0] != "education"] + [("education", "0008_exams_and_marks")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_cert")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="C-MIGC", name="ولي أمر")
        item = old.get_model("master_data", "Item").objects.create(item_code="CRS-MIGC", item_name="كورس", is_stock_tracked=False)
        course = old.get_model("education", "Course").objects.create(name="إنجليزي", item=item)
        group = old.get_model("education", "StudyGroup").objects.create(code="GR-MIGC", course=course, name="مجموعة")
        student = old.get_model("education", "Student").objects.create(code="ST-MIGC", name="سارة", payer=customer, created_by=user)
        old.get_model("education", "Enrollment").objects.create(student=student, group=group, start_date=day(30), created_by=user)
        session = old.get_model("education", "ClassSession").objects.create(group=group, date=day(1), taken_by=user)
        old.get_model("education", "Attendance").objects.create(session=session, student=student, presence="late")
        self.assertNotIn("education_certificate", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("education", "0009_certificates")])
        self.assertIn("education_certificate", connection.introspection.table_names())
        from django.contrib.auth import get_user_model

        from .models import Attendance, Student, StudyGroup

        self.assertEqual(list(Student.objects.values_list("code", "name")), [("ST-MIGC", "سارة")])
        self.assertEqual(list(StudyGroup.objects.values_list("code", flat=True)), ["GR-MIGC"])
        self.assertEqual(list(Attendance.objects.values_list("presence", flat=True)), ["late"])
        self.assertEqual(Certificate.objects.count(), 0)  # nothing is seeded
        enrollment = Enrollment.objects.get()
        author = get_user_model().objects.get(username="mig_cert")
        first = Certificate.objects.create(enrollment=enrollment, number="CRT-1", issued_on=TODAY, issued_by=author)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Certificate.objects.create(enrollment=enrollment, number="CRT-2", issued_on=TODAY, issued_by=author)  # one live certificate per enrolment
        Certificate.objects.filter(pk=first.pk).update(revoked_at=timezone.now(), revoked_by=author)
        Certificate.objects.create(enrollment=enrollment, number="CRT-2", issued_on=TODAY, issued_by=author)  # once revoked, a new one may be issued
        with self.assertRaises(IntegrityError), transaction.atomic():
            Certificate.objects.create(enrollment=enrollment, number="CRT-1", issued_on=day(1), issued_by=author, revoked_at=timezone.now())  # numbers are unique


class MakeMigrationsCleanTests(TestCase):
    databases = {"default", "feedback"}  # makemigrations reads the history of every database

    def test_the_certificate_table_is_the_only_change_to_the_schema(self):
        from django.core.management import call_command

        call_command("makemigrations", "--check", "--dry-run", verbosity=0)
