"""HG-084: who reads patient records — the access matrix.

Clinical records (history, allergies, visits, lab results, the dental chart, the animal card) are for the
users who hold a clinical permission by role: the practice-wide ``medical.view_records`` /
``write_records`` (owner, manager) or ``medical.treat_patients`` (the seeded doctor role). Being linked
to an employee no longer opens them: reception, cashiers and stock keepers are on the payroll too.
Front desk keeps booking, the agenda, the queue, invoicing and insurance, without clinical text.
"""

from datetime import datetime, time, timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_customer, make_item, make_location, make_role, make_user, make_user_profile
from permissions.models import Permission, Role, RoleCode, RolePermission
from reports.tests_dashboard import prepared_client
from reports.tests_shell import sidebar_links

from .tests import CLINIC, person

#: Distinctive text planted in every clinical field; none of it may reach a screen a non-clinical user opens.
MARKERS = ("ZZCOMPLAINT", "ZZEXAMINATION", "ZZDIAGNOSIS", "ZZTREATMENT", "ZZALLERGY", "ZZCHRONIC", "ZZMEDICATION", "ZZFILENOTE",
           "ZZLABTEXT", "ZZTOOTHNOTE", "ZZVETALLERGY")
CLINICAL_USERS = ("owner", "manager", "doctor")
NON_CLINICAL = ("desk", "keeper", "cashier", "nobody", "switched_off")


class Clinic(TestCase):
    def setUp(self):
        from accounts.models import UserProfile
        from staff.services import save_employee

        from . import services

        prepared_client("medical", "clinic", CLINIC)
        make_location(location_code="CLINIC", is_default=True, is_selling_location=True)
        self.owner = person(RoleCode.OWNER, "mx_owner")
        self.users = {"owner": self.owner, "manager": person(RoleCode.MANAGER, "mx_manager")}
        self.sara = save_employee({"name": "د. سارة", "title": "طبيبة"}, self.owner)

        def linked(user, name, title):
            employee = save_employee({"name": name, "title": title}, self.owner)
            employee.user = user
            employee.save()
            return user

        self.users["doctor"] = person(RoleCode.DOCTOR, "mx_doctor")
        self.sara.user = self.users["doctor"]
        self.sara.save()
        self.users["desk"] = linked(person(RoleCode.CASHIER, "mx_desk"), "منى الاستقبال", "استقبال")  # the leak this closes
        self.users["keeper"] = linked(person(RoleCode.STOCK_KEEPER, "mx_keeper"), "عم سيد", "مخزن")
        self.users["cashier"] = person(RoleCode.CASHIER, "mx_cashier")  # not linked to anyone
        self.users["nobody"] = linked(make_user(username="mx_nobody"), "عامل نظافة", "")  # a login with no role at all
        self.users["switched_off"] = linked(person(RoleCode.DOCTOR, "mx_off"), "د. سابق", "طبيب")
        UserProfile.objects.filter(user=self.users["switched_off"]).update(active=False)  # the owner switched this login off

        self.service = make_item(item_code="SRV-1", item_name="كشف", is_stock_tracked=False, default_sale_price="300")
        self.patient = make_customer(customer_code="P-1", name="منى علي", phone="01001234567")
        services.save_profile(services.file_for(self.patient), {"allergies": "ZZALLERGY", "chronic_conditions": "ZZCHRONIC",
                                                                "current_medications": "ZZMEDICATION", "notes": "ZZFILENOTE"}, self.owner)
        today = timezone.localdate()
        self.visit = services.save_visit(self.patient, {"complaint": "ZZCOMPLAINT", "examination": "ZZEXAMINATION", "diagnosis": "ZZDIAGNOSIS",
                                                        "treatment": "ZZTREATMENT", "doctor": self.sara, "visit_date": (today - timedelta(days=20)).isoformat(),
                                                        "follow_up_date": (today - timedelta(days=3)).isoformat()}, self.owner)
        from appointments.models import Appointment

        self.appointment = Appointment.objects.create(number="AP-MX1", customer=self.patient, employee=self.sara, service=self.service, price="300",
                                                      starts_at=timezone.make_aware(datetime.combine(today, time(23, 59))), created_by=self.owner)  # today in Cairo whatever the hour: "an hour from now" is tomorrow after 23:00

    def status(self, who, url, method="get", data=None):
        self.client.force_login(self.users[who])
        return getattr(self.client, method)(url, data or {}).status_code

    def assert_matrix(self, urls, allowed=CLINICAL_USERS, denied=NON_CLINICAL):
        for url in urls:
            for who in allowed:
                with self.subTest(url=url, who=who):
                    self.assertEqual(self.status(who, url), 200)
            for who in denied:
                with self.subTest(url=url, who=who):
                    self.assertEqual(self.status(who, url), 403)  # exactly 403: a 404 would mean the screen was not even there

    def assert_no_clinical_text(self, response):
        self.assertEqual(response.status_code, 200)
        for marker in MARKERS:
            self.assertNotContains(response, marker)

    def set_sub_activity(self, sub):
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(sub_activity_slug=sub)


class ClinicalScreensTests(Clinic):
    def test_the_clinical_screens_need_a_clinical_permission(self):
        urls = (reverse("medical:patients"), reverse("medical:patients") + "?q=منى", reverse("medical:file", args=[self.patient.pk]),
                reverse("medical:visit_new", args=[self.patient.pk]), reverse("medical:visit_edit", args=[self.visit.pk]),
                reverse("medical:prescription", args=[self.visit.pk]), reverse("medical:follow_ups"))
        self.assert_matrix(urls)
        self.client.force_login(self.users["doctor"])
        page = self.client.get(reverse("medical:file", args=[self.patient.pk]))
        for marker in ("ZZALLERGY", "ZZDIAGNOSIS", "ZZTREATMENT"):
            self.assertContains(page, marker)  # the doctor reads the whole file

    def test_recording_a_visit(self):
        from .models import ClinicalVisit

        url = reverse("medical:visit_new", args=[self.patient.pk])
        data = {"complaint": "صداع", "diagnosis": "صداع نصفي", "visit_date": timezone.localdate().isoformat(), "doctor": self.sara.pk}
        for who in NON_CLINICAL:
            with self.subTest(who=who):
                self.assertEqual(self.status(who, url, "post", data), 403)
        self.assertFalse(ClinicalVisit.objects.filter(diagnosis="صداع نصفي").exists())
        self.assertEqual(self.status("doctor", url, "post", data), 302)
        visit = ClinicalVisit.objects.get(diagnosis="صداع نصفي")
        self.assertEqual((visit.doctor_id, visit.created_by_id), (self.sara.pk, self.users["doctor"].pk))
        self.client.force_login(self.users["doctor"])
        self.assertContains(self.client.get(url), f'<option value="{self.sara.pk}" selected')  # the form offers the doctor's own employee
        for who, field in (("desk", "allergies"), ("keeper", "allergies")):
            with self.subTest(who=who):
                self.assertEqual(self.status(who, reverse("medical:file", args=[self.patient.pk]), "post", {field: "x"}), 403)

    def test_a_doctor_edits_only_their_own_visits_and_reads_no_clinic_report(self):
        from django.core.exceptions import ValidationError

        from . import services

        with self.assertRaises(ValidationError):  # the owner recorded this one; a doctor does not rewrite it (WRITE stays practice-wide)
            services.save_visit(self.patient, {"diagnosis": "تعديل"}, self.users["doctor"], self.visit)
        own = services.save_visit(self.patient, {"diagnosis": "برد"}, self.users["doctor"])
        services.save_visit(self.patient, {"diagnosis": "برد شديد"}, self.users["doctor"], own)
        services.save_visit(self.patient, {"diagnosis": "برد موسمي"}, self.users["manager"], own)  # the manager may correct anyone's
        self.assert_matrix([reverse("medical:report")], allowed=("owner", "manager"), denied=("doctor",) + NON_CLINICAL)

    def test_the_sidebar_and_the_doctor_role_land_somewhere(self):
        for who in CLINICAL_USERS:
            with self.subTest(who=who):
                self.client.force_login(self.users[who])
                page = self.client.get(reverse("dashboard_snapshot"))
                self.assertEqual(page.status_code, 200)
                self.assertIn(reverse("medical:patients"), sidebar_links(page))
        for who in ("desk", "keeper", "cashier"):
            with self.subTest(who=who):
                self.client.force_login(self.users[who])
                page = self.client.get(reverse("dashboard_snapshot"))
                self.assertNotIn(reverse("medical:patients"), sidebar_links(page))
                self.assert_no_clinical_text(page)

    def test_the_service_rule_itself(self):
        from django.contrib.auth import get_user_model

        from . import services

        self.users = {who: get_user_model().objects.get(pk=user.pk) for who, user in self.users.items()}  # profiles as stored now

        self.assertEqual({who: services.can_view(user) for who, user in self.users.items()},
                         {"owner": True, "manager": True, "doctor": True, "desk": False, "keeper": False, "cashier": False, "nobody": False,
                          "switched_off": False})
        self.assertEqual({who: services.can_write(user) for who, user in self.users.items()},
                         {"owner": True, "manager": True, "doctor": True, "desk": False, "keeper": False, "cashier": False, "nobody": False,
                          "switched_off": False})

    def test_the_roles_screens_offer_it_in_a_clinic_only(self):
        self.client.force_login(self.owner)
        roles = self.client.get(reverse("settings_core:roles") + "?lang=ar")
        self.assertContains(roles, "الدكتور أو الأخصائي")
        self.assertContains(roles, "الكشف على المرضى: قراءة ملفاتهم وتسجيل الكشوفات")
        form = self.client.get(reverse("settings_core:role_new") + "?lang=ar")
        for label in ("الكشف على المرضى", "تقرير العيادة ومتابعات كل الدكاترة (للإدارة)", "حتى اللي سجّله دكتور تاني (للإدارة)"):
            self.assertContains(form, label)  # the owner can tell the doctor's permission from the management ones
        self.assertContains(self.client.get(reverse("settings_core:roles") + "?lang=en"), "Doctor or clinician")
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        self.assertContains(self.client.get(reverse("settings_core:roles") + "?lang=ar"), "الدكتور أو الأخصائي")  # still worn by someone: kept (S06)
        from accounts.models import UserProfile

        UserProfile.objects.filter(role__code=RoleCode.DOCTOR).update(role=Role.objects.get(code=RoleCode.CASHIER))
        self.assertNotContains(self.client.get(reverse("settings_core:roles") + "?lang=ar"), "الدكتور أو الأخصائي")  # a shop has no doctor

    def test_a_custom_role_with_the_permission_is_a_clinician(self):
        """The owner's own «reception who also treats» role: a copy of the cashier plus treating patients."""

        from accounts.models import UserProfile

        role = make_role(code="custom_77", is_system_role=False)
        for code in ("sales.create_sales_invoice", "sales.view_sales_invoices", "medical.treat_patients"):
            RolePermission.objects.create(role=role, permission=Permission.objects.get(code=code))
        UserProfile.objects.filter(user=self.users["desk"]).update(role=role)
        self.assertEqual(self.status("desk", reverse("medical:file", args=[self.patient.pk])), 200)
        self.assertEqual(self.status("desk", reverse("medical:queue")), 200)


class SpecialtyScreensTests(Clinic):
    def test_dental_chart(self):
        from . import dental

        self.set_sub_activity("dental")
        dental.set_tooth(self.patient, 16, "caries", self.owner, "ZZTOOTHNOTE")
        self.assert_matrix([reverse("medical:dental", args=[self.patient.pk])])

    def test_lab_results_sheet_and_tests(self):
        from . import lab

        self.set_sub_activity("lab")
        test = lab.save_test({"name": "فصيلة الدم", "unit": "", "low": "", "high": ""}, self.owner)
        day = timezone.localdate() - timedelta(days=1)
        lab.record_results(self.patient, day, {test: ("", "ZZLABTEXT")}, self.owner)
        self.assert_matrix([reverse("medical:lab", args=[self.patient.pk]), reverse("medical:lab_sheet", args=[self.patient.pk, day.isoformat()]),
                            reverse("medical:lab_tests")])

    def test_physio_packages(self):
        self.set_sub_activity("physio")
        self.assert_matrix([reverse("medical:packages", args=[self.patient.pk])])

    def test_vet_animal_card(self):
        from . import vet

        self.set_sub_activity("vet")
        animal = vet.save_animal(self.patient, {"name": "روكي", "species": "dog", "allergies": "ZZVETALLERGY"}, self.owner)
        self.assert_matrix([reverse("medical:animal", args=[animal.pk]), reverse("medical:animal_new", args=[self.patient.pk])])


class FrontDeskTests(Clinic):
    """Reception books, queues and bills without the clinical text."""

    def test_the_queue_is_reception_and_the_clinicians(self):
        from . import queue

        self.assert_matrix([reverse("medical:queue"), reverse("medical:queue_screen")], allowed=("owner", "manager", "doctor", "desk", "cashier"),
                           denied=("keeper", "nobody", "switched_off"))  # a stock keeper on the payroll no longer works the waiting list
        self.assertIsNone(queue._doctor_of(self.users["desk"]))  # linked, but not a doctor
        self.assertEqual(queue._doctor_of(self.users["doctor"]), self.sara)

    def test_reception_checks_in_and_calls_without_becoming_the_doctor(self):
        from . import queue

        self.client.force_login(self.users["desk"])
        response = self.client.post(reverse("medical:queue"), {"action": "check_in", "appointment": self.appointment.pk})
        self.assertEqual(response.status_code, 302)
        ticket = queue.today_tickets().get()
        self.assertEqual(ticket.doctor_id, self.sara.pk)
        walk_in = queue.check_in(make_customer(customer_code="P-2", name="علي"), self.users["desk"])
        queue.move(walk_in, "called", self.users["desk"])
        walk_in.refresh_from_db()
        self.assertIsNone(walk_in.doctor_id)  # calling a patient in does not make the receptionist their doctor
        page = self.client.get(reverse("medical:queue") + "?q=منى")
        self.assertContains(page, f'data-found="{self.patient.pk}"')  # search by name
        self.assertContains(self.client.get(reverse("medical:queue") + "?q=01001234567"), f'data-found="{self.patient.pk}"')  # and by phone
        self.assert_no_clinical_text(page)
        self.assertNotContains(page, reverse("medical:file", args=[self.patient.pk]))
        self.assertNotContains(page, reverse("medical:visit_new", args=[self.patient.pk]))

    def test_reception_screens_open_without_clinical_text(self):
        self.client.force_login(self.users["desk"])
        pages = (reverse("appointments:agenda"), reverse("appointments:agenda") + f"?day={timezone.localdate().isoformat()}",
                 reverse("appointments:detail", args=[self.appointment.pk]), reverse("appointments:week"), reverse("appointments:week_json"),
                 reverse("medical:queue"), reverse("medical:insurance"), reverse("search:results") + "?q=منى",
                 reverse("search:results") + "?q=01001234567", reverse("dashboard_snapshot"), reverse("master_data:customers"))
        for url in pages:
            with self.subTest(url=url):
                self.assert_no_clinical_text(self.client.get(url))
        detail = self.client.get(reverse("appointments:detail", args=[self.appointment.pk]))
        self.assertNotContains(detail, "data-appointment-visit")  # no "record the visit" button for reception
        self.assertContains(self.client.get(reverse("search:results") + "?q=منى"), "منى علي")  # reception still finds the patient

    def test_the_customer_card_offers_the_file_only_to_clinicians(self):
        from accounts.models import UserProfile

        role = make_role(code="custom_78", is_system_role=False)  # reception that also reads customer cards
        for code in ("sales.create_sales_invoice", "sales.view_sales_invoices", "reports.view_customer_report"):
            RolePermission.objects.create(role=role, permission=Permission.objects.get(code=code))
        UserProfile.objects.filter(user=self.users["desk"]).update(role=role)
        self.client.force_login(self.users["desk"])
        card = self.client.get(reverse("parties:card", args=["customer", self.patient.pk]))
        self.assert_no_clinical_text(card)
        self.assertNotContains(card, reverse("medical:file", args=[self.patient.pk]))
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("parties:card", args=["customer", self.patient.pk])), reverse("medical:file", args=[self.patient.pk]))

    def test_insurance_billing_stays_with_reception(self):
        from . import insurance

        contract = insurance.save_contract({"name": "مصر للتأمين", "coverage_percent": "80", "claim_day": "5"}, self.owner)
        insurance.set_cover(self.patient, {"contract": contract, "member_number": "MI-1"}, self.owner)
        from appointments.services import set_status

        set_status(self.appointment, "arrived", self.owner)
        self.client.force_login(self.users["desk"])
        response = self.client.post(reverse("appointments:detail", args=[self.appointment.pk]), {"action": "bill_insured"})
        self.assertIn(response.status_code, (200, 302))
        self.assertIsNotNone(insurance.bill_of(self.appointment))  # reception billed the insured visit
        month = timezone.localdate().strftime("%Y-%m")
        for url in (reverse("medical:insurance"), reverse("medical:insurance_contract", args=[contract.pk]) + f"?month={month}",
                    reverse("medical:insurance_claim", args=[contract.pk]) + f"?month={month}"):
            with self.subTest(url=url):
                self.assert_no_clinical_text(self.client.get(url))

    def test_the_dashboard_tiles(self):
        self.client.force_login(self.users["desk"])
        page = self.client.get(reverse("dashboard_snapshot"))
        self.assert_no_clinical_text(page)
        self.assertNotContains(page, 'data-tile="follow_missed"')  # the clinic's follow-ups are the practice's view
        self.assertNotContains(page, 'data-tile="queue_waiting"')
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard_snapshot")), 'data-tile="follow_missed"')


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1", DEBUG=True)
class DemoClinicTests(TestCase):
    """The seeded demo clinic still works: its owner is the doctor (د. سارة حسين); its cashier is reception."""

    def setUp(self):
        from django.core.management import call_command

        from settings_core import activity_fit
        from settings_core.management.commands.prepare_demo import Command as Prepare
        from settings_core.models import ClientProfile
        from settings_core.setup_services import complete_setup

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_setup(ClientProfile.get_active(), "medical", "clinic", ",".join(activity_fit.default_modules("medical", "clinic")))
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        prepare = Prepare()
        prepare._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        prepare._extras()

    def test_the_doctors_read_and_write_and_reception_does_not(self):
        from django.contrib.auth import get_user_model

        from staff.models import Employee

        from .models import ClinicalVisit

        users = get_user_model().objects
        visit = ClinicalVisit.objects.exclude(diagnosis="").select_related("patient", "doctor").first()
        patient = visit.patient
        new_visit = {"complaint": "متابعة", "diagnosis": "ZZDEMODIAG", "visit_date": timezone.localdate().isoformat(), "doctor": visit.doctor_id}
        self.client.force_login(users.get(username="owner"))  # the demo's owner is the clinic's doctor
        self.assertContains(self.client.get(reverse("medical:file", args=[patient.pk])), visit.diagnosis)
        self.assertEqual(self.client.post(reverse("medical:visit_new", args=[patient.pk]), new_visit).status_code, 302)
        self.assertTrue(ClinicalVisit.objects.filter(diagnosis="ZZDEMODIAG").exists())

        doctor = Employee.objects.get(pk=visit.doctor_id)  # the demo's other doctor, given a login with the doctor role
        login = make_user(username="demo_doctor")
        make_user_profile(user=login, role=Role.objects.get(code=RoleCode.DOCTOR))
        doctor.user = login
        doctor.save()
        self.client.force_login(login)
        self.assertContains(self.client.get(reverse("medical:file", args=[patient.pk])), visit.diagnosis)
        self.assertEqual(self.client.post(reverse("medical:visit_new", args=[patient.pk]), dict(new_visit, diagnosis="ZZDEMODIAG2")).status_code, 302)
        self.assertEqual(ClinicalVisit.objects.get(diagnosis="ZZDEMODIAG2").created_by, login)
        self.assertEqual(self.client.get(reverse("medical:queue")).status_code, 200)

        self.client.force_login(users.get(username="cashier"))  # reception
        self.assertEqual(self.client.get(reverse("medical:file", args=[patient.pk])).status_code, 403)
        for url in (reverse("medical:queue"), reverse("appointments:agenda"), reverse("dashboard_snapshot")):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                self.assertNotContains(page, visit.diagnosis)
                self.assertNotContains(page, "ZZDEMODIAG")
