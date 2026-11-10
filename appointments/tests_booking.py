"""S3 (HG-077): working hours, the free-slot engine, the public online booking page and its settings screen."""

from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest import mock

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from entities.current import working_in
from entities.models import Entity
from entities.services import main_entity
from hesba_testing.factories import make_customer, make_item, make_seeded_role, make_user, make_user_profile
from master_data.models import Customer
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import default_selection, set_capability_enabled
from staff.services import save_employee

from . import booking as engine
from . import services, views_booking
from .models import Appointment, AppointmentStatus, BookableService, BookingSettings, BookingStaff, WorkingHours, Weekday


MODULES = "customers,items_services,sales_operations,cashboxes,reports,employees_technicians,appointments_visits"
TUESDAY = date(2026, 11, 10)  # a fixed day for the engine (now is passed in); Friday 13 is the closed day


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def hhmm(slots):
    return [f"{timezone.localtime(slot['start']):%H:%M}" for slot in slots]


def open_day(after_days=2):
    """A day at least ``after_days`` ahead that is not a Friday (the closed day in these tests)."""

    day = timezone.localdate() + timedelta(days=after_days)
    while day.weekday() == Weekday.FRIDAY:
        day += timedelta(days=1)
    return day


class BookingSetup(TestCase):
    ACTIVITY = ("services", "maintenance")  # M4: the clinic's tests run the same setup on a medical install

    def setUp(self):
        cache.clear()  # the throttle counters live in the cache
        self.profile = prepared_client(*self.ACTIVITY, MODULES)
        self.owner = person(RoleCode.OWNER, "bk_owner")
        set_capability_enabled(self.profile, "online_booking", True, user=self.owner)
        self.entity = main_entity()
        self.item = make_item(item_code="SRV-CUT", item_name="قص وسشوار", default_sale_price="250.00", is_stock_tracked=False)
        self.hidden_item = make_item(item_code="SRV-HID", item_name="خدمة مش أونلاين", default_sale_price="999.00", is_stock_tracked=False)
        self.mona = save_employee({"name": "منى", "title": "مصففة", "phone": "01011112222"}, self.owner)
        self.huda = save_employee({"name": "هدى", "title": "ألوان"}, self.owner)
        self.booking = engine.settings_for(self.entity)
        self.booking.accepting, self.booking.slot_minutes, self.booking.min_notice_minutes, self.booking.max_days_ahead = True, 30, 60, 30
        self.booking.save()
        for weekday in range(7):
            if weekday != Weekday.FRIDAY:
                WorkingHours.objects.create(entity=self.entity, weekday=weekday, opens=time(10), closes=time(20))
        self.service = BookableService.objects.create(entity=self.entity, item=self.item, duration_minutes=45)
        BookingStaff.objects.create(entity=self.entity, employee=self.mona)
        BookingStaff.objects.create(entity=self.entity, employee=self.huda)
        self.customer = make_customer(customer_code="C-1", name="مدام سعاد", phone="01001234567")

    def hold(self, employee, start, minutes=60, status=AppointmentStatus.BOOKED, entity=None):
        return Appointment.objects.create(number=f"AP-T-{Appointment.objects.count() + 1}", customer=self.customer, employee=employee, starts_at=start,
                                          duration_minutes=minutes, status=status, created_by=self.owner, entity=entity)

    def slots(self, day=TUESDAY, employee=None, now=None, service=None):
        return engine.free_slots(self.booking, service or self.service, day, employee, now or at(day, 8))


# ---- the slot engine ---------------------------------------------------------------


class SlotEngineTests(BookingSetup):
    def test_the_grid_fits_the_service_inside_the_opening_hours(self):
        WorkingHours.objects.filter(entity=self.entity, employee__isnull=True, weekday=TUESDAY.weekday()).update(opens=time(10), closes=time(12))
        self.assertEqual(hhmm(self.slots(employee=self.mona)), ["10:00", "10:30", "11:00"])  # 11:30 + 45 min passes 12:00
        self.booking.slot_minutes = 60
        self.booking.save()
        self.assertEqual(hhmm(self.slots(employee=self.mona)), ["10:00", "11:00"])

    def test_a_closed_day_has_no_slots(self):
        friday = TUESDAY + timedelta(days=3)
        self.assertEqual(friday.weekday(), Weekday.FRIDAY)
        self.assertEqual(self.slots(day=friday, now=at(TUESDAY, 8)), [])
        self.assertTrue(self.slots(day=TUESDAY + timedelta(days=4), now=at(TUESDAY, 8)))  # Saturday is open

    def test_appointments_that_hold_time_block_overlapping_slots_and_cancelled_ones_do_not(self):
        self.hold(self.mona, at(TUESDAY, 11), minutes=60)  # 11:00-12:00
        mona = hhmm(self.slots(employee=self.mona))
        self.assertNotIn("10:30", mona)  # 10:30-11:15 overlaps
        self.assertNotIn("11:00", mona)
        self.assertNotIn("11:30", mona)
        self.assertIn("10:00", mona)  # 10:00-10:45 ends before
        self.assertIn("12:00", mona)  # back to back is fine, as in _check_overlap
        self.hold(self.mona, at(TUESDAY, 14), status=AppointmentStatus.CANCELLED)
        self.hold(self.mona, at(TUESDAY, 15), status=AppointmentStatus.NO_SHOW)
        self.assertIn("14:00", hhmm(self.slots(employee=self.mona)))
        self.assertIn("15:00", hhmm(self.slots(employee=self.mona)))
        self.hold(self.mona, at(TUESDAY, 16), status=AppointmentStatus.DONE)
        self.assertNotIn("16:00", hhmm(self.slots(employee=self.mona)))

    def test_the_overlap_is_group_wide_like_the_booking_service(self):
        other = Entity.objects.create(code="B2", name_ar="فرع المهندسين")
        self.hold(self.mona, at(TUESDAY, 13), entity=other)  # her diary is one across entities
        self.assertNotIn("13:00", hhmm(self.slots(employee=self.mona)))

    def test_an_appointment_starting_the_evening_before_is_read(self):
        WorkingHours.objects.filter(entity=self.entity, employee__isnull=True, weekday=TUESDAY.weekday()).update(opens=time(0, 30), closes=time(5))
        self.hold(self.mona, at(TUESDAY - timedelta(days=1), 23, 30), minutes=90)  # runs to 01:00
        self.assertEqual(hhmm(self.slots(employee=self.mona, now=at(TUESDAY - timedelta(days=1), 12)))[0], "01:00")

    def test_the_minimum_notice(self):
        self.assertEqual(hhmm(self.slots(employee=self.mona, now=at(TUESDAY, 9, 50)))[0], "11:00")  # 10:50 is the earliest
        self.booking.min_notice_minutes = 0
        self.booking.save()
        self.assertEqual(hhmm(self.slots(employee=self.mona, now=at(TUESDAY, 10, 5)))[0], "10:30")  # never in the past

    def test_the_days_ahead_window(self):
        self.booking.max_days_ahead = 7
        self.booking.save()
        now = at(TUESDAY, 8)
        self.assertTrue(self.slots(day=TUESDAY + timedelta(days=7), now=now))
        self.assertEqual(self.slots(day=TUESDAY + timedelta(days=8), now=now), [])
        self.assertEqual(self.slots(day=TUESDAY - timedelta(days=1), now=now), [])

    def test_an_employee_works_their_own_hours_inside_the_entity_hours(self):
        WorkingHours.objects.create(entity=self.entity, employee=self.mona, weekday=TUESDAY.weekday(), opens=time(18), closes=time(23))
        self.assertEqual(hhmm(self.slots(employee=self.mona)), ["18:00", "18:30", "19:00"])  # clipped at the shop's 20:00
        self.assertEqual(hhmm(self.slots(employee=self.huda))[0], "10:00")  # no own week: the entity's hours
        wednesday = TUESDAY + timedelta(days=1)
        self.assertEqual(self.slots(day=wednesday, employee=self.mona, now=at(TUESDAY, 8)), [])  # her own week skips Wednesday

    def test_any_employee_lists_who_is_free_and_never_a_slot_without_one(self):
        self.hold(self.mona, at(TUESDAY, 10), minutes=120)
        slot = next(row for row in self.slots() if row["start"] == at(TUESDAY, 10))
        self.assertEqual(slot["employees"], [self.huda])
        self.hold(self.huda, at(TUESDAY, 10), minutes=120)
        self.assertNotIn("10:00", hhmm(self.slots()))
        BookingStaff.objects.all().delete()
        self.assertEqual(self.slots(), [])  # nobody takes bookings: no slot, never an unassigned one

    def test_the_cairo_day_around_midnight(self):
        self.booking.max_days_ahead, self.booking.min_notice_minutes = 0, 0
        self.booking.save()
        now = at(TUESDAY, 0, 30)  # 22:30 UTC on Monday: the UTC date is still the day before
        self.assertEqual(now.astimezone(dt_timezone.utc).date(), TUESDAY - timedelta(days=1))
        self.assertEqual(engine.booking_days(self.booking, now), (TUESDAY, TUESDAY))
        slots = self.slots(now=now)
        self.assertEqual(timezone.localtime(slots[0]["start"]).time(), time(10))
        self.assertEqual(slots[0]["start"].astimezone(dt_timezone.utc).hour, 8)  # Cairo is UTC+2 in November
        self.assertEqual(self.slots(day=TUESDAY - timedelta(days=1), now=now), [])

    def test_hours_rules_are_enforced_by_the_database(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkingHours.objects.create(entity=self.entity, weekday=TUESDAY.weekday(), opens=time(9), closes=time(10))  # a second shop row
        with self.assertRaises(IntegrityError), transaction.atomic():
            WorkingHours.objects.create(entity=self.entity, employee=self.mona, weekday=0, opens=time(12), closes=time(11))


# ---- the public booking service ------------------------------------------------------


class BookOnlineServiceTests(BookingSetup):
    def test_books_in_the_page_entity_and_reuses_the_customer_by_phone(self):
        other = Entity.objects.create(code="B2", name_ar="فرع المهندسين")
        branch = engine.settings_for(other)
        branch.accepting = True
        branch.save()
        WorkingHours.objects.create(entity=other, weekday=TUESDAY.weekday(), opens=time(10), closes=time(20))
        BookingStaff.objects.create(entity=other, employee=self.huda)
        service = BookableService.objects.create(entity=other, item=self.item, duration_minutes=30)
        with mock.patch.object(timezone, "now", return_value=at(TUESDAY, 8)):
            appointment = engine.book_online(branch, service, None, at(TUESDAY, 11), "اسم تاني", "+20 100 123 4567")
        self.assertEqual((appointment.entity, appointment.employee, appointment.customer), (other, self.huda, self.customer))
        self.assertEqual((appointment.status, appointment.duration_minutes, appointment.price), (AppointmentStatus.BOOKED, 30, Decimal("250.00")))
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.name, "مدام سعاد")  # public input never changes the card
        with working_in(other):
            self.assertIn(appointment, services.scoped())
        with working_in(self.entity):
            self.assertNotIn(appointment, services.scoped())
        self.assertEqual(appointment.created_by.username, engine.SYSTEM_USERNAME)
        self.assertFalse(appointment.created_by.is_active)
        self.assertFalse(appointment.created_by.has_usable_password())
        self.assertTrue(AuditLog.objects.filter(action="book_online", object_id=str(appointment.pk)).exists())

    def test_a_new_phone_makes_a_new_customer_and_the_walk_in_is_never_matched(self):
        from sales.pos import walk_in_customer

        walk_in = walk_in_customer()
        walk_in.phone = "01099998888"
        walk_in.save()
        with mock.patch.object(timezone, "now", return_value=at(TUESDAY, 8)):
            appointment = engine.book_online(self.booking, self.service, self.mona, at(TUESDAY, 12), "مروة السيد", "٠١٠٩٩٩٩٨٨٨٨")
        self.assertNotEqual(appointment.customer, walk_in)
        self.assertEqual((appointment.customer.name, appointment.customer.phone), ("مروة السيد", "01099998888"))

    def test_refuses_what_is_not_a_free_slot(self):
        with mock.patch.object(timezone, "now", return_value=at(TUESDAY, 8)):
            for start in (at(TUESDAY, 10, 10), at(TUESDAY + timedelta(days=3), 11), at(TUESDAY, 7), at(TUESDAY + timedelta(days=40), 11)):
                with self.subTest(start=start), self.assertRaises(ValidationError) as caught:
                    engine.book_online(self.booking, self.service, self.mona, start, "مروة", "01012345678")
                self.assertEqual(caught.exception.code, "taken")
            for name, phone, code in (("", "01012345678", "name"), ("مروة", "12", "phone"), ("مروة", "0101abc5678", "phone")):
                with self.subTest(code=code), self.assertRaises(ValidationError) as caught:
                    engine.book_online(self.booking, self.service, self.mona, at(TUESDAY, 11), name, phone)
                self.assertEqual(caught.exception.code, code)
        self.assertFalse(Appointment.objects.exists())

    def test_a_slot_taken_under_the_lock_tries_the_next_employee_then_refuses(self):
        real_slots = engine.free_slots

        def stale(*args, **kwargs):  # the list as the page saw it, before someone else booked
            return [{"start": at(TUESDAY, 11), "employees": [self.mona, self.huda]}]

        self.hold(self.mona, at(TUESDAY, 11))
        with mock.patch.object(engine, "free_slots", stale), mock.patch.object(timezone, "now", return_value=at(TUESDAY, 8)):
            appointment = engine.book_online(self.booking, self.service, None, at(TUESDAY, 11), "مروة", "01012345678")
            self.assertEqual(appointment.employee, self.huda)
            with self.assertRaises(ValidationError) as caught:
                engine.book_online(self.booking, self.service, None, at(TUESDAY, 11), "مروة", "01012345678")
        self.assertEqual(caught.exception.code, "taken")
        self.assertNotIn("منى", caught.exception.messages[0])  # the clash names nobody to the public
        self.assertEqual(Appointment.objects.filter(starts_at=at(TUESDAY, 11)).count(), 2)
        self.assertIs(engine.free_slots, real_slots)


# ---- the public page ---------------------------------------------------------------


class PublicPageTests(BookingSetup):
    def url(self, **query):
        from urllib.parse import urlencode

        return reverse("book_page", args=[self.booking.public_key]) + ("?" + urlencode(query) if query else "")

    def post(self, day, clock="11:00", staff=None, client=None, **extra):
        data = {"lang": "ar", "service": self.service.pk, "staff": staff or self.mona.pk, "day": day.isoformat(), "time": clock,
                "name": "مروة السيد", "phone": "01012345678", "notes": "", "website": ""}
        data.update(extra)
        return (client or self.client).post(self.url(), data)

    def test_404_unless_the_feature_the_module_and_the_entity_say_yes(self):
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assertEqual(self.client.get(reverse("book_page", args=["nope"])).status_code, 404)
        self.booking.accepting = False
        self.booking.save()
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.booking.accepting = True
        self.booking.save()
        set_capability_enabled(self.profile, "online_booking", False, user=self.owner)
        self.assertEqual(self.client.get(self.url()).status_code, 404)
        self.assertEqual(self.post(open_day()).status_code, 404)
        self.assertFalse(Appointment.objects.exists())

    def test_the_capability_is_off_by_default(self):
        self.assertNotIn("online_booking", default_selection("services", "maintenance"))
        self.assertNotIn("online_booking", default_selection("commercial", "retail"))

    def test_the_steps_show_only_bookable_services_and_free_times_without_prices_by_default(self):
        page = self.client.get(self.url())
        self.assertContains(page, "قص وسشوار")
        self.assertNotContains(page, "خدمة مش أونلاين")
        self.assertNotContains(page, "250")
        self.assertNotContains(page, "data-booking-price")
        staff = self.client.get(self.url(service=self.service.pk))
        self.assertContains(staff, 'data-booking-staff="any"')
        self.assertContains(staff, "منى")
        day = open_day()
        times = self.client.get(self.url(service=self.service.pk, staff=self.mona.pk, day=day.isoformat()))
        self.assertContains(times, 'data-booking-slot="11:00"')
        self.assertContains(times, f'data-booking-day="{day.isoformat()}"')
        details = self.client.get(self.url(service=self.service.pk, staff=self.mona.pk, day=day.isoformat(), time="11:00"))
        self.assertContains(details, "data-booking-form")
        self.assertContains(details, 'name="website"')
        self.assertContains(details, "csrfmiddlewaretoken")
        self.booking.show_prices = True
        self.booking.save()
        self.assertContains(self.client.get(self.url()), "data-booking-price")

    def test_the_confirmation_shows_the_booked_price_not_todays_list_price(self):
        self.booking.show_prices = True
        self.booking.save()
        response = self.post(open_day())
        self.assertEqual(response.status_code, 303)
        self.assertEqual(Appointment.objects.get().price, Decimal("250.00"))
        self.item.default_sale_price = Decimal("300.00")  # the list price moves after the booking
        self.item.save()
        done = self.client.get(response["Location"])
        self.assertContains(done, "250")
        self.assertNotContains(done, "300")

    def test_end_to_end_books_and_confirms(self):
        day = open_day()
        response = self.post(day)
        self.assertEqual(response.status_code, 303)
        appointment = Appointment.objects.get()
        self.assertEqual((appointment.employee, appointment.entity, appointment.service, appointment.duration_minutes),
                         (self.mona, self.entity, self.item, 45))
        self.assertEqual(timezone.localtime(appointment.starts_at), at(day, 11))
        self.assertEqual(appointment.customer.name, "مروة السيد")
        done = self.client.get(response["Location"])
        self.assertContains(done, appointment.number)
        self.assertContains(done, "تم حجز ميعادك")
        self.assertEqual(done["X-Robots-Tag"], "noindex, nofollow")
        # the same phone typed another way books as the same customer
        self.post(day, clock="12:00", phone="+20 101 234 5678", name="اسم مختلف")
        self.assertEqual(Customer.objects.filter(name="مروة السيد").count(), 1)
        self.assertEqual(Appointment.objects.filter(customer=appointment.customer).count(), 2)

    def test_race_a_slot_taken_between_the_page_and_the_submit_is_refused(self):
        day = open_day()
        page = self.client.get(self.url(service=self.service.pk, staff=self.mona.pk, day=day.isoformat(), time="11:00"))
        self.assertContains(page, "data-booking-form")
        self.hold(self.mona, at(day, 10, 30))  # a receptionist books her 10:30-11:30 meanwhile
        response = self.post(day)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "اتحجز", status_code=409)
        self.assertNotContains(response, "عنده ميعاد تاني", status_code=409)
        self.assertNotContains(response, "10:30", status_code=409)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_any_employee_books_whoever_is_free(self):
        day = open_day()
        self.hold(self.mona, at(day, 11))
        self.assertEqual(self.post(day, staff="any").status_code, 303)
        self.assertEqual(Appointment.objects.exclude(employee=self.mona).get().employee, self.huda)

    def test_a_crafted_time_is_refused(self):
        day = open_day()
        for clock, when in (("11:10", day), ("11:00", day + timedelta(days=60)), ("23:00", day)):
            with self.subTest(clock=clock, day=when):
                self.assertEqual(self.post(when, clock=clock).status_code, 409)
        self.assertFalse(Appointment.objects.exists())

    def test_honeypot(self):
        response = self.post(open_day(), website="http://spam.example")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Appointment.objects.exists())
        self.assertFalse(Customer.objects.filter(name="مروة السيد").exists())

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(self.post(open_day(), client=client).status_code, 403)
        self.assertFalse(Appointment.objects.exists())

    def test_throttle_per_phone_and_per_address(self):
        day = open_day()
        with mock.patch.object(views_booking, "BOOK_PHONE_LIMIT", 2):
            for _ in range(2):
                self.assertEqual(self.post(day, name="").status_code, 400)  # counted, refused for the empty name
            self.assertEqual(self.post(day).status_code, 429)
            self.assertEqual(self.post(day, phone="01155556666").status_code, 303)  # another phone still books
        cache.clear()
        with mock.patch.object(views_booking, "BOOK_IP_LIMIT", 1):
            self.assertEqual(self.post(day, clock="13:00", name="").status_code, 400)
            self.assertEqual(self.post(day, clock="13:00", phone="01277778888").status_code, 429)
        with mock.patch.object(views_booking, "PAGE_LIMIT", 0):
            self.assertEqual(self.client.get(self.url()).status_code, 429)

    def test_english_and_arabic(self):
        page = self.client.get(self.url(lang="en"))
        self.assertContains(page, 'dir="ltr"')
        self.assertContains(page, "Book your appointment")
        arabic = self.client.get(self.url())
        self.assertContains(arabic, 'dir="rtl"')
        self.assertContains(arabic, "احجز ميعادك")
        response = self.post(open_day(), name="", lang="en")
        self.assertContains(response, "Enter your name.", status_code=400)

    def test_no_staff_links_and_nothing_sensitive(self):
        day = open_day()
        self.hold(self.mona, at(day, 15))  # مدام سعاد's appointment
        pages = [self.client.get(self.url()), self.client.get(self.url(service=self.service.pk, staff=self.mona.pk, day=day.isoformat())),
                 self.client.get(self.url(service=self.service.pk, staff=self.mona.pk, day=day.isoformat(), time="11:00"))]
        for page in pages:
            with self.subTest(page=page.request["QUERY_STRING"]):
                self.assertEqual(page.status_code, 200)
                self.assertIn("no-store", page["Cache-Control"])
                self.assertEqual(page["X-Robots-Tag"], "noindex, nofollow")
                for text in ("hs-app", "app_shell", "/appointments/", "/sales/", "/master-data/", "مدام سعاد", "01001234567", "01011112222",
                             "خدمة مش أونلاين", "logout"):
                    self.assertNotContains(page, text)

    def test_the_confirmation_link_is_signed_and_tied_to_the_page(self):
        response = self.post(open_day())
        done = response["Location"]
        self.assertEqual(self.client.get(done).status_code, 200)
        forged = reverse("book_done", args=[self.booking.public_key, "abc:def"])
        self.assertEqual(self.client.get(forged).status_code, 404)
        other = Entity.objects.create(code="B2", name_ar="فرع 2")
        branch = engine.settings_for(other)
        branch.accepting = True
        branch.save()
        token = done.split("/done/")[1].split("/")[0]
        self.assertEqual(self.client.get(reverse("book_done", args=[branch.public_key, token])).status_code, 404)

    def test_the_deposit_policy_is_only_text(self):
        set_capability_enabled(self.profile, "deposits", True, user=self.owner)
        day = open_day()
        details = self.client.get(self.url(service=self.service.pk, staff=self.mona.pk, day=day.isoformat(), time="11:00"))
        self.assertContains(details, "data-booking-deposit")
        self.post(day)
        from deposits.models import Deposit

        self.assertFalse(Deposit.objects.exists())  # nothing is taken online

    def test_no_free_times_offers_the_next_one(self):
        day = open_day()
        self.hold(self.mona, at(day, 10), minutes=600)
        page = self.client.get(self.url(service=self.service.pk, staff=self.mona.pk, day=day.isoformat()))
        self.assertContains(page, "data-booking-empty")
        self.assertContains(page, "data-booking-suggestion")


# ---- the settings screen -----------------------------------------------------------


class BookingSettingsScreenTests(BookingSetup):
    def form(self, **extra):
        data = {"lang": "ar", "accepting": "1", "slot_minutes": "15", "min_notice_minutes": "120", "max_days_ahead": "14",
                f"service_{self.item.pk}": "1", f"duration_{self.item.pk}": "60", f"staff_{self.huda.pk}": "1",
                f"day_{Weekday.SATURDAY}_on": "1", f"day_{Weekday.SATURDAY}_opens": "09:00", f"day_{Weekday.SATURDAY}_closes": "17:00",
                f"emp_{self.huda.pk}_day_{Weekday.SATURDAY}_on": "1", f"emp_{self.huda.pk}_day_{Weekday.SATURDAY}_opens": "12:00",
                f"emp_{self.huda.pk}_day_{Weekday.SATURDAY}_closes": "16:00"}
        data.update(extra)
        return data

    def test_who_may_open_it(self):
        url = reverse("appointments:booking_settings")
        self.assertEqual(self.client.get(url).status_code, 302)  # signed out: to the login page
        for role, username, status in ((RoleCode.OWNER, "s_owner", 200), (RoleCode.MANAGER, "s_manager", 200), (RoleCode.CASHIER, "s_cashier", 403),
                                       (RoleCode.STOCK_KEEPER, "s_keeper", 403)):
            with self.subTest(role=role):
                self.client.force_login(person(role, username))
                self.assertEqual(self.client.get(url).status_code, status)
                self.assertEqual(self.client.post(url, self.form()).status_code, status if status != 200 else 302)

    def test_the_settings_page_carries_no_internal_note_in_either_language(self):
        self.client.force_login(self.owner)
        for lang, note in (("ar", "مقفول لحد ما تفعّله"), ("en", "Off until you switch it on")):
            page = self.client.get(reverse("appointments:booking_settings") + f"?lang={lang}")
            self.assertContains(page, note)
            self.assertNotContains(page, "أحمد")
            self.assertNotContains(page, "Ahmed")

    def test_no_user_facing_word_list_names_the_developer_as_a_decision_maker(self):
        from appointments import booking, services
        from appointments.views_booking import SETTINGS_WORDS

        for table in (SETTINGS_WORDS, booking.MESSAGES, services.MESSAGES):
            for lang, words in table.items():
                for key, text in words.items():
                    if isinstance(text, str):
                        self.assertNotRegex(text, r"أحمد\s+يقرر|until Ahmed|Ahmed decides", f"{lang}.{key}")

    def test_no_template_names_ahmed_except_the_developer_credit(self):
        import pathlib

        from django.conf import settings

        offenders = []
        for path in (pathlib.Path(settings.BASE_DIR) / "templates").rglob("*.html"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if ("أحمد" in line or "Ahmed" in line) and "credit" not in line:
                    offenders.append(f"{path.name}:{number}")
        self.assertEqual(offenders, [])

    def test_saves_hours_services_and_staff(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("appointments:booking_settings"), self.form(**{"show_prices": "1"}))
        self.assertEqual(response.status_code, 302)
        self.booking.refresh_from_db()
        self.assertEqual((self.booking.accepting, self.booking.slot_minutes, self.booking.min_notice_minutes, self.booking.max_days_ahead, self.booking.show_prices),
                         (True, 15, 120, 14, True))
        self.assertEqual(list(WorkingHours.objects.filter(entity=self.entity, employee__isnull=True).values_list("weekday", "opens", "closes")),
                         [(Weekday.SATURDAY, time(9), time(17))])
        self.assertEqual(list(WorkingHours.objects.filter(employee=self.huda).values_list("weekday", "opens", "closes")), [(Weekday.SATURDAY, time(12), time(16))])
        self.assertEqual(list(BookableService.objects.values_list("item", "duration_minutes")), [(self.item.pk, 60)])
        self.assertEqual(list(BookingStaff.objects.values_list("employee", flat=True)), [self.huda.pk])
        self.assertTrue(AuditLog.objects.filter(action="booking_settings", actor=self.owner).exists())
        page = self.client.get(reverse("appointments:booking_settings") + "?lang=en")
        self.assertContains(page, "Online booking")
        self.assertContains(page, 'name="service_%d" value="1" checked' % self.item.pk)

    def test_a_kept_service_keeps_its_id_so_open_booking_links_still_work(self):
        second = make_item(item_code="SRV-CLR", item_name="صبغة", default_sale_price="400.00", is_stock_tracked=False)
        dropped = BookableService.objects.create(entity=self.entity, item=second, duration_minutes=90)
        self.client.force_login(self.owner)
        self.client.post(reverse("appointments:booking_settings"), self.form())  # keeps the cut (now 60 min), drops the colour
        self.assertEqual(BookableService.objects.get(entity=self.entity, item=self.item).pk, self.service.pk)
        self.assertEqual(BookableService.objects.get(pk=self.service.pk).duration_minutes, 60)
        self.assertFalse(BookableService.objects.filter(pk=dropped.pk).exists())

    def test_refusals_keep_what_was_there(self):
        self.client.force_login(self.owner)
        url = reverse("appointments:booking_settings")
        for extra, text in (({f"day_{Weekday.SATURDAY}_closes": "08:00"}, "الفتح لازم قبل القفل"), ({"slot_minutes": "7"}, "مدة الخانة"),
                            ({"max_days_ahead": "0"}, "أقصى عدد أيام"), ({f"duration_{self.item.pk}": "2"}, "لازم من 5"),
                            ({f"day_{Weekday.SATURDAY}_on": ""}, "حدّد مواعيد الفتح"), ({f"staff_{self.huda.pk}": ""}, "موظف واحد على الأقل"),
                            ({f"emp_{self.huda.pk}_day_{Weekday.SATURDAY}_opens": "18:00"}, "مواعيد هدى")):
            with self.subTest(extra=extra):
                response = self.client.post(url, self.form(**extra))
                self.assertContains(response, text)
                self.assertEqual(WorkingHours.objects.filter(entity=self.entity, employee__isnull=True).count(), 6)  # unchanged
        self.assertEqual(self.client.post(url, self.form(accepting="", **{f"day_{Weekday.SATURDAY}_on": ""})).status_code, 302)  # closed: may be empty

    def test_public_link_comes_from_public_base_url_only_and_can_be_renewed(self):
        self.client.force_login(self.owner)
        url = reverse("appointments:booking_settings")
        with override_settings(PUBLIC_BASE_URL=""):
            self.assertContains(self.client.get(url), "data-booking-no-base")
        with override_settings(PUBLIC_BASE_URL="https://demo.hesba.example"):
            page = self.client.get(url)
            self.assertContains(page, f"https://demo.hesba.example/book/{self.booking.public_key}/")
        old = self.booking.public_key
        self.client.post(url, self.form(new_link="1"))
        self.booking.refresh_from_db()
        self.assertNotEqual(self.booking.public_key, old)
        self.assertEqual(self.client.get(reverse("book_page", args=[old])).status_code, 404)

    def test_the_feature_off_closes_the_screen_and_hides_the_agenda_link(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("appointments:agenda")), "data-agenda-booking-link")
        set_capability_enabled(self.profile, "online_booking", False, user=self.owner)
        self.assertEqual(self.client.get(reverse("appointments:booking_settings")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("appointments:agenda")), "data-agenda-booking-link")

    def test_a_branch_is_picked_in_the_whole_group_view(self):
        other = Entity.objects.create(code="B2", name_ar="فرع المهندسين")
        self.client.force_login(self.owner)
        url = reverse("appointments:booking_settings")
        self.assertContains(self.client.get(url), "فرع المهندسين")
        self.client.post(url, self.form(entity=str(other.pk)))
        self.assertTrue(BookingSettings.objects.get(entity=other).accepting)
        self.assertEqual(BookableService.objects.filter(entity=other).count(), 1)
        self.assertEqual(BookableService.objects.filter(entity=self.entity).get().duration_minutes, 45)  # the main entity's untouched
