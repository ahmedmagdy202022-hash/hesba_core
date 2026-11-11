"""M3: the patient's appointment link: the page shows the minimum, «confirm» works once, «ask to cancel» is only a request
reception sees, a dead link is always the same page, and no money moves from the public page."""

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from unittest import mock

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from deposits import services as deposits
from deposits.models import Deposit, DepositStatus
from entities.models import Entity
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from ledger.models import JournalEntry
from medical.models import ClinicalVisit
from permissions.models import RoleCode
from printing.share import make_token
from reports.tests_dashboard import prepared_client
from sales.models import CustomerPayment, SalesInvoice
from settings_core.capabilities import set_capability_enabled
from staff.services import save_employee

from . import services
from .models import Appointment, AppointmentStatus

CLINIC = "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians"
SECRET = "SECRET-DIAGNOSIS-XYZ"


def today():
    return timezone.localdate()


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class LinkSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client("medical", "clinic", CLINIC)
        self.owner = person(RoleCode.OWNER, "cl_owner")
        self.doctor = save_employee({"name": "د. هالة الاستشاري", "title": "استشاري قلب"}, self.owner)
        self.patient = make_customer(customer_code="P-CL", name="منى علي", phone="01001234567")
        self.other_patient = make_customer(customer_code="P-OT", name="ليلى السرية", phone="01099990000")
        self.service = make_item(item_code="SRV-KSH", item_name="كشف جديد", default_sale_price="300.00", is_stock_tracked=False)
        self.appointment = self.make(self.patient, 11)
        self.other = self.make(self.other_patient, 12)
        ClinicalVisit.objects.create(patient=self.patient, visit_date=today() - timedelta(days=3), diagnosis=SECRET, complaint=SECRET, created_by=self.owner)

    def make(self, customer, hour, day=None, status=AppointmentStatus.BOOKED, **extra):
        return Appointment.objects.create(
            number=f"AP-CL-{Appointment.objects.count() + 1}", customer=customer, employee=self.doctor, service=self.service, price=D("300.00"),
            starts_at=at(day or today() + timedelta(days=1), hour), status=status, created_by=self.owner, **extra)

    def url(self, appointment=None, lang=None):
        link = reverse("share_document", args=[make_token("appointment", (appointment or self.appointment).pk)])
        return f"{link}?lang={lang}" if lang else link

    def post(self, name, appointment=None, **extra):
        token = make_token("appointment", (appointment or self.appointment).pk)
        return self.client.post(reverse(name, args=[token]), {"lang": "ar"}, **extra)

    def audit(self, action):
        return AuditLog.objects.filter(module="appointments", action=action, object_id=str(self.appointment.pk))

    def refresh(self):
        self.appointment.refresh_from_db()
        return self.appointment.status


class PageTests(LinkSetup):
    def test_the_page_shows_the_clinic_the_time_and_the_service_and_changes_nothing(self):
        before = AuditLog.objects.count()
        page = self.client.get(self.url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-appointment-page")
        self.assertContains(page, "Demo Store")  # the clinic
        self.assertContains(page, "كشف جديد")  # the service's name
        self.assertContains(page, "11:00")
        self.assertContains(page, "data-confirm-appointment")
        self.assertContains(page, "طلب إلغاء")
        self.assertEqual(self.refresh(), AppointmentStatus.BOOKED)  # a link preview that opens it changes nothing
        self.assertEqual(AuditLog.objects.count(), before)
        self.assertEqual(page["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("no-store", page["Cache-Control"])
        self.assertEqual(page["Referrer-Policy"], "same-origin")

    def test_the_page_never_shows_the_patient_the_doctor_the_diagnosis_the_number_or_anyone_else(self):
        for lang in ("ar", "en"):
            body = self.client.get(self.url(lang=lang)).content.decode()
            for private in ("منى علي", "01001234567", "201001234567", SECRET, "هالة", "استشاري", self.appointment.number, "P-CL", "P-00001",
                            "ليلى السرية", "01099990000", self.other.number, "12:00", "300.00"):
                self.assertNotIn(private, body, (lang, private))

    def test_arabic_first_and_english_second(self):
        arabic = self.client.get(self.url())
        self.assertContains(arabic, 'dir="rtl"')
        self.assertContains(arabic, "أكّد حضورك")
        english = self.client.get(self.url(lang="en"))
        self.assertContains(english, 'dir="ltr"')
        self.assertContains(english, "Confirm you are coming")
        self.assertContains(english, "Ask to cancel")
        self.assertContains(english, "11:00")

    def test_the_page_pre_fills_no_table_labels(self):
        body = self.client.get(self.url()).content.decode()
        self.assertNotIn("data-label", body)

    def test_a_group_of_clinics_names_the_one_the_appointment_is_in(self):
        branch = Entity.objects.create(code="CL2", name_ar="عيادة المعادي", name_en="Maadi Clinic", activity_slug="medical", sub_activity_slug="clinic")
        Appointment.objects.filter(pk=self.appointment.pk).update(entity=branch)
        self.assertContains(self.client.get(self.url()), "عيادة المعادي")
        self.assertContains(self.client.get(self.url(lang="en")), "Maadi Clinic")


class ConfirmTests(LinkSetup):
    def test_confirming_moves_booked_to_confirmed_once_and_is_idempotent(self):
        first = self.post("share_appointment_confirm")
        self.assertEqual(first.status_code, 303)
        self.assertEqual(first["Location"], f"{reverse('share_document', args=[make_token('appointment', self.appointment.pk)])}?lang=ar")
        self.assertEqual(self.refresh(), AppointmentStatus.CONFIRMED)
        rows = self.audit("appointment_status")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.get().after_data, {"status": "confirmed"})
        self.assertFalse(rows.get().actor.is_active)  # the booking system user, who cannot sign in
        again = self.post("share_appointment_confirm")
        self.assertEqual(again.status_code, 303)
        self.assertEqual(self.refresh(), AppointmentStatus.CONFIRMED)
        self.assertEqual(self.audit("appointment_status").count(), 1)  # no second write
        page = self.client.get(self.url())
        self.assertContains(page, 'data-banner="confirmed"')
        self.assertNotContains(page, "data-confirm-appointment")  # nothing left to confirm

    def test_confirm_is_post_only_so_a_link_preview_cannot_confirm(self):
        token = make_token("appointment", self.appointment.pk)
        self.assertEqual(self.client.get(reverse("share_appointment_confirm", args=[token])).status_code, 405)
        self.assertEqual(self.client.get(reverse("share_appointment_cancel_request", args=[token])).status_code, 405)
        self.assertEqual(self.refresh(), AppointmentStatus.BOOKED)

    def test_the_posts_are_csrf_protected(self):
        strict = Client(enforce_csrf_checks=True)
        token = make_token("appointment", self.appointment.pk)
        for name in ("share_appointment_confirm", "share_appointment_cancel_request"):
            self.assertEqual(strict.post(reverse(name, args=[token])).status_code, 403)
        page = strict.get(self.url())  # the page sets the cookie and carries the token the forms post with
        self.assertContains(page, "csrfmiddlewaretoken")
        self.assertEqual(self.refresh(), AppointmentStatus.BOOKED)
        self.assertEqual(self.audit("appointment_cancel_requested").count(), 0)

    def test_it_confirms_only_the_appointment_the_link_is_for(self):
        self.post("share_appointment_confirm")
        self.other.refresh_from_db()
        self.assertEqual(self.other.status, AppointmentStatus.BOOKED)

    def test_a_confirmation_moves_no_money_and_makes_no_invoice(self):
        counts = (SalesInvoice.objects.count(), CustomerPayment.objects.count(), CashboxMovement.objects.count(), JournalEntry.objects.count())
        self.post("share_appointment_confirm")
        self.assertEqual((SalesInvoice.objects.count(), CustomerPayment.objects.count(), CashboxMovement.objects.count(), JournalEntry.objects.count()), counts)

    def test_reception_sees_it_confirmed_on_the_agenda(self):
        self.post("share_appointment_confirm")
        self.client.force_login(self.owner)
        agenda = self.client.get(reverse("appointments:agenda") + f"?day={(today() + timedelta(days=1)).isoformat()}")
        self.assertContains(agenda, f'data-appointment-row="{self.appointment.number}"')
        self.assertEqual(Appointment.objects.get(pk=self.appointment.pk).status, AppointmentStatus.CONFIRMED)


class CancelRequestTests(LinkSetup):
    def test_asking_to_cancel_is_only_a_request_and_it_is_recorded_once(self):
        response = self.post("share_appointment_cancel_request")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.refresh(), AppointmentStatus.BOOKED)  # nothing was cancelled
        self.assertEqual(self.audit("appointment_cancel_requested").count(), 1)
        self.post("share_appointment_cancel_request")
        self.assertEqual(self.audit("appointment_cancel_requested").count(), 1)  # asking twice records once
        self.assertEqual(self.audit("appointment_cancel_requested").get().after_data, {"number": self.appointment.number, "status": "booked"})  # no personal data
        page = self.client.get(self.url())
        self.assertContains(page, 'data-banner="requested"')
        self.assertNotContains(page, "data-appointment-actions")  # no buttons left: the clinic will call
        self.assertNotContains(page, "<form")

    def test_a_confirmed_patient_may_still_ask_and_a_pending_request_blocks_a_later_confirm(self):
        self.post("share_appointment_confirm")
        self.post("share_appointment_cancel_request")
        self.assertEqual(self.refresh(), AppointmentStatus.CONFIRMED)
        self.assertEqual(self.audit("appointment_cancel_requested").count(), 1)
        other_status_rows = self.audit("appointment_status").count()
        self.post("share_appointment_confirm")
        self.assertEqual(self.audit("appointment_status").count(), other_status_rows)

    def test_a_request_never_moves_money_even_with_a_held_deposit(self):
        set_capability_enabled(self.profile, "deposits", True, user=self.owner)
        make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        cashbox = make_cashbox(cashbox_code="CL-CASH", is_default=True)
        deposit = deposits.take(self.appointment, self.patient, cashbox, D("100.00"), self.owner)
        counts = (SalesInvoice.objects.count(), CustomerPayment.objects.count(), CashboxMovement.objects.count(), JournalEntry.objects.count(), Deposit.objects.count())
        self.post("share_appointment_cancel_request")
        self.post("share_appointment_confirm")
        self.assertEqual((SalesInvoice.objects.count(), CustomerPayment.objects.count(), CashboxMovement.objects.count(), JournalEntry.objects.count(),
                          Deposit.objects.count()), counts)
        deposit.refresh_from_db()
        self.assertEqual(deposit.status, DepositStatus.HELD)  # still the patient's money, held until reception decides
        self.assertEqual(self.refresh(), AppointmentStatus.BOOKED)

    def test_the_page_mentions_no_deposit_and_no_amount(self):
        body = self.client.get(self.url()).content.decode()
        for word in ("عربون", "deposit", "جنيه"):
            self.assertNotIn(word, body)


class ReceptionFlagTests(LinkSetup):
    def agenda(self):
        return self.client.get(reverse("appointments:agenda") + f"?day={(today() + timedelta(days=1)).isoformat()}")

    def detail(self, lang="ar"):
        return self.client.get(reverse("appointments:detail", args=[self.appointment.pk]) + f"?lang={lang}")

    def test_reception_sees_a_quiet_flag_on_the_agenda_row_and_on_the_appointment(self):
        self.client.force_login(self.owner)
        self.assertNotContains(self.agenda(), "data-cancel-asked")
        self.assertNotContains(self.detail(), "data-cancel-asked")
        self.post("share_appointment_cancel_request")
        agenda = self.agenda()
        self.assertContains(agenda, "data-cancel-asked", count=1)  # this appointment only, not the other patient's
        self.assertContains(agenda, "المريض طلب إلغاء الميعاد")
        self.assertContains(self.detail(), "data-cancel-asked")
        self.assertContains(self.detail("en"), "The patient asked to cancel")
        self.assertContains(self.detail(), "لسه محجوز ومتلغاش")  # it says nothing was cancelled

    def test_reception_marks_it_handled_without_changing_the_appointment(self):
        self.client.force_login(self.owner)
        self.post("share_appointment_cancel_request")
        response = self.client.post(reverse("appointments:detail", args=[self.appointment.pk]), {"lang": "ar", "action": "cancel_seen"})
        self.assertEqual(response.status_code, 302)
        self.assertNotContains(self.detail(), "data-cancel-asked")
        self.assertNotContains(self.agenda(), "data-cancel-asked")
        self.assertEqual(self.refresh(), AppointmentStatus.BOOKED)
        self.assertEqual(self.audit("appointment_cancel_request_handled").count(), 1)
        self.client.logout()
        self.post("share_appointment_cancel_request")  # asking again after being answered is a new request
        self.client.force_login(self.owner)
        self.assertContains(self.agenda(), "data-cancel-asked", count=1)

    def test_reception_cancelling_or_moving_it_clears_the_flag(self):
        self.client.force_login(self.owner)
        self.post("share_appointment_cancel_request")
        self.client.post(reverse("appointments:detail", args=[self.appointment.pk]), {"lang": "ar", "action": "status", "status": "cancelled"})
        self.assertEqual(self.refresh(), AppointmentStatus.CANCELLED)
        self.assertNotContains(self.agenda(), "data-cancel-asked")
        self.assertNotIn(self.appointment.pk, services.pending_cancel_requests([self.appointment.pk]))

    def test_a_status_changed_without_an_audit_row_leaves_no_stale_flag(self):
        self.post("share_appointment_cancel_request")
        self.assertEqual(services.pending_cancel_requests([self.appointment.pk]), {self.appointment.pk})
        for status in (AppointmentStatus.ARRIVED, AppointmentStatus.DONE, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
            Appointment.objects.filter(pk=self.appointment.pk).update(status=status)
            self.assertEqual(services.pending_cancel_requests([self.appointment.pk]), set(), status)

    def test_the_flag_follows_the_move_of_the_appointment(self):
        self.post("share_appointment_cancel_request")
        services.move(self.appointment, self.owner, at(today() + timedelta(days=2), 15))
        self.assertEqual(services.pending_cancel_requests([self.appointment.pk]), set())

    def test_the_patient_cannot_clear_it_and_a_stranger_without_the_right_cannot_mark_it(self):
        viewer = make_user(username="cl_viewer")
        from hesba_testing.factories import grant, make_role
        from permissions.models import Permission

        role = make_role("ROLE-cl-viewer")
        grant(role, Permission.objects.get(code="sales.view_sales_invoices"))
        make_user_profile(user=viewer, role=role)
        self.post("share_appointment_cancel_request")
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(reverse("appointments:detail", args=[self.appointment.pk]), {"lang": "ar", "action": "cancel_seen"}).status_code, 403)
        self.assertIn(self.appointment.pk, services.pending_cancel_requests([self.appointment.pk]))
        page = self.detail()
        self.assertContains(page, "data-cancel-asked")
        self.assertNotContains(page, "data-cancel-seen")  # a view-only user is shown the flag but no button

    def test_the_agenda_asks_for_the_requests_in_one_query(self):
        self.client.force_login(self.owner)
        self.agenda()
        with mock.patch.object(services, "pending_cancel_requests", wraps=services.pending_cancel_requests) as spy:
            self.agenda()
        self.assertEqual(spy.call_count, 1)


class DeadLinkTests(LinkSetup):
    def dead_tokens(self):
        yield "forged", "not-a-token"
        yield "altered", make_token("appointment", self.appointment.pk)[:-3] + "xyz"
        yield "missing appointment", make_token("appointment", 999999)
        for status in (AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW, AppointmentStatus.ARRIVED, AppointmentStatus.DONE):
            gone = self.make(self.patient, 9, day=today() + timedelta(days=2), status=status)
            yield status, make_token("appointment", gone.pk)
        past = self.make(self.patient, 9, day=today() - timedelta(days=1))
        yield "past", make_token("appointment", past.pk)

    def test_every_failure_is_the_same_expired_page_with_the_same_status_on_get_and_post(self):
        seen = set()
        for label, token in self.dead_tokens():
            for lang in ("ar", "en"):
                get = self.client.get(reverse("share_document", args=[token]) + f"?lang={lang}")
                self.assertEqual(get.status_code, 404, label)
                self.assertContains(get, "data-share-expired", status_code=404)
                seen.add((lang, "get", get.content))
                for name in ("share_appointment_confirm", "share_appointment_cancel_request"):
                    post = self.client.post(reverse(name, args=[token]), {"lang": lang})
                    self.assertEqual(post.status_code, 404, (label, name))
                    self.assertContains(post, "data-share-expired", status_code=404)
                    seen.add((lang, "post", post.content))
        self.assertEqual(len(seen), 4)  # one body per language, whatever the reason and whichever endpoint
        self.assertEqual(self.refresh(), AppointmentStatus.BOOKED)
        self.assertEqual(AuditLog.objects.filter(action="appointment_cancel_requested").count(), 0)

    def test_another_documents_token_on_the_appointment_endpoints_is_the_same_expired_page(self):
        wrong = make_token("work_order", self.appointment.pk)
        for name in ("share_appointment_confirm", "share_appointment_cancel_request"):
            response = self.client.post(reverse(name, args=[wrong]), {"lang": "ar"})
            self.assertEqual(response.status_code, 404)
            self.assertContains(response, "data-share-expired", status_code=404)
        self.assertEqual(self.refresh(), AppointmentStatus.BOOKED)

    def test_an_expired_token_is_dead_too(self):
        with mock.patch("printing.share.max_age", return_value=-1):
            self.assertEqual(self.client.get(self.url()).status_code, 404)
            self.assertEqual(self.post("share_appointment_confirm").status_code, 404)
        self.assertEqual(self.refresh(), AppointmentStatus.BOOKED)

    def test_the_dead_page_knows_no_patient_and_no_appointment(self):
        gone = self.make(self.patient, 9, day=today() + timedelta(days=2), status=AppointmentStatus.CANCELLED)
        body = self.client.get(self.url(gone)).content.decode()
        for private in ("منى علي", "01001234567", gone.number, "كشف جديد", "SECRET"):
            self.assertNotIn(private, body)

    def test_a_link_dies_when_the_appointment_is_cancelled_after_the_message(self):
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        Appointment.objects.filter(pk=self.appointment.pk).update(status=AppointmentStatus.CANCELLED)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.post("share_appointment_confirm").status_code, 404)

    def test_a_work_order_link_still_works_beside_it(self):
        # the dispatcher's other kinds are untouched: a document kind without an object is still its own 404
        token = make_token("sales_invoice", 999999)
        self.assertEqual(self.client.get(reverse("share_document", args=[token])).status_code, 404)
