"""M4: the demo clinic's public booking page is set up and open, with two doctors and the visits a patient books."""

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from appointments import booking
from appointments.models import Appointment, BookableService, BookingStaff, WorkingHours
from entities.services import main_entity
from master_data.models import Item
from reports.demo_catalogs import catalog_for
from settings_core import demo_clinic
from settings_core.capabilities import capability_enabled
from staff.models import Employee


class DemoClinicBookingTests(TestCase):
    def fill(self, activity, sub):
        """A demo template of one kind of business, built as ``prepare_demo_templates`` builds it (setup, a month of trade, the extras)."""

        from config.demo_templates import complete_demo_setup
        from settings_core.management.commands.prepare_demo import Command as Prepare

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_demo_setup(activity, sub)
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        prepare = Prepare()
        prepare._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        prepare._spread_sale_times()
        prepare._extras()
        return catalog_for(activity, sub)

    def test_the_demo_clinic_opens_with_its_booking_page_ready(self):
        self.fill("medical", "clinic")
        self.assertTrue(capability_enabled("online_booking"))
        page = booking.settings_for(main_entity())
        self.assertTrue(page.accepting)
        self.assertEqual(WorkingHours.objects.filter(employee__isnull=True).count(), 6)  # Friday is closed
        self.assertEqual(set(BookableService.objects.values_list("item__item_name", flat=True)), {"كشف", "إعادة كشف", "استشارة"})
        doctors = set(BookingStaff.objects.values_list("employee__name", flat=True))
        self.assertEqual(len(doctors), 2)
        self.assertIn("د. منى عبد الله", doctors)
        second = Employee.objects.get(name="د. منى عبد الله")
        self.assertEqual(WorkingHours.objects.filter(employee=second).count(), 6)  # her own afternoons
        url = reverse("book_page", args=[page.public_key])
        first = self.client.get(url)
        self.assertContains(first, "اختار نوع الكشف")  # the clinic's words, not the shop's
        self.assertContains(first, "إعادة كشف")
        self.assertNotContains(first, "اختار الخدمة")
        self.assertContains(self.client.get(url + "?lang=en"), "Choose the visit")

    def test_a_visitor_can_book_a_visit_on_the_demo_clinic(self):
        self.fill("medical", "clinic")
        page = booking.settings_for(main_entity())
        from datetime import timedelta

        from django.utils import timezone

        service = BookableService.objects.get(item__item_name="كشف")
        day = timezone.localdate() + timedelta(days=3)
        while day.weekday() == 4:
            day += timedelta(days=1)
        slots = booking.free_slots(page, service, day, None, timezone.now())
        self.assertTrue(slots)
        start = timezone.localtime(slots[0]["start"])
        before = Appointment.objects.count()
        response = self.client.post(reverse("book_page", args=[page.public_key]), {
            "lang": "ar", "service": service.pk, "staff": "any", "day": day.isoformat(), "time": f"{start:%H:%M}",
            "name": "زائر تجريبي", "phone": "01012345679", "notes": "", "website": ""})
        self.assertEqual(response.status_code, 303)
        self.assertEqual(Appointment.objects.count(), before + 1)

    def test_seeding_twice_adds_nothing_and_no_other_business_gets_it(self):
        catalog = self.fill("medical", "clinic")
        from django.contrib.auth import get_user_model

        from reports.management.commands.seed_demo_business import Command as Seed

        counts = (Employee.objects.count(), Item.objects.count(), BookableService.objects.count())
        master = Seed()._seed_master_data(catalog)
        owner = get_user_model().objects.get(username="owner")
        doctor = Employee.objects.exclude(name="د. منى عبد الله").first()
        self.assertFalse(demo_clinic.seed(master, doctor, owner, catalog, {"appointments"}))
        self.assertEqual((Employee.objects.count(), Item.objects.count(), BookableService.objects.count()), counts)

    def test_a_dental_or_services_clinic_demo_gets_none_of_it(self):
        for activity, sub in (("medical", "dental"), ("services", "clinic")):
            with self.subTest(activity=activity, sub=sub):
                from django.db import transaction

                with transaction.atomic():
                    self.fill(activity, sub)
                    self.assertFalse(Item.objects.filter(item_code=demo_clinic.SENTINEL).exists())
                    self.assertFalse(Employee.objects.filter(name="د. منى عبد الله").exists())
                    self.assertFalse(BookableService.objects.exists())
                    self.assertFalse(capability_enabled("online_booking"))
                    transaction.set_rollback(True)
