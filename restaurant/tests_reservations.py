"""Restaurants R8 (HG-118): table reservations («الحجوزات»).

The set-up of ``tests_split_pay``: the main entity (hall «HALL», till «TILL», card machine «CARD», «T1» with 4 seats) and the Maadi
branch («BR-TILL», its own «T1» with 2 seats), the clock on Wednesday 11 Nov 2026 at noon in Cairo, VAT on at 14%. The «العربون»
capability is on. Nour («C-NOUR») books «T1» for four on Friday 13 Nov at 20:00 for 90 minutes. The dish «مشويات مشكلة» is 112.00,
so four plates are 448.00 before VAT and **510.72** with it.

* **overlap:** a second live reservation on the same table whose time overlaps is refused; one that starts exactly when the first
  ends (21:30) or ends exactly when it starts (18:30 → 20:00) is allowed; a cancelled or no-show one frees the time;
* **seats:** six on a four-seat table is refused with a warning, and saved when the user says to go on (audited ``over_seats``);
* **«وصل»** opens the table's order (guests 4, Nour as the customer) through ``services.open_order``, and is refused while the table
  has an open order;
* **deposit 200.00:** Nour's account −200.00, the till +200.00. A no-show forfeits it into a «رسوم إلغاء» invoice of 200.00
  (175.44 + 24.56 with VAT; 200.00 without). An early cancel refunds it. The walk-in cannot pay one;
* **at the bill:** seated and billed at 510.72 (VAT on), the 200.00 deposit settles the bill and the drawer takes 310.72; VAT off,
  448.00 → 248.00. Nour owes nothing after it, and the books balance.
"""

import re
import threading
from datetime import datetime, timedelta
from decimal import Decimal as D
from unittest import mock, skipUnless

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Sum
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.services import get_cashbox_balance
from deposits import services as deposits
from deposits.models import Deposit, DepositStatus
from entities.current import working_in
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location
from ledger import reports as gl
from ledger.projector import Projector
from permissions.models import RoleCode
from sales.models import CustomerPayment, SalesInvoice
from sales.pos import WALK_IN_CODE, walk_in_customer
from sales.services import cancel_posted_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile
from settlements.models import InvoiceSettlement

from . import reservations, services
from .models import DiningTable, Order, OrderStatus, ReservationPage, ReservationStatus, TableReservation
from .tests import person
from .tests_branches import NOW
from .tests_split_pay import SplitBillSetup


def at(days, clock):
    """A local (Cairo) time ``days`` after the frozen Wednesday."""

    day = timezone.localtime(NOW).date() + timedelta(days=days)
    return timezone.make_aware(datetime.combine(day, datetime.strptime(clock, "%H:%M").time()))


def balance(customer):
    agg = customer.ledger_entries.aggregate(i=Sum("due_increase"), d=Sum("due_decrease"))
    return customer.opening_balance + (agg["i"] or D("0")) - (agg["d"] or D("0"))


class ReservationSetup(SplitBillSetup):
    def setUp(self):
        super().setUp()
        cache.clear()  # the public page's throttle counts live in the cache
        set_capability_enabled(ClientProfile.get_active(), "deposits", True, user=self.owner)
        self.grill = make_item(item_code="D-GRILL", item_name="مشويات مشكلة", default_sale_price="112.00", is_stock_tracked=False)
        self.nour = make_customer(customer_code="C-NOUR", name="نور حسن", phone="01001112222")
        self.cashier = person(RoleCode.CASHIER, "res_cashier")
        self.waiter_user = person(RoleCode.WAITER, "res_waiter")
        self.manager = person(RoleCode.MANAGER, "res_manager")
        self.keeper = person(RoleCode.STOCK_KEEPER, "res_keeper")
        self.t2 = services.save_table({"name": "T2", "seats": 6}, self.owner, entity=self.main)

    def book(self, starts_at=None, table="t1", user=None, **extra):
        data = {"name": "نور حسن", "phone": "01001112222", "party_size": 4, "starts_at": starts_at or at(2, "20:00"), "duration_minutes": 90,
                "table": self.a_t1 if table == "t1" else table, "customer": self.nour}
        override = extra.pop("override", False)
        data.update(extra)
        return reservations.save_reservation(data, user or self.owner, entity=self.main, override=override)

    def enter(self, user=None, entity="main"):
        self.work_in(self.main if entity == "main" else entity, user or self.owner)

    def page(self, user=None, **query):
        self.enter(user)
        return self.client.get(reverse("restaurant:reservations"), {"lang": "ar", "day": at(2, "00:00").date().isoformat(), **query})

    def act(self, action, reservation=None, user=None, **data):
        self.enter(user)
        payload = {"lang": "ar", "action": action, "day_view": at(2, "00:00").date().isoformat(), **data}
        if reservation is not None:
            payload["pk"] = reservation.pk
        return self.client.post(reverse("restaurant:reservations"), payload, follow=True)

    def take(self, reservation, amount="200.00"):
        return reservations.take_deposit(reservation, self.cashbox, D(amount), self.owner)

    def grills(self, order, count=4):
        services.add_item(order, self.grill, self.owner, count)
        return order


# ---- overlap and seats ----------------------------------------------------------------------------------------------


class OverlapTests(ReservationSetup):
    def test_an_overlapping_reservation_on_the_same_table_is_refused(self):
        first = self.book()
        for start, minutes in (("21:00", 90), ("19:00", 90), ("19:30", 180), ("20:00", 30), ("20:30", 15)):
            with self.subTest(start=start), self.assertRaises(ValidationError) as caught:
                self.book(at(2, start), duration_minutes=minutes)
            self.assertIn(f"الطاولة T1 محجوزة في الوقت ده ({first.number} من 20:00 لـ 21:30)", caught.exception.messages[0])
            self.assertEqual(caught.exception.code, "clash")
        self.assertEqual(TableReservation.objects.count(), 1)

    def test_touching_end_and_start_is_allowed(self):
        self.book()
        after = self.book(at(2, "21:30"))  # starts exactly when the first ends
        before = self.book(at(2, "18:30"))  # ends exactly when the first starts (90 minutes)
        self.assertEqual((after.ends_at, before.ends_at), (at(2, "23:00"), at(2, "20:00")))
        self.assertEqual(TableReservation.objects.filter(table=self.a_t1).count(), 3)

    def test_another_table_or_another_day_does_not_clash_and_an_edit_does_not_clash_with_itself(self):
        first = self.book()
        self.book(at(2, "20:00"), table=self.t2)
        self.book(at(3, "20:00"))
        moved = reservations.save_reservation({"name": "نور حسن", "party_size": 4, "starts_at": at(2, "20:30"), "duration_minutes": 90, "table": self.a_t1,
                                               "customer": self.nour}, self.owner, first)
        self.assertEqual(moved.starts_at, at(2, "20:30"))

    def test_a_cancelled_or_no_show_reservation_frees_the_table(self):
        first = self.book()
        reservations.cancel(first, ReservationStatus.CANCELLED, self.owner)
        second = self.book(at(2, "20:30"))
        reservations.cancel(second, ReservationStatus.NO_SHOW, self.owner)
        self.book(at(2, "20:15"))
        self.assertEqual(TableReservation.objects.filter(status__in=("booked", "confirmed", "seated")).count(), 1)

    def test_a_table_given_later_is_checked_too(self):
        self.book()
        loose = self.book(at(2, "20:30"), table=None)
        self.assertIsNone(loose.table)
        with self.assertRaisesMessage(ValidationError, "محجوزة في الوقت ده"):
            reservations.save_reservation({"name": "سارة", "party_size": 2, "starts_at": at(2, "20:30"), "table": self.a_t1}, self.owner, loose)
        with self.assertRaisesMessage(ValidationError, "محجوزة في الوقت ده"):
            reservations.seat(loose, self.owner, table=self.a_t1)

    def test_a_seated_reservation_still_holds_its_table(self):
        first = self.book(at(0, "12:00"))
        reservations.seat(first, self.owner)
        with self.assertRaisesMessage(ValidationError, "محجوزة في الوقت ده"):
            self.book(at(0, "13:00"))

    def test_the_refusal_in_english(self):
        self.book()
        with self.assertRaisesMessage(ValidationError, "Table T1 is booked at that time"):
            reservations.save_reservation({"name": "x", "party_size": 2, "starts_at": at(2, "20:45"), "table": self.a_t1}, self.owner, lang="en", entity=self.main)

    def test_bad_input_is_refused(self):
        for data, text in (({"name": ""}, "اكتب اسم الضيف"), ({"party_size": "0"}, "عدد الأفراد لازم من 1"), ({"duration_minutes": "5"}, "مدة الحجز لازم"),
                           ({"day": "x", "time": ""}, "اختار يوم وساعة الحجز"), ({"phone": "12"}, "رقم موبايل صحيح")):
            with self.subTest(data=data), self.assertRaisesMessage(ValidationError, text):
                values = {"name": "نور", "party_size": 2, "starts_at": at(2, "20:00"), "table": self.a_t1, **data}
                if "day" in data:
                    values.pop("starts_at")
                reservations.save_reservation(values, self.owner, entity=self.main)
        self.assertFalse(TableReservation.objects.exists())


class SeatWarningTests(ReservationSetup):
    def test_a_party_bigger_than_the_table_warns_and_the_user_can_go_on(self):
        with self.assertRaises(ValidationError) as caught:
            self.book(party_size=6)
        self.assertEqual(caught.exception.code, "seats")
        self.assertIn("عدد الأفراد (6) أكبر من كراسي الطاولة T1 (4)", caught.exception.messages[0])
        self.assertFalse(TableReservation.objects.exists())
        made = self.book(party_size=6, override=True)
        self.assertEqual((made.party_size, made.table), (6, self.a_t1))
        self.assertTrue(AuditLog.objects.get(action="create_reservation", object_id=str(made.pk)).after_data["over_seats"])

    def test_the_screen_shows_the_warning_with_the_box_to_go_on(self):
        data = {"name": "نور حسن", "party_size": "6", "day": at(2, "00:00").date().isoformat(), "time": "20:00", "duration_minutes": "90", "table": self.a_t1.pk}
        refused = self.act("save", **data)
        self.assertContains(refused, "أكبر من كراسي الطاولة T1")
        self.assertContains(refused, "data-seats-override")
        self.assertFalse(TableReservation.objects.exists())
        self.act("save", override="on", **data)
        self.assertEqual(TableReservation.objects.get().party_size, 6)

    def test_seating_six_at_a_four_seat_table_needs_the_say_so_too(self):
        loose = self.book(party_size=6, table=None)
        with self.assertRaisesMessage(ValidationError, "أكبر من كراسي الطاولة T1"):
            reservations.seat(loose, self.owner, table=self.a_t1)
        self.assertFalse(Order.objects.exists())
        seated = reservations.seat(loose, self.owner, table=self.a_t1, override=True)
        self.assertEqual((seated.status, seated.order.guests), (ReservationStatus.SEATED, 6))


# ---- seat, confirm, cancel, no-show --------------------------------------------------------------------------------


class SeatTests(ReservationSetup):
    def test_seat_opens_the_tables_order_with_the_guests_and_the_customer(self):
        booking = self.book(at(0, "12:30"))
        seated = reservations.seat(booking, self.waiter_user)
        order = seated.order
        self.assertEqual((seated.status, seated.table), (ReservationStatus.SEATED, self.a_t1))
        self.assertEqual((order.table, order.guests, order.customer, order.status, order.entity, order.opened_by),
                         (self.a_t1, 4, self.nour, OrderStatus.OPEN, self.main, self.waiter_user))
        self.assertIn(booking.number, order.notes)
        self.assertTrue(AuditLog.objects.filter(action="open_order", object_id=str(order.pk)).exists())  # open_order's own audit row
        self.assertEqual(AuditLog.objects.get(action="seat_reservation").after_data["order"], order.number)

    def test_seat_is_refused_when_the_table_already_has_an_open_order(self):
        booking = self.book(at(0, "12:30"))
        walk_in = services.open_order(self.owner, table=self.a_t1, guests=2)
        with self.assertRaisesMessage(ValidationError, "الطاولة T1 عليها طلب مفتوح"):
            reservations.seat(booking, self.owner)
        booking.refresh_from_db()
        self.assertEqual((booking.status, booking.order), (ReservationStatus.BOOKED, None))
        self.assertEqual(list(Order.objects.values_list("pk", flat=True)), [walk_in.pk])

    def test_seat_needs_a_table_and_can_give_one(self):
        loose = self.book(at(0, "12:30"), table=None, party_size=2)
        with self.assertRaisesMessage(ValidationError, "اختار الطاولة الأول"):
            reservations.seat(loose, self.owner)
        seated = reservations.seat(loose, self.owner, table=self.t2)
        self.assertEqual((seated.table, seated.order.table), (self.t2, self.t2))

    def test_seat_from_the_screen_goes_to_the_order(self):
        booking = self.book(at(0, "12:30"))
        response = self.act("seat", booking, self.waiter_user, table=self.a_t1.pk)
        booking.refresh_from_db()
        self.assertRedirects(response, f"{reverse('restaurant:order', args=[booking.order.pk])}?lang=ar")
        self.assertContains(response, "أهلاً بالضيوف")

    def test_confirm_then_a_closed_reservation_cannot_change(self):
        booking = self.book()
        reservations.confirm(booking, self.owner)
        booking.refresh_from_db()
        self.assertEqual(booking.status, ReservationStatus.CONFIRMED)
        with self.assertRaisesMessage(ValidationError, "التأكيد للحجز اللي لسه محجوز بس"):
            reservations.confirm(booking, self.owner)
        reservations.seat(booking, self.owner)
        for attempt in (lambda: reservations.cancel(booking, ReservationStatus.CANCELLED, self.owner),
                        lambda: reservations.seat(booking, self.owner),
                        lambda: reservations.save_reservation({"name": "x", "party_size": 2, "starts_at": at(2, "20:00")}, self.owner, booking)):
            with self.assertRaisesMessage(ValidationError, "حالته «وصل»"):
                attempt()


class CancelTests(ReservationSetup):
    def test_cancel_and_no_show_are_audited_status_changes(self):
        first, second = self.book(), self.book(at(3, "20:00"))
        result = reservations.cancel(first, ReservationStatus.CANCELLED, self.owner, reason="اتصلوا اعتذروا")
        self.assertEqual((result["outcome"], result["deposit"]), (None, None))
        reservations.cancel(second, ReservationStatus.NO_SHOW, self.cashier)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual((first.status, second.status), (ReservationStatus.CANCELLED, ReservationStatus.NO_SHOW))
        row = AuditLog.objects.get(action="cancel_reservation")
        self.assertEqual((row.reason, row.before_data, row.after_data["status"]), ("اتصلوا اعتذروا", {"status": "booked"}, "cancelled"))
        self.assertEqual(AuditLog.objects.get(action="no_show_reservation").actor, self.cashier)
        with self.assertRaisesMessage(ValidationError, "حالته «اتلغى»"):
            reservations.cancel(first, ReservationStatus.NO_SHOW, self.owner)
        self.assertFalse(SalesInvoice.objects.exists())

    def test_from_the_screen(self):
        booking = self.book()
        self.assertContains(self.act("no_show", booking, self.waiter_user), "ما حضروش")
        booking.refresh_from_db()
        self.assertEqual(booking.status, ReservationStatus.NO_SHOW)


# ---- permissions and branches ---------------------------------------------------------------------------------------


class PermissionTests(ReservationSetup):
    def test_a_stock_keeper_is_refused_403(self):
        booking = self.book()
        self.enter(self.keeper)
        url = reverse("restaurant:reservations")
        self.assertEqual(self.client.get(url).status_code, 403)
        for action in ("save", "confirm", "seat", "cancel", "no_show", "deposit", "page_on"):
            self.assertEqual(self.client.post(url, {"action": action, "pk": booking.pk, "name": "x", "party_size": "2"}).status_code, 403, action)
        booking.refresh_from_db()
        self.assertEqual((booking.status, TableReservation.objects.count(), Order.objects.count()), (ReservationStatus.BOOKED, 1, 0))
        self.assertFalse(ReservationPage.objects.exists())

    def test_who_takes_orders_sees_and_changes_reservations(self):
        booking = self.book()
        for user in (self.waiter_user, self.cashier, self.manager, self.owner):
            page = self.page(user)
            self.assertContains(page, booking.number, msg_prefix=user.username)
            self.assertContains(page, "data-reservation-form", msg_prefix=user.username)
        self.act("confirm", booking, self.waiter_user)
        booking.refresh_from_db()
        self.assertEqual(booking.status, ReservationStatus.CONFIRMED)
        self.act("save", user=self.cashier, name="سارة", party_size="2", day=at(3, "00:00").date().isoformat(), time="13:00", duration_minutes="60")
        self.assertEqual(TableReservation.objects.get(name="سارة").created_by, self.cashier)

    def test_the_public_page_switch_is_the_floor_set_up(self):
        response = self.act("page_on", user=self.waiter_user)
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(self.page(self.waiter_user), "data-booking-page-setup")
        self.act("page_on", user=self.manager)
        self.assertTrue(ReservationPage.objects.get(entity=self.main).accepting)

    def test_the_board_links_to_the_reservations_for_whoever_sees_the_board(self):
        for user in (self.waiter_user, self.cashier, self.manager):
            self.enter(user)
            self.assertContains(self.client.get(reverse("restaurant:board")), "data-reservations-link", msg_prefix=user.username)

    def test_deposit_buttons_follow_the_deposit_permissions(self):
        self.book()
        self.assertNotContains(self.page(self.waiter_user), "data-deposit-take")  # a waiter never takes money
        self.assertContains(self.page(self.owner), "data-deposit-take")
        booking = TableReservation.objects.get()
        self.assertEqual(self.act("deposit", booking, self.waiter_user, amount="50", cashbox=self.cashbox.pk).status_code, 403)
        self.assertFalse(Deposit.objects.exists())


class BranchTests(ReservationSetup):
    def test_one_branch_neither_sees_nor_changes_anothers_reservations(self):
        ours = self.book()
        theirs = reservations.save_reservation({"name": "ضيف المعادي", "party_size": 2, "starts_at": at(2, "20:00"), "table": self.b_t1}, self.owner, entity=self.branch)
        self.assertEqual(theirs.entity, self.branch)
        page = self.page()
        self.assertContains(page, ours.number)
        self.assertNotContains(page, theirs.number)
        self.assertNotContains(page, "ضيف المعادي")
        self.assertEqual(self.page(edit=theirs.pk).status_code, 404)
        for action in ("confirm", "seat", "cancel", "no_show"):
            self.assertEqual(self.act(action, theirs).status_code, 404, action)
        theirs.refresh_from_db()
        self.assertEqual(theirs.status, ReservationStatus.BOOKED)
        with working_in(self.main):
            with self.assertRaisesMessage(ValidationError, "مش من حجوزات الفرع اللي شغال فيه"):
                reservations.cancel(theirs, ReservationStatus.CANCELLED, self.owner)
            with self.assertRaisesMessage(ValidationError, "مش من حجوزات الفرع اللي شغال فيه"):
                reservations.save_reservation({"name": "x", "party_size": 2, "starts_at": at(2, "20:00")}, self.owner, entity=self.branch)

    def test_another_branchs_table_is_refused(self):
        with self.assertRaisesMessage(ValidationError, "الطاولة دي تبع فرع تاني"):
            self.book(table=self.b_t1)
        self.act("save", name="x", party_size="2", day=at(2, "00:00").date().isoformat(), time="20:00", table=self.b_t1.pk)
        self.assertFalse(TableReservation.objects.exists())

    def test_the_whole_group_sees_both_and_a_new_one_names_its_branch(self):
        ours = self.book()
        theirs = reservations.save_reservation({"name": "ضيف المعادي", "party_size": 2, "starts_at": at(2, "21:00"), "table": self.b_t1}, self.owner, entity=self.branch)
        self.work_in(None)
        page = self.client.get(reverse("restaurant:reservations"), {"lang": "ar", "day": at(2, "00:00").date().isoformat()})
        self.assertContains(page, ours.number)
        self.assertContains(page, theirs.number)
        self.client.post(reverse("restaurant:reservations"), {"lang": "ar", "action": "save", "name": "y", "party_size": "2",
                                                              "day": at(2, "00:00").date().isoformat(), "time": "13:00"})
        self.assertFalse(TableReservation.objects.filter(name="y").exists())  # no branch named
        self.client.post(reverse("restaurant:reservations"), {"lang": "ar", "action": "save", "name": "y", "party_size": "2", "entity": self.branch.pk,
                                                              "day": at(2, "00:00").date().isoformat(), "time": "13:00"})
        self.assertEqual(TableReservation.objects.get(name="y").entity, self.branch)


# ---- the screen ---------------------------------------------------------------------------------------------------


class ScreenTests(ReservationSetup):
    def test_the_day_view_lists_by_time_and_filters_by_day(self):
        late = self.book(at(2, "21:30"), name="ضيف متأخر")
        early = self.book(at(2, "13:00"), name="ضيف بدري")
        other_day = self.book(at(3, "13:00"), name="ضيف بكرة")
        page = self.page().content.decode()
        self.assertLess(page.index(early.number), page.index(late.number))
        self.assertNotIn(other_day.number, page)
        self.assertContains(self.page(day=at(3, "00:00").date().isoformat()), other_day.number)
        self.assertContains(self.page(day="not-a-day"), "مفيش حجوزات في اليوم ده")  # today: none

    def test_arabic_and_english(self):
        self.book()
        arabic = self.page()
        for text in ("الحجوزات", "حجز جديد", "وصل", "ما حضرش", 'dir="rtl"'):
            self.assertContains(arabic, text)
        self.enter()
        english = self.client.get(reverse("restaurant:reservations"), {"lang": "en", "day": at(2, "00:00").date().isoformat()})
        for text in ("Reservations", "New reservation", "Seat", "No-show", 'dir="ltr"'):
            self.assertContains(english, text)

    def test_an_edit_from_the_screen(self):
        booking = self.book()
        form = self.page(edit=booking.pk)
        self.assertContains(form, 'value="نور حسن"')
        self.act("save", booking, name="نور حسن", party_size="3", day=at(2, "00:00").date().isoformat(), time="20:00", duration_minutes="90",
                 table=self.a_t1.pk, customer=self.nour.pk)
        booking.refresh_from_db()
        self.assertEqual(booking.party_size, 3)
        self.assertEqual(AuditLog.objects.get(action="change_reservation").before_data["party_size"], 4)

    def test_the_page_uses_the_tokens_only(self):
        from django.contrib.staticfiles import finders
        import re

        with open(finders.find("hesba/css/restaurant_reservations.css"), encoding="utf-8") as sheet:
            css = sheet.read()
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", css), [])
        self.assertNotRegex(css, r"rgba?\(")
        with open(finders.find("hesba/css/tokens.css"), encoding="utf-8") as sheet:
            defined = set(re.findall(r"(--hs-[\w-]+)\s*:", sheet.read()))
        self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])


# ---- the public page --------------------------------------------------------------------------------------------------


class PublicPageTests(ReservationSetup):
    def setUp(self):
        super().setUp()
        self.public = reservations.set_accepting(self.main, True, self.owner)
        self.url = reverse("book_table", args=[self.public.public_key])
        self.client.logout()

    def send(self, **extra):
        data = {"lang": "ar", "name": "ضيف الموبايل", "phone": "01223334444", "party_size": "3", "day": at(2, "00:00").date().isoformat(), "time": "19:00", "notes": ""}
        data.update(extra)
        return self.client.post(self.url, data)

    def assert_private(self, response):
        for part in ("no-store", "no-cache", "private"):
            self.assertIn(part, response["Cache-Control"])
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertEqual(response["Referrer-Policy"], "same-origin")

    def test_a_guest_sends_a_booked_request_with_no_table(self):
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assert_private(page)
        self.assertContains(page, "data-book-table-form")
        response = self.send()
        self.assertEqual(response.status_code, 303)
        self.assert_private(response)
        self.assertEqual(response["Location"], f"{self.url}?lang=ar&sent=1")
        made = TableReservation.objects.get()
        self.assertEqual((made.status, made.table, made.customer, made.online, made.entity, made.party_size, made.created_by),
                         (ReservationStatus.BOOKED, None, None, True, self.main, 3, None))
        self.assertEqual(made.starts_at, at(2, "19:00"))
        self.assertTrue(AuditLog.objects.filter(action="request_reservation", object_id=str(made.pk)).exists())
        staff = self.page()
        self.assertContains(staff, "data-online-request")
        self.assertContains(staff, "ضيف الموبايل")

    def test_nothing_is_shown_back(self):
        self.book(name="ضيف سري", phone="01009998888")
        self.send()
        for response in (self.client.get(self.url), self.client.get(self.url, {"sent": "1"})):
            # the random CSRF token can spell "T1" by chance (it did on CI), so it is taken out before looking for the table
            body = re.sub(r'name="csrfmiddlewaretoken" value="[^"]*"', "", response.content.decode())
            for secret in ("ضيف سري", "01009998888", "ضيف الموبايل", "01223334444", "نور حسن", "RES-", "T1"):
                self.assertNotIn(secret, body)
        self.assertContains(self.client.get(self.url, {"sent": "1"}), "data-book-table-sent")

    def test_a_bad_key_a_page_switched_off_or_a_rotated_key_is_404(self):
        for url in (reverse("book_table", args=["nope"]), reverse("book_table", args=["x" * 41])):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 404)
            self.assert_private(response)
        reservations.rotate(self.main, self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.send().status_code, 404)
        fresh = ReservationPage.objects.get(entity=self.main)
        self.assertEqual(self.client.get(reverse("book_table", args=[fresh.public_key])).status_code, 200)
        reservations.set_accepting(self.main, False, self.owner)
        self.assertEqual(self.client.get(reverse("book_table", args=[fresh.public_key])).status_code, 404)
        self.assertFalse(TableReservation.objects.exists())

    def test_the_page_is_off_until_the_owner_switches_it_on(self):
        self.assertFalse(reservations.page_for(self.branch).accepting)
        self.assertEqual(self.client.get(reverse("book_table", args=[reservations.page_for(self.branch).public_key])).status_code, 404)

    def test_the_throttle(self):
        for index in range(10):
            self.assertEqual(self.send(phone=f"0122333{index:04d}").status_code, 303, index)
        refused = self.send(phone="01099990000")
        self.assertEqual(refused.status_code, 429)
        self.assert_private(refused)
        self.assertEqual(TableReservation.objects.count(), 10)

    def test_the_phone_throttle(self):
        with mock.patch("restaurant.views_reservations._address", side_effect=lambda request: f"10.0.0.{TableReservation.objects.count()}"):
            for index in range(5):
                self.assertEqual(self.send().status_code, 303, index)
            self.assertEqual(self.send(phone="+20 122 333 4444").status_code, 429)
        self.assertEqual(TableReservation.objects.count(), 5)

    def test_the_honeypot_a_past_time_and_a_missing_phone_are_refused(self):
        self.assertEqual(self.send(website="bot").status_code, 400)
        past = self.send(day=at(0, "00:00").date().isoformat(), time="09:00")
        self.assertEqual(past.status_code, 400)
        self.assertContains(past, "الميعاد ده فات", status_code=400)
        self.assertContains(self.send(phone=""), "رقم موبايل صحيح", status_code=400)
        self.assertContains(self.send(day=at(90, "00:00").date().isoformat()), "60 يوم", status_code=400)
        self.assertFalse(TableReservation.objects.exists())

    def test_english(self):
        page = self.client.get(self.url, {"lang": "en"})
        self.assertContains(page, "Book a table")
        self.assertContains(page, 'dir="ltr"')

    def test_the_setup_shows_the_address(self):
        with self.settings(PUBLIC_BASE_URL="https://hesba.example"):
            page = self.page(self.manager)
        self.assertContains(page, f"https://hesba.example{self.url}?lang=ar")


# ---- the deposit ------------------------------------------------------------------------------------------------------


class DepositTests(ReservationSetup):
    def test_a_200_deposit_takes_the_customer_to_minus_200_and_the_till_to_plus_200(self):
        booking = self.book()
        deposit = self.take(booking)
        self.assertEqual((deposit.status, deposit.target, deposit.payment.amount, deposit.payment.customer), (DepositStatus.HELD, booking, D("200.00"), self.nour))
        self.assertEqual(balance(self.nour), D("-200.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("200.00"))
        self.assertIn(booking.number, deposit.payment.notes)
        self.assertContains(self.page(), 'data-deposit="held"')

    def test_from_the_screen(self):
        booking = self.book()
        self.act("deposit", booking, amount="200", cashbox=self.cashbox.pk)
        self.assertEqual(deposits.held_for(booking).payment.amount, D("200.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("200.00"))

    def test_a_walk_in_deposit_is_refused(self):
        loose = self.book(customer=None)
        with self.assertRaisesMessage(ValidationError, "العربون محتاج عميل باسمه"):
            self.take(loose)
        walk_in = walk_in_customer()
        self.assertIsNone(self.book(at(3, "20:00"), customer=walk_in).customer)  # the walk-in is no one on a booking
        with self.assertRaisesMessage(ValidationError, "العميل النقدي مينفعش يدفع عربون"):
            deposits.take(loose, walk_in, self.cashbox, D("200.00"), self.owner)  # S5's own refusal, unchanged
        self.assertEqual((CustomerPayment.objects.count(), get_cashbox_balance(self.cashbox)), (0, D("0.00")))

    def test_another_branchs_cashbox_and_the_feature_off_are_refused(self):
        booking = self.book()
        with self.assertRaisesMessage(ValidationError, "اختار خزنة من خزن فرع الحجز"):
            reservations.take_deposit(booking, self.b_cashbox, D("200.00"), self.owner)
        set_capability_enabled(ClientProfile.get_active(), "deposits", False, user=self.owner)
        with self.assertRaisesMessage(ValidationError, "العربون مقفول"):
            self.take(booking)
        self.assertNotContains(self.page(), "data-deposit-take")
        self.assertFalse(Deposit.objects.exists())

    def test_the_customer_is_frozen_while_a_deposit_is_held(self):
        booking = self.book()
        self.take(booking)
        other = make_customer(customer_code="C-OTHER", name="عميل تاني")
        with self.assertRaisesMessage(ValidationError, "العميل مينفعش يتغيّر"):
            reservations.save_reservation({"name": "x", "party_size": 4, "starts_at": at(2, "20:00"), "table": self.a_t1, "customer": other}, self.owner, booking)

    def forfeit_figures(self):
        invoice = SalesInvoice.objects.get(invoice_number__startswith="FEE-")
        return invoice, (invoice.subtotal, invoice.tax_amount, invoice.total_amount)

    def test_a_late_no_show_forfeits_with_vat_inside_175_44_plus_24_56(self):
        booking = self.book(at(0, "20:00"))  # tonight: inside the 24 hours
        self.take(booking)
        result = reservations.cancel(booking, ReservationStatus.NO_SHOW, self.owner)
        invoice, figures = self.forfeit_figures()
        self.assertEqual((result["outcome"], result["invoice"]), ("forfeited", invoice))
        self.assertEqual(figures, (D("175.44"), D("24.56"), D("200.00")))
        self.assertEqual(invoice.lines.get().item.item_code, deposits.FEE_ITEM_CODE)
        self.assertEqual(invoice.lines.get().description, "رسوم إلغاء")
        self.assertEqual(Deposit.objects.get().status, DepositStatus.FORFEITED)
        self.assertEqual(list(InvoiceSettlement.objects.values_list("invoice_id", "amount", "source")), [(invoice.pk, D("200.00"), "deposit")])
        self.assertEqual(balance(self.nour), D("0.00"))  # −200.00 deposit, +200.00 fee invoice, settled by it
        self.assertEqual(get_cashbox_balance(self.cashbox), D("200.00"))  # the cash stays: it is the restaurant's now
        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertEqual({key: gl.control_balance(key, entity=self.main) for key in ("receivable", "vat_out")}, {"receivable": D("0.00"), "vat_out": D("-24.56")})

    def test_a_no_show_with_vat_off_forfeits_200(self):
        self.vat(False)
        booking = self.book(at(5, "20:00"))  # a no-show forfeits whatever the hours
        self.take(booking)
        self.assertEqual(reservations.cancel(booking, ReservationStatus.NO_SHOW, self.owner)["outcome"], "forfeited")
        self.assertEqual(self.forfeit_figures()[1], (D("200.00"), D("0.00"), D("200.00")))
        self.assertEqual(balance(self.nour), D("0.00"))

    def test_a_late_cancel_forfeits_and_an_early_cancel_refunds(self):
        late = self.book(at(0, "20:00"))
        self.take(late)
        self.assertEqual(reservations.cancel(late, ReservationStatus.CANCELLED, self.owner)["outcome"], "forfeited")
        early = self.book(at(3, "20:00"))
        self.take(early)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("400.00"))
        self.assertEqual(balance(self.nour), D("-200.00"))
        result = reservations.cancel(early, ReservationStatus.CANCELLED, self.owner, reason="اعتذار")
        self.assertEqual(result["outcome"], "refunded")
        self.assertEqual(deposits.latest_for(early).status, DepositStatus.REFUNDED)
        self.assertEqual(balance(self.nour), D("0.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("200.00"))  # the early deposit went back out
        self.assertEqual(SalesInvoice.objects.filter(invoice_number__startswith="FEE-").count(), 1)

    def test_a_waiter_cancel_leaves_the_deposit_held(self):
        booking = self.book(at(0, "20:00"))
        self.take(booking)
        result = reservations.cancel(booking, ReservationStatus.NO_SHOW, self.waiter_user)
        self.assertEqual(result["outcome"], "forfeit_not_allowed")
        self.assertEqual(Deposit.objects.get().status, DepositStatus.HELD)
        self.assertContains(self.page(self.owner, day=at(0, "00:00").date().isoformat()), "data-deposit-forfeit")
        self.act("deposit_forfeit", booking, self.owner)
        self.assertEqual(Deposit.objects.get().status, DepositStatus.FORFEITED)


class DepositAtBillTests(ReservationSetup):
    def seat_with_deposit(self, amount="200.00"):
        booking = self.book(at(0, "12:30"))
        self.take(booking, amount)
        booking = reservations.seat(booking, self.owner)
        return booking, self.grills(booking.order)

    def pay(self, order, **extra):
        extra.setdefault("tendered", D("400.00"))
        return services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, **extra)

    def assert_settled(self, invoice, total, cash):
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.customer), (total, cash, self.nour))
        self.assertEqual(list(InvoiceSettlement.objects.filter(invoice=invoice, source="deposit").values_list("amount", "source")), [(D("200.00"), "deposit")])
        self.assertEqual(Deposit.objects.get().status, DepositStatus.APPLIED)
        self.assertEqual(Deposit.objects.get().invoice, invoice)
        self.assertEqual(balance(self.nour), D("0.00"))
        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertEqual(gl.control_balance("receivable", entity=self.main), D("0.00"))

    def test_510_72_with_vat_settles_the_200_deposit_and_the_drawer_takes_310_72(self):
        booking, order = self.seat_with_deposit()
        invoice, change = self.pay(order)
        self.assert_settled(invoice, D("510.72"), D("310.72"))
        self.assertEqual((invoice.subtotal, invoice.tax_amount), (D("448.00"), D("62.72")))
        self.assertEqual(change, D("89.28"))  # 400.00 given for 310.72
        self.assertEqual(get_cashbox_balance(self.cashbox), D("510.72"))  # 200.00 on Friday's booking + 310.72 at the bill
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice), (OrderStatus.PAID, invoice))
        self.assertTrue(AuditLog.objects.filter(action="reservation_deposit_at_bill", object_id=str(booking.pk)).exists())

    def test_448_with_vat_off_leaves_248_for_the_drawer(self):
        self.vat(False)
        _, order = self.seat_with_deposit()
        invoice, change = self.pay(order, tendered=D("248.00"))
        self.assert_settled(invoice, D("448.00"), D("248.00"))
        self.assertEqual(change, D("0.00"))
        self.assertEqual(get_cashbox_balance(self.cashbox), D("448.00"))

    def test_the_bill_box_shows_the_deposit_and_asks_for_the_rest(self):
        _, order = self.seat_with_deposit()
        self.enter()
        page = self.client.get(reverse("restaurant:order", args=[order.pk]), {"lang": "ar"})
        self.assertContains(page, 'data-bill-deposit="200.00"')
        self.assertContains(page, "عربون الحجز")
        self.assertContains(page, 'name="tendered" value="310.72"')
        response = self.pay_from_screen(order, self.cashbox, tendered="310.72", print="0")
        self.assertContains(response, "اتقفل الطلب")
        order.refresh_from_db()
        self.assert_settled(order.invoice, D("510.72"), D("310.72"))

    def test_paid_two_ways_the_tenders_and_the_deposit_make_the_bill(self):
        _, order = self.seat_with_deposit()
        invoice, _ = self.pay(order, tenders=[(self.cashbox, D("200.00")), (self.card, D("110.72"))])
        self.assert_settled(invoice, D("510.72"), D("200.00"))
        self.assertEqual(sorted(InvoiceSettlement.objects.values_list("amount", "source")), [(D("110.72"), "split"), (D("200.00"), "deposit")])
        self.assertEqual((get_cashbox_balance(self.cashbox), get_cashbox_balance(self.card)), (D("400.00"), D("110.72")))

    def test_tenders_that_ignore_the_deposit_are_refused_and_nothing_is_kept(self):
        _, order = self.seat_with_deposit()
        with self.assertRaisesMessage(ValidationError, "310.72"):
            with transaction.atomic():
                self.pay(order, tenders=[(self.cashbox, D("300.00")), (self.card, D("210.72"))])
        order.refresh_from_db()
        self.assertEqual((order.status, Deposit.objects.get().status, SalesInvoice.objects.filter(invoice_number__startswith="POS").count()),
                         (OrderStatus.OPEN, DepositStatus.HELD, 0))

    def test_paid_in_another_customers_name_the_deposit_stays_held(self):
        _, order = self.seat_with_deposit()
        other = make_customer(customer_code="C-OTHER", name="عميل تاني")
        invoice, _ = self.pay(order, customer=other, tendered=D("510.72"))
        self.assertEqual((invoice.total_amount, invoice.paid_now), (D("510.72"), D("510.72")))
        self.assertEqual(Deposit.objects.get().status, DepositStatus.HELD)
        self.assertFalse(InvoiceSettlement.objects.exists())
        self.assertEqual(balance(self.nour), D("-200.00"))

    def test_a_deposit_bigger_than_the_bill_settles_it_and_the_rest_stays_credit(self):
        booking = self.book(at(0, "12:30"))
        self.take(booking, "600.00")
        order = self.grills(reservations.seat(booking, self.owner).order)
        invoice, change = self.pay(order, tendered=D("0"))
        self.assertEqual((invoice.total_amount, invoice.paid_now, change), (D("510.72"), D("0.00"), D("0.00")))
        self.assertEqual(list(InvoiceSettlement.objects.values_list("amount", "source")), [(D("510.72"), "deposit")])
        self.assertEqual(balance(self.nour), D("-89.28"))  # S5: the rest is the customer's credit on account

    def test_cancelling_the_bill_gives_the_deposit_back_to_the_booking(self):
        _, order = self.seat_with_deposit()
        invoice, _ = self.pay(order)
        cancel_posted_sales_invoice(invoice.pk, self.owner, "خطأ")
        deposit = Deposit.objects.get()
        self.assertEqual((deposit.status, deposit.invoice), (DepositStatus.HELD, None))
        self.assertFalse(InvoiceSettlement.objects.exists())
        self.assertEqual(balance(self.nour), D("-200.00"))


class CodexDepositFindingsTests(ReservationSetup):
    """Codex on #422: the deposit's customer read from a stale reservation, and a deposit stuck after its seated order or bill
    was cancelled."""

    seat_with_deposit = DepositAtBillTests.seat_with_deposit
    pay = DepositAtBillTests.pay

    def test_a_deposit_taken_on_a_stale_copy_goes_to_the_reservations_customer_now(self):
        booking = self.book()
        stale = TableReservation.objects.get(pk=booking.pk)  # the screen loaded it with نور
        other = make_customer(customer_code="C-OTHER", name="عميل تاني")
        reservations.save_reservation({"name": "عميل تاني", "party_size": 4, "starts_at": at(2, "20:00"), "table": self.a_t1, "customer": other},
                                      self.owner, booking)
        deposit = self.take(stale)
        self.assertEqual(deposit.payment.customer_id, other.pk)

    def test_after_the_seated_order_is_cancelled_the_held_deposit_can_be_refunded(self):
        booking, order = self.seat_with_deposit()
        services.cancel_order(Order.objects.get(pk=order.pk), self.owner, reason="الضيوف مشيوا")
        self.assertContains(self.page(self.owner, day=at(0, "00:00").date().isoformat()), "data-deposit-refund")
        self.act("deposit_refund", booking, self.owner, reason="رجّع العربون")
        self.assertEqual(Deposit.objects.get().status, DepositStatus.REFUNDED)
        self.assertEqual(balance(self.nour), D("0.00"))

    def test_after_the_bill_is_cancelled_the_released_deposit_can_be_refunded(self):
        booking, order = self.seat_with_deposit()
        invoice, _ = self.pay(order)
        cancel_posted_sales_invoice(invoice.pk, self.owner, "خطأ")
        self.act("deposit_refund", booking, self.owner, reason="رجّع العربون")
        self.assertEqual(Deposit.objects.get().status, DepositStatus.REFUNDED)

    def test_a_seated_reservation_with_an_open_order_still_cannot_refund_or_forfeit(self):
        booking, order = self.seat_with_deposit()
        self.assertNotContains(self.page(self.owner, day=at(0, "00:00").date().isoformat()), "data-deposit-refund")
        self.act("deposit_refund", booking, self.owner, reason="x")
        self.act("deposit_forfeit", booking, self.owner)
        self.assertEqual(Deposit.objects.get().status, DepositStatus.HELD)


class NoReservationTests(ReservationSetup):
    def test_with_no_reservation_the_bill_and_the_screens_are_as_before(self):
        order = self.grills(services.open_order(self.owner, table=self.a_t1, guests=4, customer=self.nour))
        self.enter()
        page = self.client.get(reverse("restaurant:order", args=[order.pk]), {"lang": "ar"})
        self.assertNotContains(page, "data-bill-deposit")
        self.assertNotContains(page, "عربون")
        check = self.client.get(reverse("restaurant:check", args=[order.pk]), {"lang": "ar"})
        self.assertNotContains(check, "عربون")
        invoice, change = services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, tendered=D("600.00"))
        self.assertEqual((invoice.total_amount, invoice.paid_now, change), (D("510.72"), D("510.72"), D("89.28")))
        self.assertFalse(InvoiceSettlement.objects.exists())
        self.assertFalse(AuditLog.objects.filter(object_type="restaurant.TableReservation").exists())
        board = self.client.get(reverse("restaurant:board"), {"lang": "ar"})
        self.assertNotContains(board, "data-reservation=")
        self.assertEqual(self.client.get(reverse("restaurant:report"), {"lang": "ar"}).status_code, 200)

    def test_a_held_deposit_on_a_booking_not_yet_seated_never_touches_a_bill(self):
        booking = self.book(at(0, "20:00"))
        self.take(booking)
        order = self.grills(services.open_order(self.owner, table=self.t2, guests=4, customer=self.nour))
        invoice, _ = services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, tendered=D("510.72"))
        self.assertEqual(invoice.paid_now, D("510.72"))
        self.assertEqual(Deposit.objects.get().status, DepositStatus.HELD)


@skipUnless(connection.vendor == "postgresql", "row locks are PostgreSQL's: SQLite's writers already take turns")
class OverlapLockTests(TransactionTestCase):
    """Two people booking the same table for overlapping times at once: the second waits on the table's row lock, then sees the
    first (committed) and is refused. Without the lock both would pass the clash check and both rows would be kept."""

    serialized_rollback = True

    def setUp(self):
        from reports.tests_dashboard import prepared_client

        prepared_client(activity="restaurants", sub_activity="cafe",
                        modules="items_services,sales_operations,cashboxes,reports,inventory,pdf_printing,tables_orders")
        self.owner = person(RoleCode.OWNER, "res_lock_owner")
        make_location(location_code="HALL", is_default=True)
        make_cashbox(cashbox_code="TILL", is_default=True)
        self.table = services.save_table({"name": "T1", "seats": 4}, self.owner)
        self.start = timezone.now() + timedelta(days=2)

    def data(self, minutes_later):
        return {"name": "ضيف", "party_size": 2, "starts_at": self.start + timedelta(minutes=minutes_later), "duration_minutes": 90, "table": self.table}

    def test_the_second_overlapping_booking_waits_and_is_refused(self):
        outcome = {}

        def second():
            try:
                reservations.save_reservation(self.data(30), self.owner)
                outcome["saved"] = True
            except ValidationError as error:
                outcome["refused"] = error.messages
            except Exception as error:  # noqa: BLE001  (reported below, never swallowed)
                outcome["error"] = repr(error)
            finally:
                connection.close()

        with transaction.atomic():
            first = reservations.save_reservation(self.data(0), self.owner)  # holds the table's row; not committed yet
            thread = threading.Thread(target=second)
            thread.start()
            thread.join(timeout=3)
            self.assertTrue(thread.is_alive(), outcome)  # it waits for the first booking's transaction
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(list(outcome), ["refused"], outcome)
        self.assertIn(first.number, outcome["refused"][0])
        self.assertEqual(TableReservation.objects.count(), 1)
