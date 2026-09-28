"""MFG-001: recipes and production runs, moved entirely through the inventory engine."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from hesba_testing.factories import make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.models import StockMovement
from inventory.services import get_item_authoritative_average_cost, get_item_location_stock_quantity
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import services
from .models import ProductionRun, RunStatus

TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class MfgSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="manufacturing", sub_activity="food",
                                       modules="items_services,sales_operations,purchases,inventory,cashboxes,reports,manufacturing")
        self.owner = person(RoleCode.OWNER, "mfg_owner")
        self.plant = make_location(location_code="PLANT", is_default=True)
        self.flour = make_item(item_code="RM-FLOUR", item_name="دقيق (كيلو)", is_stock_tracked=True)
        self.sugar = make_item(item_code="RM-SUGAR", item_name="سكر (كيلو)", is_stock_tracked=True)
        self.box = make_item(item_code="PK-BOX", item_name="علبة", is_stock_tracked=True)
        self.cake = make_item(item_code="FG-CAKE", item_name="كيكة", is_stock_tracked=True, default_sale_price="120")
        stock_in(self.flour, self.plant, 50, "20.00", movement_date=TODAY)
        stock_in(self.sugar, self.plant, 20, "30.00", movement_date=TODAY)
        stock_in(self.box, self.plant, 100, "2.50", movement_date=TODAY)
        # one batch: 2 kg flour + 1 kg sugar + 10 boxes -> 10 cakes; cost 40 + 30 + 25 = 95
        self.recipe = services.save_recipe({"product": self.cake, "output_quantity": "10"},
                                           [(self.flour, "2"), (self.sugar, "1"), (self.box, "10")], self.owner)


class RecipeTests(MfgSetup):
    def test_recipe_rules(self):
        self.assertEqual((self.recipe.code, self.recipe.lines.count()), ("BOM-0001", 3))
        service = make_item(item_code="SRV", item_name="خدمة", is_stock_tracked=False)
        for data, lines, text in (
            ({"product": None}, [(self.flour, "1")], "اختار المنتج"),
            ({"product": service}, [(self.flour, "1")], "متتبع في المخزون"),
            ({"product": self.cake}, [], "مكوّن واحد"),
            ({"product": self.cake}, [(self.cake, "1")], "في نفسه"),
            ({"product": self.cake}, [(service, "1")], "«خدمة» لازم يكون"),
            ({"product": self.cake}, [(self.flour, "1"), (self.flour, "2")], "متكرر"),
            ({"product": self.cake}, [(self.flour, "0")], "لازم أكبر من صفر"),
            ({"product": self.cake, "output_quantity": "0"}, [(self.flour, "1")], "إنتاج لكل دفعة"),
        ):
            with self.subTest(text=text), self.assertRaisesMessage(ValidationError, text):
                services.save_recipe(data, lines, self.owner)

    def test_plan_shows_needs_cost_and_the_most_batches_stock_allows(self):
        plan = services.plan(self.recipe, D("3"), self.plant)
        self.assertEqual((plan["output"], plan["cost"], plan["unit_cost"], plan["possible_batches"]), (D("30.000"), D("285.00"), D("9.5000"), D("10.000")))
        self.assertFalse(any(row["short"] for row in plan["rows"]))
        self.assertTrue(services.plan(self.recipe, D("11"), self.plant)["rows"][2]["short"])  # 110 boxes, 100 in stock


class RunTests(MfgSetup):
    def test_a_run_moves_components_out_and_the_product_in_at_their_cost(self):
        run = services.produce(self.recipe, self.owner, batches="3", location=self.plant)
        self.assertEqual((run.output_quantity, run.total_cost, run.status), (D("30.000"), D("285.00"), RunStatus.POSTED))
        self.assertEqual(get_item_location_stock_quantity(self.flour, self.plant), D("44.000"))
        self.assertEqual(get_item_location_stock_quantity(self.sugar, self.plant), D("17.000"))
        self.assertEqual(get_item_location_stock_quantity(self.box, self.plant), D("70.000"))
        self.assertEqual(get_item_location_stock_quantity(self.cake, self.plant), D("30.000"))
        self.assertEqual(get_item_authoritative_average_cost(self.cake), D("9.5000"))
        self.assertEqual(StockMovement.objects.filter(stock_operation__reference_number__startswith=run.number).count(), 4)
        self.assertEqual(run.consumptions.count(), 3)

    def test_a_short_component_leaves_nothing_behind(self):
        with self.assertRaisesMessage(ValidationError, "«علبة» مش كفاية: محتاج 110 والموجود 100"):
            services.produce(self.recipe, self.owner, batches="11", location=self.plant)
        self.assertFalse(ProductionRun.objects.exists())
        self.assertEqual(get_item_location_stock_quantity(self.flour, self.plant), D("50.000"))
        self.assertEqual(get_item_location_stock_quantity(self.cake, self.plant), D("0"))

    def test_cancelling_returns_everything_unless_the_product_was_sold(self):
        run = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        services.cancel_run(run, self.owner, reason="خبزة باظت")
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.CANCELLED)
        self.assertEqual(get_item_location_stock_quantity(self.flour, self.plant), D("50.000"))
        self.assertEqual(get_item_location_stock_quantity(self.cake, self.plant), D("0.000"))
        with self.assertRaisesMessage(ValidationError, "اتلغت بالفعل"):
            services.cancel_run(run, self.owner, reason="تاني")
        sold = services.produce(self.recipe, self.owner, batches="1", location=self.plant)
        from sales.pos import checkout, walk_in_customer
        from hesba_testing.factories import make_cashbox

        checkout(lines=[{"item": self.cake, "quantity": D("5"), "unit_sale_price": D("120"), "line_discount_amount": D("0"), "description": ""}],
                 customer=walk_in_customer(), location=self.plant, cashbox=make_cashbox(), discount=D("0"), tendered=D("600"), user=self.owner)
        with self.assertRaises(ValidationError):  # only 5 of the 10 cakes are left
            services.cancel_run(sold, self.owner, reason="غلط")
        sold.refresh_from_db()
        self.assertEqual(sold.status, RunStatus.POSTED)

    def test_an_inactive_recipe_cannot_run(self):
        services.save_recipe({"product": self.cake, "output_quantity": "10", "active": False}, [(self.flour, "2")], self.owner, self.recipe)
        with self.assertRaisesMessage(ValidationError, "متوقفة"):
            services.produce(self.recipe, self.owner, batches="1", location=self.plant)


class ScreenTests(MfgSetup):
    def test_write_a_recipe_plan_and_run_from_the_screens(self):
        self.client.force_login(self.owner)
        juice = make_item(item_code="FG-JUICE", item_name="عصير", is_stock_tracked=True)
        made = self.client.post(reverse("manufacturing:home"), {"product": str(juice.pk), "output_quantity": "4", "component_0": str(self.sugar.pk), "qty_0": "1", "active": "on"})
        recipe = juice.recipes.get()
        url = reverse("manufacturing:recipe", args=[recipe.pk])
        self.assertRedirects(made, f"{url}?lang=ar", fetch_redirect_response=False)
        page = self.client.get(url, {"batches": "2"})
        self.assertContains(page, 'data-plan-row="RM-SUGAR"')
        self.assertEqual(page.context["plan"]["output"], D("8.000"))
        ran = self.client.post(url, {"action": "produce", "batches": "2", "location": str(self.plant.pk)})
        run = ProductionRun.objects.get()
        self.assertRedirects(ran, f"{reverse('manufacturing:run', args=[run.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("manufacturing:run", args=[run.pk])), 'data-consumed="RM-SUGAR"')
        self.client.post(reverse("manufacturing:run", args=[run.pk]), {"reason": "تجربة"})
        run.refresh_from_db()
        self.assertEqual(run.status, RunStatus.CANCELLED)

    def test_permissions(self):
        keeper = person(RoleCode.STOCK_KEEPER, "mfg_keeper")  # can adjust stock but not see costs
        self.client.force_login(keeper)
        url = reverse("manufacturing:recipe", args=[self.recipe.pk])
        page = self.client.get(url)
        self.assertNotContains(page, "data-plan-cost")
        self.assertNotContains(page, "data-produce-form")
        self.client.post(url, {"action": "produce", "batches": "1", "location": str(self.plant.pk)})
        self.assertFalse(ProductionRun.objects.exists())
        cashier = person(RoleCode.CASHIER, "mfg_cashier")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(reverse("manufacturing:home")).status_code, 403)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard_snapshot")), reverse("manufacturing:home"))
        from settings_core.setup_services import ModuleChangeRefused, set_module_enabled

        with self.assertRaises(ModuleChangeRefused):
            set_module_enabled(self.profile, "manufacturing", False, self.owner)
