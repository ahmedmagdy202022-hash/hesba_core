"""Restaurants R5a (HG-115): a table's QR menu, and ordering from the table (paying from it is R5b, not here).

The set-up of ``tests_split_pay`` (the main hall with «T1», the Maadi branch with its own «T1», VAT 14% on, the clock on
Wednesday 11 Nov 2026 at noon in Cairo) and R1a's burger: 120.00 with a required «ميديم» and the priced add-on «جبنة زيادة»
at 15.00. With VAT on the guest sees the burger at **136.80** (120.00 + 16.80) and the cheese at 17.10; with VAT off, 120.00.

* the public page (``/t/<key>/``) needs no account, and a forged, rotated, inactive-table, inactive-branch or module-off key
  answers one and the same dead 404 page; it shows the menu and nothing of any order, bill, other table or staff;
* a guest's request waits (``TableRequest``); it never writes an order line. A staff member accepts it: the lines go through
  ``services.add_item`` as that waiter, unsent, at the menu price at that moment, all or nothing, once;
* nothing moves: no invoice, stock, cashbox, ledger or loyalty row is made by a request or its acceptance.
"""

import threading
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal as D
from unittest import mock, skipUnless

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from audit.models import AuditLog
from entities.models import EntityMembership
from hesba_testing.factories import make_item
from permissions.models import RoleCode
from variants.models import Variant, VariantGroup

from . import charges, combos, qr, services, views_qr
from .models import DiningTable, Order, OrderLine, OrderStatus, RequestKind, RequestStatus, TableRequest
from .tests import person
from .tests_charges import _counts
from .tests_combos import meal
from .tests_split_pay import SplitBillSetup

LATER = datetime(2026, 11, 11, 11, 0, tzinfo=dt_timezone.utc)  # an hour after the frozen clock
BASE = "https://hesba.example"


def moved(before, after):
    """The labels whose row count changed between two ``_counts()``."""

    return {label for label in after if after[label] != before.get(label)}


@override_settings(PUBLIC_BASE_URL=BASE)
class QrSetup(SplitBillSetup):
    def setUp(self):
        cache.clear()  # the throttles' counters live in the cache
        super().setUp()
        self.waiter = person(RoleCode.WAITER, "qr_waiter")
        EntityMembership.objects.create(user=self.waiter, entity=self.main, is_default=True)
        self.guest = Client()
        self.group = {"doneness": self.medium.group_id, "extras": self.cheese_opt.group_id}

    # ---- helpers ------------------------------------------------------------------------------------------------

    def url(self, table=None, **query):
        return reverse("table_menu", args=[(table or self.a_t1).qr_key]) + ("?" + "&".join(f"{k}={v}" for k, v in query.items()) if query else "")

    def burger_lines(self, quantity=2, cheese=True, note=""):
        data = {f"q-{self.burger.pk}": quantity, f"o-{self.burger.pk}-{self.group['doneness']}": self.medium.pk, f"n-{self.burger.pk}": note}
        if cheese:
            data[f"o-{self.burger.pk}-{self.group['extras']}"] = self.cheese_opt.pk
        return data

    def ask(self, data=None, table=None, client=None, lang="ar"):
        return (client or self.guest).post(self.url(table, lang=lang), {"action": "order", **(data if data is not None else self.burger_lines())})

    def request(self, data=None, table=None):
        response = self.ask(data, table)
        self.assertEqual(response.status_code, 303, response.content.decode()[:600] if response.status_code != 303 else "")
        return TableRequest.objects.order_by("-pk").first()

    def lines(self, order):
        return list(order.lines.order_by("pk").values_list("item__item_code", "quantity", "unit_price", "ticket_id", "options", "note"))

    def staff(self, user=None, entity=None):
        self.work_in(entity, user or self.waiter)


class DeadPageTests(QrSetup):
    def test_a_forged_a_rotated_and_an_inactive_key_answer_the_same_page(self):
        qr.rotate_key(self.b_t1, self.owner)  # b_t1 gets a new key; the old one is only remembered below
        old = self.a_t1.qr_key
        qr.rotate_key(self.a_t1, self.owner)
        off = services.save_table({"name": "T9", "seats": 2, "active": "on"}, self.owner, entity=self.main)
        services.save_table({"name": "T9", "seats": 2, "active": ""}, self.owner, table=off)
        off.refresh_from_db()
        answers = {name: self.guest.get(reverse("table_menu", args=[key])) for name, key in
                   (("forged", "not-a-real-key-0000"), ("rotated", old), ("inactive", off.qr_key), ("long", "k" * 90))}
        for name, response in answers.items():
            self.assertEqual(response.status_code, 404, name)
            self.assertIn("no-store", response["Cache-Control"], name)
            self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow", name)
            self.assertEqual(response["Referrer-Policy"], "same-origin", name)
            self.assertContains(response, "data-table-dead", status_code=404)
        self.assertEqual(len({response.content for response in answers.values()}), 1)  # byte for byte the same page
        self.a_t1.refresh_from_db()
        self.assertEqual(self.guest.get(self.url()).status_code, 200)  # the new key opens

    def test_an_inactive_branch_and_a_module_that_is_off_answer_the_same_page(self):
        forged = self.guest.get(reverse("table_menu", args=["nope"])).content
        self.branch.active = False
        self.branch.save(update_fields=["active"])
        gone = self.guest.get(self.url(self.b_t1))
        self.assertEqual((gone.status_code, gone.content), (404, forged))
        from settings_core.models import FeatureFlag
        from settings_core.setup_services import module_flag_code

        FeatureFlag.objects.filter(code=module_flag_code("tables_orders")).update(enabled=False)
        shut = self.guest.get(self.url())
        self.assertEqual((shut.status_code, shut.content), (404, forged))
        shut_post = self.guest.post(self.url(), {"action": "call"})
        self.assertEqual(shut_post.status_code, 404)
        self.assertFalse(TableRequest.objects.exists())

    def test_the_public_route_needs_no_account_and_is_not_a_login_redirect(self):
        response = self.guest.get(self.url())
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("/login/", response.get("Location", ""))
        self.assertEqual(self.guest.get("/t/").status_code, 404)  # no key, no page (and no login redirect for a public prefix: it is a 404 route miss)

    def test_a_post_without_the_csrf_token_is_refused(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(strict.post(self.url(), {"action": "call"}).status_code, 403)
        self.assertFalse(TableRequest.objects.exists())


class PublicPageTests(QrSetup):
    def test_the_page_shows_the_menu_with_vat_in_the_price_and_the_headers(self):
        page = self.guest.get(self.url())
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "136.80")  # 120.00 + 16.80 (14%), the bill's own per-item rate path
        self.assertContains(page, "17.10")  # the cheese add-on at its own item's rate: 15.00 + 2.10
        self.assertContains(page, "الأسعار شاملة ضريبة القيمة المضافة")
        self.assertNotContains(page, "120.00")
        self.assertIn("no-store", page["Cache-Control"])
        self.assertEqual((page["X-Robots-Tag"], page["Referrer-Policy"]), ("noindex, nofollow", "same-origin"))
        self.assertContains(page, "طاولة T1")
        self.assertContains(page, "اطلب")
        self.assertContains(page, "نادي الجرسون")
        self.assertContains(page, "الحساب")
        for word in ("دوس", "لينك", "بتاع", "الأسطى", "ورشة"):
            self.assertNotContains(page, word)

    def test_with_vat_off_the_price_is_the_menu_price(self):
        self.vat(False)
        page = self.guest.get(self.url())
        self.assertContains(page, "120.00")
        self.assertNotContains(page, "136.80")
        self.assertNotContains(page, "الأسعار شاملة")

    def test_the_menu_is_the_waiters_without_combos_ingredients_and_the_fees_a_bill_adds_by_itself(self):
        pepsi = make_item(item_code="C-PEPSI", item_name="بيبسي", default_sale_price="25.00", is_stock_tracked=False)
        fries = make_item(item_code="C-FRIES", item_name="بطاطس", default_sale_price="40.00", is_stock_tracked=False)
        large = make_item(item_code="C-FRIES-L", item_name="بطاطس كبيرة", default_sale_price="50.00", is_stock_tracked=False)
        combo = combos.save_combo(meal(self.burger, fries, large, pepsi), self.owner)
        charges.charge_item(charges.SERVICE_ITEM_CODE)
        self.configure_service()
        ids = {item.pk for item in qr.public_items()}
        self.assertIn(self.burger.pk, ids)
        self.assertIn(self.tea.pk, ids)
        for hidden in (combo.item, self.rice, self.lentil):
            self.assertNotIn(hidden.pk, ids)
        from master_data.models import Item

        self.assertFalse(Item.objects.filter(item_code__in=services.OFF_MENU_CODES, pk__in=ids).exists())
        page = self.guest.get(self.url()).content.decode()
        self.assertNotIn("وجبة برجر", page)
        self.assertNotIn("أرز", page)
        self.assertIn("كشري كبير", page)
        from .views import _menu

        waiter_menu = {item.pk for item in _menu()[0]}
        self.assertEqual(ids, waiter_menu - {combo.item_id, self.rice.pk, self.lentil.pk})

    def configure_service(self, percent="12", minimum="50"):
        return charges.save_settings({"percent": percent, "kinds": ["dine_in"], "minimum": minimum}, self.owner)

    def test_the_service_and_the_minimum_are_told_when_they_are_set(self):
        self.assertNotContains(self.guest.get(self.url()), "خدمة 12")
        self.configure_service()
        page = self.guest.get(self.url())
        self.assertContains(page, "بتضاف نسبة خدمة 12%")
        self.assertContains(page, "الحد الأدنى للفرد 50.00")

    def test_a_size_family_is_one_tile_with_its_sizes_and_each_size_is_priced_with_vat(self):
        small = make_item(item_code="P-S", item_name="بيتزا صغيرة", default_sale_price="80.00", is_stock_tracked=False)
        large = make_item(item_code="P-L", item_name="بيتزا كبيرة", default_sale_price="100.00", is_stock_tracked=False)
        group = VariantGroup.objects.create(code="PIZZA", name="بيتزا", sizes=["صغير", "كبير"], colors=[], default_sale_price=D("80"))
        Variant.objects.create(group=group, item=small, size="صغير")
        Variant.objects.create(group=group, item=large, size="كبير")
        page = self.guest.get(self.url())
        self.assertContains(page, "data-menu-family", count=1)
        self.assertContains(page, "91.20")  # 80.00 + 11.20
        self.assertContains(page, "114.00")  # 100.00 + 14.00
        self.assertContains(page, f'name="q-{small.pk}"')
        self.assertContains(page, f'name="q-{large.pk}"')

    def test_a_dish_with_choices_lists_them_and_its_required_group_is_marked(self):
        page = self.guest.get(self.url())
        self.assertContains(page, f'name="o-{self.burger.pk}-{self.group["doneness"]}"')
        self.assertContains(page, 'type="radio"')  # a single required choice
        self.assertContains(page, "ميديم")
        self.assertContains(page, "جبنة زيادة")

    def test_the_page_never_shows_an_order_a_bill_another_table_or_a_staff_name(self):
        garden = services.save_table({"name": "Garden-77", "seats": 2}, self.owner, entity=self.main)
        order = services.open_order(self.owner, table=self.a_t1, guests=3)
        services.add_item(order, self.tea, self.owner, 2, "سكر زيادة")
        other = services.open_order(self.owner, table=garden)
        services.add_item(other, self.koshary, self.owner, 1)
        page = self.guest.get(self.url()).content.decode()
        self.assertNotIn(order.number, page)
        self.assertNotIn(other.number, page)
        self.assertNotIn("سكر زيادة", page)
        self.assertNotIn("Garden-77", page)
        self.assertNotIn(garden.qr_key, page)
        self.assertNotIn(self.a_t1.qr_key, page)  # not even its own key is printed on the page
        self.assertNotIn(self.owner.username, page)
        self.assertNotIn(self.waiter.username, page)
        self.assertNotIn("34.20", page)  # 2 teas with VAT is what the bill would be: not shown

    def test_english(self):
        page = self.guest.get(self.url(lang="en"))
        self.assertContains(page, "Call the waiter")
        self.assertContains(page, "Prices include VAT.")
        self.assertContains(page, 'dir="ltr"')
        self.assertContains(page, "Table T1")
        self.assertContains(page, "Order")

    def test_a_branch_table_shows_the_company_wide_menu_under_the_branch_name(self):
        page = self.guest.get(self.url(self.b_t1))
        self.assertContains(page, "فرع المعادي")
        self.assertContains(page, "136.80")
        self.assertEqual(set(self.menu_ids(self.a_t1)), set(self.menu_ids(self.b_t1)))  # an item carries no entity: one menu

    def menu_ids(self, table):
        import re

        return re.findall(r'data-menu-dish="(\d+)"', self.guest.get(self.url(table)).content.decode())


class LimitTests(QrSetup):
    def test_the_per_address_page_limit_answers_429_and_counts_guessed_keys_too(self):
        with mock.patch.object(views_qr, "PAGE_LIMIT", 3):
            for _ in range(3):
                self.assertEqual(self.guest.get(reverse("table_menu", args=["guess"])).status_code, 404)
            blocked = self.guest.get(self.url())
        self.assertEqual(blocked.status_code, 429)
        self.assertIn("no-store", blocked["Cache-Control"])

    def test_the_per_address_request_limit(self):
        with mock.patch.object(views_qr, "POST_IP_LIMIT", 2):
            self.assertEqual(self.guest.post(self.url(), {"action": "call"}).status_code, 303)
            self.assertEqual(self.guest.post(self.url(), {"action": "bill"}).status_code, 303)
            blocked = self.guest.post(self.url(), {"action": "order", **self.burger_lines()})
        self.assertEqual(blocked.status_code, 429)
        self.assertEqual(TableRequest.objects.count(), 2)

    def test_the_per_table_hourly_limit_holds_whoever_sends(self):
        with mock.patch.object(views_qr, "TABLE_LIMIT", 2):
            for client in (Client(), Client()):
                self.assertEqual(client.post(self.url(), {"action": "call"}).status_code, 303)
            self.assertEqual(Client().post(self.url(), {"action": "bill"}).status_code, 429)

    def test_the_open_requests_cap_per_table(self):
        for number in range(qr.MAX_OPEN):
            self.assertEqual(self.ask({f"q-{self.tea.pk}": 1}).status_code, 303, number)
        refused = self.ask({f"q-{self.tea.pk}": 1})
        self.assertEqual(refused.status_code, 400)
        self.assertContains(refused, "فيه طلبات كتير مستنية", status_code=400)
        self.assertEqual(TableRequest.objects.filter(table=self.a_t1).count(), qr.MAX_OPEN)
        self.assertEqual(self.ask({f"q-{self.tea.pk}": 1}, table=self.b_t1).status_code, 303)  # another table has its own cap
        self.assertEqual(self.guest.post(self.url(), {"action": "call"}).status_code, 400)  # the cap counts calls too

    def test_a_call_or_a_bill_asked_twice_is_one_request(self):
        for _ in range(3):
            self.assertEqual(self.guest.post(self.url(), {"action": "call"}).status_code, 303)
            self.assertEqual(self.guest.post(self.url(), {"action": "bill"}).status_code, 303)
        self.assertEqual(list(TableRequest.objects.order_by("kind").values_list("kind", flat=True)), ["bill", "call_waiter"])

    def test_a_cap_on_lines_on_quantity_and_on_the_notes(self):
        items = [make_item(item_code=f"X-{n:02d}", item_name=f"صنف {n}", default_sale_price="10.00", is_stock_tracked=False) for n in range(qr.MAX_LINES + 1)]
        too_many = self.ask({f"q-{item.pk}": 1 for item in items})
        self.assertEqual(too_many.status_code, 400)
        self.assertContains(too_many, f"لحد {qr.MAX_LINES} أصناف", status_code=400)
        self.assertEqual(self.ask({f"q-{item.pk}": 1 for item in items[: qr.MAX_LINES]}).status_code, 303)
        TableRequest.objects.all().delete()
        for quantity in (str(qr.MAX_QUANTITY + 1), "1.5", "-1", "abc", "1e2", "٣", "²", "999999"):
            refused = self.ask({f"q-{self.tea.pk}": quantity})
            self.assertEqual(refused.status_code, 400, quantity)
        self.assertFalse(TableRequest.objects.exists())
        made = self.request({f"q-{self.tea.pk}": qr.MAX_QUANTITY, f"n-{self.tea.pk}": "ن" * 300, "note": "م" * 300})
        self.assertEqual((made.items[0]["quantity"], len(made.items[0]["note"]), len(made.note)), (qr.MAX_QUANTITY, qr.LINE_NOTE, qr.NOTE))

    def test_an_empty_cart_is_refused(self):
        self.assertEqual(self.ask({f"q-{self.tea.pk}": 0}).status_code, 400)
        self.assertEqual(self.ask({}).status_code, 400)
        self.assertFalse(TableRequest.objects.exists())

    def test_the_honeypot_and_a_bad_action_keep_nothing(self):
        self.assertEqual(self.ask({f"q-{self.tea.pk}": 1, "website": "http://spam"}).status_code, 400)
        self.assertEqual(self.guest.post(self.url(), {"action": "delete"}).status_code, 400)
        self.assertFalse(TableRequest.objects.exists())


class CraftedCartTests(QrSetup):
    def refused(self, data, *needles):
        before = TableRequest.objects.count()
        response = self.ask(data)
        self.assertEqual(response.status_code, 400)
        for needle in needles:
            self.assertContains(response, needle, status_code=400)
        self.assertEqual(TableRequest.objects.count(), before)  # nothing kept
        self.assertFalse(OrderLine.objects.exists())
        return response

    def test_an_inactive_item_is_refused(self):
        self.tea.active = False
        self.tea.save(update_fields=["active"])
        self.refused({f"q-{self.tea.pk}": 1, f"q-{self.koshary.pk}": 1}, "مش متاح")

    def test_a_fee_a_combo_and_a_raw_ingredient_are_refused(self):
        service = charges.charge_item(charges.SERVICE_ITEM_CODE)
        pepsi = make_item(item_code="C-PEPSI", item_name="بيبسي", default_sale_price="25.00", is_stock_tracked=False)
        fries = make_item(item_code="C-FRIES", item_name="بطاطس", default_sale_price="40.00", is_stock_tracked=False)
        large = make_item(item_code="C-FRIES-L", item_name="بطاطس كبيرة", default_sale_price="50.00", is_stock_tracked=False)
        combo = combos.save_combo(meal(self.burger, fries, large, pepsi), self.owner)
        for item in (service, combo.item, self.rice):
            self.refused({f"q-{item.pk}": 1}, "مش متاح")

    def test_an_unknown_item_and_a_forged_number_are_refused(self):
        self.refused({"q-999999": 1}, "مش متاح")
        self.refused({f"q-{self.burger.pk}": 1, f"o-{self.burger.pk}-1": ["²", "٣"]}, "مش متاح")  # not 0-9: refused, never a server error
        self.refused({"q-abc": 1, "q-": 1, "x-1": 1})  # not even a cart line: the cart is empty

    def test_a_required_choice_missing_is_refused_and_the_cart_comes_back(self):
        response = self.refused({f"q-{self.burger.pk}": 2, f"q-{self.tea.pk}": 1}, "محتاج تختار من «درجة الاستواء»")
        self.assertContains(response, f'name="q-{self.burger.pk}" value="2"', status_code=400)  # the guest's quantities are kept
        self.assertContains(response, f'name="q-{self.tea.pk}" value="1"', status_code=400)

    def test_another_dishs_option_a_disabled_option_and_a_repeated_one_are_refused(self):
        other = make_item(item_code="D-OTHER", item_name="طبق تاني", default_sale_price="30.00", is_stock_tracked=False)
        self.refused({f"q-{other.pk}": 1, f"o-{other.pk}-1": self.medium.pk}, "مش من اختيارات")
        self.cheese_opt.active = False
        self.cheese_opt.save(update_fields=["active"])
        self.refused(self.burger_lines(), "مش متاح")
        self.cheese_opt.active = True
        self.cheese_opt.save(update_fields=["active"])
        self.refused({f"q-{self.burger.pk}": 1, f"o-{self.burger.pk}-{self.group['doneness']}": [self.medium.pk, self.medium.pk]}, "")

    def test_a_cart_cannot_carry_a_price_or_a_name(self):
        made = self.request({**self.burger_lines(), f"price-{self.burger.pk}": "1.00", "unit_price": "0.01"})
        self.assertEqual(set(made.items[0]), {"item", "quantity", "options", "note"})
        self.assertEqual(made.items[0]["options"], sorted([self.medium.pk, self.cheese_opt.pk]))


class GuestStatusTests(QrSetup):
    def test_a_guest_sees_the_status_of_their_own_requests_only(self):
        made = self.request()
        mine = self.guest.get(self.url())
        self.assertContains(mine, 'data-request-status="new"')
        self.assertContains(mine, "وصل طلبك")
        self.assertNotContains(mine, self.burger.item_name + " ×")  # what was asked is never echoed
        stranger = Client().get(self.url())
        self.assertNotContains(stranger, "data-request-status")
        qr.accept(made, self.waiter)
        self.assertContains(self.guest.get(self.url()), 'data-request-status="accepted"')
        self.assertContains(self.guest.get(self.url()), "الجرسون استلم طلبك")

    def test_a_declined_request_is_told_and_another_tables_request_is_never_shown(self):
        made = self.request()
        qr.decline(made, self.waiter)
        self.assertContains(self.guest.get(self.url()), "الطلب ما اتقبلش")
        other = self.request({f"q-{self.tea.pk}": 1}, table=self.b_t1)  # the same browser asks at another table
        page = self.guest.get(self.url())
        self.assertEqual(page.content.decode().count("data-request-status="), 1)
        self.assertEqual(page.content.decode().count("data-request-kind=\"order\""), 1)
        self.assertEqual(other.table_id, self.b_t1.pk)

    def test_the_cookie_is_signed_http_only_and_scoped_to_the_table_and_a_forged_one_shows_nothing(self):
        self.request()
        cookie = self.guest.cookies[views_qr.COOKIE]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["path"], reverse("table_menu", args=[self.a_t1.qr_key]))
        self.assertEqual(cookie["samesite"], "Lax")
        forger = Client()
        forger.cookies[views_qr.COOKIE] = str(TableRequest.objects.get().pk)  # an unsigned id
        self.assertNotContains(forger.get(self.url()), "data-request-status")
        forger.cookies[views_qr.COOKIE] = "1,2,3:forged-signature"
        self.assertNotContains(forger.get(self.url()), "data-request-status")

    def test_a_call_and_a_bill_are_told_in_their_own_words(self):
        self.guest.post(self.url(), {"action": "call"})
        self.guest.post(self.url(), {"action": "bill"})
        page = self.guest.get(self.url())
        self.assertContains(page, "نادينا الجرسون")
        self.assertContains(page, "طلبنا الحساب")
        english = self.guest.get(self.url(lang="en"))
        self.assertContains(english, "We called the waiter")
        self.assertContains(english, "We asked for the bill")


class AcceptTests(QrSetup):
    def test_accept_adds_exactly_the_requested_lines_with_their_addons_unsent_as_the_waiter(self):
        made = self.request(self.burger_lines(2, note="من غير مخلل"))
        self.assertFalse(Order.objects.exists())  # nothing yet: a request is not an order
        done, order = qr.accept(made, self.waiter)
        self.assertEqual((order.table_id, order.status, order.opened_by_id, order.entity_id), (self.a_t1.pk, OrderStatus.OPEN, self.waiter.pk, self.main.pk))
        self.assertEqual(self.lines(order), [("D-BURG", D("2.000"), D("120.00"), None, "ميديم", "من غير مخلل"),
                                              ("A-CHEESE", D("2.000"), D("15.00"), None, "جبنة زيادة", "")])
        self.assertFalse(order.tickets.exists())  # unsent: the waiter sends to the kitchen as usual
        done.refresh_from_db()
        self.assertEqual((done.status, done.handled_by_id, done.order_id), (RequestStatus.ACCEPTED, self.waiter.pk, order.pk))
        self.assertIsNotNone(done.handled_at)

    def test_the_price_is_the_menu_price_when_it_is_accepted(self):
        made = self.request(self.burger_lines(1, cheese=False))
        self.burger.default_sale_price = D("130.00")
        self.burger.save(update_fields=["default_sale_price"])
        _, order = qr.accept(made, self.waiter)
        self.assertEqual(order.lines.get(item=self.burger).unit_price, D("130.00"))  # not the 120.00 the guest saw

    def test_the_lines_join_the_tables_open_order(self):
        order = services.open_order(self.owner, table=self.a_t1, guests=3)
        services.add_item(order, self.tea, self.owner, 1)
        made = self.request({f"q-{self.tea.pk}": 2, f"q-{self.koshary.pk}": 1})
        _, joined = qr.accept(made, self.waiter)
        self.assertEqual(joined.pk, order.pk)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(sorted(self.lines(order), key=lambda row: row[0]), [("D-KOSH", D("1.000"), D("60.00"), None, "", ""),
                                                                              ("D-TEA", D("3.000"), D("15.00"), None, "", "")])  # the tea merged: 1 + 2
        self.assertEqual(order.opened_by_id, self.owner.pk)  # the order the owner opened keeps its opener

    def test_an_item_made_inactive_before_accept_refuses_everything_and_adds_nothing(self):
        made = self.request({f"q-{self.tea.pk}": 1, f"q-{self.koshary.pk}": 1})
        self.koshary.active = False
        self.koshary.save(update_fields=["active"])
        with self.assertRaisesMessage(ValidationError, "«كشري كبير» مش متاح دلوقتي"):
            qr.accept(made, self.waiter)
        self.assertEqual((Order.objects.count(), OrderLine.objects.count()), (0, 0))  # not even the order that was to be opened
        made.refresh_from_db()
        self.assertEqual(made.status, RequestStatus.NEW)

    def test_a_choice_no_longer_valid_refuses_the_accept(self):
        made = self.request(self.burger_lines())
        self.cheese_opt.active = False
        self.cheese_opt.save(update_fields=["active"])
        with self.assertRaises(ValidationError):
            qr.accept(made, self.waiter)
        self.assertEqual((Order.objects.count(), OrderLine.objects.count()), (0, 0))

    def test_a_failure_part_way_adds_nothing(self):
        made = self.request({f"q-{self.tea.pk}": 1, f"q-{self.koshary.pk}": 1})
        real = services.add_item
        calls = []

        def second_fails(order, item, *args, **kwargs):
            calls.append(item.pk)
            if len(calls) == 2:
                raise ValidationError("boom")
            return real(order, item, *args, **kwargs)

        with mock.patch.object(services, "add_item", second_fails), self.assertRaises(ValidationError):
            qr.accept(made, self.waiter)
        self.assertEqual((Order.objects.count(), OrderLine.objects.count()), (0, 0))
        made.refresh_from_db()
        self.assertEqual(made.status, RequestStatus.NEW)

    def test_accepting_twice_adds_the_lines_once(self):
        made = self.request({f"q-{self.tea.pk}": 2})
        qr.accept(made, self.waiter)
        with self.assertRaisesMessage(ValidationError, "اتعامل معاه قبل كده"):
            qr.accept(made, self.owner)  # a stale copy of the object: the row is read again under its lock
        self.assertEqual(Order.objects.get().lines.get().quantity, D("2.000"))

    def test_decline_keeps_nothing(self):
        made = self.request()
        declined = qr.decline(made, self.waiter)
        self.assertEqual((declined.status, declined.handled_by_id, declined.order_id), (RequestStatus.DECLINED, self.waiter.pk, None))
        self.assertEqual((Order.objects.count(), OrderLine.objects.count()), (0, 0))
        with self.assertRaises(ValidationError):
            qr.accept(made, self.waiter)

    def test_a_call_and_a_bill_are_marked_handled_and_add_nothing(self):
        self.guest.post(self.url(), {"action": "call"})
        self.guest.post(self.url(), {"action": "bill"})
        before = _counts()
        for found in TableRequest.objects.all():
            handled = qr.handle(found, self.waiter)
            self.assertEqual((handled.status, handled.handled_by_id, handled.order_id), (RequestStatus.ACCEPTED, self.waiter.pk, None))
        self.assertEqual(moved(before, _counts()), {"audit.AuditLog"})
        with self.assertRaises(ValidationError):
            qr.accept(TableRequest.objects.filter(kind="bill").first(), self.waiter)  # already handled
        order_request = self.request({f"q-{self.tea.pk}": 1})
        with self.assertRaises(ValidationError):
            qr.handle(order_request, self.waiter)  # an order is accepted, not «تمام»
        self.assertFalse(Order.objects.exists())

    def test_an_inactive_table_refuses_the_accept(self):
        made = self.request({f"q-{self.tea.pk}": 1})
        DiningTable.objects.filter(pk=self.a_t1.pk).update(active=False)
        with self.assertRaisesMessage(ValidationError, "مش شغالة"):
            qr.accept(made, self.waiter)
        self.assertFalse(Order.objects.exists())

    def test_audit_rows_name_the_acting_waiter(self):
        made = self.request({f"q-{self.tea.pk}": 1})
        second = self.request({f"q-{self.koshary.pk}": 1})
        qr.accept(made, self.waiter)
        qr.decline(second, self.owner)
        rows = AuditLog.objects.filter(module="restaurant", object_type="restaurant.TableRequest").order_by("pk")
        self.assertEqual([(row.action, row.actor_id) for row in rows], [("accept_table_request", self.waiter.pk), ("decline_table_request", self.owner.pk)])
        self.assertEqual(rows[0].after_data["lines"], 1)


class StaleRequestTests(QrSetup):
    def test_a_request_from_a_party_that_has_left_is_declined_not_added_to_the_next_party(self):
        first = services.open_order(self.owner, table=self.a_t1)
        services.add_item(first, self.tea, self.owner, 1)
        made = self.request({f"q-{self.koshary.pk}": 1})
        with mock.patch("django.utils.timezone.now", return_value=LATER):
            services.cancel_order(first, self.owner, "ضيوف مشيوا")  # the party leaves; closed after the request was made
            second = services.open_order(self.owner, table=self.a_t1)  # the next party
            with self.assertRaisesMessage(ValidationError, "اتبعت قبل ما حساب الطاولة يتقفل"):
                qr.accept(made, self.waiter)
        made.refresh_from_db()
        self.assertEqual(made.status, RequestStatus.DECLINED)  # and the refusal is kept, not rolled back
        self.assertEqual(second.lines.count(), 0)

    def test_the_waiting_list_sweeps_a_stale_request_and_keeps_a_fresh_one(self):
        first = services.open_order(self.owner, table=self.a_t1)
        services.add_item(first, self.tea, self.owner, 1)
        old = self.request({f"q-{self.koshary.pk}": 1})
        old_call = self.guest.post(self.url(), {"action": "call"})
        self.assertEqual(old_call.status_code, 303)
        with mock.patch("django.utils.timezone.now", return_value=LATER):
            services.cancel_order(first, self.owner, "مشيوا")
            fresh = Client().post(self.url(), {"action": "order", f"q-{self.tea.pk}": 1})
            self.assertEqual(fresh.status_code, 303)
            live = qr.waiting()
        self.assertEqual([found.kind for found in live], ["order"])
        self.assertEqual(live[0].pk, TableRequest.objects.order_by("-pk").first().pk)
        old.refresh_from_db()
        self.assertEqual(old.status, RequestStatus.DECLINED)
        self.assertIsNone(old.handled_by_id)  # swept by the system, not by a person

    def test_a_request_made_before_the_bill_was_paid_is_declined_when_the_next_party_sits(self):
        order = self.burgers()
        made = self.request({f"q-{self.tea.pk}": 1})
        with mock.patch("django.utils.timezone.now", return_value=LATER):
            services.pay(order, self.owner, cashbox=self.cashbox, tendered=D("1000"))  # the party pays and leaves
            self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.PAID)
            next_party = services.open_order(self.owner, table=self.a_t1)
            with self.assertRaisesMessage(ValidationError, "اتبعت قبل ما حساب الطاولة يتقفل"):
                qr.accept(made, self.waiter)
        made.refresh_from_db()
        self.assertEqual((made.status, made.order_id), (RequestStatus.DECLINED, None))
        self.assertEqual(next_party.lines.count(), 0)

    def test_a_request_at_a_table_that_was_merged_away_is_stale(self):
        first = services.open_order(self.owner, table=self.a_t1)
        services.add_item(first, self.tea, self.owner, 1)
        other_table = services.save_table({"name": "T2", "seats": 4}, self.owner, entity=self.main)
        second = services.open_order(self.owner, table=other_table)
        services.add_item(second, self.koshary, self.owner, 1)
        made = self.request({f"q-{self.koshary.pk}": 1})  # at T1
        with mock.patch("django.utils.timezone.now", return_value=LATER):
            services.merge_orders(second, first, self.owner)  # T1's bill is folded into T2's: T1's order is closed
            self.assertTrue(qr.is_stale(made))
            with self.assertRaises(ValidationError):
                qr.accept(made, self.waiter)
        made.refresh_from_db()
        self.assertEqual(made.status, RequestStatus.DECLINED)

    def test_a_waiting_request_follows_its_party_when_the_order_moves_table(self):
        first = services.open_order(self.owner, table=self.a_t1)
        services.add_item(first, self.tea, self.owner, 1)
        made = self.request({f"q-{self.koshary.pk}": 1})  # at T1, by the party sitting there
        other_table = services.save_table({"name": "T2", "seats": 4}, self.owner, entity=self.main)
        services.move_table(first, other_table, self.owner)  # the party moves to T2; their order is still open
        made.refresh_from_db()
        self.assertEqual((made.table_id, made.status), (other_table.pk, RequestStatus.NEW))
        self.assertEqual(qr.waiting(self.a_t1), [])
        next_party = services.open_order(self.owner, table=self.a_t1)  # someone else sits at T1
        qr.accept(made, self.waiter)
        self.assertEqual(next_party.lines.count(), 0)  # the koshary never lands on the new party's bill
        self.assertEqual(first.lines.filter(item=self.koshary).count(), 1)

    def test_a_part_of_a_bill_paid_at_the_table_does_not_stale_a_request(self):
        order = self.burgers()
        made = self.request({f"q-{self.tea.pk}": 1})
        with mock.patch("django.utils.timezone.now", return_value=LATER):
            part_rows = {str(line.pk): "1" for line in order.lines.filter(parent=None)}
            part = services.split_order(order, part_rows, self.owner)
            self.assertFalse(qr.is_stale(made))
            self.assertTrue(part.pk)


class NothingMovesTests(QrSetup):
    def test_a_request_and_its_acceptance_move_no_money_stock_ledger_or_points(self):
        before = _counts()
        made = self.request(self.burger_lines())
        self.guest.post(self.url(), {"action": "call"})
        after_asking = _counts()
        self.assertLessEqual(moved(before, after_asking), {"restaurant.TableRequest", "sessions.Session"})
        qr.accept(made, self.waiter)
        qr.decline(TableRequest.objects.get(kind="call_waiter"), self.waiter)
        changed = moved(before, _counts())
        self.assertLessEqual(changed, {"restaurant.TableRequest", "restaurant.Order", "restaurant.OrderLine", "audit.AuditLog", "sessions.Session"})
        for label in ("sales.SalesInvoice", "sales.SalesLine", "inventory.StockMovement", "cashboxes.CashboxMovement", "ledger.JournalLine",
                      "ledger.JournalEntry", "loyalty.PointsEntry", "giftcards.GiftCardEntry", "sales.CustomerPayment", "settlements.InvoiceSettlement",
                      "promotions.InvoicePromotion", "manufacturing.ProductionRun"):
            self.assertIn(label, before, label)  # a label that does not exist would pass vacuously
            self.assertEqual(after_asking[label], before[label], label)
            self.assertEqual(_counts()[label], before[label], label)


class StaffScreenTests(QrSetup):
    def post_act(self, request, action, user=None, entity=None, **extra):
        self.staff(user, entity)
        return self.client.post(reverse("restaurant:request_act", args=[request.pk]), {"lang": "ar", "action": action, **extra})

    def test_the_board_shows_a_badge_per_table_and_the_waiting_requests(self):
        self.request({f"q-{self.tea.pk}": 1})
        self.request({f"q-{self.koshary.pk}": 1})
        self.guest.post(self.url(), {"action": "call"})
        self.staff()
        board = self.client.get(reverse("restaurant:board"))
        self.assertContains(board, 'data-table-requests="3"', count=1)
        self.assertContains(board, "data-table-requests-panel")
        self.assertContains(board, "data-request-accept", count=2)
        self.assertContains(board, "data-request-done", count=1)
        self.assertContains(board, 'data-requests-total>3<')
        self.assertContains(board, "restaurant_qr.css")

    def test_a_free_table_with_a_request_still_opens_and_a_busy_one_links_to_its_order(self):
        order = services.open_order(self.owner, table=self.a_t1)
        self.request({f"q-{self.tea.pk}": 1})
        self.staff()
        board = self.client.get(reverse("restaurant:board"))
        self.assertContains(board, f'href="{reverse("restaurant:order", args=[order.pk])}?lang=ar"')
        self.assertContains(board, 'data-table-requests="1"')

    def test_the_order_page_shows_its_tables_requests_and_accepting_lands_on_it(self):
        order = services.open_order(self.owner, table=self.a_t1)
        made = self.request(self.burger_lines())
        self.staff()
        page = self.client.get(reverse("restaurant:order", args=[order.pk]))
        self.assertContains(page, f'data-table-request="{made.pk}"')
        self.assertContains(page, "جبنة زيادة")
        done = self.post_act(made, "accept", order=order.pk)
        self.assertRedirects(done, f"{reverse('restaurant:order', args=[order.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertEqual(order.lines.filter(item=self.burger).get().quantity, D("2.000"))
        page = self.client.get(reverse("restaurant:order", args=[order.pk]))
        self.assertNotContains(page, "data-table-request=")

    def test_accept_and_decline_and_done_from_the_screen(self):
        one, two = self.request({f"q-{self.tea.pk}": 1}), self.request({f"q-{self.koshary.pk}": 1})
        self.guest.post(self.url(), {"action": "bill"})
        bill = TableRequest.objects.get(kind="bill")
        accepted = self.post_act(one, "accept")
        order = Order.objects.get()
        self.assertRedirects(accepted, f"{reverse('restaurant:order', args=[order.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertRedirects(self.post_act(two, "decline"), f"{reverse('restaurant:board')}?lang=ar", fetch_redirect_response=False)
        self.assertRedirects(self.post_act(bill, "done"), f"{reverse('restaurant:board')}?lang=ar", fetch_redirect_response=False)
        self.assertEqual(list(TableRequest.objects.order_by("pk").values_list("status", flat=True)), ["accepted", "declined", "accepted"])
        again = self.post_act(one, "accept")  # handled already
        self.assertRedirects(again, f"{reverse('restaurant:board')}?lang=ar", fetch_redirect_response=False)
        self.assertEqual(order.lines.count(), 1)
        self.assertEqual(self.post_act(bill, "frobnicate").status_code, 302)

    def test_a_refused_accept_says_why_on_the_screen_and_adds_nothing(self):
        made = self.request({f"q-{self.tea.pk}": 1})
        self.tea.active = False
        self.tea.save(update_fields=["active"])
        response = self.post_act(made, "accept")
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response["Location"])
        self.assertContains(page, "مش متاح دلوقتي")
        self.assertFalse(Order.objects.exists())

    def test_a_waiter_of_another_branch_cannot_see_or_accept_a_request(self):
        made = self.request({f"q-{self.tea.pk}": 1})  # at the main hall's T1
        self.work_in(self.branch, self.manager_b)
        board = self.client.get(reverse("restaurant:board"))
        self.assertNotContains(board, f'data-table-request="{made.pk}"')
        self.assertNotContains(board, "data-table-requests-panel")
        refused = self.client.post(reverse("restaurant:request_act", args=[made.pk]), {"lang": "ar", "action": "accept"})
        self.assertEqual(refused.status_code, 404)  # as one that does not exist
        made.refresh_from_db()
        self.assertEqual(made.status, RequestStatus.NEW)
        self.assertFalse(Order.objects.exists())
        mine = self.request({f"q-{self.tea.pk}": 1}, table=self.b_t1)
        self.assertEqual(mine.entity_id, self.branch.pk)
        self.assertContains(self.client.get(reverse("restaurant:board")), f'data-table-request="{mine.pk}"')
        self.assertEqual(self.post_act(mine, "accept", user=self.manager_b, entity=self.branch).status_code, 302)
        self.assertEqual(Order.objects.get().entity_id, self.branch.pk)

    def test_someone_who_cannot_take_orders_sees_but_cannot_act_and_a_guest_is_sent_to_login(self):
        made = self.request({f"q-{self.tea.pk}": 1})
        accountant = person(RoleCode.ACCOUNTANT, "qr_accountant")
        refused = self.post_act(made, "accept", user=accountant)
        self.assertEqual(refused.status_code, 403)
        self.staff(accountant)
        self.assertNotContains(self.client.get(reverse("restaurant:board")), "data-request-accept")
        made.refresh_from_db()
        self.assertEqual(made.status, RequestStatus.NEW)
        anonymous = Client().post(reverse("restaurant:request_act", args=[made.pk]), {"action": "accept"})
        self.assertEqual(anonymous.status_code, 302)
        self.assertIn("/login/", anonymous["Location"])

    def test_the_act_view_takes_post_only(self):
        made = self.request({f"q-{self.tea.pk}": 1})
        self.staff()
        self.assertEqual(self.client.get(reverse("restaurant:request_act", args=[made.pk])).status_code, 405)

    def test_english_staff_screen(self):
        self.request({f"q-{self.tea.pk}": 1})
        self.staff()
        board = self.client.get(reverse("restaurant:board") + "?lang=en")
        self.assertContains(board, "Requests from the tables")
        self.assertContains(board, "Accept")


@override_settings(PUBLIC_BASE_URL=BASE)
class QrCardTests(QrSetup):
    def cards(self, user=None, entity=None, lang="ar"):
        self.work_in(entity or self.main, user or self.owner)
        return self.client.get(reverse("restaurant:qr_cards") + f"?lang={lang}")

    def test_one_card_per_active_table_and_the_qr_carries_the_full_address(self):
        services.save_table({"name": "Off", "seats": 2, "active": ""}, self.owner, entity=self.main)
        page = self.cards()
        self.assertEqual(page.status_code, 200)
        tables = DiningTable.objects.filter(entity=self.main, active=True)
        self.assertContains(page, "data-qr-card=", count=tables.count())
        self.assertNotContains(page, ">طاولة Off<")
        for table in tables:
            self.assertContains(page, f'data-qr-text="{BASE}/t/{table.qr_key}/"', count=1)
        self.assertContains(page, "امسح المنيو واطلب من مكانك")
        self.assertContains(page, "data-rotate-form", count=tables.count())  # «غيّر الكود» per table, outside the printed sheet
        self.assertContains(page, "qrcode-1.4.4.js")
        self.assertNotContains(page, self.b_t1.qr_key)  # the branch's tables are the branch's cards

    def test_the_branch_prints_its_own_tables_and_names_itself(self):
        page = self.cards(self.manager_b, self.branch)
        self.assertContains(page, f'data-qr-text="{BASE}/t/{self.b_t1.qr_key}/"')
        self.assertNotContains(page, self.a_t1.qr_key)
        self.assertContains(page, "فرع المعادي")

    def test_with_no_site_address_no_qr_is_drawn_and_the_page_says_why(self):
        with override_settings(PUBLIC_BASE_URL=""):
            page = self.cards()
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-no-base")
        self.assertNotContains(page, 'data-qr-text="')

    def test_with_no_tables_the_page_still_opens(self):
        DiningTable.objects.all().update(active=False)
        page = self.cards()
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-no-cards")

    def test_only_who_manages_the_menu_prints_or_rotates(self):
        self.assertEqual(self.cards(self.waiter).status_code, 403)
        self.staff()
        self.assertEqual(self.client.post(reverse("restaurant:qr_rotate", args=[self.a_t1.pk])).status_code, 403)
        self.a_t1.refresh_from_db()
        self.assertEqual(self.client.get(reverse("restaurant:tables")).status_code, 403)  # the waiter has no tables page either
        self.assertEqual(Client().get(reverse("restaurant:qr_cards")).status_code, 302)  # signed out: login

    def test_the_tables_page_links_to_the_cards(self):
        self.work_in(None, self.owner)
        self.assertContains(self.client.get(reverse("restaurant:tables")), 'data-qr-cards-link')

    def test_rotating_a_key_kills_the_old_card_at_once_and_the_new_one_opens(self):
        old = self.a_t1.qr_key
        self.work_in(None, self.owner)
        response = self.client.post(reverse("restaurant:qr_rotate", args=[self.a_t1.pk]), {"lang": "ar"})
        self.assertRedirects(response, f"{reverse('restaurant:qr_cards')}?lang=ar", fetch_redirect_response=False)
        self.a_t1.refresh_from_db()
        self.assertNotEqual(self.a_t1.qr_key, old)
        self.assertEqual(self.guest.get(reverse("table_menu", args=[old])).status_code, 404)
        self.assertEqual(self.guest.get(self.url()).status_code, 200)
        self.assertEqual(self.client.get(reverse("restaurant:qr_cards")).status_code, 200)
        row = AuditLog.objects.get(action="rotate_table_qr")
        self.assertNotIn(old, str(row.after_data) + str(row.before_data))  # the keys themselves are never logged
        self.assertNotIn(self.a_t1.qr_key, str(row.after_data))

    def test_rotating_takes_post_and_another_branchs_table_is_a_404(self):
        self.work_in(None, self.owner)
        self.assertEqual(self.client.get(reverse("restaurant:qr_rotate", args=[self.a_t1.pk])).status_code, 405)
        self.work_in(self.main, self.owner)
        before = self.b_t1.qr_key
        self.assertEqual(self.client.post(reverse("restaurant:qr_rotate", args=[self.b_t1.pk])).status_code, 404)
        self.b_t1.refresh_from_db()
        self.assertEqual(self.b_t1.qr_key, before)

    def test_every_table_has_its_own_random_key(self):
        keys = list(DiningTable.objects.values_list("qr_key", flat=True))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(len(key) >= 20 for key in keys))
        self.assertNotEqual(self.a_t1.qr_key, self.b_t1.qr_key)


@skipUnless(connection.vendor == "postgresql", "row locks are PostgreSQL's: SQLite's writers already take turns")
class AcceptLockTests(TransactionTestCase):
    """Two waiters accepting the same request at once: one wins, the other is told, and the lines are added once."""

    serialized_rollback = True

    def setUp(self):
        from hesba_testing.factories import make_cashbox, make_location
        from reports.tests_dashboard import prepared_client

        cache.clear()
        prepared_client(activity="restaurants", sub_activity="cafe",
                        modules="items_services,sales_operations,cashboxes,reports,inventory,pdf_printing,tables_orders")
        self.owner = person(RoleCode.OWNER, "qr_lock_owner")
        self.waiter = person(RoleCode.WAITER, "qr_lock_waiter")
        make_location(location_code="HALL", is_default=True)
        make_cashbox(cashbox_code="TILL", is_default=True)
        self.tea = make_item(item_code="D-TEA", item_name="شاي", default_sale_price="15.00", is_stock_tracked=False)
        self.table = services.save_table({"name": "T1", "seats": 4}, self.owner)
        self.made = TableRequest.objects.create(table=self.table, kind=RequestKind.ORDER, items=[{"item": self.tea.pk, "quantity": 2, "options": [], "note": ""}])

    def test_the_second_accept_waits_for_the_first_and_is_then_refused(self):
        outcome = {}

        def second():
            try:
                qr.accept(TableRequest.objects.get(pk=self.made.pk), self.waiter)
                outcome["accepted"] = True
            except ValidationError as error:
                outcome["refused"] = error.messages
            except Exception as error:  # noqa: BLE001  (reported below, never swallowed)
                outcome["error"] = repr(error)
            finally:
                connection.close()

        with transaction.atomic():
            qr.accept(self.made, self.owner)  # holds the request's row; not committed yet
            thread = threading.Thread(target=second)
            thread.start()
            thread.join(timeout=3)
            self.assertTrue(thread.is_alive(), outcome)  # it waits for the first's transaction
        thread.join(timeout=30)
        self.assertFalse(thread.is_alive())
        self.assertEqual(outcome, {"refused": ["الطلب ده اتعامل معاه قبل كده."]})
        order = Order.objects.get()
        self.assertEqual(order.lines.get().quantity, D("2.000"))  # added once
        self.made.refresh_from_db()
        self.assertEqual((self.made.status, self.made.handled_by_id), (RequestStatus.ACCEPTED, self.owner.pk))


class MigrationTests(TransactionTestCase):
    """0009 on a database that already holds tables: every table gets its own key, and it can be migrated back and forward."""

    serialized_rollback = True
    before, after = ("restaurant", "0008_combos"), ("restaurant", "0009_table_qr")

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_tables_each_get_a_different_key(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([self.before])
        leaves = [node for node in executor.loader.graph.leaf_nodes() if node[0] != "restaurant"] + [self.before]
        old = executor.loader.project_state(leaves).apps
        Entity = old.get_model("entities", "Entity")
        main = Entity.objects.filter(is_main=True).first() or Entity.objects.create(code="MAIN", name_ar="الرئيسي", is_main=True)
        Table = old.get_model("restaurant", "DiningTable")
        made = [Table.objects.create(name=f"T{n}", entity=main).pk for n in range(1, 5)]
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([self.after])
        new = executor.loader.project_state([self.after]).apps
        keys = list(new.get_model("restaurant", "DiningTable").objects.filter(pk__in=made).values_list("qr_key", flat=True))
        self.assertEqual(len(keys), 4)
        self.assertEqual(len(set(keys)), 4)
        self.assertTrue(all(key and len(key) >= 20 for key in keys))
