"""What the medical demos showed while the walkthrough video was recorded, and must not show again.

Each test here is one numbered finding from that recording: a company standing in for a patient, one patient in
three places at once, sales reps offered as doctors, car parts on a lab's shelf, a tour step into a 404, and a lab
reading a clinic's words. The seeded demos are built the way the demo landing builds them
(``config.demo_templates``), with the clock frozen on a Tuesday mid-morning so no test depends on the hour or on
Friday being a day off.
"""

import re
from datetime import datetime, time, timezone as utc
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

#: A Tuesday, 10:40 Cairo (UTC+2) — a working morning in every demo's hours, and never Friday.
FROZEN = datetime(2026, 11, 10, 8, 40, tzinfo=utc.utc)


def frozen_clock():
    return mock.patch.object(timezone, "now", return_value=FROZEN)


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1", DEBUG=True)
class MedicalDemoCase(TestCase):
    """A seeded medical demo of one sub-activity, built once for the class."""

    sub_activity = ""

    @classmethod
    def setUpTestData(cls):
        from config.demo_templates import complete_demo_setup, seed_sample_business

        with frozen_clock():
            call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
            call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0, stdout=StringIO())
            complete_demo_setup("medical", cls.sub_activity)
            assert seed_sample_business(), cls.sub_activity

    def setUp(self):
        self.assertTrue(self.client.login(username="owner", password="Demo-pass-1"))

    def tour_steps(self):
        """The «جرّب دلوقتي» steps the dashboard really offers: [(title, url)]."""

        page = self.client.get("/dashboard/?lang=ar")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        return re.findall(r'<a class="fr-step" href="([^"]+)" data-demo-tour-step="\d+">.*?<strong>([^<]*)</strong>', html, re.S)

    def assert_every_tour_step_opens(self):
        """Video finding 20: «قائمة الانتظار» linked to /medical/queue/, a 404 outside a clinic and a medical centre."""

        steps = self.tour_steps()
        self.assertTrue(steps, "the demo tour card has no steps")
        for url, title in steps:
            with self.subTest(step=title):
                self.assertEqual(self.client.get(url).status_code, 200, url)


# ---- findings 7 and 8: who the clinic's demo says is where -----------------------------------------------------


class ClinicDemoDataTests(MedicalDemoCase):
    sub_activity = "clinic"

    def test_no_company_stands_in_for_a_patient(self):
        """Finding 7: «شركة النور للتجارة (تأمين موظفين)» had a patient file, an online booking, a place in the
        queue's lists and the dashboard's «الموعد الجاي»."""

        from appointments.models import Appointment
        from master_data.models import Customer
        from medical.models import QueueTicket

        sample = list(Customer.objects.filter(customer_code__startswith="DEMO-CUST-").values_list("name", flat=True))
        self.assertEqual(len(sample), 4)
        for name in sample:
            self.assertNotIn("شركة", name)
        for appointment in Appointment.objects.select_related("customer"):
            self.assertNotIn("شركة", appointment.customer.name, appointment.number)
        for ticket in QueueTicket.objects.select_related("patient"):
            self.assertNotIn("شركة", ticket.patient.name)

    def test_the_queue_the_arrived_appointment_and_the_bed_are_different_patients(self):
        """Finding 8: «سارة محمود» waited in the queue, her 17:00 appointment read «جاري» at nine in the morning,
        and she was in bed 101 — all at the same time."""

        from appointments.models import Appointment, AppointmentStatus
        from medical.models import Admission, AdmissionState, QueueTicket

        with frozen_clock():
            today = timezone.localdate()
        in_queue = {ticket.patient_id for ticket in QueueTicket.objects.all()}
        arrived = {a.customer_id for a in Appointment.objects.filter(starts_at__date=today, status=AppointmentStatus.ARRIVED)}
        in_bed = {stay.patient_id for stay in Admission.objects.filter(state=AdmissionState.ADMITTED)}
        self.assertEqual(len(in_queue), 2)
        self.assertEqual(len(arrived), 1)
        self.assertEqual(len(in_bed), 1)
        self.assertFalse(in_queue & in_bed)
        self.assertFalse(arrived & in_bed)
        # The appointment that reads «جاري» is the one its own patient was checked in for: the queue marks it, so it
        # is never a second patient's turn, and never one more situation for somebody already in another.
        self.assertTrue(arrived <= in_queue)
        self.assertEqual(QueueTicket.objects.exclude(appointment=None).count(), 1)

    def test_no_appointment_reads_as_in_progress_before_it_starts(self):
        """Finding 8 again: «جاري» hours in the future."""

        from appointments.models import Appointment, AppointmentStatus

        with frozen_clock():
            now = timezone.localtime()
            for appointment in Appointment.objects.filter(status=AppointmentStatus.ARRIVED):
                self.assertLessEqual(timezone.localtime(appointment.starts_at), now, appointment.number)

    def test_the_online_consultations_belong_to_people_of_their_own(self):
        """Findings 7 and 8: they used to take the sample's last two customers — the company, and a patient who was
        already in the queue or in a bed."""

        from appointments.models import Appointment, AppointmentKind
        from medical.models import Admission, AdmissionState, QueueTicket

        online = list(Appointment.objects.filter(kind=AppointmentKind.ONLINE).select_related("customer"))
        self.assertEqual(len(online), 2)
        busy = {t.patient_id for t in QueueTicket.objects.all()} | {s.patient_id for s in Admission.objects.filter(state=AdmissionState.ADMITTED)}
        for appointment in online:
            self.assertNotIn("شركة", appointment.customer.name)
            self.assertNotIn(appointment.customer_id, busy, appointment.customer.name)
        self.assertNotEqual(online[0].customer_id, online[1].customer_id)

    def test_the_booking_settings_offer_doctors_and_not_sales_reps(self):
        """Finding 9: «سمير عادل — مندوب مبيعات» and «هالة مصطفى — مندوب مبيعات» stood under «الدكاترة اللي
        بياخدوا حجوزات»."""

        from staff.models import Employee
        from staff.services import is_sales_rep

        page = self.client.get("/appointments/booking/?lang=ar")
        self.assertEqual(page.status_code, 200)
        offered = {int(pk) for pk in re.findall(r'name="staff_(\d+)"', page.content.decode())}
        self.assertTrue(offered)
        reps = {person.pk for person in Employee.objects.filter(active=True) if is_sales_rep(person)}
        self.assertTrue(reps, "the sample has sales reps, or this test proves nothing")
        self.assertFalse(offered & reps)

    def test_the_clinic_keeps_its_own_report_name_and_the_tour_opens_everywhere(self):
        from settings_core.vocabulary import term

        self.assertEqual(term("clinic_report", "ar"), "تقرير العيادة")
        self.assert_every_tour_step_opens()

    def test_a_clinic_calls_the_till_role_the_reception_desk(self):
        """Finding 6: the role beside the demo's reception login read «الكاشير»."""

        from accounts.models import UserProfile
        from permissions.models import Role

        page = self.client.get("/settings/users/?lang=ar")
        self.assertEqual(page.status_code, 200)
        row = re.search(r'<tr[^>]*>(?:(?!</tr>).)*cashier(?:(?!</tr>).)*</tr>', page.content.decode(), re.S)
        self.assertIsNotNone(row)
        self.assertIn("موظف الاستقبال", row.group(0))
        self.assertEqual(Role.objects.get(code="cashier").name_ar, "الكاشير")  # the seeded role itself is untouched
        self.assertEqual(UserProfile.objects.get(user__username="cashier").display_name, "أ. مروة عبد اللطيف")


# ---- findings 5, 10 and 20: the lab -----------------------------------------------------------------------------


class LabDemoTests(MedicalDemoCase):
    sub_activity = "lab"

    def test_no_car_part_is_on_a_lab_shelf(self):
        """Finding 10: «زيت محرك 4 لتر», «فلتر زيت تويوتا» and «تيل فرامل أمامي» were in the lab's item list."""

        from master_data.models import Item

        self.assertFalse(Item.objects.filter(item_code__startswith="DEMO-AI-").exists())
        for name in ("زيت محرك", "فلتر زيت", "تيل فرامل"):
            self.assertFalse(Item.objects.filter(item_name__contains=name).exists(), name)

    def test_a_lab_reads_its_own_words_and_not_a_clinic_s(self):
        """Finding 5: the menu, the tabs and the buttons said «تقرير العيادة» and «+ كشف»."""

        from settings_core.vocabulary import term

        self.assertEqual(term("clinic_report", "ar"), "تقرير المعمل")
        self.assertEqual(term("clinic_report", "en"), "Lab report")
        self.assertEqual(term("new_sale", "ar"), "طلب تحليل / فاتورة جديدة")
        page = self.client.get("/medical/report/?lang=ar")
        self.assertEqual(page.status_code, 200)
        html = page.content.decode()
        self.assertIn("<h1>تقرير المعمل</h1>", html)
        tab = re.search(r'<a [^>]*data-tab-report[^>]*>([^<]*)</a>', html)
        self.assertIsNotNone(tab)
        self.assertEqual(tab.group(1), "تقرير المعمل")
        new = re.search(r'<a [^>]*data-sales-new[^>]*>([^<]*)</a>', self.client.get("/sales/?lang=ar").content.decode())
        self.assertIsNotNone(new)
        self.assertEqual(new.group(1).strip(), "+ طلب تحليل / فاتورة جديدة")

    def test_a_lab_records_visits_so_it_keeps_the_visit_templates(self):
        """Finding 5 asked whether «قوالب الكشف» belongs in a lab: it does — the lab's patient files hold visits."""

        from medical.models import ClinicalVisit

        self.assertTrue(ClinicalVisit.objects.exists())
        page = self.client.get("/medical/?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertIn("data-tab-templates", page.content.decode())

    def test_the_tour_opens_everywhere(self):
        self.assert_every_tour_step_opens()


# ---- findings 7, 9, 10 and 20: the vet --------------------------------------------------------------------------


class VetDemoTests(MedicalDemoCase):
    sub_activity = "vet"

    def test_no_car_part_and_no_company_pet_owner(self):
        """Finding 10 and finding 7: brake pads in «الصنف للفاتورة», and «شركة النور للتجارة (كلاب حراسة)» among
        the pet owners."""

        from master_data.models import Customer, Item

        self.assertFalse(Item.objects.filter(item_code__startswith="DEMO-AI-").exists())
        for name in Customer.objects.filter(customer_code__startswith="DEMO-CUST-").values_list("name", flat=True):
            self.assertNotIn("شركة", name)

    def test_the_vaccine_form_offers_doctors_and_not_sales_reps(self):
        """Finding 9: the two sales reps stood in the vaccine form's «الدكتور»."""

        from medical.models import Animal
        from staff.models import Employee
        from staff.services import is_sales_rep

        animal = Animal.objects.order_by("pk").first()
        self.assertIsNotNone(animal)
        page = self.client.get(f"/medical/animals/{animal.pk}/vaccinate/?lang=ar")
        self.assertEqual(page.status_code, 200)
        picker = re.search(r'<select[^>]*name="given_by".*?</select>', page.content.decode(), re.S)
        self.assertIsNotNone(picker)
        offered = {int(pk) for pk in re.findall(r'value="(\d+)"', picker.group(0))}
        reps = {person.pk for person in Employee.objects.filter(active=True) if is_sales_rep(person)}
        self.assertTrue(offered and reps)
        self.assertFalse(offered & reps)

    def test_the_tour_opens_everywhere(self):
        self.assert_every_tour_step_opens()


# ---- finding 20: the dentist ------------------------------------------------------------------------------------


class DentistDemoTests(MedicalDemoCase):
    sub_activity = "dental"

    def test_the_tour_has_no_dead_link(self):
        """Finding 20: step 3 «قائمة الانتظار» opened /medical/queue/, which answers 404 in a dentist."""

        steps = self.tour_steps()
        self.assertEqual(len(steps), 3)
        self.assertNotIn("/medical/queue/", [url.split("?")[0] for url, _ in steps])
        self.assert_every_tour_step_opens()
        self.assertEqual(self.client.get("/medical/queue/?lang=ar").status_code, 404)  # still not the dentist's screen

    def test_the_dental_chart_says_the_short_state_in_a_tooth_s_cell(self):
        """Finding 21: «مخلوعة / مش موجودة» wrapped over three lines in a cell as narrow as a tooth."""

        from medical import dental
        from master_data.models import Customer
        from medical.models import ToothRecord

        patient = Customer.objects.filter(customer_code="DEMO-CUST-01").first()
        self.assertIsNotNone(patient)
        ToothRecord.objects.update_or_create(patient=patient, tooth=46, defaults={"condition": "missing", "notes": ""})
        page = self.client.get(f"/medical/patients/{patient.pk}/dental/?lang=ar")
        self.assertEqual(page.status_code, 200)
        cell = re.search(r'<button class="dn-tooth"[^>]*data-tooth="46"[^>]*>.*?</button>', page.content.decode(), re.S)
        self.assertIsNotNone(cell)
        self.assertIn("<small>مفقودة</small>", cell.group(0))
        self.assertIn('title="مخلوعة / مش موجودة"', cell.group(0))  # the full wording stays where there is room
        self.assertEqual(dental.CONDITION_LABELS["ar"]["missing"], "مخلوعة / مش موجودة")


# ---- findings 1 to 4 and 19: the words, read from the sources ---------------------------------------------------


class MedicalWordingTests(SimpleTestCase):
    APPS = ("medical", "appointments", "messaging")

    def sources(self, apps=None):
        base = Path(settings.BASE_DIR)
        for app in apps or self.APPS:
            for path in sorted((base / app).rglob("*.py")):
                if "migrations" not in path.parts and not path.name.startswith("tests"):
                    yield path
            folder = base / "templates" / app
            if folder.is_dir():
                yield from sorted(folder.rglob("*.html"))

    def test_the_medical_screens_say_press_not_the_street_word(self):
        """Finding 1: «لما الكشف يخلص، اعمل فاتورته بدوسة» and «دوس على الفاتورة»."""

        from settings_core.vocabulary import BASE, vocabulary

        for table in (BASE, vocabulary("medical", "clinic"), vocabulary("medical", "vet")):
            self.assertIn("بضغطة", table["agenda_intro"]["ar"])
            self.assertNotIn("دوس", table["agenda_intro"]["ar"])
        # Only the clinic's own apps: the wider pass over the product's wording is a separate task, and the outbox's
        # «دوس «ابعت» تاني» (messaging/views.py) waits for it.
        found = [path.name for path in self.sources(("medical", "appointments")) if "دوس" in path.read_text(encoding="utf-8")]
        self.assertEqual(found, [])

    def test_the_staff_screens_say_rabt_not_the_transliterated_word(self):
        """Finding 2: «اعمل لينك مكالمة», «نسخ اللينك», «{link} لينك العميل الآمن»."""

        found = [path.name for path in self.sources() if "لينك" in path.read_text(encoding="utf-8")]
        self.assertEqual(found, [])

    def test_the_outbox_banner_is_written_for_any_business(self):
        """Finding 3: «محتاج حساب WhatsApp Business للمحل نفسه» — a Latin brand inside the Arabic, and «المحل»."""

        from messaging.views import WORDS

        banner = WORDS["ar"]["api_off"]
        self.assertEqual(banner, "الإرسال التلقائي من غير ضغط محتاج حساب واتساب للأعمال خاص بالنشاط نفسه. "
                                 "لحد ما يتربط، الرسالة بتتبعت بضغطة من هنا.")
        for text in (banner, WORDS["ar"]["api_on"], WORDS["ar"]["wa_template"]):
            self.assertNotRegex(text, r"[A-Za-z]")
        self.assertIn("WhatsApp Business", WORDS["en"]["api_off"])  # English keeps the brand names

    def test_the_doctor_s_own_templates_tab_and_the_test_code_field(self):
        """Finding 4: «بتاعي» and «الكود بتاع التحليل»."""

        from medical.views_lab import WORDS as LAB_WORDS
        from medical.visit_templates import WORDS

        self.assertEqual(WORDS["ar"]["mine"], "قوالبي")
        self.assertTrue(LAB_WORDS["ar"]["format_note"].startswith("كود التحليل"))

    def test_the_insurance_contract_names_the_company_s_share(self):
        """Finding 19: «نسبة التحمّل %» holds what the company covers, not what the patient bears."""

        from medical.insurance import MESSAGES
        from medical.views import INSURANCE_WORDS

        self.assertEqual(INSURANCE_WORDS["ar"]["percent"], "نسبة تغطية الشركة %")
        self.assertEqual(INSURANCE_WORDS["en"]["percent"], "Company cover %")
        self.assertNotIn("التحمّل", MESSAGES["ar"]["percent"])
