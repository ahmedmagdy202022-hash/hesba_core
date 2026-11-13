"""Restaurants R4 (HG-097): kitchen stations, one ticket per station, and the kitchen screen with its feed.

Nothing here may write a sale, a stock movement, a cashbox movement, a ledger row or a journal entry: the screen only
moves the station's own marks."""

import re
from decimal import Decimal as D

from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from entities.current import SESSION_KEY, working_in
from entities.models import Entity
from entities.services import main_entity
from hesba_testing.factories import make_item
from inventory.models import StockMovement
from ledger.models import JournalEntry
from ledger.projector import Projector
from manufacturing.models import ProductionRun
from master_data.models import Category
from permissions.models import RoleCode
from permissions.services import user_has_permission
from sales.models import CustomerLedgerEntry, CustomerPayment, SalesInvoice

from . import services, stations
from .models import KitchenStation, KitchenTicket, OrderLine, OrderStatus
from .tests import RestaurantSetup, person


class ScreenSetup(RestaurantSetup):
    """A grill and a bar: a burger (grill category) and a juice (drinks category), and the two stations holding them."""

    def setUp(self):
        super().setUp()
        self.grill_cat = Category.objects.create(category_code="C-GRILL", name_ar="مشويات", name_en="Grill")
        self.drinks_cat = Category.objects.create(category_code="C-DRINK", name_ar="مشروبات", name_en="Drinks")
        self.burger = make_item(item_code="D-BURGER", item_name="برجر", default_sale_price="120.00", is_stock_tracked=False, category=self.grill_cat)
        self.juice = make_item(item_code="D-JUICE", item_name="عصير مانجو", default_sale_price="35.00", is_stock_tracked=False, category=self.drinks_cat)
        self.cashier = person(RoleCode.CASHIER, "kds_cashier")
        self.waiter = person(RoleCode.WAITER, "kds_waiter")
        self.client.force_login(self.owner)

    def stations(self):
        self.kitchen = stations.save_station({"name_ar": "المطبخ", "name_en": "Kitchen", "sort": "1", "is_default": True, "categories": [self.grill_cat.pk]}, self.owner)
        self.bar = stations.save_station({"name_ar": "المشروبات", "name_en": "Drinks", "sort": "2", "categories": [self.drinks_cat.pk]}, self.owner)

    def order(self, *items, table=None):
        order = services.open_order(self.owner, table=table or self.t1, guests=2)
        for item, quantity in items:
            services.add_item(order, item, self.owner, quantity)
        return order

    def feed(self, station=None, lang="ar", user=None):
        if user is not None:
            self.client.force_login(user)
        url = reverse("restaurant:kds_feed") + f"?lang={lang}" + (f"&station={station.pk}" if station else "")
        response = self.client.get(url)
        return response

    def cards(self, station=None):
        return self.feed(station).json()["tickets"]

    def act(self, op, pk, station=None):
        data = {"op": op, "id": pk}
        if station:
            data["station"] = station.pk
        return self.client.post(reverse("restaurant:kds_act"), data)

    def written(self):
        Projector().rebuild()
        return {"invoices": SalesInvoice.objects.count(), "payments": CustomerPayment.objects.count(), "cash": CashboxMovement.objects.count(),
                "stock": StockMovement.objects.count(), "journal": JournalEntry.objects.count(), "ledger": CustomerLedgerEntry.objects.count(),
                "plates": ProductionRun.objects.count()}


class RoutingTests(ScreenSetup):
    def test_a_burger_and_a_juice_make_two_tickets_and_each_feed_has_only_its_own_line(self):
        self.stations()
        order = self.order((self.burger, 2), (self.juice, 1))
        before = self.written()
        first = services.send_to_kitchen(order, self.owner)
        tickets = list(order.tickets.order_by("sequence"))
        self.assertEqual([(t.sequence, t.station) for t in tickets], [(1, self.kitchen), (2, self.bar)])  # station order; sequence unique per order
        self.assertEqual((first, first.batch), (tickets[0], tickets))
        self.assertEqual([line.item for line in tickets[0].lines.all()], [self.burger])
        self.assertEqual([line.item for line in tickets[1].lines.all()], [self.juice])
        self.assertEqual(self.written(), before)  # sending moves no money, no stock, no ledger
        self.assertEqual([[line["name"] for line in card["lines"]] for card in self.cards(self.kitchen)], [["برجر"]])
        self.assertEqual([[line["name"] for line in card["lines"]] for card in self.cards(self.bar)], [["عصير مانجو"]])
        self.assertEqual(len(self.cards()), 2)  # all stations
        more = services.add_item(order, self.juice, self.owner, 1)
        self.assertEqual(services.send_to_kitchen(order, self.owner).sequence, 3)  # a later send carries on the numbering
        self.assertEqual(more.__class__.objects.get(pk=more.pk).ticket.station, self.bar)

    def test_the_send_screen_prints_every_stations_ticket_on_its_own_page(self):
        self.stations()
        order = self.order((self.burger, 1), (self.juice, 1))
        sent = self.client.post(reverse("restaurant:order", args=[order.pk]), {"lang": "ar", "action": "send"})
        first = order.tickets.get(sequence=1)
        from django.contrib.messages import get_messages

        self.assertEqual([str(m) for m in get_messages(sent.wsgi_request)], ["اتبعتت تذكرتين للمطبخ (المطبخ، المشروبات)."])
        self.assertRedirects(sent, f"{reverse('restaurant:ticket', args=[first.pk])}?lang=ar&autoprint=1&upto=2", fetch_redirect_response=False)
        page = self.client.get(f"{reverse('restaurant:ticket', args=[first.pk])}?lang=ar&upto=2")
        self.assertContains(page, 'data-kitchen-ticket="%s-1"' % order.number)
        self.assertContains(page, 'data-kitchen-ticket="%s-2"' % order.number)
        self.assertContains(page, 'data-ticket-line="D-BURGER"')
        self.assertContains(page, 'data-ticket-line="D-JUICE"')
        self.assertContains(page, "break-after:page")
        alone = self.client.get(reverse("restaurant:ticket", args=[first.pk]))
        self.assertNotContains(alone, 'data-ticket-line="D-JUICE"')  # a reprint of one ticket is one ticket
        detail = self.client.get(reverse("restaurant:order", args=[order.pk]))
        self.assertContains(detail, "المشروبات")  # the order lists each ticket with its station

    def test_with_no_station_set_up_the_send_is_one_default_ticket_as_before(self):
        order = self.order((self.burger, 1), (self.juice, 1))
        ticket = services.send_to_kitchen(order, self.owner)
        self.assertEqual((order.tickets.count(), ticket.station, ticket.lines.count(), len(ticket.batch)), (1, None, 2, 1))
        self.assertEqual(len(self.cards()), 1)  # the screen works from the first day, before any setup
        self.assertEqual(len(self.cards()[0]["lines"]), 2)

    def test_what_no_station_holds_goes_to_the_default_station_or_stays_one_station_less_ticket(self):
        stations.save_station({"name_ar": "المشروبات", "sort": "1", "categories": [self.drinks_cat.pk]}, self.owner)  # no default
        order = self.order((self.burger, 1), (self.juice, 1), (self.tea, 1))
        services.send_to_kitchen(order, self.owner)
        self.assertEqual(sorted((t.station.name_ar if t.station else "", t.lines.count()) for t in order.tickets.all()), [("", 2), ("المشروبات", 1)])
        # (the burger and the tea have no station and no default: they stay together on one ticket with no station)
        second = self.order((self.burger, 1), table=self.t2)
        default = stations.save_station({"name_ar": "المطبخ", "sort": "2", "is_default": True}, self.owner)
        services.send_to_kitchen(second, self.owner)
        self.assertEqual(second.tickets.get().station, default)

    def test_a_station_holding_a_parent_category_takes_what_sits_under_it(self):
        child = Category.objects.create(category_code="C-JUICE", name_ar="عصاير طازة", parent=self.drinks_cat)
        self.juice.category = child
        self.juice.save()
        self.stations()
        order = self.order((self.juice, 1), (self.burger, 1))
        services.send_to_kitchen(order, self.owner)
        self.assertEqual({t.station.name_ar: t.lines.get().item.item_code for t in order.tickets.all()}, {"المطبخ": "D-BURGER", "المشروبات": "D-JUICE"})

    def test_setup_rules_one_station_per_category_one_default_a_unique_name(self):
        self.stations()
        with self.assertRaisesMessage(ValidationError, "متربطة بمحطة «المشروبات»"):
            stations.save_station({"name_ar": "حلويات", "categories": [self.drinks_cat.pk]}, self.owner)
        with self.assertRaisesMessage(ValidationError, "already goes to the station"):
            stations.save_station({"name_ar": "Sweets", "categories": [self.drinks_cat.pk]}, self.owner, lang="en")
        with self.assertRaisesMessage(ValidationError, "فيه محطة بنفس الاسم"):
            stations.save_station({"name_ar": "المطبخ"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "اكتب اسم المحطة"):
            stations.save_station({"name_ar": "  "}, self.owner)
        sweets = stations.save_station({"name_ar": "حلويات", "is_default": True}, self.owner)
        self.kitchen.refresh_from_db()
        self.assertEqual((self.kitchen.is_default, sweets.is_default), (False, True))  # one default: the last chosen
        kept = stations.save_station({"name_ar": "المشروبات", "sort": "5", "active": True, "categories": [self.drinks_cat.pk]}, self.owner, self.bar)
        self.assertEqual((kept.pk, kept.sort), (self.bar.pk, 5))  # changing a station keeps its own categories without clashing with itself
        services.send_to_kitchen(self.order((self.juice, 1)), self.owner)
        with self.assertRaisesMessage(ValidationError, "لسه ما خلصتش"):
            stations.save_station({"name_ar": "المشروبات", "active": False}, self.owner, self.bar)  # a station with tickets on its screen stays on
        KitchenTicket.objects.filter(station=self.bar).update(sent_at=timezone.now() - timezone.timedelta(hours=30))  # nobody can see it any more
        self.assertFalse(stations.save_station({"name_ar": "المشروبات", "active": False}, self.owner, self.bar).active)  # so it does not hold the station on
        self.assertTrue(AuditLog.objects.filter(action="create_station").exists() and AuditLog.objects.filter(action="change_station").exists())


class ScreenTests(ScreenSetup):
    def setUp(self):
        super().setUp()
        self.stations()
        self.order_a = self.order((self.burger, 2), (self.juice, 1))
        services.send_to_kitchen(self.order_a, self.owner)
        self.kt = self.order_a.tickets.get(station=self.kitchen)
        self.bt = self.order_a.tickets.get(station=self.bar)

    def test_the_page_and_the_feed_carry_the_card(self):
        page = self.client.get(reverse("restaurant:kds") + "?lang=ar")
        self.assertContains(page, "شاشة المطبخ")
        self.assertContains(page, 'data-station-tab="%s"' % self.bar.pk)
        self.assertContains(page, "hesba/js/kds.js")
        response = self.feed(self.kitchen)
        self.assertEqual(response["Cache-Control"], "no-store")
        data = response.json()
        card = data["tickets"][0]
        self.assertEqual((card["where"], card["number"], card["station"], card["level"], card["done"], card["cancelled"]),
                         ("الطاولة 1", self.order_a.number, "المطبخ", "fresh", False, False))
        self.assertEqual(card["lines"], [{"id": card["lines"][0]["id"], "name": "برجر", "quantity": "2", "note": "", "ready": False, "void": False, "reason": "", "seen": True}])
        self.assertIsNone(data["recall"])
        from datetime import datetime

        from django.utils import timezone

        # the business's own time of day, in the same local time the tickets are stamped in (never UTC beside local)
        self.assertEqual(data["clock"], timezone.localtime(datetime.fromisoformat(data["now"])).strftime("%H:%M"))
        english = self.feed(self.kitchen, lang="en").json()["tickets"][0]
        self.assertEqual((english["where"], english["station"]), ("Table 1", "Kitchen"))
        self.assertContains(self.client.get(reverse("restaurant:kds") + "?lang=en"), "Kitchen screen")

    def test_age_turns_the_card_amber_then_red(self):
        for minutes, level in ((2, "fresh"), (9, "warn"), (16, "late")):
            KitchenTicket.objects.filter(pk=self.kt.pk).update(sent_at=timezone.now() - timezone.timedelta(minutes=minutes))
            card = self.cards(self.kitchen)[0]
            self.assertEqual((card["level"], card["age"] // 60), (level, minutes))

    def test_ready_done_and_recall_stamp_the_times_and_move_nothing_else(self):
        line = self.kt.lines.get()
        before = self.written()
        self.assertEqual(self.act("ready", line.pk, self.kitchen).status_code, 200)
        line.refresh_from_db()
        self.assertIsNotNone(line.ready_at)
        self.assertTrue(self.cards(self.kitchen)[0]["lines"][0]["ready"])
        self.act("ready", line.pk)  # tapped again: not ready (a slip of the finger)
        line.refresh_from_db()
        self.assertIsNone(line.ready_at)
        self.act("ready", line.pk)
        answer = self.act("done", self.kt.pk, self.kitchen).json()
        self.kt.refresh_from_db()
        line.refresh_from_db()
        self.assertIsNotNone(self.kt.done_at)
        self.assertEqual(answer["tickets"], [])  # the card leaves this screen...
        self.assertEqual(answer["recall"]["id"], self.kt.pk)  # ...and «رجّع» can bring it back
        self.assertEqual(len(self.cards(self.bar)), 1)  # the bar's card is untouched
        # a finished ticket stamps every line that was not ready yet with its own time
        two = self.order((self.burger, 1), (self.cola, 1), table=self.t2)
        services.send_to_kitchen(two, self.owner)
        ticket = two.tickets.get(station=self.kitchen)
        self.assertEqual(ticket.lines.count(), 2)  # the cola has no category: it goes to the default station
        self.act("done", ticket.pk)
        ticket.refresh_from_db()
        self.assertEqual({line.ready_at for line in ticket.lines.all()}, {ticket.done_at})
        recalled = self.client.post(reverse("restaurant:kds_act"), {"op": "recall", "id": ticket.pk}).json()
        ticket.refresh_from_db()
        self.assertIsNone(ticket.done_at)
        self.assertEqual({line.ready_at for line in ticket.lines.all()}, {None})  # back exactly as it was before the tap
        self.assertIn(ticket.pk, [card["id"] for card in recalled["tickets"]])
        self.act("recall", self.kt.pk)
        self.kt.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(self.kt.done_at, None)
        self.assertIsNotNone(line.ready_at)  # the line the kitchen tapped itself stays ready: «رجّع» undoes only what the done tap did
        self.assertEqual(self.written(), before)  # no sale, stock, cashbox, ledger or journal row
        self.assertEqual(self.order_a.status, OrderStatus.OPEN)

    def test_the_waiters_order_page_shows_ready_and_the_board_marks_the_table(self):
        line = self.kt.lines.get()
        self.act("ready", line.pk)
        page = self.client.get(reverse("restaurant:order", args=[self.order_a.pk]) + "?lang=ar")
        self.assertContains(page, "data-line-ready")
        self.assertContains(page, "جاهز")
        board = self.client.get(reverse("restaurant:board") + "?lang=ar")
        self.assertContains(board, 'data-table-ready="1"')
        self.assertContains(board, "جاهز 1")
        self.assertContains(self.client.get(reverse("restaurant:board") + "?lang=en"), "1 ready")
        self.act("ready", line.pk)  # un-tapped: the mark goes
        self.assertNotContains(self.client.get(reverse("restaurant:board") + "?lang=ar"), "data-table-ready")
        # a waiter sees it too (the floor role)
        self.client.force_login(self.waiter)
        self.act("ready", line.pk)
        self.assertContains(self.client.get(reverse("restaurant:order", args=[self.order_a.pk])), "data-line-ready")

    def test_a_void_after_firing_stays_on_the_card_struck_through_until_the_kitchen_sees_it(self):
        line = self.kt.lines.get()
        self.act("ready", line.pk)  # the dish was already made when the waiter voided it
        before = self.written()
        self.client.force_login(self.cashier)
        sent = self.client.post(reverse("restaurant:order", args=[self.order_a.pk]), {"lang": "ar", "action": "void", "line": line.pk, "reason": "الزبون مشي"}, follow=True)
        self.assertEqual(sent.status_code, 200)
        line.refresh_from_db()
        self.assertEqual((line.voided, line.void_reason, line.ready_at), (True, "الزبون مشي", None))
        card = self.cards(self.kitchen)[0]
        self.assertEqual(card["lines"][0]["void"], True)
        self.assertEqual((card["lines"][0]["reason"], card["lines"][0]["seen"]), ("الزبون مشي", False))
        shown = self.client.get(reverse("restaurant:order", args=[self.order_a.pk]) + "?lang=ar")
        self.assertContains(shown, "الزبون مشي")
        self.assertContains(shown, "data-void-reason")
        self.act("done", self.kt.pk)  # the kitchen finishes the card knowing about it: the void counts as seen
        self.assertEqual(self.cards(self.kitchen), [])
        # a void that comes after the card was finished brings it back until it is seen
        later = services.add_item(self.order_a, self.burger, self.owner, 1)
        extra = services.send_to_kitchen(self.order_a, self.owner).batch
        self.act("done", extra[0].pk)
        self.assertEqual(self.cards(self.kitchen), [])
        self.client.force_login(self.cashier)
        services.void_line(later, self.cashier, reason="غلط في الطلب")
        back = self.cards(self.kitchen)
        self.assertEqual([(card["done"], card["lines"][0]["seen"]) for card in back], [(True, False)])
        self.act("ready", later.pk)  # a tap on the struck-through line is «seen»
        self.assertEqual(self.cards(self.kitchen), [])
        self.assertEqual(self.written(), before)  # a void is the bill's business, never a stock write-off here
        self.assertEqual(StockMovement.objects.filter(item=self.burger).count(), 0)

    def test_a_cancelled_order_shows_on_the_card_until_it_is_finished(self):
        services.cancel_order(self.order_a, self.owner, "خرجوا")
        card = next(c for c in self.cards(self.kitchen) if c["number"] == self.order_a.number)
        self.assertTrue(card["cancelled"])
        self.act("done", self.kt.pk)
        self.assertEqual(self.cards(self.kitchen), [])

    def test_a_paid_takeaway_is_still_cooked_and_an_old_ticket_drops_off(self):
        services.cancel_order(self.order_a, self.owner, "x")
        self.act("done", self.kt.pk)
        self.act("done", self.bt.pk)
        takeaway = services.open_order(self.owner, kind="takeaway")
        services.add_item(takeaway, self.burger, self.owner, 1)
        ticket = services.send_to_kitchen(takeaway, self.owner)
        services.pay(takeaway, self.owner, cashbox=self.cashbox, tendered=D("500"))
        self.assertEqual([c["number"] for c in self.cards(self.kitchen)], [takeaway.number])  # paid at the counter, still on the screen
        KitchenTicket.objects.filter(pk=ticket.pk).update(sent_at=timezone.now() - timezone.timedelta(hours=30))
        self.assertEqual(self.cards(self.kitchen), [])


class AccessTests(ScreenSetup):
    def setUp(self):
        super().setUp()
        self.stations()
        self.order_a = self.order((self.burger, 1))
        services.send_to_kitchen(self.order_a, self.owner)
        self.ticket = self.order_a.tickets.get()
        self.accountant = person(RoleCode.ACCOUNTANT, "kds_accountant")

    def test_the_screen_feed_and_taps_need_take_orders(self):
        self.assertFalse(user_has_permission(self.accountant, "restaurant.take_orders"))
        self.client.force_login(self.accountant)
        line = self.ticket.lines.get()
        for response in (self.client.get(reverse("restaurant:kds")), self.client.get(reverse("restaurant:kds_feed")),
                         self.client.post(reverse("restaurant:kds_act"), {"op": "ready", "id": line.pk}),
                         self.client.get(reverse("restaurant:kds_act"))):
            self.assertEqual(response.status_code, 403)
        line.refresh_from_db()
        self.assertIsNone(line.ready_at)
        for user in (self.waiter, self.cashier, self.owner):  # the floor roles hold it
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("restaurant:kds")).status_code, 200, user.username)
            self.assertEqual(self.client.get(reverse("restaurant:kds_feed")).status_code, 200, user.username)
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(reverse("restaurant:kds_act")).status_code, 405)  # taps are POST only
        self.assertEqual(self.act("explode", line.pk).status_code, 400)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("restaurant:kds_feed")).status_code, 302)  # signed out: the login page, which the screen handles

    def test_setup_is_seen_with_the_invoices_view_and_changed_with_manage_items(self):
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get(reverse("restaurant:stations")).status_code, 200)  # the same as the tables setup
        self.assertEqual(self.client.post(reverse("restaurant:stations"), {"name_ar": "جديدة"}).status_code, 403)
        self.client.force_login(self.waiter)
        self.assertEqual(self.client.get(reverse("restaurant:stations")).status_code, 403)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("restaurant:stations") + "?lang=ar")
        self.assertContains(page, 'data-station-row="المطبخ"')
        self.assertContains(page, "مشروبات")
        saved = self.client.post(reverse("restaurant:stations"), {"lang": "ar", "name_ar": "حلويات", "name_en": "Sweets", "sort": "3", "active": "on"}, follow=True)
        self.assertContains(saved, "اتحفظت المحطة")
        self.assertTrue(KitchenStation.objects.filter(name_ar="حلويات", entity=main_entity()).exists())
        clash = self.client.post(reverse("restaurant:stations"), {"lang": "ar", "name_ar": "حلويات2", "active": "on", "categories": [self.grill_cat.pk]})
        self.assertContains(clash, "data-station-error")
        edit = self.client.get(reverse("restaurant:stations") + f"?lang=en&edit={self.bar.pk}")
        self.assertContains(edit, "Drinks")

    def test_the_board_links_to_the_screen_and_the_setup(self):
        board = self.client.get(reverse("restaurant:board") + "?lang=ar")
        self.assertContains(board, "data-kds-link")
        self.assertContains(board, "data-stations-link")


class EntityTests(ScreenSetup):
    """Orders carry no entity yet, so stations do: the feed, the taps and the setup never cross entities."""

    def setUp(self):
        super().setUp()
        self.stations()  # the main entity's kitchen and bar
        self.branch = Entity.objects.create(code="E2", name_ar="فرع المعادي")
        with working_in(self.branch):
            self.far = stations.save_station({"name_ar": "مطبخ الفرع", "is_default": True, "categories": [self.grill_cat.pk]}, self.owner)
        self.order_main = self.order((self.burger, 1))
        services.send_to_kitchen(self.order_main, self.owner)  # sent while working across the group: routed to the main entity's stations
        with working_in(self.branch):
            self.order_far = self.order((self.burger, 1), table=self.t2)
            services.send_to_kitchen(self.order_far, self.owner)

    def work_in(self, entity):
        session = self.client.session
        session[SESSION_KEY] = entity.pk
        session.save()

    def test_a_send_is_routed_only_to_the_working_entitys_stations(self):
        self.assertEqual(self.far.entity, self.branch)
        self.assertEqual(self.order_main.tickets.get().station, self.kitchen)  # never the branch's «مطبخ الفرع», though both hold the grill
        self.assertEqual(self.order_far.tickets.get().station, self.far)

    def test_each_entitys_feed_has_only_its_own_orders(self):
        self.work_in(main_entity())
        self.assertEqual([card["number"] for card in self.cards()], [self.order_main.number])
        self.work_in(self.branch)
        self.assertEqual([card["number"] for card in self.cards()], [self.order_far.number])
        self.work_in(main_entity())
        self.assertEqual(self.feed(self.far).status_code, 404)  # another entity's station is a 404, page and feed
        self.assertEqual(self.client.get(reverse("restaurant:kds") + f"?station={self.far.pk}").status_code, 404)
        self.work_in(self.branch)
        self.assertEqual(self.feed(self.kitchen).status_code, 404)
        self.assertEqual([c["number"] for c in self.feed(self.far).json()["tickets"]], [self.order_far.number])

    def test_taps_cannot_reach_another_entitys_ticket_or_line(self):
        self.work_in(main_entity())
        far_ticket = self.order_far.tickets.get()
        far_line = far_ticket.lines.get()
        self.assertEqual(self.act("ready", far_line.pk).status_code, 404)
        self.assertEqual(self.act("done", far_ticket.pk).status_code, 404)
        self.assertEqual(self.act("recall", far_ticket.pk).status_code, 404)
        far_line.refresh_from_db()
        far_ticket.refresh_from_db()
        self.assertEqual((far_line.ready_at, far_ticket.done_at), (None, None))
        self.assertEqual(self.client.get(reverse("restaurant:stations") + f"?edit={self.far.pk}").status_code, 404)
        page = self.client.get(reverse("restaurant:stations") + "?lang=ar")
        self.assertNotContains(page, "مطبخ الفرع")

    def test_tickets_with_no_station_belong_to_the_main_entity(self):
        legacy = services.open_order(self.owner, kind="takeaway")
        line = services.add_item(legacy, self.tea, self.owner)
        ticket = KitchenTicket.objects.create(order=legacy, sequence=1, sent_by=self.owner)  # as every ticket made before stations existed
        OrderLine.objects.filter(pk=line.pk).update(ticket=ticket)
        self.work_in(main_entity())
        self.assertIn(legacy.number, [card["number"] for card in self.cards()])
        self.work_in(self.branch)
        self.assertNotIn(legacy.number, [card["number"] for card in self.cards()])
        self.assertEqual(ticket.station, None)

    def test_setup_lists_and_saves_for_the_working_entity_only(self):
        self.work_in(self.branch)
        page = self.client.get(reverse("restaurant:stations") + "?lang=ar")
        self.assertContains(page, "مطبخ الفرع")
        self.assertNotContains(page, 'data-station-row="المطبخ"')
        self.client.post(reverse("restaurant:stations"), {"lang": "ar", "name_ar": "بار الفرع", "active": "on"})
        self.assertEqual(KitchenStation.objects.get(name_ar="بار الفرع").entity, self.branch)


class StaticTests(ScreenSetup):
    """The new styles read the identity tokens only, and the screen draws what the server sends as text, never as markup."""

    def read(self, path):
        with open(finders.find(path), encoding="utf-8") as handle:
            return handle.read()

    def test_the_screens_styles_use_defined_tokens_and_no_colour_literals(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", self.read("hesba/css/tokens.css")))
        for sheet in ("hesba/css/kds.css", "hesba/css/restaurant.css"):
            css = self.read(sheet)
            self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [], sheet)
            self.assertEqual([c for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", css) if c.lower() not in {"#fff", "#ffffff"}], [], sheet)
            self.assertNotRegex(css, r"rgba?\(")

    def test_the_screens_script_never_writes_markup(self):
        script = self.read("hesba/js/kds.js")
        self.assertNotIn("innerHTML", script)
        self.assertNotIn("insertAdjacentHTML", script)
        self.assertIn("visibilitychange", script)  # a hidden tab stops polling
        self.assertIn("5000", script)

    def test_the_station_list_does_not_prefill_cell_labels(self):
        self.stations()
        page = self.client.get(reverse("restaurant:stations") + "?lang=ar").content.decode()
        self.assertIsNone(re.search(r"<t[dh][^>]*data-label", page))
