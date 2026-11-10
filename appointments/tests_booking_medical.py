"""M4: online booking for patients: the public pages and the settings screen in a clinic's words, and checking a booked
patient into the waiting list from the agenda and the appointment's page."""

import re
from datetime import datetime, time, timedelta
from unittest import mock
from urllib.parse import urlencode

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_customer
from medical.models import QueueTicket
from permissions.models import RoleCode

from . import booking as engine
from . import views_booking
from .models import Appointment, AppointmentStatus
from .tests_booking import BookingSetup, open_day, person
from .views_booking import MEDICAL_MESSAGES, MEDICAL_SETTINGS_WORDS, MEDICAL_WORDS, MEDICAL_SUBS, SETTINGS_WORDS, VET_WORDS, WORDS


class ClinicBooking(BookingSetup):
    """The S3 setup on a clinic: «كشف» bookable with two doctors (the base names are the shop's: منى، هدى)."""

    ACTIVITY = ("medical", "clinic")

    def url(self, **query):
        return reverse("book_page", args=[self.booking.public_key]) + ("?" + urlencode(query) if query else "")

    def post(self, day, clock="11:00", staff=None, **extra):
        data = {"lang": "ar", "service": self.service.pk, "staff": staff or self.mona.pk, "day": day.isoformat(), "time": clock,
                "name": "مروة السيد", "phone": "01012345678", "notes": "صداع", "website": ""}
        data.update(extra)
        return self.client.post(self.url(), data)

    def steps(self, lang, day):
        one = self.url(lang=lang)
        two = self.url(lang=lang, service=self.service.pk)
        three = self.url(lang=lang, service=self.service.pk, staff=self.mona.pk, day=day.isoformat())
        four = self.url(lang=lang, service=self.service.pk, staff=self.mona.pk, day=day.isoformat(), time="11:00")
        return [self.client.get(link) for link in (one, two, three, four)]


class ClinicWordsTests(ClinicBooking):
    def test_a_patient_books_a_visit_with_a_doctor_end_to_end_without_a_conflict(self):
        day = open_day()
        response = self.post(day)
        self.assertEqual(response.status_code, 303)
        appointment = Appointment.objects.get()
        self.assertEqual((appointment.employee, appointment.service, appointment.entity, appointment.duration_minutes),
                         (self.mona, self.item, self.entity, 45))
        self.assertEqual(appointment.customer.phone, "01012345678")
        done = self.client.get(response["Location"])
        self.assertContains(done, appointment.number)
        self.assertContains(done, "الكشف")  # the row's label, not «الخدمة»
        self.assertNotContains(done, "الخدمة")
        # the slot is now the doctor's: the same slot with her is refused, with «any» it goes to the other doctor
        again = self.post(day, name="سعاد", phone="01099998888")
        self.assertEqual(again.status_code, 409)
        self.assertEqual(Appointment.objects.count(), 1)
        self.assertEqual(self.post(day, staff="any", name="سعاد", phone="01099998888").status_code, 303)
        self.assertCountEqual(Appointment.objects.values_list("employee__name", flat=True), ["هدى", "منى"])

    def test_the_pages_speak_in_the_clinics_words_in_arabic(self):
        one, two, three, four = self.steps("ar", open_day())
        self.assertContains(one, "اختار نوع الكشف")
        self.assertContains(two, "اختار الدكتور")
        self.assertContains(two, "أي دكتور فاضي")
        self.assertContains(three, "المواعيد معروضة على مدة الكشف (45 دقيقة)")
        self.assertContains(four, "علشان العيادة تقدر تكلمك")
        self.assertContains(four, "مفيش دفع هنا: الحساب في العيادة.")
        self.assertContains(four, "سبب الزيارة أو ملاحظاتك")
        for page in (one, two, three, four):
            for shop_word in ("اختار الخدمة", "اختار الموظف", "المحل", "أي حد فاضي"):
                self.assertNotContains(page, shop_word)

    def test_the_pages_speak_in_the_clinics_words_in_english(self):
        one, two, three, four = self.steps("en", open_day())
        self.assertContains(one, "Choose the visit")
        self.assertContains(two, "Choose the doctor")
        self.assertContains(two, "Any available doctor")
        self.assertContains(four, "so the clinic can reach you")
        self.assertContains(four, "you pay at the clinic")
        for page in (one, two, three, four):
            for shop_word in ("Choose a service", "Choose who", "the shop", "Anyone available"):
                self.assertNotContains(page, shop_word)

    def test_a_missing_choice_and_a_failure_name_the_visit_and_the_clinic(self):
        day = open_day()
        response = self.post(day, service="")
        self.assertContains(response, "اختار نوع الكشف من القايمة.", status_code=400)
        response = self.post(day, staff="nobody", lang="en")
        self.assertContains(response, "Choose a doctor from the list.", status_code=400)
        self.assertContains(self.post(day, website="x"), "كلّم العيادة", status_code=400)

    def test_the_settings_screen_speaks_in_the_clinics_words(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("appointments:booking_settings"))
        self.assertContains(page, "الكشوفات اللي بتتحجز أونلاين")
        self.assertContains(page, "الدكاترة اللي بياخدوا حجوزات")
        self.assertContains(page, "المريض بيحجز منها لوحده")
        self.assertNotContains(page, "الخدمات اللي بتتحجز أونلاين")
        english = self.client.get(reverse("appointments:booking_settings") + "?lang=en")
        self.assertContains(english, "Visits bookable online")
        self.assertContains(english, "Doctors who take bookings")

    def test_the_public_page_shows_no_patient_data(self):
        day = open_day()
        self.post(day)
        for page in self.steps("ar", day):
            self.assertEqual(page.status_code, 200)
            for text in ("مروة السيد", "01012345678", "صداع", "01001234567", "مدام سعاد"):
                self.assertNotContains(page, text)


class VetWordsTests(ClinicBooking):
    ACTIVITY = ("medical", "vet")

    def test_a_vet_clinic_speaks_to_the_animals_owner(self):
        one, two, three, four = self.steps("ar", open_day())
        self.assertContains(two, "اختار الدكتور البيطري")
        self.assertContains(two, "أي دكتور بيطري فاضي")
        self.assertContains(four, "علشان العيادة البيطرية تقدر تكلمك")
        self.assertContains(four, "اسم الحيوان وسبب الزيارة")
        english = self.steps("en", open_day())
        self.assertContains(english[1], "Choose the vet")
        self.assertContains(english[3], "Pet&#x27;s name and reason for the visit")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("appointments:booking_settings")), "صاحب الحيوان بيحجز منها لوحده")
        self.assertContains(self.client.get(reverse("appointments:booking_settings") + "?lang=en"), "Vets who take bookings")


class OtherActivitiesUnchangedTests(BookingSetup):
    """The shop's words stay exactly as they were for every activity that is not medical."""

    def test_a_services_install_still_reads_the_shop_words(self):
        page = self.client.get(reverse("book_page", args=[self.booking.public_key]))
        self.assertContains(page, "اختار الخدمة")
        self.assertNotContains(page, "اختار نوع الكشف")
        self.client.force_login(self.owner)
        settings_page = self.client.get(reverse("appointments:booking_settings"))
        self.assertContains(settings_page, "الخدمات اللي بتتحجز أونلاين")
        self.assertContains(self.client.get(reverse("book_page", args=[self.booking.public_key]) + "?lang=en"), "Choose a service")

    def test_only_medical_activities_get_the_overlay(self):
        for pair in (("services", "maintenance"), ("services", "beauty"), ("services", "education"), ("commercial", "retail"), ("commercial", "pharmacy"),
                     ("education", "training"), ("restaurants", "restaurant"), ("contracting", "general"), ("manufacturing", "food"), ("", "")):
            with self.subTest(pair=pair), mock.patch("entities.services.activity_of", return_value=pair):
                for lang in ("ar", "en"):
                    self.assertIs(views_booking.words_for(self.entity, lang), WORDS[lang])
                    self.assertIs(views_booking.settings_words_for(self.entity, lang), SETTINGS_WORDS[lang])
                    self.assertIs(views_booking.messages_for(self.entity, lang), engine.MESSAGES[lang])
        for sub in MEDICAL_SUBS:
            with self.subTest(sub=sub), mock.patch("entities.services.activity_of", return_value=("medical", sub)):
                self.assertEqual(views_booking.words_for(self.entity, "ar")["sec_service"], "اختار نوع الكشف")

    def test_a_services_clinic_follows_the_vocabularys_precedent(self):
        """settings_core/vocabulary.py already gives («services», «clinic») the medical words: the patient reads them here too."""

        with mock.patch("entities.services.activity_of", return_value=("services", "clinic")):
            self.assertEqual(views_booking.words_for(self.entity, "ar")["sec_staff"], "اختار الدكتور")
            self.assertEqual(views_booking.words_for(self.entity, "en")["sec_staff"], "Choose the doctor")

    def test_the_overlay_only_replaces_keys_that_exist_and_leaves_no_placeholder(self):
        for lang in ("ar", "en"):
            self.assertLessEqual(set(MEDICAL_WORDS[lang]) | set(VET_WORDS[lang]), set(WORDS[lang]))
            self.assertLessEqual(set(MEDICAL_MESSAGES[lang]), set(engine.MESSAGES[lang]))
            self.assertLessEqual(set(MEDICAL_SETTINGS_WORDS[lang]), set(SETTINGS_WORDS[lang]))
        self.assertEqual(set(MEDICAL_WORDS["ar"]), set(MEDICAL_WORDS["en"]))
        self.assertEqual(set(MEDICAL_MESSAGES["ar"]), set(MEDICAL_MESSAGES["en"]))
        self.assertEqual(set(MEDICAL_SETTINGS_WORDS["ar"]), set(MEDICAL_SETTINGS_WORDS["en"]))
        self.assertEqual(set(VET_WORDS["ar"]), set(VET_WORDS["en"]))
        left = re.compile(r"\{(?!n\}|hours\}|shop\})")  # {n}, {hours} and {shop} are filled by the page itself
        for kind in MEDICAL_SUBS:
            with mock.patch("entities.services.activity_of", return_value=("medical", kind)):
                for lang in ("ar", "en"):
                    for table, overlay in ((views_booking.words_for(self.entity, lang), {**MEDICAL_WORDS[lang], **VET_WORDS[lang]}),
                                           (views_booking.settings_words_for(self.entity, lang), MEDICAL_SETTINGS_WORDS[lang]),
                                           (views_booking.messages_for(self.entity, lang), MEDICAL_MESSAGES[lang])):
                        for key in overlay:
                            for text in table[key] if isinstance(table[key], tuple) else (table[key],):
                                self.assertIsNone(left.search(text), (kind, lang, key, text))


# ---- from a booked appointment to the waiting list -------------------------------------------------------------------


class CheckInFromTheAgendaTests(BookingSetup):
    ACTIVITY = ("medical", "clinic")

    def setUp(self):
        super().setUp()
        self.desk = person(RoleCode.CASHIER, "ci_desk")
        self.agenda = reverse("appointments:agenda")
        self.today = timezone.localdate()

    def book(self, customer=None, status=AppointmentStatus.BOOKED, days=0, number=None):
        # Noon of the Cairo day, not of the UTC one: between midnight and 02:00 in Cairo the two days differ.
        moment = timezone.make_aware(datetime.combine(timezone.localdate(), time(12, 0))) + timedelta(days=days)
        return Appointment.objects.create(number=number or f"AP-CI-{Appointment.objects.count() + 1}", customer=customer or self.customer, employee=self.mona,
                                          starts_at=moment, duration_minutes=30, status=status, created_by=self.owner, service=self.item, price=250)

    def test_reception_checks_a_booked_patient_in_from_the_agenda(self):
        appointment = self.book()
        self.client.force_login(self.desk)
        page = self.client.get(self.agenda + f"?day={self.today.isoformat()}")
        self.assertContains(page, "data-agenda-check-in")
        self.assertContains(page, "سجّل وصوله")
        response = self.client.post(self.agenda, {"action": "check_in", "appointment": appointment.pk, "day": self.today.isoformat()})
        self.assertEqual(response.status_code, 302)
        ticket = QueueTicket.objects.get()
        self.assertEqual((ticket.appointment_id, ticket.patient_id, ticket.doctor_id, ticket.number, ticket.day), (appointment.pk, self.customer.pk, self.mona.pk, 1, self.today))
        appointment.refresh_from_db()
        self.assertEqual(appointment.status, AppointmentStatus.ARRIVED)  # through the appointments service
        page = self.client.get(self.agenda + f"?day={self.today.isoformat()}")
        self.assertNotContains(page, "data-agenda-check-in")
        self.assertContains(page, 'data-agenda-queue-number="1"')
        self.assertContains(page, "في الدور رقم 1")
        self.assertContains(self.client.get(self.agenda + f"?day={self.today.isoformat()}&lang=en"), "In the queue: no. 1")

    def test_a_second_click_is_a_message_not_a_second_turn(self):
        appointment = self.book()
        self.client.force_login(self.desk)
        data = {"action": "check_in", "appointment": appointment.pk, "day": self.today.isoformat()}
        self.client.post(self.agenda, data)
        response = self.client.post(self.agenda, data, follow=True)
        self.assertContains(response, "في الدور رقم 1")
        self.assertEqual(QueueTicket.objects.count(), 1)

    def test_an_arrived_appointment_without_a_turn_can_still_be_checked_in(self):
        appointment = self.book(status=AppointmentStatus.ARRIVED)
        self.client.force_login(self.desk)
        self.assertContains(self.client.get(self.agenda + f"?day={self.today.isoformat()}"), "data-agenda-check-in")
        self.client.post(self.agenda, {"action": "check_in", "appointment": appointment.pk})
        self.assertEqual(QueueTicket.objects.get().appointment_id, appointment.pk)

    def test_only_a_live_appointment_of_today_is_offered_and_accepted(self):
        tomorrow = self.book(days=1)
        cancelled = self.book(status=AppointmentStatus.CANCELLED, customer=make_customer(customer_code="C-2", name="علي"))
        done = self.book(status=AppointmentStatus.DONE, customer=make_customer(customer_code="C-3", name="نور"))
        self.client.force_login(self.desk)
        self.assertNotContains(self.client.get(self.agenda + f"?day={self.today.isoformat()}"), "data-agenda-check-in")
        self.assertNotContains(self.client.get(self.agenda + f"?day={(self.today + timedelta(days=1)).isoformat()}"), "data-agenda-check-in")
        for appointment in (tomorrow, cancelled, done):
            with self.subTest(appointment=appointment.number):
                response = self.client.post(self.agenda, {"action": "check_in", "appointment": appointment.pk}, follow=True)
                self.assertContains(response, "تسجيل الوصول لميعاد النهارده اللي لسه ماخلصش بس")
        self.assertFalse(QueueTicket.objects.exists())  # never a stray turn for another day's booking

    def test_the_detail_page_offers_it_and_shows_the_turn(self):
        appointment = self.book()
        self.client.force_login(self.desk)
        detail = reverse("appointments:detail", args=[appointment.pk])
        self.assertContains(self.client.get(detail), "data-appointment-check-in")
        response = self.client.post(detail, {"action": "check_in"}, follow=True)
        self.assertContains(response, "رقم دوره 1")
        page = self.client.get(detail)
        self.assertNotContains(page, "data-appointment-check-in")
        self.assertContains(page, 'data-appointment-queue-number="1"')
        again = self.client.post(detail, {"action": "check_in"})
        self.assertContains(again, "في الدور رقم 1")
        self.assertEqual(QueueTicket.objects.count(), 1)

    def test_it_needs_the_desk_permission_the_queue_uses(self):
        appointment = self.book()
        viewer = person(RoleCode.ACCOUNTANT, "ci_viewer")  # may see appointments, not book or check in
        self.client.force_login(viewer)
        page = self.client.get(self.agenda)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-agenda-check-in")
        self.assertEqual(self.client.post(self.agenda, {"action": "check_in", "appointment": appointment.pk}).status_code, 403)
        self.assertEqual(self.client.post(reverse("appointments:detail", args=[appointment.pk]), {"action": "check_in"}).status_code, 403)
        self.assertFalse(QueueTicket.objects.exists())

    def test_a_bad_or_missing_appointment_is_a_404_not_a_500(self):
        self.client.force_login(self.desk)
        for raw in ("x", "", "999999", "9" * 40):
            self.assertEqual(self.client.post(self.agenda, {"action": "check_in", "appointment": raw}).status_code, 404, raw)

    def test_the_action_follows_the_queues_own_rule_about_the_kind_of_business(self):
        """The waiting list exists for the clinic and the medical centre only; everywhere else there is no button and no way in."""

        for pair, queue_exists in ((("medical", "medical_center"), True), (("medical", "dental"), False), (("medical", "vet"), False),
                                   (("services", "clinic"), False), (("services", "maintenance"), False)):
            with self.subTest(pair=pair):
                self.client.logout()
                QueueTicket.objects.all().delete()
                Appointment.objects.all().delete()
                from settings_core.models import ClientProfile

                ClientProfile.objects.filter(pk=self.profile.pk).update(activity_slug=pair[0], sub_activity_slug=pair[1])
                appointment = self.book()
                self.client.force_login(self.desk)
                page = self.client.get(self.agenda + f"?day={self.today.isoformat()}")
                self.assertEqual(page.status_code, 200)
                self.assertEqual("data-agenda-check-in" in page.content.decode(), queue_exists)
                response = self.client.post(self.agenda, {"action": "check_in", "appointment": appointment.pk})
                self.assertEqual(response.status_code, 302 if queue_exists else 403)
                self.assertEqual(QueueTicket.objects.count(), 1 if queue_exists else 0)
                detail = self.client.get(reverse("appointments:detail", args=[appointment.pk]))
                self.assertEqual(detail.status_code, 200)
                self.assertEqual("data-appointment-check-in" in detail.content.decode(), False)  # now has a turn, or no queue at all

