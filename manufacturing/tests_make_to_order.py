"""F1 (HG-098): a confirmed sales order line makes a production order (make to order).

Bakery from ``MfgSetup``: one batch = 2 kg flour (20) + 1 kg sugar (30) + 10 boxes (2.50) = 95 for 10 cakes. SO for 30 cakes at
25.00 (VAT off) is 3 batches = 285.00 of materials; with 90.00 labour and 60.00 overhead the 30 cakes cost 435.00, 14.5000 each.
"""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import CashboxMovement, CashboxOperation
from hesba_testing.factories import make_customer, make_item, make_location, stock_in
from inventory.models import StockMovement, StockOperation
from inventory.services import get_item_location_stock_quantity
from ledger import reports as gl
from ledger.models import JournalEntry, JournalLine
from ledger.projector import Projector
from orders import services as sales
from orders.models import SalesOrder, Status
from permissions.models import RoleCode
from permissions.services import user_has_permission
from sales.models import SalesInvoice
from sales.services import post_sales_invoice
from settings_core import capabilities as caps
from taxes.services import create_sales_draft_with_tax, vat_enabled

from . import make_to_order, orders
from .models import OrderMaterialIssue, OrderStatus, ProductionOrder, ProductionRun
from .tests import MfgSetup, person

TODAY = timezone.localdate()
DELIVERY = TODAY + timedelta(days=9)


class MakeToOrderSetup(MfgSetup):
    def setUp(self):
        super().setUp()
        caps.set_capability_enabled(self.profile, "quotations", True)
        self.assertFalse(vat_enabled())
        self.customer = make_customer(customer_code="CUS-MTO", name="مخبز الأمل", phone="01001234567")
        self.order = self.sale("30")

    def sale(self, quantity, item=None, confirm=True, customer=None, deliver_by=DELIVERY):
        quote = sales.create_quote({"customer": customer or self.customer, "deliver_by": deliver_by}, self.owner)
        sales.add_line(quote, item or self.cake, self.owner, quantity=quantity, unit_price="25")
        return sales.move(quote, Status.CONFIRMED, self.owner) if confirm else quote

    def line(self, order=None):
        return (order or self.order).lines.get()

    def make(self, order=None, **extra):
        return make_to_order.make(self.line(order), self.owner, extra)

    def walk(self, order):
        for _ in order.stages:
            orders.advance(order, self.owner)

    def invoice(self, order, quantity):
        line = self.line(order)
        invoice = create_sales_draft_with_tax(
            {"invoice_number": f"SI-MTO-{SalesInvoice.objects.count() + 1}", "invoice_date": TODAY, "customer": order.customer,
             "selling_location": self.plant, "paid_now": D("0")},
            [{"item": line.item, "quantity": D(quantity), "unit_sale_price": line.unit_price, "line_discount_amount": line.discount_for(D(quantity))}],
            self.owner)
        sales.link_invoice(order, invoice, self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        return invoice

    def counts(self):
        return {model.__name__: model.objects.count() for model in (
            StockOperation, StockMovement, JournalEntry, JournalLine, CashboxMovement, CashboxOperation, SalesInvoice, ProductionRun, OrderMaterialIssue)}


class MakeTests(MakeToOrderSetup):
    def test_a_confirmed_line_makes_one_production_order_with_the_customer_and_the_date(self):
        order = self.make()
        self.assertEqual((order.quantity, order.status, order.recipe, order.location), (D("30.000"), OrderStatus.PLANNED, self.recipe, self.plant))
        self.assertEqual(orders._batches(order), D("3.000"))
        self.assertEqual((order.customer, order.due_date, order.sales_order_line), (self.customer, DELIVERY, self.line()))
        self.assertEqual((order.labor_cost, order.overhead_cost), (D("0.00"), D("0.00")))
        self.assertEqual(make_to_order.uncovered(self.line()), D("0"))
        self.assertEqual([o.pk for o in make_to_order.open_for(self.order)], [order.pk])

    def test_a_second_tap_is_refused_and_cancelling_frees_the_quantity(self):
        order = self.make()
        with self.assertRaisesMessage(ValidationError, "بتتصنع بالفعل"):
            self.make()
        self.assertEqual(ProductionOrder.objects.count(), 1)
        orders.cancel(order, self.owner, "العميل غيّر رأيه")
        self.assertEqual(make_to_order.uncovered(self.line()), D("30"))
        again = self.make()
        self.assertEqual((again.quantity, again.sales_order_line), (D("30.000"), self.line()))

    def test_the_quantity_can_go_down_never_up_and_the_rest_stays_uncovered(self):
        for raw, text in (("0", "أكبر من صفر"), ("-3", "أكبر من صفر"), ("abc", "أكبر من صفر"), ("31", "أكتر من اللي لسه ما اتغطاش")):
            with self.subTest(raw=raw), self.assertRaisesMessage(ValidationError, text):
                self.make(quantity=raw)
        self.assertFalse(ProductionOrder.objects.exists())
        first = self.make(quantity="10")
        self.assertEqual((first.quantity, make_to_order.uncovered(self.line())), (D("10.000"), D("20")))
        with self.assertRaisesMessage(ValidationError, "أكتر من اللي لسه ما اتغطاش"):
            self.make(quantity="21")
        self.make(quantity="20")
        self.assertEqual(make_to_order.uncovered(self.line()), D("0"))
        self.assertEqual(sum(o.quantity for o in ProductionOrder.objects.all()), D("30.000"))

    def test_an_order_that_is_not_confirmed_or_an_item_without_a_recipe_is_refused(self):
        quote = self.sale("5", confirm=False)
        with self.assertRaisesMessage(ValidationError, "لسه مش مؤكد"):
            make_to_order.make(self.line(quote), self.owner, {})
        loaf = make_item(item_code="FG-LOAF", item_name="رغيف", is_stock_tracked=True, default_sale_price="3")
        order = self.sale("5", item=loaf)
        with self.assertRaisesMessage(ValidationError, "مفيش وصفة شغالة"):
            make_to_order.make(self.line(order), self.owner, {})
        self.assertFalse(ProductionOrder.objects.exists())

    def test_the_first_active_recipe_by_code_is_used(self):
        from . import services

        second = services.save_recipe({"product": self.cake, "output_quantity": "5"}, [(self.flour, "1")], self.owner)
        self.assertEqual(self.make().recipe, self.recipe)  # BOM-0001, not BOM-0002
        type(self.recipe).objects.filter(pk=self.recipe.pk).update(active=False)
        order = self.sale("5")
        self.assertEqual(make_to_order.make(self.line(order), self.owner, {}).recipe, second)

    def test_planned_labour_and_overhead_are_typed_as_for_any_order(self):
        order = self.make(labor_cost="90", overhead_cost="60")
        self.assertEqual((order.labor_cost, order.overhead_cost), (D("90.00"), D("60.00")))
        with self.assertRaisesMessage(ValidationError, "العمالة"):
            self.make(order=self.sale("5"), labor_cost="-1")

    def test_a_place_outside_the_orders_entity_is_refused(self):
        from entities.models import Entity

        branch = Entity.objects.create(code="MFG-BR", name_ar="فرع", active=True)
        other = make_location(location_code="BRANCH-STORE", entity=branch, is_default=True)
        with self.assertRaisesMessage(ValidationError, "من مخازن جهة الأمر"):
            self.make(location=other)


class FinishAndSellTests(MakeToOrderSetup):
    def test_the_finished_order_costs_435_and_the_invoice_books_750_less_435(self):
        order = self.make(labor_cost="90", overhead_cost="60")
        self.walk(order)
        orders.finish(order, self.owner)
        order.refresh_from_db()
        card = orders.cost_card(order)
        self.assertEqual((card["material"], card["total"], card["per_unit"]), (D("285.00"), D("435.00"), D("14.5000")))
        self.assertEqual(get_item_location_stock_quantity(self.cake, self.plant), D("30"))
        invoice = self.invoice(self.order, "30")
        lines = list(invoice.lines.all())
        self.assertEqual((invoice.total_amount, sum(l.line_cost_amount for l in lines), sum(l.line_profit_amount for l in lines)),
                         (D("750.00"), D("435.00"), D("315.00")))
        Projector().rebuild()
        self.assertEqual(gl.control_balance("cogs"), D("435.00"))
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertTrue(gl.reconciliation()["ok"], gl.reconciliation()["rows"])
        self.assertEqual(sales.state(self.order), "done")
        self.assertEqual(make_to_order.uncovered(self.line()), D("0"))

    def test_units_already_invoiced_are_not_counted_twice_against_what_is_still_to_make(self):
        # 20 of the 30 are made, finished and invoiced: 10 are still owed (a formula that subtracts both "invoiced" and "made" says 0).
        order = self.make(quantity="20")
        self.walk(order)
        orders.finish(order, self.owner)
        self.invoice(self.order, "20")
        self.assertEqual(make_to_order.uncovered(self.line()), D("10"))
        self.make(quantity="10")
        self.assertEqual(make_to_order.uncovered(self.line()), D("0"))

    def test_units_lost_to_defects_are_still_owed_and_can_be_made_again(self):
        # 30 planned, 5 fail at the first stage: the run puts 25 in stock, so 5 are still to make.
        order = self.make()
        first, *rest = order.stages
        orders.advance(order, self.owner, good="25", defect="5")
        for _ in rest:
            orders.advance(order, self.owner)
        orders.finish(order, self.owner)
        order.refresh_from_db()
        self.assertEqual(order.run.output_quantity, D("25.000"))
        self.assertEqual(make_to_order.uncovered(self.line()), D("5"))
        state = make_to_order.line_states(self.order, sales.quantities(self.order), "ar", can_make=True)[self.line().pk]
        self.assertEqual((state["free"], state["can_make"], state["made"]), (D("5.000"), True, D("25.000")))
        self.assertEqual(self.make().quantity, D("5.000"))  # the replacement order, for exactly what was lost

    def test_a_finished_order_whose_run_was_reversed_no_longer_covers_the_line(self):
        from manufacturing.services import cancel_run

        order = self.make()
        self.walk(order)
        orders.finish(order, self.owner)
        self.assertEqual(make_to_order.uncovered(self.line()), D("0"))
        order.refresh_from_db()
        cancel_run(order.run, self.owner, reason="تشغيلة اتسجلت بالخطأ")
        order.refresh_from_db()
        self.assertEqual(order.status, "done")  # cancel_run leaves the order as it was; only its run is reversed
        self.assertEqual(make_to_order.uncovered(self.line()), D("30"))
        state = make_to_order.line_states(self.order, sales.quantities(self.order), "ar", can_make=True)[self.line().pk]
        self.assertEqual((state["orders"], state["has_made"], state["can_make"]), ([], False, True))
        self.assertEqual(self.make().quantity, D("30.000"))

    def test_the_line_says_what_was_made_and_warns_when_the_till_sold_it_first(self):
        order = self.make()
        self.walk(order)
        orders.finish(order, self.owner)
        row = sales.quantities(self.order)[0]
        state = make_to_order.line_states(self.order, [row], "ar", can_make=True)[self.line().pk]
        self.assertEqual((state["made"], state["stock"], state["waiting"], state["short"]), (D("30.000"), D("30.000"), D("30"), False))
        self.assertEqual(state["made_text"], "اتصنع 30 · في المخزن 30")
        # the till sells 20 to someone else: nothing is reserved for the customer
        invoice = create_sales_draft_with_tax(
            {"invoice_number": "SI-WALK-1", "invoice_date": TODAY, "customer": make_customer(customer_code="CUS-OTHER", name="عميل تاني"),
             "selling_location": self.plant, "paid_now": D("0")},
            [{"item": self.cake, "quantity": D("20"), "unit_sale_price": D("25"), "line_discount_amount": D("0")}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        state = make_to_order.line_states(self.order, sales.quantities(self.order), "ar", can_make=True)[self.line().pk]
        self.assertEqual((state["stock"], state["waiting"], state["short"]), (D("10.000"), D("30"), True))
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("orders:detail", args=[self.order.pk]) + "?lang=ar"), "data-stock-short")


class NothingPostedTests(MakeToOrderSetup):
    def test_making_the_order_moves_no_stock_cash_ledger_or_invoice_row(self):
        Projector().rebuild()
        before = self.counts()
        stock = tuple(get_item_location_stock_quantity(item, self.plant) for item in (self.flour, self.sugar, self.box, self.cake))
        self.make(labor_cost="90", overhead_cost="60")
        Projector().rebuild()
        self.assertEqual(self.counts(), before)
        self.assertEqual(tuple(get_item_location_stock_quantity(item, self.plant) for item in (self.flour, self.sugar, self.box, self.cake)), stock)
        self.assertEqual(ProductionOrder.objects.count(), 1)
        self.assertTrue(gl.trial_balance()["balanced"])

    def test_the_screen_action_posts_nothing_either(self):
        Projector().rebuild()
        before = self.counts()
        self.client.force_login(self.owner)
        response = self.client.post(reverse("manufacturing:make_for_sale", args=[self.line().pk]), {"lang": "ar", "quantity": "30", "labor_cost": "90", "overhead_cost": "60"})
        self.assertRedirects(response, f"{reverse('orders:detail', args=[self.order.pk])}?lang=ar", fetch_redirect_response=False)
        Projector().rebuild()
        self.assertEqual(self.counts(), before)
        self.assertEqual(ProductionOrder.objects.get().sales_order_line, self.line())


class DeliveryDateTests(MakeToOrderSetup):
    def shown(self, name, *args, lang="ar"):
        self.client.force_login(self.owner)
        return self.client.get(reverse(name, args=args) + f"?lang={lang}")

    def test_the_date_is_on_the_sales_order_the_production_order_and_the_board(self):
        order = self.make()
        sale_page = self.shown("orders:detail", self.order.pk)
        self.assertContains(sale_page, f'data-deliver-by>{DELIVERY:%Y-%m-%d}<')
        order_page = self.shown("manufacturing:order", order.pk)
        self.assertContains(order_page, "data-for-sale")
        self.assertContains(order_page, f'data-deliver-by>{DELIVERY:%Y-%m-%d}<')
        self.assertContains(order_page, self.order.order_number)
        self.assertContains(order_page, reverse("orders:detail", args=[self.order.pk]))
        board = self.shown("manufacturing:orders")
        self.assertContains(board, f'data-for-sale>لطلب <bdi>{self.order.order_number}</bdi> · مخبز الأمل')
        self.assertContains(board, f"{DELIVERY:%Y-%m-%d}")
        self.assertContains(self.shown("manufacturing:order", order.pk, lang="en"), "Delivery to the customer")

    def test_a_late_delivery_date_flags_the_production_order(self):
        order = self.make()
        late = SalesOrder.objects.filter(pk=self.order.pk)
        late.update(deliver_by=TODAY - timedelta(days=2))
        make_to_order.sync_due(late.get())
        order.refresh_from_db()
        page = self.shown("manufacturing:orders")
        self.assertEqual(page.context["late_count"], 1)
        self.assertContains(self.shown("manufacturing:order", order.pk), "op-message-error")

    def test_a_new_date_follows_the_order_while_it_is_on_the_floor_and_not_after(self):
        floor, done = self.make(quantity="10"), self.make(quantity="10")
        self.walk(done)
        orders.finish(done, self.owner)
        later = TODAY + timedelta(days=20)
        sales.set_deliver_by(self.order, later.isoformat(), self.owner)
        self.order.refresh_from_db()
        floor.refresh_from_db()
        done.refresh_from_db()
        self.assertEqual((self.order.deliver_by, floor.due_date, done.due_date), (later, later, DELIVERY))
        sales.set_deliver_by(self.order, "", self.owner)
        floor.refresh_from_db()
        self.assertIsNone(floor.due_date)

    def test_the_date_rules(self):
        with self.assertRaisesMessage(ValidationError, "في يوم العرض أو بعده"):
            sales.set_deliver_by(self.order, (self.order.quote_date - timedelta(days=1)).isoformat(), self.owner)
        with self.assertRaisesMessage(ValidationError, "التاريخ مش مظبوط"):
            sales.set_deliver_by(self.order, "غلط", self.owner)
        sales.move(self.order, Status.CANCELLED, self.owner)
        with self.assertRaisesMessage(ValidationError, "المفتوح بس"):
            sales.set_deliver_by(self.order, DELIVERY.isoformat(), self.owner)

    def test_the_date_is_set_on_the_quotation_and_kept_by_a_header_save_that_does_not_send_it(self):
        quote = self.sale("5", confirm=False, deliver_by=None)
        self.assertIsNone(quote.deliver_by)
        sales.update_header(quote, {"deliver_by": DELIVERY.isoformat()}, self.owner)
        quote.refresh_from_db()
        self.assertEqual(quote.deliver_by, DELIVERY)
        sales.update_header(quote, {"notes": "بدون تاريخ"}, self.owner)
        quote.refresh_from_db()
        self.assertEqual(quote.deliver_by, DELIVERY)

    def test_the_sales_order_screen_saves_the_date(self):
        self.client.force_login(self.owner)
        url = reverse("orders:detail", args=[self.order.pk])
        page = self.client.get(url + "?lang=ar")
        self.assertContains(page, "data-deliver-form")
        later = TODAY + timedelta(days=30)
        self.client.post(url, {"lang": "ar", "action": "deliver_by", "deliver_by": later.isoformat()})
        self.order.refresh_from_db()
        self.assertEqual(self.order.deliver_by, later)


class ScreenTests(MakeToOrderSetup):
    def page(self, user=None, order=None, lang="ar"):
        self.client.force_login(user or self.owner)
        return self.client.get(reverse("orders:detail", args=[(order or self.order).pk]) + f"?lang={lang}")

    def test_the_column_and_the_button_for_the_owner(self):
        page = self.page()
        for marker in ("data-production-cell", "data-make-production", "data-make-form"):
            self.assertContains(page, marker)
        self.assertContains(page, f'action="{reverse("manufacturing:make_for_sale", args=[self.line().pk])}"')
        self.assertContains(page, "اعمل أمر إنتاج")
        self.assertContains(page, 'value="30"')                       # the quantity is prefilled with what is not covered
        self.assertContains(self.page(lang="en"), "Make a production order")
        order = self.make()
        page = self.page()
        self.assertNotContains(page, "data-make-form")                # nothing left to make
        self.assertContains(page, f'data-production-order="{order.number}"')
        self.assertContains(page, 'data-production-state="planned"')

    def test_the_column_follows_the_order_through_its_stages(self):
        order = self.make()
        orders.advance(order, self.owner)
        self.assertContains(self.page(), "في مرحلة طهي")
        for _ in range(3):
            orders.advance(order, self.owner)
        self.assertContains(self.page(), 'data-production-state="ready"')
        orders.finish(order, self.owner)
        self.assertContains(self.page(), 'data-production-state="done"')
        self.assertContains(self.page(), "data-made-stock")

    def test_a_user_with_sales_rights_but_no_adjust_stock_sees_the_column_but_not_the_button(self):
        order = self.make(quantity="10")
        seller = person(RoleCode.CASHIER, "mto_cashier")
        self.assertTrue(user_has_permission(seller, "sales.view_sales_invoices"))
        self.assertFalse(user_has_permission(seller, "inventory.adjust_stock"))
        page = self.page(seller)
        self.assertContains(page, "data-production-cell")
        self.assertContains(page, f'data-production-order="{order.number}"')
        self.assertNotContains(page, "data-make-production")
        self.assertNotContains(page, "data-make-form")
        self.client.force_login(seller)
        denied = self.client.post(reverse("manufacturing:make_for_sale", args=[self.line().pk]), {"lang": "ar", "quantity": "20"})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(ProductionOrder.objects.count(), 1)

    def test_no_recipe_means_no_button(self):
        loaf = make_item(item_code="FG-LOAF", item_name="رغيف", is_stock_tracked=True, default_sale_price="3")
        order = self.sale("5", item=loaf)
        page = self.page(order=order)
        self.assertContains(page, "data-production-cell")
        self.assertContains(page, "data-no-recipe")
        self.assertNotContains(page, "data-make-form")
        self.client.force_login(self.owner)
        self.client.post(reverse("manufacturing:make_for_sale", args=[self.line(order).pk]), {"lang": "ar"})
        self.assertFalse(ProductionOrder.objects.exists())

    def test_a_quotation_has_no_production_column(self):
        self.assertNotContains(self.page(order=self.sale("5", confirm=False)), "data-production-cell")

    def test_a_shop_without_the_manufacturing_module_has_no_column(self):
        from unittest import mock

        with mock.patch.object(make_to_order, "manufacturing_on", return_value=False):
            self.assertNotContains(self.page(), "data-production-cell")
        self.assertTrue(make_to_order.manufacturing_on())

    def test_the_screen_action_reports_what_it_refused(self):
        self.client.force_login(self.owner)
        url = reverse("manufacturing:make_for_sale", args=[self.line().pk])
        response = self.client.post(url, {"lang": "ar", "quantity": "99"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("أكتر من اللي لسه ما اتغطاش", [m.message for m in get_messages(response.wsgi_request)][-1])
        response = self.client.post(url, {"lang": "ar", "location": "999999"})
        self.assertIn("من مخازن جهة الأمر", [m.message for m in get_messages(response.wsgi_request)][-1])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertFalse(ProductionOrder.objects.exists())

    def test_cancelling_the_sales_order_warns_and_leaves_the_production_order_alone(self):
        order = self.make()
        page = self.page()
        self.assertContains(page, "data-open-production")
        self.client.force_login(self.owner)
        response = self.client.post(reverse("orders:detail", args=[self.order.pk]), {"lang": "ar", "action": "move", "to": "cancelled"})
        self.assertEqual(response.status_code, 302)
        warned = [m.message for m in get_messages(response.wsgi_request)]
        self.assertTrue(any(order.number in text and "لسه شغال" in text for text in warned), warned)
        self.order.refresh_from_db()
        order.refresh_from_db()
        self.assertEqual((self.order.status, order.status), (Status.CANCELLED, OrderStatus.PLANNED))

    def test_a_production_order_made_for_stock_shows_no_sale(self):
        plain = orders.create_order({"recipe": self.recipe, "location": self.plant, "quantity": "10"}, self.owner)
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(reverse("manufacturing:order", args=[plain.pk])), "data-for-sale")


class EntityAndPermissionTests(MakeToOrderSetup):
    def test_another_entitys_sales_order_is_a_404_and_its_own_place_is_used(self):
        from entities.current import SESSION_KEY, working_in
        from entities.models import Entity
        from entities.services import main_entity

        branch = Entity.objects.create(code="MFG-BR", name_ar="فرع", active=True)
        branch_store = make_location(location_code="BRANCH-STORE", entity=branch, is_default=True)
        with working_in(branch):
            far = self.sale("12")
        self.assertEqual(far.entity_id, branch.pk)
        self.client.force_login(self.owner)
        session = self.client.session
        session[SESSION_KEY] = main_entity().pk
        session.save()
        self.assertEqual(self.client.get(reverse("orders:detail", args=[far.pk]) + "?lang=ar").status_code, 404)
        denied = self.client.post(reverse("manufacturing:make_for_sale", args=[self.line(far).pk]), {"lang": "ar"})
        self.assertEqual(denied.status_code, 404)
        self.assertFalse(ProductionOrder.objects.exists())
        # the branch's own order, worked on by the owner: made at the branch's store, not the main plant
        session[SESSION_KEY] = branch.pk
        session.save()
        self.client.post(reverse("manufacturing:make_for_sale", args=[self.line(far).pk]), {"lang": "ar"})
        made = ProductionOrder.objects.get()
        self.assertEqual((made.location, made.quantity), (branch_store, D("12.000")))
        # and the production order of the other entity is a 404 from the main one
        session[SESSION_KEY] = main_entity().pk
        session.save()
        self.assertEqual(self.client.get(reverse("manufacturing:order", args=[made.pk])).status_code, 404)

    def test_a_storekeeper_without_sales_rights_cannot_use_the_action(self):
        keeper = person(RoleCode.STOCK_KEEPER, "mto_keeper")
        self.assertTrue(user_has_permission(keeper, "inventory.adjust_stock"))
        self.assertFalse(user_has_permission(keeper, "sales.view_sales_invoices"))
        self.client.force_login(keeper)
        response = self.client.post(reverse("manufacturing:make_for_sale", args=[self.line().pk]), {"lang": "ar"})
        self.assertEqual(response.status_code, 403)
        self.assertFalse(ProductionOrder.objects.exists())

    def test_a_switched_off_quotations_capability_closes_the_action(self):
        caps.set_capability_enabled(self.profile, "quotations", False)
        self.client.force_login(self.owner)
        response = self.client.post(reverse("manufacturing:make_for_sale", args=[self.line().pk]), {"lang": "ar"})
        self.assertEqual(response.status_code, 404)
