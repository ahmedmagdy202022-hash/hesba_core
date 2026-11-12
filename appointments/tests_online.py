"""M10 (HG-096): the online consultation: the call link of an online booking (made or pasted, audited without its text), who sees the
button (staff and the patient's confirm page, inside its window), «paid first», the reminder that never carries the call link, the public
booking page's online choice, the demo, and the migration. Nothing here posts, pays or counts money: the tests count it.

"Now" comes from ``appointments.online.now`` (patched), never a fixed clock; "today" from ``timezone``.
"""

import inspect
import json
import math
import re
from datetime import datetime, time, timedelta
from decimal import Decimal as D
from unittest import mock

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from deposits import services as deposits
from deposits.models import Deposit
from entities.models import Entity, EntityMembership
from hesba_testing.factories import (add_sales_line, grant, make_cashbox, make_draft_sales_invoice, make_location, make_role, make_seeded_role, make_user,
                                     make_user_profile, recalculate_invoice_totals)
from ledger.models import JournalEntry
from medical.models import ClinicalVisit
from messaging import services as messaging
from messaging.models import MessageKind, OutboxMessage
from payments.models import PaymentLink
from permissions.models import Permission, RoleCode
from sales.models import CustomerPayment, SalesInvoice
from sales.services import post_sales_invoice
from settings_core.capabilities import set_capability_enabled
from staff.services import save_employee

from . import booking as engine
from . import online, services
from .models import Appointment, AppointmentKind, AppointmentStatus
from messaging.tests_m3 import ClinicSetup

from .tests_booking import BookingSetup, open_day
from .tests_booking_medical import ClinicBooking
from .tests_confirm_link import LinkSetup, at, today

CALL = "https://meet.example.org/ZZCALLROOM4f9k2"
BASE = "https://hesba.test"
PRIVATE_SCAN = re.compile(r"data-label")


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def money_counts():
    """Everything that would change if money moved: invoices, payments, deposits, cashbox movements, journal entries, pay links."""

    return (SalesInvoice.objects.count(), CustomerPayment.objects.count(), Deposit.objects.count(), CashboxMovement.objects.count(),
            JournalEntry.objects.count(), PaymentLink.objects.count())


class OnlineSetup(LinkSetup):
    """The M3 clinic (an owner, a doctor employee, two patients) plus the people a call link is about."""

    def setUp(self):
        super().setUp()
        self.doc_user = person(RoleCode.DOCTOR, "on_doc")  # the seeded doctor role holds only medical.treat_patients: no agenda, no booking permission
        self.doctor.user = self.doc_user
        self.doctor.save()
        self.other_doctor = save_employee({"name": "د. كريم"}, self.owner)
        self.other_doc_user = person(RoleCode.DOCTOR, "on_doc2")
        self.other_doctor.user = self.other_doc_user
        self.other_doctor.save()
        self.manager = person(RoleCode.MANAGER, "on_mgr")
        self.viewer = make_user(username="on_viewer")  # may see the agenda, may not manage it
        role = make_role("ROLE-on-viewer")
        grant(role, Permission.objects.get(code="sales.view_sales_invoices"))
        make_user_profile(user=self.viewer, role=role)
        self.online = self.book_online()

    def book_online(self, status=AppointmentStatus.CONFIRMED, start=None, link=CALL, price="300.00", customer=None, **extra):
        return Appointment.objects.create(
            number=f"AP-ON-{Appointment.objects.count() + 1}", customer=customer or self.patient, employee=self.doctor, service=self.service, price=D(price),
            starts_at=start or at(today(), 23, 59), status=status, kind=AppointmentKind.ONLINE, call_url=link, created_by=self.owner, **extra)

    def detail(self, appointment=None, lang="ar"):
        return f"{reverse('appointments:detail', args=[(appointment or self.online).pk])}?lang={lang}"

    def agenda(self, lang="ar", appointment=None):
        day = timezone.localtime((appointment or self.online).starts_at).date().isoformat()
        return f"{reverse('appointments:agenda')}?lang={lang}&day={day}"

    def post(self, action, appointment=None, lang="ar", **data):
        return self.client.post(self.detail(appointment, lang), {"lang": lang, "action": action, **data})

    def page(self, appointment, now, lang="ar", client=None):
        with mock.patch("appointments.online.now", return_value=now):
            return (client or self.client).get(self.url(appointment, lang))

    def audit_rows(self, appointment=None):
        return AuditLog.objects.filter(module="appointments", object_id=str((appointment or self.online).pk),
                                       action__in=("set_call_link", "remove_call_link"))


# ---- making and pasting the link --------------------------------------------------------------


class MakingTheLinkTests(OnlineSetup):
    def test_a_generated_link_is_unguessable_different_each_time_and_about_nothing(self):
        links = {online.make_link() for _ in range(300)}
        self.assertEqual(len(links), 300)
        prefix = "https://meet.jit.si/"
        for link in list(links)[:20]:
            self.assertTrue(link.startswith(prefix), link)
            room = link[len(prefix):]
            self.assertRegex(room, r"^[a-z0-9]+$")  # letters and digits only
            self.assertTrue(room.startswith("demostore"))  # the clinic's own name (ASCII letters and digits of it), recognisably the clinic's
            self.assertEqual(len(room) - len("demostore"), online.ROOM_LENGTH)
        self.assertGreaterEqual(online.ROOM_LENGTH * math.log2(len(online.ROOM_ALPHABET)), 128)  # at least 128 bits from ``secrets``, lower case only
        self.assertEqual(list(inspect.signature(online.make_link).parameters), ["prefix", "base"])  # nothing about a booking can flow in

    def test_the_room_never_holds_the_patient_the_phone_the_number_or_the_booking(self):
        self.client.force_login(self.manager)
        for _ in range(5):
            self.post("call_generate")
            link = Appointment.objects.get(pk=self.online.pk).call_url
            for private in ("منى", "01001234567", "1234567", self.online.number, self.online.number.lower(), "AP-ON", "ap-on", "هالة"):
                self.assertNotIn(private, link)

    def test_the_base_is_the_clinic_setting_and_defaults_to_the_public_jitsi(self):
        self.assertEqual(online.base_url(), "https://meet.jit.si")
        online.save_settings({"base_url": "https://meet.clinic.example/rooms/"}, self.owner)
        self.assertEqual(online.base_url(), "https://meet.clinic.example/rooms")
        self.assertTrue(online.make_link().startswith("https://meet.clinic.example/rooms/demostore"))
        self.client.force_login(self.manager)
        self.post("call_generate")
        self.assertTrue(Appointment.objects.get(pk=self.online.pk).call_url.startswith("https://meet.clinic.example/rooms/demostore"))
        online.save_settings({"base_url": ""}, self.owner)  # empty: back to the public server
        self.assertEqual(online.base_url(), "https://meet.jit.si")

    def test_a_damaged_stored_base_is_never_used(self):
        from settings_core.models import SystemSetting

        SystemSetting.objects.create(key=online.SETTING_BASE, value="http://evil.example", active=True)
        self.assertEqual(online.base_url(), "https://meet.jit.si")

    def test_the_base_is_checked_like_a_pasted_link(self):
        for bad, message in (("http://meet.example.org", "اللينك لازم يبدأ بـ https:// بس"), ("https://u:p@meet.example.org", "اللينك فيه اسم مستخدم"),
                             ("https://meet.example.org/?a=1", "عنوان السيرفر لازم يكون من غير علامة ? ولا #"), ("https://meet.example.org/#x", "من غير علامة ?")):
            with self.subTest(bad=bad), self.assertRaisesMessage(ValidationError, message):
                online.save_settings({"base_url": bad}, self.owner)
        self.assertEqual(online.base_url(), "https://meet.jit.si")  # a refused value saves nothing

    def test_generating_sets_replaces_and_audits_without_the_link(self):
        self.client.force_login(self.manager)
        Appointment.objects.filter(pk=self.online.pk).update(call_url="")
        first = self.post("call_generate")
        self.assertEqual(first.status_code, 302)
        one = Appointment.objects.get(pk=self.online.pk).call_url
        self.assertTrue(one.startswith("https://meet.jit.si/"))
        self.post("call_generate")
        two = Appointment.objects.get(pk=self.online.pk).call_url
        self.assertNotEqual(one, two)
        rows = list(self.audit_rows().order_by("pk"))
        self.assertEqual([row.after_data for row in rows], [{"call_link": "set", "source": "generated"}, {"call_link": "replaced", "source": "generated"}])
        self.assertTrue(all(row.actor == self.manager for row in rows))  # who; ``created_at`` says when
        everything = json.dumps(list(AuditLog.objects.values_list("before_data", "after_data")), ensure_ascii=False)
        self.assertNotIn(one, everything)
        self.assertNotIn(two, everything)
        self.assertNotIn("meet.jit.si", everything)

    def test_pasting_accepts_https_and_replaces_and_removing_audits(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.post("call_paste", call_url="https://meet.google.com/abc-defg-hij").status_code, 302)
        self.assertEqual(Appointment.objects.get(pk=self.online.pk).call_url, "https://meet.google.com/abc-defg-hij")
        self.post("call_paste", call_url=" https://zoom.example.com/j/123456789?pwd=abc ")
        self.assertEqual(Appointment.objects.get(pk=self.online.pk).call_url, "https://zoom.example.com/j/123456789?pwd=abc")
        self.assertEqual(self.post("call_remove").status_code, 302)
        self.assertEqual(Appointment.objects.get(pk=self.online.pk).call_url, "")
        rows = list(self.audit_rows().order_by("pk"))
        self.assertEqual([(row.action, row.after_data) for row in rows],
                         [("set_call_link", {"call_link": "replaced", "source": "pasted"}), ("set_call_link", {"call_link": "replaced", "source": "pasted"}),
                          ("remove_call_link", {"call_link": "none"})])
        self.assertNotIn("zoom.example.com", json.dumps(list(AuditLog.objects.values_list("before_data", "after_data"))))
        self.assertContains(self.post("call_remove"), "مفيش لينك مكالمة عشان يتشال")  # nothing left to remove

    def test_a_pasted_link_that_is_not_plain_https_is_refused_with_the_exact_message(self):
        cases = (("http://meet.example.org/room", "اللينك لازم يبدأ بـ https:// بس (http وjavascript وأي نوع تاني مرفوض).",
                  "The link must start with https:// only (http, javascript and every other kind is refused)."),
                 ("javascript:alert(1)", "اللينك لازم يبدأ بـ https:// بس (http وjavascript وأي نوع تاني مرفوض).",
                  "The link must start with https:// only (http, javascript and every other kind is refused)."),
                 ("meet.example.org/room", "اللينك لازم يبدأ بـ https:// بس (http وjavascript وأي نوع تاني مرفوض).",
                  "The link must start with https:// only (http, javascript and every other kind is refused)."),
                 ("https://user:secret@meet.example.org/room", "اللينك فيه اسم مستخدم أو كلمة سر قبل اسم الموقع؛ ده مينفعش. استخدم لينك من غيرهم.",
                  "The link has a user name or password in front of the site's name, which is not allowed. Use a link without them."),
                 ("https://meet.example.org/" + "a" * 600, "اللينك طويل أوي؛ الحد الأقصى 600 حرف.", "The link is too long; the maximum is 600 characters."),
                 ("https://meet.example.org/a b", "اللينك فيه مسافة أو حرف مش مسموح بيه.", "The link has a space or a character that is not allowed."),
                 ('https://meet.example.org/"x', "اللينك فيه مسافة أو حرف مش مسموح بيه.", "The link has a space or a character that is not allowed."),
                 ("https://", "اللينك مش مظبوط.", "The link is not valid."), ("", "اكتب اللينك.", "Enter the link."))
        self.client.force_login(self.manager)
        for bad, arabic, english in cases:
            for lang, message in (("ar", arabic), ("en", english)):
                with self.subTest(bad=bad[:40], lang=lang):
                    page = self.post("call_paste", lang=lang, call_url=bad)
                    self.assertEqual(page.status_code, 200)
                    self.assertContains(page, "data-appointment-error")
                    self.assertContains(page, message.replace("'", "&#x27;") if "'" in message else message)
                    self.assertEqual(Appointment.objects.get(pk=self.online.pk).call_url, CALL)  # the old link stays
        self.assertEqual(self.audit_rows().count(), 0)

    def test_a_link_changes_only_while_the_booking_is_not_finished_and_is_online(self):
        self.client.force_login(self.manager)
        for status in (AppointmentStatus.DONE, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
            booking = self.book_online(status=status)
            for action in ("call_generate", "call_remove"):
                with self.subTest(status=status, action=action):
                    page = self.post(action, booking)
                    self.assertContains(page, "الميعاد ده خلص أو اتلغى؛ مينفعش تغيّر لينك المكالمة")
            self.assertEqual(Appointment.objects.get(pk=booking.pk).call_url, CALL)
            self.assertNotContains(self.client.get(self.detail(booking)), "data-call-controls")
        plain = self.make(self.patient, 15)
        self.assertContains(self.post("call_generate", plain), "الميعاد ده مش كشف أونلاين")
        self.assertContains(self.post("call_generate", plain, lang="en"), "This booking is not an online visit.")
        self.assertEqual(Appointment.objects.get(pk=plain.pk).call_url, "")

    def test_changing_the_kind_away_from_online_drops_the_link(self):
        self.client.force_login(self.manager)
        form = {"day": today().isoformat(), "time": "23:59", "customer": str(self.patient.pk), "service": str(self.service.pk), "employee": str(self.doctor.pk),
                "price": "300.00", "duration_minutes": "30", "kind": "appointment"}
        self.post("reschedule", **form)
        booking = Appointment.objects.get(pk=self.online.pk)
        self.assertEqual((booking.kind, booking.call_url), ("appointment", ""))
        self.assertEqual(self.audit_rows().get().after_data, {"call_link": "none", "why": "kind_changed"})

    def test_editing_an_online_booking_keeps_its_kind_and_link(self):
        self.client.force_login(self.manager)
        form = {"day": today().isoformat(), "time": "23:59", "customer": str(self.patient.pk), "service": str(self.service.pk), "employee": str(self.doctor.pk),
                "price": "350.00", "duration_minutes": "30", "kind": "online"}
        self.post("reschedule", **form)
        booking = Appointment.objects.get(pk=self.online.pk)
        self.assertEqual((booking.kind, booking.call_url, booking.price), ("online", CALL, D("350.00")))


# ---- who sees the button: staff ----------------------------------------------------------------


class StaffVisibilityTests(OnlineSetup):
    def test_the_doctor_of_the_booking_and_a_manager_see_the_button_everywhere_they_may_open(self):
        self.client.force_login(self.manager)
        for lang in ("ar", "en"):
            for url in (self.agenda(lang), self.detail(lang=lang), reverse("medical:queue") + f"?lang={lang}"):
                with self.subTest(url=url):
                    page = self.client.get(url)
                    self.assertContains(page, f'href="{CALL}"')
                    self.assertContains(page, 'rel="noopener noreferrer"')
                    self.assertContains(page, 'referrerpolicy="no-referrer"')
                    self.assertContains(page, 'target="_blank"')
                    self.assertContains(page, "data-join-call")
        self.assertContains(self.client.get(self.agenda("en")), "Join the call")
        self.assertContains(self.client.get(self.agenda()), "ادخل المكالمة")
        self.client.force_login(self.doc_user)  # the doctor role has no agenda: the queue and the visit page are where the call opens
        queue = self.client.get(reverse("medical:queue"))
        self.assertContains(queue, f'href="{CALL}"')
        self.assertContains(queue, "data-my-online-row")
        self.assertContains(queue, "كشوفاتك الأونلاين النهارده")
        self.assertEqual(self.client.get(self.agenda()).status_code, 403)

    def test_a_checked_in_online_patient_shows_the_button_on_the_ticket_too(self):
        from medical import queue

        ticket = queue.check_in(self.patient, self.manager, appointment=self.online, doctor=self.doctor)
        self.client.force_login(self.manager)
        page = self.client.get(reverse("medical:queue"))
        self.assertContains(page, f'data-ticket="{ticket.pk}"')
        self.assertContains(page, f'href="{CALL}"')
        self.assertContains(page, "data-queue-online")

    def test_nobody_else_gets_the_link_in_any_page_and_the_week_view_never_carries_it(self):
        Appointment.objects.filter(pk=self.online.pk).update(status=AppointmentStatus.CONFIRMED)
        self.client.force_login(self.viewer)
        for url in (self.agenda(), self.detail(), self.agenda("en"), self.detail(lang="en"),
                    reverse("appointments:week_json") + f"?start={today().isoformat()}", reverse("appointments:week") + f"?day={today().isoformat()}"):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, "ZZCALLROOM")
                self.assertNotContains(page, "data-join-call")
        self.assertContains(self.client.get(self.agenda()), f'data-appointment-row="{self.online.number}"')  # the booking is there; only the link is not
        self.assertNotContains(self.client.get(self.detail()), "data-call-controls")  # and no controls
        self.assertEqual(self.client.get(reverse("medical:queue")).status_code, 403)
        # another doctor: no button on the queue and none on the visit page of a booking that is not theirs
        self.client.force_login(self.other_doc_user)
        queue = self.client.get(reverse("medical:queue"))
        self.assertEqual(queue.status_code, 200)
        self.assertNotContains(queue, "ZZCALLROOM")
        self.assertNotContains(queue, "data-my-online-row")
        visit = self.client.get(f"{reverse('medical:visit_new', args=[self.patient.pk])}?appointment={self.online.pk}")
        self.assertEqual(visit.status_code, 200)
        self.assertContains(visit, "data-visit-online")  # the mark, yes
        self.assertNotContains(visit, "ZZCALLROOM")

    def test_a_finished_booking_shows_no_button_to_anyone(self):
        for status in (AppointmentStatus.DONE, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
            Appointment.objects.filter(pk=self.online.pk).update(status=status)
            for user in (self.manager, self.doc_user):
                self.client.force_login(user)
                for url in (self.agenda(), self.detail(), reverse("medical:queue")):
                    if user == self.doc_user and "appointments" in url:
                        continue
                    with self.subTest(status=status, user=user.username, url=url):
                        self.assertNotContains(self.client.get(url), "ZZCALLROOM")

    def test_a_booking_that_is_not_online_never_shows_a_button_even_with_a_link_on_it(self):
        Appointment.objects.filter(pk=self.online.pk).update(kind=AppointmentKind.APPOINTMENT)
        self.client.force_login(self.manager)
        for url in (self.agenda(), self.detail(), reverse("medical:queue")):
            self.assertNotContains(self.client.get(url), "ZZCALLROOM")

    def test_a_user_without_the_right_is_refused_on_every_link_post_and_nothing_changes(self):
        before = (Appointment.objects.get(pk=self.online.pk).call_url, AuditLog.objects.count())
        for user in (self.viewer, self.doc_user, self.other_doc_user):
            self.client.force_login(user)
            for action, data in (("call_generate", {}), ("call_paste", {"call_url": "https://meet.example.org/other"}), ("call_remove", {})):
                with self.subTest(user=user.username, action=action):
                    self.assertEqual(self.post(action, **data).status_code, 403)
        self.assertEqual((Appointment.objects.get(pk=self.online.pk).call_url, AuditLog.objects.count()), before)

    def test_another_entitys_booking_in_a_crafted_post_is_refused(self):
        main_booking = self.online
        branch = Entity.objects.create(code="CL2", name_ar="عيادة المعادي", name_en="Maadi Clinic", activity_slug="medical", sub_activity_slug="clinic")
        branch_manager = person(RoleCode.MANAGER, "on_branch_mgr")
        EntityMembership.objects.create(user=branch_manager, entity=branch, is_default=True)
        self.client.force_login(branch_manager)
        self.assertEqual(self.client.get(self.detail(main_booking)).status_code, 404)
        for action, data in (("call_generate", {}), ("call_paste", {"call_url": "https://meet.example.org/crafted"}), ("call_remove", {})):
            with self.subTest(action=action):
                self.assertEqual(self.post(action, main_booking, **data).status_code, 404)
        self.assertEqual(Appointment.objects.get(pk=main_booking.pk).call_url, CALL)
        self.assertNotContains(self.client.get(self.agenda()), "ZZCALLROOM")
        self.assertEqual(self.audit_rows(main_booking).count(), 0)

    def test_the_visit_page_of_an_online_booking_shows_the_mark_and_the_doctors_button(self):
        self.client.force_login(self.doc_user)
        url = f"{reverse('medical:visit_new', args=[self.patient.pk])}?appointment={self.online.pk}"
        page = self.client.get(url)
        self.assertContains(page, "data-visit-online")
        self.assertContains(page, "كشف أونلاين")
        self.assertContains(page, f'href="{CALL}"')
        self.assertContains(self.client.get(url + "&lang=en"), "Online visit")
        # opened exactly like any booking: the visit is recorded against it
        response = self.client.post(url, {"lang": "ar", "appointment": str(self.online.pk), "visit_date": today().isoformat(), "doctor": str(self.doctor.pk),
                                          "complaint": "صداع", "diagnosis": "إرهاق", "treatment": "راحة"})
        self.assertEqual(response.status_code, 302)
        visit = ClinicalVisit.objects.get(appointment=self.online)
        self.assertEqual((visit.patient, visit.doctor, visit.diagnosis), (self.patient, self.doctor, "إرهاق"))
        again = self.client.get(f"{reverse('medical:visit_edit', args=[visit.pk])}?lang=ar")
        self.assertContains(again, f'href="{CALL}"')  # still open: the saved visit keeps the button
        services.set_status(self.online, AppointmentStatus.DONE, self.owner)
        finished = self.client.get(f"{reverse('medical:visit_edit', args=[visit.pk])}?lang=ar")
        self.assertContains(finished, "data-visit-online")
        self.assertNotContains(finished, "ZZCALLROOM")  # the booking is finished: no button

    def test_an_ordinary_booking_visit_page_is_unchanged(self):
        self.client.force_login(self.doc_user)
        page = self.client.get(f"{reverse('medical:visit_new', args=[self.patient.pk])}?appointment={self.appointment.pk}")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-visit-online")
        self.assertNotContains(page, "data-join-call")

    def test_the_doctor_and_the_patient_open_the_very_same_address(self):
        """Item 22's evidence: both buttons carry the identical address, so both get into the same call."""

        self.client.force_login(self.doc_user)
        doctor = re.search(r'href="([^"]+)"[^>]*data-join-call', self.client.get(reverse("medical:queue")).content.decode()).group(1)
        start = self.online.starts_at
        patient_client = self.client_class()
        patient = re.search(r'href="([^"]+)"[^>]*data-join-call', self.page(self.online, start - timedelta(minutes=5), client=patient_client).content.decode()).group(1)
        self.assertEqual((doctor, patient), (CALL, CALL))


# ---- the patient's confirm page and its window --------------------------------------------------


class PatientWindowTests(OnlineSetup):
    def setUp(self):
        super().setUp()
        self.start = at(today() + timedelta(days=1), 11)
        self.online.starts_at = self.start
        self.online.save()

    def join(self, minutes, **expect):
        page = self.page(self.online, self.start + timedelta(minutes=minutes))
        self.assertEqual(page.status_code, expect.get("status", 200), minutes)
        return page

    def test_the_button_is_in_the_window_only_from_15_minutes_before_to_3_hours_after_the_start(self):
        for minutes, shown in ((-16, False), (-15, True), (-1, True), (0, True), (120, True), (179, True), (180, True)):
            with self.subTest(minutes=minutes):
                page = self.join(minutes)
                if shown:
                    self.assertContains(page, f'href="{CALL}"')
                    self.assertContains(page, "data-join-call")
                    self.assertContains(page, 'data-call-state="open"')
                else:
                    self.assertNotContains(page, "ZZCALLROOM")
                    self.assertNotContains(page, "data-join-call")
        late = self.join(181, status=404)  # after the window the page is the dead page it always was
        self.assertNotContains(late, "ZZCALLROOM", status_code=404)
        self.assertNotContains(late, "data-call-box", status_code=404)

    def test_the_button_opens_in_a_new_page_with_no_referrer_and_the_page_keeps_its_private_headers(self):
        page = self.join(-10)
        self.assertContains(page, 'target="_blank" rel="noopener noreferrer" referrerpolicy="no-referrer" data-join-call')
        self.assertEqual(page["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("no-store", page["Cache-Control"])
        self.assertEqual(page["Referrer-Policy"], "same-origin")
        self.assertNotIn(CALL, "".join(str(value) for key, value in page.items()))  # not in any header, and so not in a redirect

    def test_before_the_window_the_page_says_when_the_button_will_show_in_words(self):
        page = self.join(-16)
        self.assertContains(page, 'data-call-state="early"')
        body = page.content.decode()
        self.assertIn("زر دخول المكالمة هيظهر يوم", body)
        self.assertIn("10:45", body)  # 15 minutes before 11:00
        self.assertNotRegex(re.sub(r"<[^>]+>", " ", body[body.index("data-call-note"):]).split("data-call-note")[0], r"\d{4}-\d{2}-\d{2}")  # a day in words, not ISO
        english = self.page(self.online, self.start - timedelta(minutes=16), lang="en").content.decode()
        self.assertIn("The join button will show on", english)
        self.assertIn("10:45", english)
        self.assertIn("15 minutes before the appointment", english)

    def test_only_a_confirmed_or_arrived_online_booking_with_a_link_gets_the_button(self):
        cases = (("booked", AppointmentStatus.BOOKED, CALL, "unconfirmed", 200), ("confirmed with no link", AppointmentStatus.CONFIRMED, "", "no_link", 200),
                 ("arrived", AppointmentStatus.ARRIVED, CALL, "open", 200))
        for label, status, link, state, code in cases:
            with self.subTest(case=label):
                Appointment.objects.filter(pk=self.online.pk).update(status=status, call_url=link)
                page = self.join(-5)
                self.assertEqual(page.status_code, code)
                self.assertContains(page, f'data-call-state="{state}"')
                if state == "open":
                    self.assertContains(page, f'href="{CALL}"')
                else:
                    self.assertNotContains(page, "ZZCALLROOM")
                    self.assertNotContains(page, "data-join-call")
        for status in (AppointmentStatus.CANCELLED, AppointmentStatus.DONE, AppointmentStatus.NO_SHOW):
            with self.subTest(status=status):
                Appointment.objects.filter(pk=self.online.pk).update(status=status, call_url=CALL)
                dead = self.join(-5, status=404)
                self.assertNotContains(dead, "ZZCALLROOM", status_code=404)
                self.assertNotContains(dead, "data-call-box", status_code=404)

    def test_a_booking_that_is_not_online_gets_no_call_box_even_with_a_link_on_it(self):
        Appointment.objects.filter(pk=self.online.pk).update(kind=AppointmentKind.APPOINTMENT, status=AppointmentStatus.CONFIRMED)
        page = self.join(-5)
        self.assertNotContains(page, "ZZCALLROOM")
        self.assertNotContains(page, "data-call-box")
        after_start = self.join(60, status=404)  # and once it has started it is dead, as before M10
        self.assertNotContains(after_start, "ZZCALLROOM", status_code=404)

    def test_after_the_start_the_page_offers_no_confirm_or_cancel_and_arrived_has_its_own_banner(self):
        started = self.join(30)
        self.assertNotContains(started, "data-appointment-actions")
        self.assertNotContains(started, "data-confirm-appointment")
        self.assertNotContains(started, "<form")
        self.assertContains(started, 'data-banner="confirmed"')
        Appointment.objects.filter(pk=self.online.pk).update(status=AppointmentStatus.ARRIVED)
        arrived = self.join(30)
        self.assertContains(arrived, 'data-banner="arrived"')
        self.assertContains(arrived, "ميعادك بدأ الآن")
        self.assertNotContains(arrived, "data-confirm-appointment")

    def test_before_the_start_the_confirm_and_ask_buttons_are_what_they_were(self):
        Appointment.objects.filter(pk=self.online.pk).update(status=AppointmentStatus.BOOKED)
        page = self.join(-60)
        self.assertContains(page, "data-confirm-appointment")
        self.assertContains(page, "data-appointment-actions")
        self.assertContains(page, "كشف أونلاين")  # the page says what kind of visit it is
        self.assertContains(page, 'data-call-state="unconfirmed"')

    def test_the_posts_still_refuse_after_the_start_and_never_redirect_to_the_link(self):
        token = services.patient_token(self.online)
        with mock.patch("appointments.online.now", return_value=self.start + timedelta(minutes=30)), \
                mock.patch("django.utils.timezone.now", return_value=self.start + timedelta(minutes=30)):
            for name in ("share_appointment_confirm", "share_appointment_cancel_request"):
                response = self.client.post(reverse(name, args=[token]), {"lang": "ar"})
                self.assertEqual(response.status_code, 404)  # link_alive is not loosened
                self.assertNotIn(CALL, response.content.decode())
        before = self.client.post(reverse("share_appointment_cancel_request", args=[token]), {"lang": "ar"})
        self.assertEqual(before.status_code, 303)
        self.assertNotIn(CALL, before["Location"])
        self.assertNotIn("ZZCALLROOM", before["Location"])

    def test_a_wrong_or_expired_token_gives_the_same_dead_page_as_for_any_dead_link(self):
        dead = self.join(181, status=404).content
        wrong = self.client.get(reverse("share_document", args=["not-a-token"]))
        self.assertEqual(wrong.status_code, 404)
        self.assertEqual(wrong.content, dead)
        other = self.make(self.other_patient, 14, status=AppointmentStatus.CANCELLED)
        cancelled = self.client.get(self.url(other))
        self.assertEqual((cancelled.status_code, cancelled.content), (404, dead))
        forged = self.url(self.online).rsplit("/", 2)[0] + "/x" + self.url(self.online).rsplit("/", 2)[1][1:] + "/"
        self.assertEqual(self.client.get(forged).content, dead)

    def test_another_patients_link_does_not_open_a_moved_booking(self):
        token_page = self.url(self.online)  # made for the patient
        Appointment.objects.filter(pk=self.online.pk).update(customer=self.other_patient)
        with mock.patch("appointments.online.now", return_value=self.start - timedelta(minutes=5)):
            page = self.client.get(token_page)
        self.assertEqual(page.status_code, 404)
        self.assertNotContains(page, "ZZCALLROOM", status_code=404)

    def test_the_page_shows_nothing_private_beyond_the_kind(self):
        for lang in ("ar", "en"):
            body = self.page(self.online, self.start - timedelta(minutes=5), lang).content.decode()
            for private in ("منى علي", "01001234567", "P-CL", self.online.number, "هالة", "استشاري", "300.00", "ليلى السرية"):
                self.assertNotIn(private, body, (lang, private))
        self.assertNotIn("data-label", body)


# ---- paying before the call --------------------------------------------------------------------


@override_settings(PUBLIC_BASE_URL=BASE)
class PrepaidTests(OnlineSetup):
    def setUp(self):
        super().setUp()
        self.location = make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        self.cashbox = make_cashbox(cashbox_code="ON-CASH", is_default=True)
        set_capability_enabled(self.profile, "deposits", True, user=self.owner)
        online.save_settings({"prepaid": "1"}, self.owner)
        self.start = at(today() + timedelta(days=1), 11)
        self.online.starts_at = self.start
        self.online.save()

    def shows(self, appointment=None, minutes=-5):
        page = self.page(appointment or self.online, self.start + timedelta(minutes=minutes))
        self.assertEqual(page.status_code, 200)
        return page, f'href="{CALL}"' in page.content.decode()

    def test_off_by_default_so_an_unpaid_booking_shows_the_button(self):
        online.save_settings({"prepaid": ""}, self.owner)
        self.assertFalse(online.prepaid_required())
        self.assertTrue(self.shows()[1])

    def test_with_no_deposit_the_button_is_absent_and_the_page_says_to_pay_first(self):
        page, shown = self.shows()
        self.assertFalse(shown)
        self.assertContains(page, 'data-call-state="unpaid"')
        self.assertContains(page, "لازم تدفع قيمة الكشف أولًا")
        self.assertNotContains(page, "data-call-pay")  # no invoice, so no pay link: the page makes none
        self.assertFalse(online.covered(self.online))

    def test_a_deposit_below_the_price_is_not_enough_and_one_equal_to_it_is(self):
        deposits.take(self.online, self.patient, self.cashbox, D("100.00"), self.owner)
        self.assertFalse(self.shows()[1])
        self.assertFalse(online.covered(Appointment.objects.get(pk=self.online.pk)))
        second = self.book_online(start=self.start + timedelta(days=1), customer=self.other_patient)  # price 300.00
        Appointment.objects.filter(pk=second.pk).update(starts_at=self.start + timedelta(hours=5))
        second.refresh_from_db()
        deposits.take(second, self.other_patient, self.cashbox, D("300.00"), self.owner)
        page = self.page(second, second.starts_at - timedelta(minutes=5))
        self.assertContains(page, f'href="{CALL}"')
        self.assertTrue(online.covered(second))
        # exact amounts at the edge: 299.99 is not covered, 300.00 is
        third = self.book_online(start=self.start + timedelta(hours=9), customer=self.other_patient)
        deposits.take(third, self.other_patient, self.cashbox, D("299.99"), self.owner)
        self.assertFalse(online.covered(third))

    def test_a_refunded_deposit_does_not_count(self):
        deposit = deposits.take(self.online, self.patient, self.cashbox, D("300.00"), self.owner)
        self.assertTrue(self.shows()[1])
        deposit.status = "refunded"
        deposit.save()
        self.assertFalse(self.shows()[1])

    def test_a_free_visit_needs_nothing_paid(self):
        free = self.book_online(start=self.start + timedelta(hours=5), price="0.00", customer=self.other_patient)
        self.assertTrue(online.covered(free))
        self.assertContains(self.page(free, free.starts_at - timedelta(minutes=5)), f'href="{CALL}"')

    def posted_invoice(self, paid_now, number):
        invoice = make_draft_sales_invoice(customer=self.patient, location=self.location, cashbox=self.cashbox if D(paid_now) else None, invoice_number=number)
        add_sales_line(invoice, self.service, 1, "300.00")
        recalculate_invoice_totals(invoice, paid_now=paid_now)
        post_sales_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        Appointment.objects.filter(pk=self.online.pk).update(invoice=invoice)
        self.online.refresh_from_db()
        return invoice

    def test_a_posted_invoice_paid_in_full_covers_the_visit_and_one_with_a_balance_does_not(self):
        invoice = self.posted_invoice("0.00", "ON-INV-1")
        self.assertEqual((invoice.status, invoice.total_amount, invoice.remaining_due), ("posted", D("300.00"), D("300.00")))
        self.assertFalse(online.covered(self.online))
        self.assertFalse(self.shows()[1])
        Appointment.objects.filter(pk=self.online.pk).update(invoice=None)
        paid = self.posted_invoice("300.00", "ON-INV-2")
        self.assertEqual((paid.status, paid.total_amount, paid.paid_now, paid.remaining_due), ("posted", D("300.00"), D("300.00"), D("0.00")))
        self.assertTrue(online.covered(self.online))
        self.assertTrue(self.shows()[1])

    def test_an_existing_pay_link_is_shown_but_the_public_page_never_makes_one(self):
        from django.utils import timezone as tz

        invoice = self.posted_invoice("0.00", "ON-INV-3")
        bank = make_cashbox(cashbox_code="ON-BANK", kind="bank", name_ar="بوابة الدفع الأونلاين")
        link = PaymentLink.objects.create(invoice=invoice, customer=self.patient, cashbox=bank, amount=D("300.00"), provider="paymob", reference="ON-REF-1",
                                          token="on-token-1", expires_at=tz.now() + timedelta(days=2), created_by=self.owner)
        before = money_counts()
        audit_before = AuditLog.objects.count()
        page, shown = self.shows()
        self.assertFalse(shown)
        self.assertContains(page, "data-call-pay")
        self.assertContains(page, f"{BASE}/pay/")
        self.assertContains(page, "ادفع أونلاين")
        self.assertEqual(money_counts(), before)  # a GET made no link and wrote no row
        self.assertEqual(AuditLog.objects.count(), audit_before)
        link.refresh_from_db()
        self.assertEqual(link.status, "new")
        PaymentLink.objects.filter(pk=link.pk).update(expires_at=tz.now() - timedelta(minutes=1))  # an expired link is not offered (and not rewritten here)
        page, _ = self.shows()
        self.assertNotContains(page, "data-call-pay")
        self.assertEqual(PaymentLink.objects.get(pk=link.pk).status, "new")

    def test_the_staff_see_the_button_whatever_the_payment_and_a_note_on_the_booking_page(self):
        self.client.force_login(self.manager)
        page = self.client.get(self.detail())
        self.assertContains(page, f'href="{CALL}"')
        self.assertContains(page, "data-prepaid-pending")
        deposits.take(self.online, self.patient, self.cashbox, D("300.00"), self.owner)
        self.assertNotContains(self.client.get(self.detail()), "data-prepaid-pending")


# ---- the reminder: it says online and never carries the call link ------------------------------


@override_settings(PUBLIC_BASE_URL=BASE)
class ReminderTests(OnlineSetup):
    def reminders(self):
        messaging.build()
        return {row.key: row for row in OutboxMessage.objects.filter(kind=MessageKind.APPOINTMENT)}

    def test_an_online_reminder_says_so_and_carries_the_confirm_link_but_never_the_call(self):
        tomorrow = at(today() + timedelta(days=1), 14)
        online_booking = self.book_online(start=tomorrow, status=AppointmentStatus.BOOKED)
        rows = self.reminders()
        text = rows[f"appointment:{online_booking.pk}"].text
        self.assertIn("الكشف ده أونلاين", text)
        self.assertIn("هتدخل المكالمة بزر من نفس الرابط", text)
        self.assertIn("لتأكيد الحضور ودخول المكالمة:", text)
        self.assertIn(f"{BASE}/share/", text)
        everything = list(OutboxMessage.objects.values_list("text", "link", "name", "phone"))
        self.assertFalse(any("ZZCALLROOM" in value or "meet.example.org" in value for row in everything for value in row))
        self.assertFalse(OutboxMessage.objects.filter(text__contains="meet.example.org").exists())
        ordinary = rows[f"appointment:{self.appointment.pk}"].text
        self.assertNotIn("أونلاين", ordinary)
        self.assertIn("لتأكيد الحضور أو طلب تغيير الميعاد:", ordinary)  # the in-clinic reminder is what it was

    def test_the_english_reminder_and_one_without_a_public_address(self):
        tomorrow = at(today() + timedelta(days=1), 14)
        booking = self.book_online(start=tomorrow, status=AppointmentStatus.BOOKED)
        with mock.patch("messaging.services.workorder_language", return_value="en"):
            text = self.reminders()[f"appointment:{booking.pk}"].text
        self.assertIn("This is an online visit: you will join the call with a button on the same link", text)
        self.assertIn("To confirm and join the call:", text)
        OutboxMessage.objects.all().delete()
        with override_settings(PUBLIC_BASE_URL=""):  # no confirm link possible: it must not promise one
            text = self.reminders()[f"appointment:{booking.pk}"].text
        self.assertIn("الكشف ده أونلاين. تواصل مع العيادة", text)
        self.assertNotIn("من نفس الرابط", text)
        self.assertNotIn("ZZCALLROOM", text)


@override_settings(PUBLIC_BASE_URL=BASE)
class ShopReminderTests(ClinicSetup):
    ACTIVITY = ("services", "maintenance")

    def test_a_shops_reminder_is_untouched_whatever_the_kind_of_its_booking(self):
        booking = self.book()
        plain = {}
        messaging.build()
        plain = OutboxMessage.objects.get(kind=MessageKind.APPOINTMENT).text
        OutboxMessage.objects.all().delete()
        Appointment.objects.filter(pk=booking.pk).update(kind=AppointmentKind.ONLINE)
        messaging.build()
        self.assertEqual(OutboxMessage.objects.get(kind=MessageKind.APPOINTMENT).text, plain)
        self.assertNotIn("أونلاين", plain)
        self.assertNotIn(BASE, plain)  # a shop's reminder has no confirm link (M3), so no online sentence about one


# ---- the public booking page's online choice -----------------------------------------------------


class PublicChoiceTests(ClinicBooking):
    def four(self, day, **query):
        return self.client.get(self.url(lang=query.pop("lang", "ar"), service=self.service.pk, staff=self.mona.pk, day=day.isoformat(), time="11:00", **query))

    def test_off_by_default_the_page_has_no_choice_and_a_posted_mode_is_ignored(self):
        day = open_day()
        self.assertNotContains(self.four(day), "data-booking-mode")
        self.assertEqual(self.post(day, mode="online").status_code, 303)
        self.assertEqual(Appointment.objects.get().kind, AppointmentKind.APPOINTMENT)

    def test_on_the_page_offers_in_the_clinic_or_online_for_every_visit(self):
        online.save_settings({"public_booking": "1"}, self.owner)
        day = open_day()
        for lang, words in (("ar", ("الكشف فين؟", "في العيادة", "أونلاين (مكالمة فيديو)")), ("en", ("Where is the visit?", "At the clinic", "Online (video call)"))):
            page = self.four(day, lang=lang)
            self.assertContains(page, "data-booking-mode-clinic")
            self.assertContains(page, "data-booking-mode-online")
            for word in words:
                self.assertContains(page, word)
        self.assertNotContains(self.client.get(self.url(service=self.service.pk)), "data-booking-mode")  # only on the details step

    def test_an_online_booking_by_the_patient_has_no_link_and_the_clinics_other_bookings_stay_in_the_clinic(self):
        online.save_settings({"public_booking": "1"}, self.owner)
        day = open_day()
        response = self.post(day, mode="online")
        self.assertEqual(response.status_code, 303)
        appointment = Appointment.objects.get()
        self.assertEqual((appointment.kind, appointment.call_url, appointment.status), (AppointmentKind.ONLINE, "", AppointmentStatus.BOOKED))
        done = self.client.get(response["Location"])
        self.assertContains(done, "كشف أونلاين")
        self.assertNotContains(done, "http")  # nothing but the clinic's own links; no call link exists yet
        self.assertEqual(self.post(day, staff=self.huda.pk, mode="clinic", name="سعاد", phone="01099998888").status_code, 303)
        self.assertEqual(Appointment.objects.exclude(pk=appointment.pk).get().kind, AppointmentKind.APPOINTMENT)


class ShopChoiceTests(BookingSetup):
    def test_a_shop_never_offers_the_choice(self):
        online.save_settings({"public_booking": "1"}, self.owner)
        day = open_day()
        url = reverse("book_page", args=[self.booking.public_key]) + f"?service={self.service.pk}&staff={self.mona.pk}&day={day.isoformat()}&time=11:00"
        self.assertNotContains(self.client.get(url), "data-booking-mode")


# ---- screens in both languages, the settings screen, money untouched -----------------------------


class ScreenTests(OnlineSetup):
    def test_every_screen_opens_in_arabic_and_english_with_no_pre_labelled_cells(self):
        self.client.force_login(self.manager)
        urls = [lambda lang: self.agenda(lang), lambda lang: self.detail(lang=lang), lambda lang: reverse("medical:queue") + f"?lang={lang}", lambda lang: reverse("appointments:online_settings") + f"?lang={lang}",
                lambda lang: f"{reverse('medical:visit_new', args=[self.patient.pk])}?appointment={self.online.pk}&lang={lang}"]
        for build in urls:
            for lang in ("ar", "en"):
                with self.subTest(url=build(lang)):
                    page = self.client.get(build(lang))
                    self.assertEqual(page.status_code, 200)
                    self.assertNotRegex(page.content.decode(), r"<td\b[^>]*\bdata-label\b")
                    self.assertContains(page, 'dir="rtl"' if lang == "ar" else 'dir="ltr"')
        self.assertContains(self.client.get(self.detail()), "اعمل لينك مكالمة" if not self.online.call_url else "اعمل لينك جديد")
        self.assertContains(self.client.get(self.detail(lang="en")), "Make a new link")
        self.assertContains(self.client.get(self.detail()), "حط لينك")
        self.assertContains(self.client.get(self.detail()), "شيل اللينك")
        self.assertContains(self.client.get(self.agenda()), "كشف أونلاين")  # the kind in the agenda row

    def test_the_booking_form_offers_online_in_a_clinic_and_keeps_an_online_booking_online(self):
        self.client.force_login(self.manager)
        page = self.client.get(self.agenda())
        self.assertContains(page, '<option value="online"')
        self.assertContains(page, "data-agenda-online-link")  # the settings screen is one click from the agenda
        english = self.client.get(self.agenda("en"))
        self.assertContains(english, "Online visit")
        self.assertContains(self.client.get(self.detail()), '<option value="online" selected>')

    def test_the_settings_screen_says_the_public_jitsi_asks_the_doctor_to_sign_in(self):
        self.client.force_login(self.manager)
        page = self.client.get(reverse("appointments:online_settings"))
        self.assertContains(page, "data-jitsi-note")
        for word in ("meet.jit.si", "Google", "Facebook", "GitHub", "الدكتور"):
            self.assertContains(page, word)
        english = self.client.get(reverse("appointments:online_settings") + "?lang=en")
        self.assertContains(english, "asks the first person who opens a room, the doctor, to sign in")
        self.assertContains(english, "paste a ready link")
        self.assertContains(page, "data-online-prepaid")
        self.assertContains(page, "data-online-public")

    def test_the_settings_screen_saves_checks_audits_and_is_for_managers_only(self):
        self.client.force_login(self.manager)
        url = reverse("appointments:online_settings")
        bad = self.client.post(url, {"lang": "ar", "base_url": "http://meet.example.org"})
        self.assertContains(bad, "data-online-settings-error")
        self.assertContains(bad, "اللينك لازم يبدأ بـ https:// بس")
        self.assertEqual(online.base_url(), "https://meet.jit.si")
        ok = self.client.post(url, {"lang": "ar", "base_url": "https://meet.clinic.example", "prepaid": "1", "public_booking": "1"})
        self.assertEqual(ok.status_code, 302)
        self.assertEqual((online.base_url(), online.prepaid_required(), online.public_choice()), ("https://meet.clinic.example", True, True))
        row = AuditLog.objects.filter(action="online_settings").latest("pk")
        self.assertEqual((row.actor, row.after_data["prepaid"], row.before_data["prepaid"]), (self.manager, True, False))
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(url, {"base_url": "https://evil.example"}).status_code, 403)
        self.client.force_login(self.doc_user)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(online.base_url(), "https://meet.clinic.example")

    def test_nothing_financial_changes_through_any_of_it(self):
        before = money_counts()
        self.client.force_login(self.manager)
        self.post("call_generate")
        self.post("call_paste", call_url="https://meet.google.com/aaa-bbbb-ccc")
        self.post("call_remove")
        self.post("call_generate")
        for url in (self.agenda(), self.detail(), reverse("medical:queue"), reverse("appointments:online_settings"),
                    f"{reverse('medical:visit_new', args=[self.patient.pk])}?appointment={self.online.pk}"):
            self.client.get(url)
        self.client.post(reverse("appointments:online_settings"), {"lang": "ar", "prepaid": "1", "public_booking": "1"})
        for minutes in (-20, -5, 60, 170):
            self.page(self.online, self.online.starts_at + timedelta(minutes=minutes))
        self.client.force_login(self.doc_user)
        self.client.get(reverse("medical:queue"))
        self.client.get(f"{reverse('medical:visit_new', args=[self.patient.pk])}?appointment={self.online.pk}")
        self.assertEqual(money_counts(), before)


# ---- the migration and the demo ------------------------------------------------------------------


class ShopSettingsTests(BookingSetup):
    def test_the_settings_screen_is_for_medical_activities_only(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("appointments:online_settings")).status_code, 404)
        self.assertEqual(self.client.post(reverse("appointments:online_settings"), {"prepaid": "1"}).status_code, 404)
        self.assertFalse(online.prepaid_required())
        agenda = self.client.get(reverse("appointments:agenda"))
        self.assertNotContains(agenda, "data-agenda-online-link")
        self.assertNotContains(agenda, '<option value="online"')  # a shop is never offered the kind


class MigrationTests(TransactionTestCase):
    """0004 adds one choice and one column after 0003; the bookings that exist keep every value and get no call link."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_depends_on_0003_adds_one_column_and_one_choice_and_keeps_the_data(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("appointments", "0004_online_consultation")]
        self.assertIn(("appointments", "0003_online_booking"), node.dependencies)
        self.assertEqual([type(op).__name__ for op in node.operations], ["AddField", "AlterField"])
        executor.migrate([("appointments", "0003_online_booking")])
        old = executor.loader.project_state([n for n in executor.loader.graph.leaf_nodes() if n[0] != "appointments"] + [("appointments", "0003_online_booking")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_m10")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="P-MIG", name="منى")
        old.get_model("appointments", "Appointment").objects.create(
            number="AP-MIG-1", customer=customer, kind="visit", address="شارع النيل 5", price=D("300.00"), starts_at=timezone.now() + timedelta(days=1), notes="قبل الإضافة",
            status="confirmed", created_by=user)
        old.get_model("appointments", "Appointment").objects.create(number="AP-MIG-2", customer=customer, price=D("0.00"), starts_at=timezone.now() + timedelta(days=2), created_by=user)
        self.assertNotIn("call_url", [column.name for column in connection.introspection.get_table_description(connection.cursor(), "appointments_appointment")])
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("appointments", "0004_online_consultation")])
        rows = {row.number: row for row in Appointment.objects.all()}
        self.assertEqual({row.call_url for row in rows.values()}, {""})  # blank by default, nothing invented
        self.assertEqual((rows["AP-MIG-1"].kind, rows["AP-MIG-1"].address, rows["AP-MIG-1"].price, rows["AP-MIG-1"].notes, rows["AP-MIG-1"].status),
                         ("visit", "شارع النيل 5", D("300.00"), "قبل الإضافة", "confirmed"))
        self.assertEqual(rows["AP-MIG-2"].kind, "appointment")
        self.assertIn("online", [value for value, _ in Appointment._meta.get_field("kind").choices])
        Appointment.objects.filter(number="AP-MIG-2").update(kind="online", call_url=CALL)  # and the new values fit the column
        self.assertEqual(Appointment.objects.get(number="AP-MIG-2").call_url, CALL)


class MakeMigrationsCleanTests(TestCase):
    databases = {"default", "feedback"}  # makemigrations reads the history of every database

    def test_one_choice_and_one_column_are_the_only_change_to_the_schema(self):
        from django.core.management import call_command

        call_command("makemigrations", "--check", "--dry-run", verbosity=0)


class DemoSeedTests(TestCase):
    """The clinic demo as ``prepare_demo_templates`` builds it: two online bookings today, one inside its join window with a generated link."""

    def test_the_demo_clinic_has_two_online_bookings_one_in_the_window_and_one_with_no_link(self):
        from django.contrib.auth import get_user_model

        from settings_core.tests_demo_clinic import DemoClinicBookingTests

        DemoClinicBookingTests("fill").fill("medical", "clinic")
        rows = list(Appointment.objects.filter(kind=AppointmentKind.ONLINE).order_by("starts_at"))
        self.assertEqual(len(rows), 2)
        first, second = rows
        self.assertEqual((first.status, bool(first.call_url)), (AppointmentStatus.CONFIRMED, True))
        self.assertTrue(first.call_url.startswith("https://meet.jit.si/"))
        self.assertEqual((second.status, second.call_url), (AppointmentStatus.BOOKED, ""))
        self.assertLess(second.starts_at - first.starts_at, timedelta(hours=3))
        self.assertGreater(second.starts_at, first.starts_at)
        owner = get_user_model().objects.get(username="owner")
        # the first is inside the patient's window the moment the demo is built
        self.assertIsNotNone(online.page_box(first, "ar", timezone.now()))
        self.assertEqual(online.page_box(first, "ar", timezone.now())["state"], "open")
        self.assertEqual(online.page_box(second, "ar", timezone.now())["state"], "unconfirmed")
        self.assertEqual(AuditLog.objects.filter(action="set_call_link", object_id=str(first.pk)).count(), 1)
        from settings_core import demo_online

        master = {"items": [], "customers": []}
        self.assertFalse(demo_online.seed(master, None, owner, None, {"appointments"}))  # idempotent / safe
