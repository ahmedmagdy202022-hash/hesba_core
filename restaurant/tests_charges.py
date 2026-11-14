"""Restaurants R3 (HG-113): the service charge, the minimum per guest, and the printed check before paying.

Two guests at table «1» order a mixed grill (250.00) and a fatta (150.00): food 400.00. With a 12% service on dine-in:

* service 48.00; VAT on (14% per line): 35.00 + 21.00 + 6.72 = 62.72, total **510.72**; VAT off **448.00**;
* with a minimum of 300.00 a guest (600.00 for two): a top-up «فرق الحد الأدنى» of 200.00, service on 600.00 = 72.00;
  VAT on 35.00 + 21.00 + 28.00 + 10.08 = 94.08, total **766.08**; VAT off **672.00**;
* a bill discount of 50.00 comes after tax and never touches the service line: VAT on 460.72 (VAT stays 62.72), VAT off 398.00;
* the same food as a takeaway has no service: VAT on 456.00, VAT off 400.00.

Service and the top-up are ordinary invoice lines of two non-stock service items, ``SERVICE-CHARGE`` and ``MIN-CHARGE``,
made once at the first bill that needs them, exactly like the delivery fee's ``DELIVERY-FEE``.

The clock stands still on a Wednesday at noon in Cairo, as in ``tests_combos``.
"""

from datetime import datetime, timezone as dt_timezone
from decimal import Decimal as D
from unittest import mock

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import router
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from cashboxes.services import get_cashbox_balance
from hesba_testing.factories import make_cashbox, make_item, open_test_shift, stock_in
from master_data.models import Item
from permissions.models import RoleCode
from sales.models import SalesInvoice
from sales.services import cancel_posted_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile, SystemSetting
from taxes.models import SalesLineTax

from . import charges, combos, services
from .insights import period_report
from .models import DiningTable, Order, OrderKind, OrderStatus
from .tests import RestaurantSetup, person
from .tests_branches import BranchSetup
from .tests_combos import meal
from .tests_split_pay import SplitBillSetup

NOW = datetime(2026, 11, 11, 10, 0, tzinfo=dt_timezone.utc)  # a Wednesday, 12:00 in Cairo


def _counts():
    return {model._meta.label: model.objects.count() for model in apps.get_models()
            if not model._meta.proxy and router.db_for_read(model) == "default"}


class ChargeSetup(RestaurantSetup):
    def setUp(self):
        clock = mock.patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)
        super().setUp()
        self.grill = make_item(item_code="D-MIX", item_name="مشويات مشكلة", default_sale_price="250.00", is_stock_tracked=False)
        self.fatta = make_item(item_code="D-FATTA", item_name="فتة", default_sale_price="150.00", is_stock_tracked=False)
        self.waiter = person(RoleCode.WAITER, "charge_waiter")
        self.vat(False)

    def vat(self, on):
        set_capability_enabled(ClientProfile.get_active(), "vat", on, user=self.owner)

    def configure(self, percent="12", kinds=("dine_in",), minimum=""):
        return charges.save_settings({"percent": percent, "kinds": list(kinds), "minimum": minimum}, self.owner)

    def order(self, kind=OrderKind.DINE_IN, guests=2, table=None, **extra):
        if kind == OrderKind.DINE_IN:
            order = services.open_order(self.owner, table=table or self.t1, guests=guests)
        else:
            order = services.open_order(self.owner, kind=kind, guests=guests, address="المعادي", **extra)
        services.add_item(order, self.grill, self.owner, 1)
        services.add_item(order, self.fatta, self.owner, 1)
        return order

    def pay(self, order, **extra):
        return services.pay(Order.objects.get(pk=order.pk), self.owner, cashbox=self.cashbox, tendered=D("10000"), **extra)[0]

    def invoice_rows(self, invoice):
        taxes = {row.line_id: row.tax_amount for row in SalesLineTax.objects.filter(line__invoice=invoice)}
        return [(line.item.item_code, line.quantity, line.unit_sale_price, line.line_discount_amount, line.line_total_amount, taxes.get(line.pk, D("0.00")))
                for line in invoice.lines.select_related("item").order_by("line_number")]

    def preview_rows(self, order):
        bill = charges.preview(Order.objects.get(pk=order.pk))
        return [(row["code"], row["quantity"], row["unit_sale_price"], row["line_discount_amount"], row["net"], row["tax"]) for row in bill["rows"]]


# ---- the settings ---------------------------------------------------------------------------------------------------


class SettingsTests(ChargeSetup):
    def test_everything_is_off_until_set(self):
        config = charges.settings()
        self.assertEqual((config.percent, config.kinds, config.minimum), (None, ("dine_in",), None))
        self.assertFalse(config.on)

    def test_saving_keeps_three_system_settings_and_one_audit_row(self):
        self.configure("12", ("dine_in", "takeaway"), "300")
        config = charges.settings()
        self.assertEqual((config.percent, config.kinds, config.minimum), (D("12"), ("dine_in", "takeaway"), D("300.00")))
        self.assertEqual(dict(SystemSetting.objects.filter(key__startswith="restaurant.").values_list("key", "value")),
                         {"restaurant.service_percent": "12", "restaurant.service_kinds": "dine_in,takeaway", "restaurant.min_per_guest": "300.00"})
        audit = AuditLog.objects.get(action="save_charges")
        self.assertEqual(audit.after_data, {"percent": "12", "kinds": ["dine_in", "takeaway"], "minimum": "300.00"})
        self.assertEqual(audit.before_data, {"percent": "", "kinds": ["dine_in"], "minimum": ""})

    def test_empty_or_zero_switches_each_off(self):
        self.configure("12", minimum="300")
        self.configure("", minimum="0")
        config = charges.settings()
        self.assertEqual((config.percent, config.minimum, config.on), (None, None, False))

    def test_bad_values_are_refused_and_change_nothing(self):
        self.configure("12")
        for data, text in (({"percent": "abc"}, "نسبة الخدمة"), ({"percent": "-1"}, "نسبة الخدمة"), ({"percent": "51"}, "نسبة الخدمة"),
                           ({"percent": "12", "kinds": []}, "نوع طلب"), ({"minimum": "-5"}, "الحد الأدنى"), ({"minimum": "x"}, "الحد الأدنى")):
            full = {"percent": "12", "kinds": ["dine_in"], "minimum": "", **data}
            with self.assertRaisesMessage(ValidationError, text):
                charges.save_settings(full, self.owner)
        self.assertEqual(charges.settings().percent, D("12"))
        self.assertEqual(AuditLog.objects.filter(action="save_charges").count(), 1)


# ---- the bill -------------------------------------------------------------------------------------------------------


class ServiceTests(ChargeSetup):
    def test_12_percent_with_vat_on_is_510_72_and_posts_exactly(self):
        self.configure()
        self.vat(True)
        order = self.order()
        self.assertEqual(services.bill_total(order, list(order.lines.all())), D("510.72"))
        invoice = self.pay(order)
        self.assertEqual(self.invoice_rows(invoice), [
            ("D-MIX", D("1.000"), D("250.00"), D("0.00"), D("250.00"), D("35.00")),
            ("D-FATTA", D("1.000"), D("150.00"), D("0.00"), D("150.00"), D("21.00")),
            ("SERVICE-CHARGE", D("1.000"), D("48.00"), D("0.00"), D("48.00"), D("6.72")),
        ])
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.discount_amount, invoice.total_amount), (D("448.00"), D("62.72"), D("0.00"), D("510.72")))
        self.assertEqual(invoice.lines.get(item__item_code="SERVICE-CHARGE").description, "خدمة 12%")
        self.assertEqual(get_cashbox_balance(self.cashbox), D("510.72"))

    def test_12_percent_with_vat_off_is_448(self):
        self.configure()
        invoice = self.pay(self.order())
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount), (D("448.00"), D("0.00"), D("448.00")))

    def test_the_minimum_tops_up_to_600_and_service_is_on_the_top_up_too(self):
        self.configure(minimum="300")
        self.vat(True)
        order = self.order()
        self.assertEqual(services.bill_total(order, list(order.lines.all())), D("766.08"))
        invoice = self.pay(order)
        self.assertEqual(self.invoice_rows(invoice), [
            ("D-MIX", D("1.000"), D("250.00"), D("0.00"), D("250.00"), D("35.00")),
            ("D-FATTA", D("1.000"), D("150.00"), D("0.00"), D("150.00"), D("21.00")),
            ("MIN-CHARGE", D("1.000"), D("200.00"), D("0.00"), D("200.00"), D("28.00")),
            ("SERVICE-CHARGE", D("1.000"), D("72.00"), D("0.00"), D("72.00"), D("10.08")),
        ])
        self.assertEqual((invoice.tax_amount, invoice.total_amount), (D("94.08"), D("766.08")))
        self.assertEqual(invoice.lines.get(item__item_code="MIN-CHARGE").description, "فرق الحد الأدنى (2 × 300.00)")

    def test_the_minimum_with_vat_off_is_672(self):
        self.configure(minimum="300")
        self.assertEqual(self.pay(self.order()).total_amount, D("672.00"))

    def test_food_above_the_minimum_needs_no_top_up(self):
        self.configure(minimum="150")  # 300.00 for two, below the 400.00 of food
        invoice = self.pay(self.order())
        self.assertEqual([row[0] for row in self.invoice_rows(invoice)], ["D-MIX", "D-FATTA", "SERVICE-CHARGE"])
        self.assertEqual(invoice.total_amount, D("448.00"))
        self.assertFalse(Item.objects.filter(item_code=charges.MIN_ITEM_CODE).exists())  # never made for nothing

    def test_a_minimum_alone_with_no_service(self):
        self.configure(percent="", minimum="300")
        invoice = self.pay(self.order())
        self.assertEqual([row[:5] for row in self.invoice_rows(invoice)][-1], ("MIN-CHARGE", D("1.000"), D("200.00"), D("0.00"), D("200.00")))
        self.assertEqual(invoice.total_amount, D("600.00"))
        self.assertFalse(Item.objects.filter(item_code=charges.SERVICE_ITEM_CODE).exists())

    def test_a_bill_discount_comes_after_tax_and_never_reduces_the_service_line(self):
        self.configure()
        self.vat(True)
        invoice = self.pay(self.order(), discount=D("50.00"))
        self.assertEqual((invoice.tax_amount, invoice.discount_amount, invoice.total_amount), (D("62.72"), D("50.00"), D("460.72")))
        self.assertEqual(invoice.lines.get(item__item_code="SERVICE-CHARGE").line_total_amount, D("48.00"))

    def test_a_bill_discount_with_vat_off(self):
        self.configure()
        self.assertEqual(self.pay(self.order(), discount=D("50.00")).total_amount, D("398.00"))

    def test_takeaway_has_no_service_by_default(self):
        self.configure(minimum="300")
        self.vat(True)
        invoice = self.pay(self.order(kind=OrderKind.TAKEAWAY))
        self.assertEqual([row[0] for row in self.invoice_rows(invoice)], ["D-MIX", "D-FATTA"])  # nor a minimum: guests mean nothing on a takeaway
        self.assertEqual(invoice.total_amount, D("456.00"))

    def test_takeaway_with_vat_off(self):
        self.configure()
        self.assertEqual(self.pay(self.order(kind=OrderKind.TAKEAWAY)).total_amount, D("400.00"))

    def test_takeaway_gets_service_when_the_owner_ticks_it_but_never_a_minimum(self):
        self.configure(kinds=("dine_in", "takeaway"), minimum="300")
        invoice = self.pay(self.order(kind=OrderKind.TAKEAWAY))
        self.assertEqual([row[:5] for row in self.invoice_rows(invoice)][-1], ("SERVICE-CHARGE", D("1.000"), D("48.00"), D("0.00"), D("48.00")))
        self.assertEqual(invoice.total_amount, D("448.00"))

    def test_the_delivery_fee_is_never_in_the_service_base(self):
        self.configure(kinds=("dine_in", "delivery"))
        invoice = self.pay(self.order(kind=OrderKind.DELIVERY, delivery_fee="20"))
        self.assertEqual([row[:5] for row in self.invoice_rows(invoice)], [
            ("D-MIX", D("1.000"), D("250.00"), D("0.00"), D("250.00")),
            ("D-FATTA", D("1.000"), D("150.00"), D("0.00"), D("150.00")),
            ("DELIVERY-FEE", D("1.000"), D("20.00"), D("0.00"), D("20.00")),
            ("SERVICE-CHARGE", D("1.000"), D("48.00"), D("0.00"), D("48.00")),  # 12% of 400.00, not of 420.00
        ])
        self.assertEqual(invoice.total_amount, D("468.00"))

    def test_a_delivery_by_default_has_its_fee_and_no_service(self):
        self.configure()
        invoice = self.pay(self.order(kind=OrderKind.DELIVERY, delivery_fee="20"))
        self.assertEqual([row[0] for row in self.invoice_rows(invoice)], ["D-MIX", "D-FATTA", "DELIVERY-FEE"])
        self.assertEqual(invoice.total_amount, D("420.00"))

    def test_a_voided_line_is_not_in_the_base(self):
        self.configure()
        order = self.order()
        services.send_to_kitchen(order, self.owner)
        services.void_line(order.lines.get(item=self.fatta), self.owner, reason="اتأخر")
        invoice = self.pay(order)
        self.assertEqual(invoice.lines.get(item__item_code="SERVICE-CHARGE").line_total_amount, D("30.00"))  # 12% of 250.00
        self.assertEqual(invoice.total_amount, D("280.00"))

    def test_service_rounds_to_the_piastre(self):
        self.configure("12.5")
        order = services.open_order(self.owner, table=self.t1, guests=1)
        services.add_item(order, self.tea, self.owner, 1)  # 15.00 → 1.875 → 1.88
        invoice = self.pay(order)
        self.assertEqual(invoice.lines.get(item__item_code="SERVICE-CHARGE").line_total_amount, D("1.88"))
        self.assertEqual(invoice.lines.get(item__item_code="SERVICE-CHARGE").description, "خدمة 12.5%")

    def test_english_descriptions(self):
        self.configure(minimum="300")
        invoice = services.pay(Order.objects.get(pk=self.order().pk), self.owner, cashbox=self.cashbox, tendered=D("10000"), lang="en")[0]
        self.assertEqual([line.description for line in invoice.lines.order_by("line_number")][2:], ["Minimum charge top-up (2 × 300.00)", "Service 12%"])


class AddOnAndComboTests(ChargeSetup):
    def test_a_priced_add_on_is_food_and_in_the_base(self):
        from . import modifiers

        cheese = make_item(item_code="A-CHEESE", item_name="جبنة زيادة", default_sale_price="15.00", is_stock_tracked=False)
        extras = modifiers.save_group({"name_ar": "إضافات", "min_choices": "0", "max_choices": "1", "options": [{"name_ar": "جبنة زيادة", "item": cheese}]},
                                      self.owner)
        modifiers.set_dish_groups(self.fatta, [extras], self.owner)
        self.configure()
        order = services.open_order(self.owner, table=self.t1, guests=2)
        services.add_item(order, self.fatta, self.owner, 1, options=[extras.options.get().pk])  # 150.00 + 15.00
        invoice = self.pay(order)
        self.assertEqual([row[:5] for row in self.invoice_rows(invoice)], [
            ("D-FATTA", D("1.000"), D("150.00"), D("0.00"), D("150.00")),
            ("A-CHEESE", D("1.000"), D("15.00"), D("0.00"), D("15.00")),
            ("SERVICE-CHARGE", D("1.000"), D("19.80"), D("0.00"), D("19.80")),  # 12% of 165.00
        ])

    def test_a_combo_pays_service_on_what_it_charges_after_its_discounts(self):
        burger = make_item(item_code="C-BURG", item_name="برجر", default_sale_price="120.00", is_stock_tracked=False)
        fries = make_item(item_code="C-FRIES", item_name="بطاطس", default_sale_price="40.00", is_stock_tracked=False)
        large = make_item(item_code="C-FRIES-L", item_name="بطاطس كبيرة", default_sale_price="50.00", is_stock_tracked=False)
        pepsi = make_item(item_code="C-PEPSI", item_name="بيبسي", default_sale_price="25.00", is_stock_tracked=True)
        stock_in(pepsi, self.location, 5, "8.00")
        combo = combos.save_combo(meal(burger, fries, large, pepsi), self.owner)
        sandwich, side, drink = combo.slots.order_by("sort", "pk")
        picks = {sandwich.pk: sandwich.choices.get().pk, side.pk: side.choices.order_by("sort", "pk").first().pk, drink.pk: drink.choices.get().pk}
        self.configure()
        order = services.open_order(self.owner, table=self.t1, guests=2)
        services.add_combo(order, combo, self.owner, picks)  # 185.00 at menu prices, 150.00 after the 35.00 saving
        invoice = self.pay(order)
        self.assertEqual([row[:5] for row in self.invoice_rows(invoice)], [
            ("C-BURG", D("1.000"), D("120.00"), D("22.70"), D("97.30")),
            ("C-FRIES", D("1.000"), D("40.00"), D("7.57"), D("32.43")),
            ("C-PEPSI", D("1.000"), D("25.00"), D("4.73"), D("20.27")),
            ("SERVICE-CHARGE", D("1.000"), D("18.00"), D("0.00"), D("18.00")),  # 12% of 150.00, not of 185.00
        ])
        self.assertEqual(invoice.total_amount, D("168.00"))


class ItemTests(ChargeSetup):
    def test_the_two_items_are_made_once_and_never_stock_tracked(self):
        self.configure(minimum="300")
        self.pay(self.order())
        self.pay(self.order(table=self.t2))
        for code, name in ((charges.SERVICE_ITEM_CODE, "خدمة"), (charges.MIN_ITEM_CODE, "فرق الحد الأدنى")):
            item = Item.objects.get(item_code=code)
            self.assertEqual((item.item_name, item.is_stock_tracked, item.default_sale_price), (name, False, D("0.00")))
        self.assertEqual(SalesInvoice.objects.filter(lines__item__item_code=charges.SERVICE_ITEM_CODE).distinct().count(), 2)

    def test_a_charge_item_switched_to_stock_refuses_the_bill_and_nothing_is_written(self):
        self.configure(minimum="300")
        self.pay(self.order())
        for code in (charges.SERVICE_ITEM_CODE, charges.MIN_ITEM_CODE):
            Item.objects.filter(item_code__in=[charges.SERVICE_ITEM_CODE, charges.MIN_ITEM_CODE]).update(is_stock_tracked=False)
            Item.objects.filter(item_code=code).update(is_stock_tracked=True)
            order = self.order(table=self.t2)
            before = _counts()
            with self.assertRaisesMessage(ValidationError, code):
                self.pay(order)
            self.assertEqual(_counts(), before)
            self.assertEqual(Order.objects.get(pk=order.pk).status, OrderStatus.OPEN)
            services.cancel_order(order, self.owner)

    def test_the_charge_items_are_off_the_menu_and_cannot_be_ordered(self):
        self.configure(minimum="300")
        self.pay(self.order())
        from .views import _dishes, _menu

        menu = {item.item_code for item in _menu()[0]}
        self.assertFalse(menu & {charges.SERVICE_ITEM_CODE, charges.MIN_ITEM_CODE})
        self.assertFalse({item.item_code for item in _dishes()} & {charges.SERVICE_ITEM_CODE, charges.MIN_ITEM_CODE})
        order = services.open_order(self.owner, table=self.t2)
        for code in (charges.SERVICE_ITEM_CODE, charges.MIN_ITEM_CODE):
            with self.assertRaisesMessage(ValidationError, "بيتحسب لوحده"):
                services.add_item(order, Item.objects.get(item_code=code), self.owner, 1)
        self.assertFalse(order.lines.exists())

    def test_settings_off_the_bill_is_exactly_as_before_and_no_item_is_made(self):
        self.configure(minimum="300")
        self.configure("", minimum="")
        order = self.order()
        before = _counts()
        invoice = self.pay(order)
        self.assertEqual([row[0] for row in self.invoice_rows(invoice)], ["D-MIX", "D-FATTA"])
        self.assertEqual(invoice.total_amount, D("400.00"))
        self.assertFalse(Item.objects.filter(item_code__in=[charges.SERVICE_ITEM_CODE, charges.MIN_ITEM_CODE]).exists())
        written = {label for label, count in _counts().items() if count != before[label]}
        self.assertNotIn("master_data.Item", written)
        self.assertEqual(list(AuditLog.objects.order_by("-pk").values_list("action", flat=True)[:3])[::-1],
                         ["create_sales_draft", "post_sales_invoice", "pay_order"])

    def test_the_e_receipt_holds_an_uncoded_charge_line_as_it_holds_any_uncoded_item(self):
        from einvoice.models import ItemCode
        from ereceipt.services import build

        self.configure(minimum="300")
        invoice = self.pay(self.order())
        for item in (self.grill, self.fatta):
            ItemCode.objects.create(item=item, code="EG-1-" + item.item_code)
        _document, problems = build(invoice, None)
        self.assertIn("الصنف «SERVICE-CHARGE» مالوش كود EGS/GS1 مسجّل.", problems)
        self.assertIn("الصنف «MIN-CHARGE» مالوش كود EGS/GS1 مسجّل.", problems)
        self.assertNotIn("الصنف «D-MIX» مالوش كود EGS/GS1 مسجّل.", problems)
        for code in (charges.SERVICE_ITEM_CODE, charges.MIN_ITEM_CODE):
            ItemCode.objects.create(item=Item.objects.get(item_code=code), code="EG-1-" + code)
        _document, problems = build(invoice, None)
        self.assertFalse([problem for problem in problems if "EGS/GS1" in problem])
        self.assertEqual(charges.uncoded_items(), [])


# ---- R2a: a table paid in parts --------------------------------------------------------------------------------------


class PartTests(ChargeSetup):
    def test_each_part_pays_service_on_its_own_food(self):
        self.configure()
        order = self.order()
        part = services.split_order(order, {order.lines.get(item=self.fatta).pk: 1}, self.owner)
        part_invoice = self.pay(part)
        self.assertEqual([row[:5] for row in self.invoice_rows(part_invoice)][-1], ("SERVICE-CHARGE", D("1.000"), D("18.00"), D("0.00"), D("18.00")))
        self.assertEqual(part_invoice.total_amount, D("168.00"))
        rest = self.pay(order)
        self.assertEqual([row[:5] for row in self.invoice_rows(rest)][-1], ("SERVICE-CHARGE", D("1.000"), D("30.00"), D("0.00"), D("30.00")))
        self.assertEqual(rest.total_amount, D("280.00"))
        self.assertEqual(part_invoice.total_amount + rest.total_amount, D("448.00"))  # as the bill paid whole

    def test_the_minimum_is_the_whole_tables_and_tops_up_on_the_last_bill_only(self):
        self.configure(minimum="300")
        order = self.order()
        part = services.split_order(order, {order.lines.get(item=self.fatta).pk: 1}, self.owner)
        part_invoice = self.pay(part)
        self.assertNotIn("MIN-CHARGE", [row[0] for row in self.invoice_rows(part_invoice)])  # a part never tops up
        self.assertEqual(part_invoice.total_amount, D("168.00"))
        rest = self.pay(order)
        # 600.00 − (250.00 here + 150.00 paid in the part) = 200.00; service on 250.00 + 200.00 = 54.00
        self.assertEqual([row[:5] for row in self.invoice_rows(rest)][1:], [
            ("MIN-CHARGE", D("1.000"), D("200.00"), D("0.00"), D("200.00")),
            ("SERVICE-CHARGE", D("1.000"), D("54.00"), D("0.00"), D("54.00")),
        ])
        self.assertEqual(rest.total_amount, D("504.00"))
        self.assertEqual(part_invoice.total_amount + rest.total_amount, D("672.00"))  # the same as the table paid whole

    def test_paying_a_token_part_first_does_not_escape_the_minimum(self):
        self.configure(minimum="300")
        order = self.order()
        services.add_item(order, self.tea, self.owner, 1)
        part = services.split_order(order, {order.lines.get(item=self.tea).pk: 1}, self.owner)
        self.assertEqual(self.pay(part).total_amount, D("16.80"))  # 15.00 + 1.80 service
        rest = self.pay(order)
        self.assertEqual(rest.lines.get(item__item_code="MIN-CHARGE").line_total_amount, D("185.00"))  # 600 − 400 − 15
        self.assertEqual(rest.total_amount, D("655.20"))  # 400 + 185 + 70.20

    def test_a_part_whose_invoice_was_cancelled_no_longer_counts_toward_the_minimum(self):
        self.configure(minimum="300")
        order = self.order()
        part = services.split_order(order, {order.lines.get(item=self.fatta).pk: 1}, self.owner)
        part_invoice = self.pay(part)
        cancel_posted_sales_invoice(part_invoice.pk, self.owner)
        rest = self.pay(order)
        self.assertEqual(rest.lines.get(item__item_code="MIN-CHARGE").line_total_amount, D("350.00"))  # 600 − 250
        self.assertEqual(rest.total_amount, D("672.00"))

    def test_merged_tables_count_both_tables_guests(self):
        self.configure(percent="", minimum="300")
        first = self.order(guests=2)
        second = services.open_order(self.owner, table=self.t2, guests=1)
        services.add_item(second, self.tea, self.owner, 1)
        services.merge_orders(first, second, self.owner)
        invoice = self.pay(first)
        self.assertEqual(invoice.lines.get(item__item_code="MIN-CHARGE").line_total_amount, D("485.00"))  # 3 × 300 − 415
        self.assertEqual(invoice.total_amount, D("900.00"))


# ---- R2b: one bill paid several ways ---------------------------------------------------------------------------------


class SplitPaymentTests(SplitBillSetup):
    def test_the_tenders_must_equal_the_total_with_service(self):
        charges.save_settings({"percent": "12", "kinds": ["dine_in"], "minimum": ""}, self.owner)
        order = self.burgers()  # 270.00; service 32.40; VAT 33.60 + 4.20 + 4.54 = 42.34; total 344.74
        self.assertEqual(services.bill_total(order, list(order.lines.all())), D("344.74"))
        self.assertIn('data-bill="344.74"', self.page(order))
        response = self.split_from_screen(order, [(self.cashbox, "200.00"), (self.card, "107.80")])  # the old total without service
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.OPEN)
        self.assertEqual(SalesInvoice.objects.count(), 0)
        self.assertEqual(response.status_code, 200)
        self.split_from_screen(order, [(self.cashbox, "200.00"), (self.card, "144.74")])
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice.total_amount, order.invoice.tax_amount), (OrderStatus.PAID, D("344.74"), D("42.34")))
        self.assertEqual((get_cashbox_balance(self.cashbox), get_cashbox_balance(self.card)), (D("200.00"), D("144.74")))


# ---- R12: a branch's bill ---------------------------------------------------------------------------------------------


class BranchChargeTests(BranchSetup):
    def test_a_branchs_bill_with_service_posts_to_its_branch(self):
        self.tables_t1()
        charges.save_settings({"percent": "12", "kinds": ["dine_in"], "minimum": ""}, self.owner)
        order = self.bill(services.open_order(self.owner, table=self.b_t1, guests=2))  # 2 × 60.00 + 20.00 = 140.00
        invoice = services.pay(order, self.manager_b, cashbox=self.b_cashbox, tendered=D("1000"))[0]
        self.assertEqual((invoice.selling_location, invoice.cashbox, invoice.total_amount), (self.b_location, self.b_cashbox, D("156.80")))
        self.assertEqual(invoice.lines.get(item__item_code="SERVICE-CHARGE").line_total_amount, D("16.80"))
        self.assertEqual(get_cashbox_balance(self.b_cashbox), D("156.80"))
        self.assertEqual(CashboxMovement.objects.filter(cashbox=self.cashbox).count(), 0)


# ---- the printed check ------------------------------------------------------------------------------------------------


class CheckTests(ChargeSetup):
    def check_page(self, order, user=None, lang="ar"):
        self.client.force_login(user or self.waiter)
        return self.client.get(reverse("restaurant:check", args=[order.pk]), {"lang": lang})

    def test_the_check_shows_every_row_the_invoice_then_posts_and_writes_only_an_audit_row(self):
        self.configure(minimum="300")
        self.vat(True)
        order = self.order()
        expected = self.preview_rows(order)
        before = _counts()
        page = self.check_page(order)
        self.assertEqual(page.status_code, 200)
        after = _counts()
        self.assertEqual({label: after[label] - before[label] for label in after if after[label] != before[label]}, {"audit.AuditLog": 1})
        audit = AuditLog.objects.latest("pk")
        self.assertEqual((audit.action, audit.actor, audit.after_data["total"]), ("print_check", self.waiter, "766.08"))
        self.assertFalse(Item.objects.filter(item_code__in=[charges.SERVICE_ITEM_CODE, charges.MIN_ITEM_CODE]).exists())  # nothing made
        html = page.content.decode()
        for code, quantity, price, discount, net, tax in expected:
            self.assertIn(f'data-check-row="{code}" data-check-net="{net:.2f}" data-check-tax="{tax:.2f}"', html)
        self.assertIn('data-check-total="766.08"', html)
        self.assertIn('data-check-tax-total="94.08"', html)
        self.assertIn("فرق الحد الأدنى", html)
        self.assertIn("خدمة 12%", html)
        invoice = self.pay(order)
        self.assertEqual(self.invoice_rows(invoice), expected)  # every row: item, quantity, price, discount, net and VAT
        self.assertEqual(invoice.total_amount, D("766.08"))

    def test_the_check_of_a_combo_and_an_add_on_matches_the_invoice(self):
        burger = make_item(item_code="C-BURG", item_name="برجر", default_sale_price="120.00", is_stock_tracked=False)
        fries = make_item(item_code="C-FRIES", item_name="بطاطس", default_sale_price="40.00", is_stock_tracked=False)
        large = make_item(item_code="C-FRIES-L", item_name="بطاطس كبيرة", default_sale_price="50.00", is_stock_tracked=False)
        pepsi = make_item(item_code="C-PEPSI", item_name="بيبسي", default_sale_price="25.00", is_stock_tracked=True)
        stock_in(pepsi, self.location, 5, "8.00")
        combo = combos.save_combo(meal(burger, fries, large, pepsi), self.owner)
        sandwich, side, drink = combo.slots.order_by("sort", "pk")
        picks = {sandwich.pk: sandwich.choices.get().pk, side.pk: side.choices.order_by("sort", "pk").last().pk, drink.pk: drink.choices.get().pk}
        self.configure()
        self.vat(True)
        order = services.open_order(self.owner, table=self.t1, guests=2)
        services.add_combo(order, combo, self.owner, picks)
        services.add_item(order, self.fatta, self.owner, 1)
        expected = self.preview_rows(order)
        html = self.check_page(order).content.decode()
        self.assertIn('data-check-combo="1"', html)
        self.assertIn("وجبة برجر", html)
        self.assertEqual(self.invoice_rows(self.pay(order)), expected)

    def test_with_the_settings_off_the_check_is_the_plain_bill(self):
        self.vat(True)
        order = self.order()
        html = self.check_page(order).content.decode()
        self.assertIn('data-check-total="456.00"', html)
        self.assertNotIn('data-check-row="SERVICE-CHARGE"', html)

    def test_the_check_follows_the_ticket_permission_and_is_for_an_open_order_only(self):
        order = self.order()
        self.assertEqual(self.check_page(order, user=self.owner).status_code, 200)
        self.assertEqual(self.check_page(order, user=person(RoleCode.STOCK_KEEPER, "charge_keeper")).status_code, 403)
        self.pay(order)
        self.assertEqual(self.check_page(order).status_code, 404)

    def test_the_check_in_english(self):
        self.configure()
        html = self.check_page(self.order(), lang="en").content.decode()
        self.assertIn("Service 12%", html)
        self.assertIn('dir="ltr"', html)


# ---- the screens ------------------------------------------------------------------------------------------------------


class ScreenTests(ChargeSetup):
    def page(self, order, user=None, lang="ar"):
        self.client.force_login(user or self.owner)
        return self.client.get(reverse("restaurant:order", args=[order.pk]), {"lang": lang})

    def test_with_settings_off_the_order_page_and_board_show_nothing_new(self):
        order = self.order()
        html = self.page(order).content.decode()
        for marker in ("data-charge-row", "data-print-check", "data-charge-note"):
            self.assertNotIn(marker, html)
        self.assertIn('<span data-order-total>400.00</span>', html.replace("٫", "."))
        board = self.client.get(reverse("restaurant:board")).content.decode()
        self.assertNotIn("data-charge", board)

    def test_the_bill_box_shows_the_top_up_and_service_and_the_total_that_posts(self):
        self.configure(minimum="300")
        order = self.order()
        response = self.page(order)
        html = response.content.decode()
        self.assertIn('data-charge-row="min" data-charge-amount="200.00"', html)
        self.assertIn('data-charge-row="service" data-charge-amount="72.00"', html)
        self.assertEqual(response.context["total"], D("672.00"))
        self.assertIn('data-print-check', html)
        board = self.client.get(reverse("restaurant:board"))
        self.assertEqual(board.context["tables"][0]["bill"], D("672.00"))
        self.assertEqual(self.pay(order).total_amount, D("672.00"))

    def test_a_part_and_its_parent_say_how_the_minimum_works(self):
        self.configure(minimum="300")
        order = self.order()
        part = services.split_order(order, {order.lines.get(item=self.fatta).pk: 1}, self.owner)
        html = self.page(part).content.decode()
        self.assertIn("data-charge-note", html)
        self.assertNotIn('data-charge-row="min"', html)

    def test_the_waiter_sees_the_check_button_but_not_the_pay_form(self):
        self.configure()
        html = self.page(self.order(), user=self.waiter).content.decode()
        self.assertIn("data-print-check", html)
        self.assertNotIn("data-pay-form", html)

    def test_the_open_value_tile_stays_the_food_before_vat_and_service(self):
        from reports.activity_panel import WORDS, _kitchen

        self.configure(minimum="300")
        self.order()
        tiles = {tile["key"]: tile["value"] for tile in _kitchen(WORDS["ar"], timezone.localdate())[0]}
        self.assertEqual(tiles["open_value"], D("400.00"))


class SetupScreenTests(ChargeSetup):
    def post(self, user, **data):
        self.client.force_login(user)
        return self.client.post(reverse("restaurant:tables"), {"lang": "ar", "action": "charges", **data}, follow=True)

    def test_the_owner_sets_the_service_and_the_minimum_on_the_tables_page(self):
        self.client.force_login(self.owner)
        html = self.client.get(reverse("restaurant:tables"), {"lang": "ar"}).content.decode()
        self.assertIn("data-charges-form", html)
        self.assertIn("نسبة الخدمة", html)
        response = self.post(self.owner, percent="12", kinds=["dine_in"], minimum="300")
        self.assertContains(response, "اتحفظت الخدمة والحد الأدنى")
        self.assertEqual((charges.settings().percent, charges.settings().minimum), (D("12"), D("300.00")))
        self.assertContains(response, "SERVICE-CHARGE")  # the e-receipt note names the two items that need their codes
        self.assertContains(response, "MIN-CHARGE")
        english = self.client.get(reverse("restaurant:tables"), {"lang": "en"}).content.decode()
        self.assertIn("Service charge", english)

    def test_a_bad_value_shows_the_reason_and_saves_nothing(self):
        response = self.post(self.owner, percent="80", kinds=["dine_in"], minimum="")
        self.assertContains(response, "نسبة الخدمة")
        self.assertIsNone(charges.settings().percent)
        self.assertEqual(DiningTable.objects.count(), 2)  # the settings form never reaches the table save

    def test_a_waiter_cannot_change_them(self):
        response = self.post(self.waiter, percent="12", kinds=["dine_in"])
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(charges.settings().percent)


# ---- the report -------------------------------------------------------------------------------------------------------


class ReportTests(ChargeSetup):
    def test_service_and_top_up_totals_and_no_charge_item_in_the_dishes(self):
        self.configure(minimum="300")
        self.pay(self.order())
        self.pay(self.order(kind=OrderKind.TAKEAWAY))
        today = timezone.localdate()
        data = period_report(today, today)
        totals = data["totals"]
        self.assertEqual((totals["sales"], totals["service"], totals["min_topup"]), (D("1072.00"), D("72.00"), D("200.00")))
        codes = {row["item"].item_code for row in data["dishes"]}
        self.assertEqual(codes, {"D-MIX", "D-FATTA"})
        dishes = sum((row["sales"] for row in data["dishes"]), D("0"))
        self.assertEqual(dishes, D("800.00"))
        self.assertEqual(dishes - totals["combo_discount"] + totals["service"] + totals["min_topup"], totals["sales"])  # adds up to the invoices
        self.assertEqual(totals["sales"], sum((invoice.total_amount - invoice.tax_amount for invoice in SalesInvoice.objects.all()), D("0")))

    def test_the_report_page_shows_the_two_totals_only_when_there_are_any(self):
        self.client.force_login(self.owner)
        self.pay(self.order())
        html = self.client.get(reverse("restaurant:report")).content.decode()
        self.assertNotIn("data-r-service", html)
        self.configure(minimum="300")
        self.pay(self.order(table=self.t2))
        html = self.client.get(reverse("restaurant:report")).content.decode()
        self.assertIn('data-r-service="72.00"', html)
        self.assertIn('data-r-min-topup="200.00"', html)
