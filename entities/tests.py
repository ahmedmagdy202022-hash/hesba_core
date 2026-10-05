"""ENT-001 (HG-031): entities, binding, and the group-wide stock view."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_cashbox, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from permissions.models import Permission, RoleCode, RolePermission
from reports.tests_dashboard import prepared_client

from . import services
from .models import Entity


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class MainEntityTests(TestCase):
    def test_every_store_and_cashbox_belongs_to_the_main_entity_by_default(self):
        location, cashbox = make_location(location_code="L1"), make_cashbox(cashbox_code="C1")
        main = Entity.objects.get(is_main=True)
        self.assertEqual((location.entity, cashbox.entity), (main, main))
        self.assertFalse(services.is_multi_entity())

    def test_only_one_main_entity(self):
        services.main_entity()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Entity.objects.create(code="X", name_ar="x", is_main=True)

    def test_the_group_stock_permission_follows_view_stock(self):
        permission = Permission.objects.get(code="inventory.view_group_stock")
        holders = set(RolePermission.objects.filter(permission=permission, allow=True).values_list("role__code", flat=True))
        stock_viewers = set(RolePermission.objects.filter(permission__code="inventory.view_stock", allow=True).values_list("role__code", flat=True))
        self.assertEqual(holders, stock_viewers | {RoleCode.CASHIER})


class EntityScreenTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "ent_owner")
        self.client.force_login(self.owner)

    def test_owner_adds_a_factory_with_its_own_activity(self):
        response = self.client.post(reverse("entities:new"), {"code": "fac", "name_ar": "المصنع", "kind": "branch", "activity": "manufacturing:food", "active": "on"})
        self.assertRedirects(response, "/entities/?lang=ar", fetch_redirect_response=False)
        factory = Entity.objects.get(code="FAC")
        self.assertEqual((factory.activity_slug, factory.sub_activity_slug), ("manufacturing", "food"))
        self.assertEqual(services.activity_of(factory), ("manufacturing", "food"))
        self.assertEqual(services.activity_of(services.main_entity()), ("commercial", "retail"))
        self.assertTrue(AuditLog.objects.filter(action="create_entity").exists())
        self.assertTrue(services.is_multi_entity())
        self.assertContains(self.client.get(reverse("entities:list")), 'data-entity="FAC"')
        # Now a new store can be put in the factory.
        page = self.client.get(reverse("master_data:create", kwargs={"entity": "locations"}))
        self.assertContains(page, "المصنع")

    def test_rules(self):
        services.save_entity({"code": "A", "name_ar": "أ"}, self.owner)
        with self.assertRaises(ValidationError):
            services.save_entity({"code": "a", "name_ar": "تاني"}, self.owner)
        main = services.main_entity()
        with self.assertRaises(ValidationError):
            services.save_entity({"code": main.code, "name_ar": main.name_ar, "active": False}, self.owner, main)
        shop = services.save_entity({"code": "SHOP", "name_ar": "محل"}, self.owner)
        make_location(location_code="SHOP-1", entity=shop)
        with self.assertRaises(ValidationError):
            services.save_entity({"code": "SHOP", "name_ar": "محل", "active": False}, self.owner, shop)

    def test_only_settings_managers_change_entities(self):
        self.client.force_login(person(RoleCode.CASHIER, "ent_cashier"))
        self.assertEqual(self.client.post(reverse("entities:new"), {"code": "Z", "name_ar": "ز"}).status_code, 403)
        self.assertFalse(Entity.objects.filter(code="Z").exists())


class GroupStockTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "gs_owner")
        self.factory = services.save_entity({"code": "FAC", "name_ar": "المصنع"}, self.owner)
        self.shop_store = make_location(location_code="SHOP", name_ar="المحل")
        self.factory_store = make_location(location_code="FAC-FG", name_ar="مخزن المنتج التام", entity=self.factory)
        self.item = make_item(item_code="CH-1", item_name="كرسي")
        stock_in(self.item, self.factory_store, 40, "100.00")
        stock_in(self.item, self.shop_store, 3, "100.00")
        recalculate_item_average_cost(self.item)

    def test_where_is_it_across_entities(self):
        places = services.group_stock([self.item])[self.item.pk]
        self.assertEqual({(p["entity"].code, p["location"].location_code, p["quantity"]) for p in places},
                         {("MAIN", "SHOP", D("3")), ("FAC", "FAC-FG", D("40"))})
        elsewhere = services.stock_elsewhere(self.item, self.shop_store)
        self.assertEqual([(p["location"].location_code, p["quantity"]) for p in elsewhere], [("FAC-FG", D("40"))])

    def test_the_screen_shows_quantities_and_cost_only_with_permission(self):
        self.client.force_login(person(RoleCode.CASHIER, "gs_cashier"))
        page = self.client.get(reverse("entities:where"), {"q": "كرسي"})
        self.assertContains(page, 'data-where-place="FAC-FG"')
        self.assertContains(page, "المصنع")
        self.assertNotContains(page, "4,300.00")  # 43 x 100: cost is the owner's
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("entities:where"), {"q": "CH-1"}), "4,300.00")
        self.assertContains(self.client.get(reverse("inventory:stock")), "data-where-link")
