"""FB-QUICKADD: a new item from inside a purchase or sales invoice, without leaving it."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from master_data.models import Item
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class QuickItemTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "qi_owner")
        self.client.force_login(self.owner)
        self.url = reverse("master_data:item_quick_create")

    def test_an_item_is_made_with_an_automatic_code_and_handed_back(self):
        answer = self.client.post(self.url, {"item_name": "شاحن سريع", "unit": "قطعة", "default_sale_price": "250",
                                             "default_purchase_price": "180", "is_stock_tracked": "on"}).json()
        self.assertTrue(answer["ok"])
        item = Item.objects.get(pk=answer["item"]["id"])
        self.assertTrue(item.item_code.startswith("IT-"))
        self.assertEqual((item.item_name, item.unit, item.default_sale_price, item.default_purchase_price, item.is_stock_tracked, item.active),
                         ("شاحن سريع", "قطعة", Decimal("250.00"), Decimal("180.00"), True, True))
        self.assertEqual(answer["item"]["label"], item.search_label)
        service = self.client.post(self.url, {"item_name": "تركيب", "default_sale_price": "50"}).json()
        self.assertFalse(Item.objects.get(pk=service["item"]["id"]).is_stock_tracked)  # unticked: a service

    def test_a_missing_name_says_which_field(self):
        answer = self.client.post(self.url, {"item_name": "", "default_sale_price": "10"})
        self.assertEqual(answer.status_code, 400)
        self.assertIn("اسم", " ".join(answer.json()["errors"]))
        self.assertFalse(Item.objects.filter(default_sale_price=Decimal("10")).exists())

    def test_only_who_may_add_items_sees_the_button_or_can_post(self):
        page = self.client.get(reverse("purchases:create"))
        self.assertContains(page, "data-quick-item-open")
        self.assertContains(page, 'form="qi-detached"')  # its fields are never sent with the invoice
        cashier = person(RoleCode.CASHIER, "qi_cashier")
        self.client.force_login(cashier)
        self.assertNotContains(self.client.get(reverse("sales:create")), "data-quick-item-open")
        refused = self.client.post(self.url, {"item_name": "ممنوع"})
        self.assertEqual(refused.status_code, 403)
        self.assertFalse(Item.objects.filter(item_name="ممنوع").exists())

    def test_cost_stays_hidden_from_who_may_not_see_it(self):
        from permissions.services import user_has_permission

        keeper = person(RoleCode.STOCK_KEEPER, "qi_keeper")
        self.assertFalse(user_has_permission(keeper, "inventory.view_cost"))
        self.client.force_login(keeper)
        self.assertNotContains(self.client.get(reverse("purchases:create")), 'id="qi-buy"')
        answer = self.client.post(self.url, {"item_name": "صنف المخزن", "default_purchase_price": "99"}).json()
        self.assertEqual(answer["item"]["purchase_price"], "")
        self.assertEqual(Item.objects.get(pk=answer["item"]["id"]).default_purchase_price, Decimal("0.00"))
