"""AN7 (G07, Ahmed approved): the shop picks the till's tap-to-add buttons; what has no barcode comes first."""

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_cashbox, make_item, make_location, make_seeded_role, make_user, make_user_profile, open_test_shift, stock_in
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.models import SystemSetting

from .pos_buttons import MAX_PINNED, SETTING_KEY, pinned_ids, save_pinned
from .tests_pos import PosTests

BUTTONS = reverse("sales:pos_buttons")


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class TillButtonsTests(TestCase):
    def setUp(self):
        prepared_client(sub_activity="grocery")
        self.owner = person(RoleCode.OWNER, "pb_owner")
        self.client.force_login(self.owner)
        self.location = make_location(location_code="SHOP", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        open_test_shift(self.owner, self.cashbox)
        self.rice = make_item(item_code="RICE", item_name="أرز", barcode="6221234567890", default_sale_price="38.00")
        self.tomato = make_item(item_code="TOM", item_name="طماطم", unit="كيلو", default_sale_price="20.00")
        self.bread = make_item(item_code="BRD", item_name="عيش بلدي", default_sale_price="10.00")
        for item in (self.rice, self.tomato, self.bread):
            stock_in(item, self.location, 50, "5.00", movement_date=timezone.localdate())

    def sell(self, item, qty):
        return PosTests.sell(self, [(item, qty, str(item.default_sale_price))], "1000")

    def picks(self):
        return [pick["id"] for pick in self.client.get(reverse("sales:pos"), {"lang": "ar"}).context["quick_picks"]]

    def test_what_sells_without_a_barcode_comes_before_what_scans(self):
        self.sell(self.rice, "9")
        self.sell(self.tomato, "2")
        self.assertEqual(self.picks()[:2], [self.tomato.pk, self.rice.pk])  # rice sells more, but the scanner finds it

    def test_the_shop_picks_its_buttons_and_their_order(self):
        self.client.post(BUTTONS, {"action": "add", "item": self.rice.pk, "lang": "ar"})
        self.client.post(BUTTONS, {"action": "add", "item": self.bread.pk, "lang": "ar"})
        self.assertEqual(pinned_ids(), [self.rice.pk, self.bread.pk])
        self.assertEqual(self.picks()[:2], [self.rice.pk, self.bread.pk])
        self.client.post(BUTTONS, {"action": "up", "item": self.bread.pk, "lang": "ar"})
        self.assertEqual(pinned_ids(), [self.bread.pk, self.rice.pk])
        response = self.client.post(BUTTONS, {"action": "remove", "item": self.rice.pk, "lang": "ar", "q": "طم"})
        self.assertEqual(response["Location"], "/sales/pos/buttons/?lang=ar&q=%D8%B7%D9%85")
        self.assertEqual(pinned_ids(), [self.bread.pk])
        page = self.client.get(BUTTONS, {"lang": "ar"}).content.decode()
        self.assertIn(f'data-pos-button-remove="{self.bread.pk}"', page)
        self.assertIn(f'data-pos-button-add="{self.tomato.pk}"', page)  # suggested: no barcode
        self.assertNotIn(f'data-pos-button-add="{self.rice.pk}"', page)  # it scans; found by searching
        self.assertIn(f'data-pos-button-add="{self.rice.pk}"', self.client.get(BUTTONS, {"lang": "ar", "q": "أرز"}).content.decode())
        self.assertIn("data-pos-pick-buttons", self.client.get(reverse("sales:pos"), {"lang": "ar"}).content.decode())

    def test_a_stopped_item_or_a_bad_setting_never_breaks_the_till(self):
        save_pinned([self.bread.pk, self.rice.pk])
        type(self.bread).objects.filter(pk=self.bread.pk).update(active=False)
        self.assertNotIn(self.bread.pk, self.picks())
        SystemSetting.objects.filter(key=SETTING_KEY).update(value="not json")
        self.assertEqual(pinned_ids(), [])
        self.assertEqual(self.client.get(reverse("sales:pos"), {"lang": "ar"}).status_code, 200)

    def test_there_is_a_limit(self):
        # Far above any real id: on PostgreSQL the ids keep counting across tests, so 1000+ could be rice itself.
        save_pinned(list(range(10**9, 10**9 + MAX_PINNED)))
        self.client.post(BUTTONS, {"action": "add", "item": self.rice.pk, "lang": "ar"})
        self.assertNotIn(self.rice.pk, pinned_ids())

    def test_a_cashier_sells_with_the_buttons_but_does_not_choose_them(self):
        cashier = person(RoleCode.CASHIER, "pb_cashier")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(BUTTONS).status_code, 403)
        self.assertEqual(self.client.post(BUTTONS, {"action": "add", "item": self.rice.pk}).status_code, 403)
        self.assertEqual(pinned_ids(), [])
        open_test_shift(cashier, self.cashbox)
        self.assertNotIn("data-pos-pick-buttons", self.client.get(reverse("sales:pos"), {"lang": "ar"}).content.decode())
