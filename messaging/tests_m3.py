"""M3: automatic appointment and follow-up reminders: the follow-up kind, the hourly endpoint, the clinic's appointment
reminder with the patient's link, and who may hold that link."""

import os
from datetime import datetime, time, timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from appointments.models import Appointment, AppointmentStatus
from entities.models import Entity
from entities.services import main_entity
from hesba_testing.factories import grant, make_customer, make_item, make_role, make_seeded_role, make_user, make_user_profile
from medical.models import ClinicalVisit
from permissions.models import Permission, RoleCode
from reports.tests_dashboard import prepared_client
from staff.services import save_employee

from . import services, whatsapp
from .models import MessageKind, MessageTemplate, OutboxMessage, OutboxStatus

CLINIC = "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians"
ENV = {"WHATSAPP_TOKEN": "test-token", "WHATSAPP_PHONE_NUMBER_ID": "1234567890"}
SECRET = "ورم خبيث في الرئة SECRET-DIAGNOSIS"
BASE = "https://hesba.test"


def today():
    return timezone.localdate()


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class Graph:
    """A stand-in for WhatsApp: records the calls; no network anywhere."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, body, token):
        self.calls.append(body)
        return 200, {"messages": [{"id": f"wamid.{len(self.calls)}"}]}


class ClinicSetup(TestCase):
    ACTIVITY = ("medical", "clinic")

    def setUp(self):
        self.profile = prepared_client(*self.ACTIVITY, CLINIC)
        self.owner = person(RoleCode.OWNER, "m3_owner")
        self.doctor = save_employee({"name": "د. هالة", "title": "استشاري قلب"}, self.owner)
        self.patient = make_customer(customer_code="P-M3", name="منى علي", phone="01001234567")

    def visit(self, patient=None, follow_in=1, appointment=None, days_ago=7, doctor=None, **extra):
        return ClinicalVisit.objects.create(
            patient=patient or self.patient, doctor=doctor or self.doctor, visit_date=today() - timedelta(days=days_ago), complaint="شكوى SECRET-COMPLAINT",
            diagnosis=SECRET, treatment="علاج SECRET-TREATMENT", follow_up_date=today() + timedelta(days=follow_in) if follow_in is not None else None,
            appointment=appointment, created_by=self.owner, **extra)

    def turn_on(self, kind=MessageKind.FOLLOWUP, **extra):
        return services.save_template(kind, {"text": services.template(kind).text, "active": "1", "every_days": str(services.DEFAULT_EVERY[kind]), **extra}, self.owner)

    def followups(self):
        return OutboxMessage.objects.filter(kind=MessageKind.FOLLOWUP)

    def book(self, customer=None, day=None, entity=None, status=AppointmentStatus.BOOKED, hour=11):
        return Appointment.objects.create(
            number=f"AP-M3-{Appointment.objects.count() + 1}", customer=customer or self.patient, employee=self.doctor,
            starts_at=at(day or today() + timedelta(days=1), hour), status=status, entity=entity, created_by=self.owner)


class FollowUpGatheringTests(ClinicSetup):
    def test_it_is_off_until_the_owner_switches_it_on(self):
        self.visit()
        self.assertFalse(services.template(MessageKind.FOLLOWUP).active)
        self.assertEqual(services.build(), 0)
        self.turn_on()
        self.assertEqual(services.build(), 1)
        services.save_template(MessageKind.FOLLOWUP, {"text": services.template(MessageKind.FOLLOWUP).text, "every_days": "7"}, self.owner)  # unticked: off
        services.build()
        self.assertEqual(self.followups().count(), 0)  # nothing of this kind is left to send

    def test_the_patient_gets_one_message_with_the_clinic_and_the_date_and_nothing_clinical(self):
        self.visit()
        self.turn_on()
        self.assertEqual(services.build(), 1)
        message = self.followups().get()
        self.assertEqual((message.phone, message.status, message.name), ("201001234567", OutboxStatus.PENDING, "منى علي"))
        self.assertIn("Demo Store", message.text)
        self.assertIn((today() + timedelta(days=1)).isoformat(), message.text)
        for secret in ("SECRET", "ورم", "استشاري", "هالة", "قلب"):
            self.assertNotIn(secret, message.text)
        self.assertEqual(message.link, f"/appointments/?day={(today() + timedelta(days=1)).isoformat()}")  # reception's agenda, not a clinical page
        self.assertEqual(services.build(), 0)  # the same day again adds nothing

    def test_an_edited_template_cannot_pull_clinical_text_in(self):
        for word in ("{diagnosis}", "{treatment}", "{doctor}", "{complaint}"):
            with self.assertRaises(ValidationError):
                services.save_template(MessageKind.FOLLOWUP, {"text": f"أهلاً {word}", "active": "1", "every_days": "7"}, self.owner)

    def test_the_english_business_gets_the_english_wording(self):
        self.profile.default_language = "en"
        self.profile.save()
        self.visit()
        self.turn_on()
        services.build()
        text = self.followups().get().text
        self.assertIn("Hello", text)
        self.assertIn("Demo Store", text)
        self.assertNotIn("SECRET", text)

    def test_only_a_follow_up_close_enough_is_reminded(self):
        self.visit(follow_in=services.FOLLOWUP_AHEAD_DAYS + 1)
        self.turn_on()
        self.assertEqual(services.build(), 0)
        self.assertEqual(services.build(today() + timedelta(days=1)), 1)  # now it is within the lead

    def test_each_follow_up_is_reminded_once_even_after_the_repeat_days(self):
        self.visit(follow_in=2)
        self.turn_on(every_days="1")
        self.assertEqual(services.build(), 1)
        services.mark(self.followups().get(), OutboxStatus.SENT, self.owner)
        self.assertEqual(services.build(today() + timedelta(days=1)), 0)  # every_days is 1, yet this follow-up was sent
        self.assertEqual(self.followups().count(), 1)

    def test_a_skipped_one_is_not_queued_again_the_next_day(self):
        self.visit(follow_in=2)
        self.turn_on(every_days="1")
        services.build()
        services.mark(self.followups().get(), OutboxStatus.SKIPPED, self.owner)
        self.assertEqual(services.build(today() + timedelta(days=1)), 0)

    def test_a_new_follow_up_date_is_a_new_reminder(self):
        first = self.visit(follow_in=2)
        self.turn_on(every_days="1")
        services.build()
        services.mark(self.followups().get(), OutboxStatus.SENT, self.owner)
        newer = self.visit(days_ago=0, follow_in=None)  # seen again, no further follow-up asked
        ClinicalVisit.objects.filter(pk=newer.pk).update(follow_up_date=today() + timedelta(days=3))
        self.assertEqual(services.build(today() + timedelta(days=1)), 1)
        self.assertEqual(self.followups().count(), 2)
        self.assertEqual({row.key.split(":")[1] for row in self.followups()}, {str(first.pk), str(newer.pk)})

    def test_a_patient_already_booked_is_not_reminded_and_a_booking_withdraws_the_waiting_message(self):
        self.visit()
        self.turn_on()
        services.build()
        self.assertEqual(self.followups().count(), 1)
        self.book(day=today() + timedelta(days=2))
        services.build()
        self.assertEqual(self.followups().count(), 0)

    def test_a_booking_in_another_entity_also_stops_it(self):
        other = Entity.objects.create(code="CL2", name_ar="عيادة ٢", activity_slug="medical", sub_activity_slug="clinic")
        self.visit()
        self.turn_on()
        self.book(day=today() + timedelta(days=2), entity=other)
        self.assertEqual(services.build(), 0)

    def test_nobody_without_a_number_and_nobody_deactivated(self):
        quiet = make_customer(customer_code="P-NO", name="من غير رقم", phone="")
        gone = make_customer(customer_code="P-GONE", name="موقوفة", phone="01009998888")
        self.visit(patient=quiet)
        self.visit(patient=gone)
        type(gone).objects.filter(pk=gone.pk).update(active=False)
        self.turn_on()
        self.assertEqual(services.build(), 0)

    def test_only_the_latest_visit_decides(self):
        self.visit(follow_in=2, days_ago=10)
        self.visit(follow_in=None, days_ago=0)  # seen since: the old follow-up is answered
        self.turn_on()
        self.assertEqual(services.build(), 0)

    def test_a_follow_up_whose_date_passed_is_withdrawn_before_anyone_sends_it(self):
        self.visit(follow_in=1)
        self.turn_on()
        services.build()
        self.assertEqual(self.followups().count(), 1)
        services.build(today() + timedelta(days=2))
        self.assertEqual(self.followups().count(), 0)


class NotMedicalTests(TestCase):
    def test_a_shop_never_gets_follow_up_messages_or_the_template(self):
        prepared_client("commercial", "retail")
        owner = person(RoleCode.OWNER, "m3_shop_owner")
        patient = make_customer(customer_code="P-SHOP", name="زبون", phone="01001234567")
        ClinicalVisit.objects.create(patient=patient, visit_date=today() - timedelta(days=5), follow_up_date=today() + timedelta(days=1), created_by=owner)
        services.save_template(MessageKind.FOLLOWUP, {"text": services.template(MessageKind.FOLLOWUP).text, "active": "1", "every_days": "7"}, owner)
        self.assertEqual(services.build(), 0)
        self.assertNotIn(MessageKind.FOLLOWUP, [row.kind for row in services.templates()])

    def test_the_clinic_sees_the_template_to_edit(self):
        prepared_client("medical", "clinic", CLINIC)
        self.assertIn(MessageKind.FOLLOWUP, [row.kind for row in services.templates()])


class TwoClinicsTests(ClinicSetup):
    def setUp(self):
        super().setUp()
        self.second = Entity.objects.create(code="CL2", name_ar="عيادة ٢", name_en="Clinic 2", activity_slug="medical", sub_activity_slug="clinic")
        self.turn_on()

    def test_a_patient_in_a_group_of_two_clinics_is_reminded_once(self):
        self.visit()
        self.assertEqual(services.build(), 1)
        self.assertEqual(self.followups().count(), 1)
        self.assertEqual(self.followups().get().entity, main_entity())  # no appointment: the first medical entity
        self.assertEqual(services.build(), 0)

    def test_a_visit_from_the_second_clinics_appointment_is_that_clinics_message_and_names_it(self):
        appointment = self.book(entity=self.second, day=today() - timedelta(days=7), status=AppointmentStatus.DONE)
        self.visit(appointment=appointment)
        self.assertEqual(services.build(), 1)
        message = self.followups().get()
        self.assertEqual(message.entity, self.second)
        self.assertIn("عيادة ٢", message.text)  # several entities: the clinic's own name
        self.assertNotIn("SECRET", message.text)

    def test_a_non_medical_main_entity_leaves_it_to_the_first_medical_one(self):
        Entity.objects.filter(pk=main_entity().pk).update(activity_slug="commercial", sub_activity_slug="retail")
        self.visit()
        self.assertEqual(services.build(), 1)
        self.assertEqual(self.followups().get().entity, self.second)


class ScheduledRunTests(ClinicSetup):
    def setUp(self):
        super().setUp()
        self.book()
        self.turn_on(MessageKind.APPOINTMENT, wa_template="hesba_reminder")

    def test_without_keys_the_messages_only_wait_in_the_outbox(self):
        with mock.patch.dict(os.environ, {"WHATSAPP_TOKEN": "", "WHATSAPP_PHONE_NUMBER_ID": ""}), mock.patch.object(whatsapp, "_http") as http:
            result = services.run_scheduled(at(today(), 12))
        http.assert_not_called()
        self.assertEqual(result, {"status": "gathered", "added": 1, "sent": 0, "failed": 0, "left": 0})
        message = OutboxMessage.objects.get()
        self.assertEqual((message.kind, message.status), (MessageKind.APPOINTMENT, OutboxStatus.PENDING))  # ready for a click
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("messaging:outbox")), 'data-kind="appointment"')

    def test_with_keys_it_sends_inside_the_sending_hours_through_a_mocked_provider(self):
        graph = Graph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):
            result = services.run_scheduled(at(today(), 12))
        self.assertEqual((result["status"], result["added"], result["sent"], result["failed"]), ("sent", 1, 1, 0))
        message = OutboxMessage.objects.get()
        self.assertEqual((message.status, message.channel), (OutboxStatus.SENT, "api"))
        self.assertEqual(len(graph.calls), 1)
        self.assertEqual(graph.calls[0]["to"], "201001234567")

    def test_it_never_sends_at_night_but_still_gathers(self):
        graph = Graph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):
            for hour in (0, 3, services.SEND_FROM_HOUR - 1, services.SEND_UNTIL_HOUR, 23):
                result = services.run_scheduled(at(today(), hour))
                self.assertEqual((result["status"], result["sent"]), ("outside_hours", 0), hour)
        self.assertEqual(graph.calls, [])
        self.assertEqual(OutboxMessage.objects.get().status, OutboxStatus.PENDING)
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):
            self.assertEqual(services.run_scheduled(at(today(), services.SEND_FROM_HOUR))["sent"], 1)

    def test_a_refused_message_stays_waiting_for_a_click_and_a_second_run_does_not_double_send(self):
        refuse = mock.Mock(return_value=(400, {"error": {"code": 131030, "message": "not allowed"}}))
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", refuse):
            result = services.run_scheduled(at(today(), 12))
        self.assertEqual((result["sent"], result["failed"]), (0, 1))
        self.assertEqual(OutboxMessage.objects.get().status, OutboxStatus.PENDING)
        graph = Graph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):
            services.run_scheduled(at(today(), 13))
            services.run_scheduled(at(today(), 14))
        self.assertEqual(len(graph.calls), 1)


@override_settings(NIGHTLY_TOKEN="t0ken-for-m3")
class EndpointGuardTests(ClinicSetup):
    URL = "/ops/messages/"

    def setUp(self):
        super().setUp()
        self.book()
        self.turn_on(MessageKind.APPOINTMENT)

    def test_the_route_exists_and_is_named(self):
        self.assertEqual(reverse("ops_messages"), self.URL)

    def test_no_token_or_a_wrong_token_is_a_plain_404_and_does_nothing(self):
        with mock.patch.object(services, "run_scheduled") as run:
            for kwargs in ({}, {"HTTP_X_HESBA_TOKEN": "wrong"}, {"HTTP_X_HESBA_TOKEN": ""}):
                for method in (self.client.get, self.client.post):
                    self.assertEqual(method(self.URL, **kwargs).status_code, 404)
            self.assertEqual(self.client.get(self.URL + "?token=wrong").status_code, 404)
        run.assert_not_called()
        self.assertEqual(OutboxMessage.objects.count(), 0)

    @override_settings(NIGHTLY_TOKEN="")
    def test_an_unset_token_never_opens_it_even_with_an_empty_one(self):
        self.assertEqual(self.client.post(self.URL, HTTP_X_HESBA_TOKEN="").status_code, 404)
        self.assertEqual(self.client.post(self.URL + "?token=").status_code, 404)

    def test_the_right_token_runs_it_by_get_or_post_in_the_header_or_the_query(self):
        with mock.patch.dict(os.environ, {"WHATSAPP_TOKEN": "", "WHATSAPP_PHONE_NUMBER_ID": ""}):
            first = self.client.post(self.URL, HTTP_X_HESBA_TOKEN="t0ken-for-m3")
            second = self.client.get(self.URL + "?token=t0ken-for-m3")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), {"status": "gathered", "added": 1, "sent": 0, "failed": 0, "left": 0})
        self.assertEqual(second.json()["added"], 0)  # the second call adds nothing new
        self.assertEqual(first["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")

    def test_the_answer_carries_counts_only(self):
        with mock.patch.dict(os.environ, {"WHATSAPP_TOKEN": "", "WHATSAPP_PHONE_NUMBER_ID": ""}):
            body = self.client.post(self.URL, HTTP_X_HESBA_TOKEN="t0ken-for-m3").content.decode()
        for private in ("منى", "01001234567", "201001234567", "تذكير"):
            self.assertNotIn(private, body)

    def test_it_needs_no_login_and_no_csrf_token(self):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        with mock.patch.dict(os.environ, {"WHATSAPP_TOKEN": "", "WHATSAPP_PHONE_NUMBER_ID": ""}):
            self.assertEqual(strict.post(self.URL, HTTP_X_HESBA_TOKEN="t0ken-for-m3").status_code, 200)

    def test_with_keys_the_endpoint_sends_through_the_mock_only_inside_the_sending_hours(self):
        graph = Graph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):
            body = self.client.post(self.URL, HTTP_X_HESBA_TOKEN="t0ken-for-m3").json()
        self.assertIn(body["status"], ("sent", "outside_hours"))  # which one depends on the hour the suite runs at
        self.assertEqual(len(graph.calls), body["sent"])


@override_settings(PUBLIC_BASE_URL=BASE)
class AppointmentReminderLinkTests(ClinicSetup):
    def link_of(self, text):
        return next(word for word in text.split() if word.startswith("http"))

    def test_a_clinics_reminder_carries_the_patients_link(self):
        appointment = self.book()
        services.build()
        message = OutboxMessage.objects.get()
        link = self.link_of(message.text)
        self.assertTrue(link.startswith(f"{BASE}/share/"))
        self.assertIn("لتأكيد الحضور", message.text)
        page = self.client.get(link.replace(BASE, ""))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-appointment-page")
        self.assertEqual(Appointment.objects.get(pk=appointment.pk).status, AppointmentStatus.BOOKED)  # opening the link changes nothing

    def test_without_a_public_address_the_reminder_is_the_plain_one(self):
        self.book()
        with override_settings(PUBLIC_BASE_URL=""):
            services.build()
        self.assertNotIn("http", OutboxMessage.objects.get().text)

    def test_the_link_is_refreshed_with_the_waiting_message_each_day(self):
        self.book(day=today() + timedelta(days=2))
        services.build(today() + timedelta(days=1))
        first = OutboxMessage.objects.get().text
        services.build(today() + timedelta(days=1))
        self.assertEqual(OutboxMessage.objects.count(), 1)
        self.assertEqual(OutboxMessage.objects.get().text, first)

    def test_a_waiting_message_keeps_its_link_all_day_and_takes_a_fresh_one_the_next_day(self):
        # A share token is signed with the second it is made in. The outbox page gathers every time it opens, so
        # without this the waiting row would be rewritten with a new link on every visit.
        import time

        self.book(day=today() + timedelta(days=2))
        now = time.time()
        with mock.patch("django.core.signing.time.time", return_value=now):
            services.build(today() + timedelta(days=1))
        first = OutboxMessage.objects.get().text
        with mock.patch("django.core.signing.time.time", return_value=now + 90):
            services.build(today() + timedelta(days=1))
        self.assertEqual(OutboxMessage.objects.get().text, first)
        OutboxMessage.objects.update(day=today())  # as if it had been queued yesterday and is carried to a new day
        with mock.patch("django.core.signing.time.time", return_value=now + 180):
            services.build(today() + timedelta(days=1))
        carried = OutboxMessage.objects.get().text
        self.assertNotEqual(carried, first)
        self.assertIn("/share/", carried)

    def test_an_edited_template_with_its_own_link_place_gets_the_bare_link(self):
        self.turn_on(MessageKind.APPOINTMENT)
        services.save_template(MessageKind.APPOINTMENT, {"text": "أهلاً {name} {date} {time} {link}", "active": "1", "every_days": "1"}, self.owner)
        self.book()
        services.build()
        text = OutboxMessage.objects.get().text
        self.assertEqual(text.count("http"), 1)
        self.assertNotIn("لتأكيد الحضور", text)


@override_settings(PUBLIC_BASE_URL=BASE)
class LinkMaskingTests(ClinicSetup):
    def outsider(self, code, username):
        user = make_user(username=username)
        role = make_role(f"ROLE-{username}")
        grant(role, Permission.objects.get(code=code))
        make_user_profile(user=user, role=role)
        return user

    def setUp(self):
        super().setUp()
        self.book()
        services.build()
        self.message = OutboxMessage.objects.get()
        self.link = next(word for word in self.message.text.split() if word.startswith("http"))

    def test_the_kind_that_holds_a_link_is_recognised(self):
        self.assertTrue(services.holds_link(self.message))
        self.assertFalse(services.holds_link(OutboxMessage(kind=MessageKind.DUE, text="see https://x.test")))

    def test_someone_who_may_not_book_never_sees_or_sends_the_link(self):
        viewer = self.outsider("sales.view_sales_invoices", "m3_viewer")
        self.assertFalse(services.may_hold_link(viewer, MessageKind.APPOINTMENT))
        self.assertTrue(services.may_hold_link(self.owner, MessageKind.APPOINTMENT))
        url = reverse("messaging:outbox")
        self.client.force_login(viewer)
        for lang, mask in (("ar", "[لينك العميل]"), ("en", "[customer link]")):
            page = self.client.get(url + f"?lang={lang}")
            self.assertNotContains(page, self.link)
            self.assertContains(page, mask)
        self.assertEqual(self.client.post(url, {"lang": "ar", "action": "send", "message": self.message.pk}).status_code, 403)
        self.assertEqual(OutboxMessage.objects.get().status, OutboxStatus.PENDING)
        self.assertNotIn(self.message.pk, [row.pk for row in services.sendable(viewer, services.visible(viewer))])
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url + "?lang=ar"), self.link)

    def test_the_bulk_send_by_someone_who_may_not_hold_the_link_skips_it(self):
        viewer = self.outsider("sales.receive_customer_payment", "m3_collector")
        self.turn_on(MessageKind.APPOINTMENT, wa_template="hesba_reminder")
        graph = Graph()
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):
            result = services.send_by_api(viewer, services.sendable(viewer, services.visible(viewer)))
        self.assertEqual((result["sent"], graph.calls), (0, []))
        with mock.patch.dict(os.environ, ENV), mock.patch.object(whatsapp, "_http", graph):  # the scheduler is trusted server code
            self.assertEqual(services.send_by_api()["sent"], 1)

    def test_reception_with_the_booking_right_sends_it_by_a_click(self):
        cashier = person(RoleCode.CASHIER, "m3_cashier")
        self.assertTrue(services.may_hold_link(cashier, MessageKind.APPOINTMENT))
        services.mark(self.message, OutboxStatus.SENT, cashier)
        self.assertEqual(OutboxMessage.objects.get().status, OutboxStatus.SENT)

    def test_a_work_order_message_keeps_its_own_rule(self):
        viewer = self.outsider("sales.view_sales_invoices", "m3_wo_viewer")
        self.assertFalse(services.may_hold_link(viewer))  # the default kind is the work order's
        self.assertFalse(services.may_hold_link(viewer, MessageKind.WORKORDER))
        self.assertTrue(services.may_hold_link(self.owner, MessageKind.WORKORDER))


@override_settings(PUBLIC_BASE_URL=BASE)
class ShopReminderTests(TestCase):
    def test_a_shops_appointment_reminder_is_exactly_what_it_was_and_has_no_link(self):
        prepared_client("services", "maintenance", CLINIC)
        owner = person(RoleCode.OWNER, "m3_shop")
        customer = make_customer(customer_code="C-SHOP", name="مدام سعاد", phone="01001234567")
        Appointment.objects.create(number="AP-SHOP-1", customer=customer, starts_at=at(today() + timedelta(days=1), 11), created_by=owner)
        self.assertEqual(services.build(), 1)
        text = OutboxMessage.objects.get().text
        self.assertNotIn("http", text)
        self.assertEqual(text, services.render(services.DEFAULT_TEXTS[MessageKind.APPOINTMENT], name="مدام سعاد", company="Demo Store",
                                               date=(today() + timedelta(days=1)).isoformat(), time="11:00"))
