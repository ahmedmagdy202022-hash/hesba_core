"""S9: "my jobs today", the technician's phone screen: own jobs only, map links, quick actions through the existing services."""

import re
from datetime import datetime, time, timedelta

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from appointments import services as appointments
from appointments.models import Appointment, AppointmentStatus
from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_item, make_location, make_role, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from workorders import services as workorders
from workorders.models import Status

from . import myday
from .services import save_employee

TODAY = timezone.localdate()
MODULES = "customers,items_services,sales_operations,cashboxes,reports,employees_technicians,appointments_visits"
MAPS = "https://www.google.com/maps/search/?api=1&query="


def at(hour, minute=0, day=None):
    return timezone.make_aware(datetime.combine(day or TODAY, time(hour, minute)))


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def plain(username):
    """A technician login: a role with no permission at all, as in the work-order tests."""

    user = make_user(username=username)
    make_user_profile(user=user, role=make_role(f"ROLE-{username}"))
    return user


class MyDaySetup(TestCase):
    sub = "maintenance"

    def setUp(self):
        self.profile = prepared_client(activity="services", sub_activity=self.sub, modules=MODULES)
        self.owner = person(RoleCode.OWNER, "md_owner")
        make_location(location_code="SHOP", is_default=True)
        self.customer = make_customer(customer_code="C-1", name="مدام سعاد", phone="0100 123-4567")
        self.customer.address = "12 شارع التحرير\nالدقي - الجيزة"
        self.customer.save(update_fields=["address"])
        self.service = make_item(item_code="SRV-AC", item_name="صيانة تكييف", default_sale_price="300.00", is_stock_tracked=False)
        self.me = plain("md_tech")
        self.tech = save_employee({"name": "أحمد الفني", "user": str(self.me.pk)}, self.owner)
        self.other_user = plain("md_other")
        self.other = save_employee({"name": "فني تاني", "user": str(self.other_user.pk)}, self.owner)

    def visit(self, hour, *, employee=None, address="", kind="visit", customer=None, **extra):
        data = {"customer": customer or self.customer, "employee": employee or self.tech, "service": self.service, "starts_at": at(hour),
                "duration_minutes": 30, "kind": kind, "address": address or "x", **extra}
        if not address and kind == "visit":
            data["address"] = "عنوان مؤقت"
        appointment = appointments.book(data, self.owner)
        if not address:
            Appointment.objects.filter(pk=appointment.pk).update(address="")
            appointment.refresh_from_db()
        return appointment

    def order(self, technician=None, **extra):
        return workorders.receive({"customer": self.customer, "device": "غسالة", "problem": "مش بتعصر", "technician": technician or self.tech, **extra}, self.owner)

    def get(self, user, lang="ar"):
        self.client.force_login(user)
        return self.client.get(reverse("staff:my_day"), {"lang": lang})

    def post(self, user, **data):
        self.client.force_login(user)
        return self.client.post(reverse("staff:my_day"), {"lang": "ar", **{key: str(value) for key, value in data.items()}})


class OwnJobsOnlyTests(MyDaySetup):
    def test_a_technician_sees_their_own_visits_and_orders_and_not_a_colleagues(self):
        mine, theirs = self.visit(9, address="12 شارع الهرم"), self.visit(11, employee=self.other, address="99 شارع فيصل")
        my_order, their_order = self.order(), self.order(technician=self.other)
        page = self.get(self.me)
        self.assertContains(page, f'data-job="appointment-{mine.pk}"')
        self.assertContains(page, f'data-job="work_order-{my_order.pk}"')
        self.assertNotContains(page, f'data-job="appointment-{theirs.pk}"')
        self.assertNotContains(page, f'data-job="work_order-{their_order.pk}"')
        self.assertNotContains(page, "99 شارع فيصل")

    def test_only_todays_live_jobs_show(self):
        today_visit = self.visit(9, address="12 شارع الهرم")
        tomorrow = appointments.book({"customer": self.customer, "employee": self.tech, "service": self.service, "starts_at": at(9, day=TODAY + timedelta(days=1)),
                                      "duration_minutes": 30}, self.owner)
        cancelled = self.visit(13, address="5 شارع فيصل")
        appointments.set_status(cancelled, AppointmentStatus.CANCELLED, self.owner)
        ready, quoted = self.order(), self.order()
        workorders.move(ready, Status.IN_REPAIR, self.owner)
        workorders.move(ready, Status.READY, self.owner)
        workorders.move(quoted, Status.DIAGNOSED, self.owner)
        workorders.move(quoted, Status.QUOTED, self.owner)
        page = self.get(self.me)
        self.assertContains(page, f'data-job="appointment-{today_visit.pk}"')
        for hidden in (tomorrow, cancelled):
            self.assertNotContains(page, f'data-job="appointment-{hidden.pk}"')
        for hidden in (ready, quoted):  # waiting for pickup or for the customer: not on the bench
            self.assertNotContains(page, f'data-job="work_order-{hidden.pk}"')

    def test_timed_jobs_come_first_in_time_order_then_orders_late_first(self):
        late_visit, early_visit = self.visit(15, address="a"), self.visit(8, address="b")
        due = self.order(promised_on=TODAY.isoformat())
        past = self.order(promised_on=(TODAY - timedelta(days=2)).isoformat())
        undated = self.order()
        html = self.get(self.me).content.decode()
        positions = [html.index(f'data-job="{key}"') for key in (f"appointment-{early_visit.pk}", f"appointment-{late_visit.pk}",
                                                              f"work_order-{past.pk}", f"work_order-{due.pk}", f"work_order-{undated.pk}")]
        self.assertEqual(positions, sorted(positions))

    def test_a_manager_who_is_also_a_technician_sees_only_their_own_jobs_here(self):
        boss = save_employee({"name": "المعلم", "user": str(self.owner.pk)}, self.owner)
        mine = self.order(technician=boss)
        not_mine = self.order()
        page = self.get(self.owner)
        self.assertContains(page, f'data-job="work_order-{mine.pk}"')
        self.assertNotContains(page, f'data-job="work_order-{not_mine.pk}"')

    def test_who_may_open_the_screen(self):
        self.assertEqual(self.get(self.owner).status_code, 403)  # not linked to an employee
        self.assertEqual(self.get(person(RoleCode.CASHIER, "md_cashier")).status_code, 403)
        self.client.logout()
        response = self.client.get(reverse("staff:my_day"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])
        inactive = plain("md_gone")
        save_employee({"name": "فني سابق", "user": str(inactive.pk), "active": False}, self.owner)
        self.assertEqual(self.get(inactive).status_code, 403)

    def test_arabic_and_english(self):
        self.visit(9, address="12 شارع الهرم")
        self.assertContains(self.get(self.me), "شغلي النهارده")
        self.assertContains(self.get(self.me), "افتح الخريطة")
        english = self.get(self.me, "en")
        self.assertContains(english, "My jobs today")
        self.assertContains(english, "Open map")
        self.assertContains(english, 'dir="ltr"')

    def test_the_other_features_stay_off_when_their_module_or_activity_is(self):
        from settings_core.setup_services import set_module_enabled

        visit, order = self.visit(9, address="12 شارع الهرم"), self.order()
        set_module_enabled(self.profile, "appointments_visits", False, self.owner)
        page = self.get(self.me)
        self.assertNotContains(page, f'data-job="appointment-{visit.pk}"')
        self.assertContains(page, f'data-job="work_order-{order.pk}"')

    def test_an_empty_day(self):
        page = self.get(self.me)
        self.assertContains(page, "data-empty")
        self.assertNotContains(page, "data-route")


class NotAWorkshopTests(MyDaySetup):
    sub = "beauty"

    def test_no_work_orders_outside_a_workshop_but_visits_still_show(self):
        visit = self.visit(9, address="12 شارع الهرم")
        order = self.order()  # exists, but this is not a workshop: the work-order screens are closed, so it is not offered here either
        self.assertFalse(workorders.is_workshop())
        page = self.get(self.me)
        self.assertContains(page, f'data-job="appointment-{visit.pk}"')
        self.assertNotContains(page, f'data-job="work_order-{order.pk}"')
        self.assertNotContains(page, 'data-section="orders"')


class MapLinkTests(SimpleTestCase):
    def test_the_search_link_is_url_encoded(self):
        self.assertEqual(myday.map_link("12 شارع الهرم"), MAPS + "12%20%D8%B4%D8%A7%D8%B1%D8%B9%20%D8%A7%D9%84%D9%87%D8%B1%D9%85")
        self.assertEqual(myday.map_link("Tower A & B #5, Giza"), MAPS + "Tower%20A%20%26%20B%20%235%2C%20Giza")

    def test_lines_are_joined_and_no_address_gives_no_link(self):
        self.assertEqual(myday.map_link("12 Tahrir\n  Dokki "), MAPS + "12%20Tahrir%20Dokki")
        for empty in ("", "  \n ", None):
            self.assertEqual(myday.map_link(empty), "")

    def test_the_route_link_ends_at_the_last_stop_with_the_rest_as_waypoints(self):
        base = "https://www.google.com/maps/dir/?api=1&destination="
        self.assertEqual(myday.route_link(["A 1", "B & 2", "C"]), base + "C&waypoints=A%201%7CB%20%26%202")
        self.assertEqual(myday.route_link(["A 1"]), base + "A%201")  # one stop: no waypoints
        self.assertEqual(myday.route_link(["", None]), "")
        self.assertEqual(myday.route_link(["A", "A", "B"]), base + "B&waypoints=A")  # the same stop twice in a row is one

    def test_a_long_day_is_cut_to_what_maps_takes(self):
        stops = [f"S{n}" for n in range(1, 14)]
        link = myday.route_link(stops)
        self.assertIn("destination=S10&", link)
        self.assertEqual(link.split("waypoints=")[1].count("%7C"), 8)

    def test_the_phone_link_keeps_digits_and_plus_only(self):
        self.assertEqual(myday.tel_link("+20 100-123 4567"), "tel:+201001234567")
        self.assertEqual(myday.tel_link("٠١٠٠١٢٣٤٥٦٧"), "tel:01001234567")
        self.assertEqual(myday.tel_link(" "), "")


class CardsTests(MyDaySetup):
    def test_a_visit_card_has_phone_address_and_map_link(self):
        visit = self.visit(9, address="12 شارع الهرم & ميدان #2")
        html = self.get(self.me).content.decode()
        card = html[html.index(f'data-job="appointment-{visit.pk}"'):]
        card = card[:card.index("</article>")]
        self.assertIn('href="tel:01001234567"', card)
        self.assertIn("12 شارع الهرم &amp; ميدان #2", card)
        self.assertIn(f'href="{MAPS.replace("&", "&amp;")}12%20%D8%B4%D8%A7%D8%B1%D8%B9%20%D8%A7%D9%84%D9%87%D8%B1%D9%85%20%26%20%D9%85%D9%8A%D8%AF%D8%A7%D9%86%20%232"', card)
        self.assertIn('rel="noopener noreferrer"', card)
        self.assertIn(visit.number, card)

    def test_a_visit_without_its_own_address_uses_the_customers(self):
        visit = self.visit(9)  # no address on the visit, one on the customer
        self.assertEqual(visit.address, "")
        html = self.get(self.me).content.decode()
        self.assertIn("12 شارع التحرير الدقي - الجيزة", html)
        self.assertIn(MAPS.replace("&", "&amp;") + "12%20%D8%B4%D8%A7%D8%B1%D8%B9", html)

    def test_no_address_means_no_map_link_and_no_route(self):
        bare = make_customer(customer_code="C-2", name="بدون عنوان", phone="")
        self.visit(9, customer=bare)
        page = self.get(self.me)
        self.assertNotContains(page, "google.com/maps")
        self.assertNotContains(page, "data-map")
        self.assertNotContains(page, "data-call")  # and no phone: no call button either
        self.assertContains(page, "data-route-empty")
        self.assertContains(page, "مفيش عنوان متسجّل")

    def test_an_appointment_at_the_shop_never_borrows_the_customers_address(self):
        shop = self.visit(9, kind="appointment")
        page = self.get(self.me)
        self.assertContains(page, f'data-job="appointment-{shop.pk}"')
        self.assertNotContains(page, "data-map")
        self.assertNotContains(page, "مفيش عنوان متسجّل")

    def test_a_work_order_card_uses_the_customers_address_and_links_to_the_order(self):
        order = self.order()
        page = self.get(self.me)
        self.assertContains(page, MAPS.replace("&", "&amp;") + "12%20%D8%B4%D8%A7%D8%B1%D8%B9")
        self.assertContains(page, f'href="{reverse("workorders:detail", args=[order.pk])}?lang=ar"')

    def test_the_whole_route_is_the_remaining_visits_in_time_order(self):
        done = self.visit(8, address="First Done St")
        self.visit(10, address="Second St")
        self.visit(12, address="Third St")
        shop = self.visit(14, kind="appointment")
        appointments.set_status(done, AppointmentStatus.DONE, self.owner)
        page = self.get(self.me)
        self.assertContains(page, "https://www.google.com/maps/dir/?api=1&amp;destination=Third%20St&amp;waypoints=Second%20St")
        self.assertNotContains(page, "First%20Done%7C")  # a finished visit is no longer a stop (its own card keeps its map link)
        self.assertNotContains(page, "waypoints=First")
        self.assertContains(page, "data-stop", count=2)
        self.assertContains(page, f'data-job="appointment-{shop.pk}"')


class QuickActionTests(MyDaySetup):
    def test_a_technician_moves_their_own_order_along_the_bench(self):
        order = self.order()
        page = self.get(self.me)
        self.assertContains(page, 'data-action="in_repair"')
        self.assertContains(page, 'data-action="diagnosed"')
        self.assertNotContains(page, 'data-action="cancelled"')  # cancelling is the counter's
        response = self.post(self.me, kind="work_order", pk=order.pk, to="in_repair")
        self.assertRedirects(response, f"{reverse('staff:my_day')}?lang=ar", fetch_redirect_response=False)
        order.refresh_from_db()
        self.assertEqual(order.status, Status.IN_REPAIR)
        self.assertEqual(order.events.order_by("-pk").first().user, self.me)
        self.assertTrue(AuditLog.objects.filter(module="workorders", action="move", actor=self.me).exists())
        self.assertContains(self.client.get(reverse("staff:my_day")), 'data-action="ready"')

    def test_counter_only_moves_and_illegal_moves_change_nothing(self):
        order = self.order()
        for to in ("cancelled", "quoted", "delivered", "nonsense"):
            with self.subTest(to=to):
                self.post(self.me, kind="work_order", pk=order.pk, to=to)
                order.refresh_from_db()
                self.assertEqual(order.status, Status.RECEIVED)
        self.post(self.me, kind="work_order", pk=order.pk, to="ready")  # received -> ready is not a legal step
        order.refresh_from_db()
        self.assertEqual(order.status, Status.RECEIVED)

    def test_a_colleagues_order_cannot_be_moved(self):
        theirs = self.order(technician=self.other)
        self.assertEqual(self.post(self.me, kind="work_order", pk=theirs.pk, to="in_repair").status_code, 404)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, Status.RECEIVED)

    def test_a_manager_cannot_move_someone_elses_order_from_this_screen(self):
        save_employee({"name": "المعلم", "user": str(self.owner.pk)}, self.owner)
        theirs = self.order()
        self.assertEqual(self.post(self.owner, kind="work_order", pk=theirs.pk, to="in_repair").status_code, 404)

    def test_an_unlinked_user_and_junk_requests_are_refused(self):
        order = self.order()
        self.assertEqual(self.post(self.owner, kind="work_order", pk=order.pk, to="in_repair").status_code, 403)
        self.assertEqual(self.post(self.me, kind="work_order", pk="abc", to="in_repair").status_code, 404)
        self.post(self.me, kind="invoice", pk=order.pk, to="in_repair")
        order.refresh_from_db()
        self.assertEqual(order.status, Status.RECEIVED)

    def test_a_plain_technician_has_no_appointment_buttons_and_cannot_post_them(self):
        visit = self.visit(9, address="12 شارع الهرم")
        page = self.get(self.me)
        self.assertNotContains(page, 'data-action="arrived"')
        self.assertNotContains(page, f'href="{reverse("appointments:detail", args=[visit.pk])}')  # no view permission: no link into a 403
        self.assertEqual(self.post(self.me, kind="appointment", pk=visit.pk, to="arrived").status_code, 403)
        visit.refresh_from_db()
        self.assertEqual(visit.status, AppointmentStatus.BOOKED)

    def test_someone_who_may_book_marks_their_own_visit_arrived_then_done(self):
        boss = save_employee({"name": "المعلم", "user": str(self.owner.pk)}, self.owner)
        mine = self.visit(9, employee=boss, address="12 شارع الهرم")
        theirs = self.visit(11, address="5 شارع فيصل")
        page = self.get(self.owner)
        self.assertContains(page, 'data-action="arrived"')
        self.assertContains(page, f'href="{reverse("appointments:detail", args=[mine.pk])}?lang=ar"')
        self.post(self.owner, kind="appointment", pk=mine.pk, to="arrived")
        mine.refresh_from_db()
        self.assertEqual(mine.status, AppointmentStatus.ARRIVED)
        self.post(self.owner, kind="appointment", pk=mine.pk, to="done")
        mine.refresh_from_db()
        self.assertEqual(mine.status, AppointmentStatus.DONE)
        self.assertEqual(self.post(self.owner, kind="appointment", pk=theirs.pk, to="arrived").status_code, 404)  # not theirs
        self.assertEqual(self.post(self.owner, kind="appointment", pk=mine.pk, to="cancelled").status_code, 302)
        mine.refresh_from_db()
        self.assertEqual(mine.status, AppointmentStatus.DONE)  # only arrived/done are phone actions, and a done visit never moves
        self.assertEqual(mine.invoice_id, None)  # nothing is billed from here


class MenuAndDashboardTests(MyDaySetup):
    def test_the_technician_menu_and_dashboard_lead_to_the_screen_and_the_owners_do_not(self):
        url = reverse("staff:my_day")
        self.client.force_login(self.me)
        dashboard = self.client.get(reverse("dashboard_snapshot"))
        self.assertContains(dashboard, f'href="{url}')
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("dashboard_snapshot")), f'href="{url}')

    def test_the_screen_is_closed_with_the_employees_module(self):
        from settings_core.setup_services import set_module_enabled

        set_module_enabled(self.profile, "employees_technicians", False, self.owner)
        self.assertNotEqual(self.get(self.me).status_code, 200)


class StylesheetTests(SimpleTestCase):
    def test_the_technician_sheet_uses_only_defined_tokens_and_no_colour_literals(self):
        from settings_core.tests_tokens_adoption import read_static

        css = read_static("hesba/css/technician.css")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", read_static("hesba/css/tokens.css")))
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertTrue(used)
        self.assertEqual(sorted(used - defined), [])
        self.assertEqual([c for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", css)], [])
        self.assertNotRegex(css, r"rgba?\(")
