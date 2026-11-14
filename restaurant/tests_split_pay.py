"""Restaurants R2b (HG-110): «ادفع بأكتر من طريقة» on the table bill.

The set-up of ``tests_branches`` (the main hall «HALL» with «TILL», the Maadi branch «BR-HALL» with «BR-TILL», a «T1» in
each, the owner's shift on «TILL», the Maadi manager's on «BR-TILL», the clock on Wednesday 11 Nov 2026 at noon in Cairo),
plus a card machine cashbox in each (main «CARD», Maadi «BR-CARD») and R1a's burger: 120.00 with a required «ميديم» and
the priced add-on «جبنة زيادة» at 15.00. Two burgers with cheese are 270.00; with VAT on (14% per line, 33.60 + 4.20)
the bill is **307.80**, paid as 200.00 into the till and 107.80 into the card machine.
"""

import re
from decimal import Decimal as D

from django.contrib.staticfiles import finders
from django.urls import reverse

from cashboxes.models import CashboxKind, CashboxMovement
from cashboxes.services import get_cashbox_balance
from hesba_testing.factories import make_cashbox, make_customer, make_item
from ledger import reports as gl
from ledger.models import JournalLine
from ledger.projector import Projector
from sales.models import CustomerPayment, SalesInvoice
from sales.pos import walk_in_customer
from sales.pos_buttons import save_card_cashbox
from sales.services import cancel_posted_sales_invoice
from settings_core.capabilities import set_capability_enabled
from settings_core.models import ClientProfile
from settlements.models import InvoiceSettlement
from shifts.services import open_shift_for, summary

from . import modifiers, services
from .models import Order, OrderStatus
from .tests_branches import BranchSetup


def walk_in_balance():
    customer = walk_in_customer()
    rows = customer.ledger_entries.all()
    return customer.opening_balance + sum((row.due_increase - row.due_decrease for row in rows), D("0.00"))


class SplitBillSetup(BranchSetup):
    def setUp(self):
        super().setUp()
        self.card = make_cashbox(cashbox_code="CARD", name_ar="ماكينة الفيزا")
        self.b_card = make_cashbox(cashbox_code="BR-CARD", name_ar="فيزا المعادي", entity=self.branch)
        save_card_cashbox(self.card)
        self.burger = make_item(item_code="D-BURG", item_name="برجر", default_sale_price="120.00", is_stock_tracked=False)
        self.cheese = make_item(item_code="A-CHEESE", item_name="جبنة زيادة", default_sale_price="15.00", is_stock_tracked=False)
        doneness = modifiers.save_group({"name_ar": "درجة الاستواء", "name_en": "Doneness", "min_choices": "1", "max_choices": "1", "sort": "1",
                                         "options": [{"name_ar": "ميديم", "name_en": "Medium"}]}, self.owner)
        extras = modifiers.save_group({"name_ar": "إضافات", "name_en": "Extras", "min_choices": "0", "max_choices": "2", "sort": "2",
                                       "options": [{"name_ar": "جبنة زيادة", "name_en": "Extra cheese", "item": self.cheese}]}, self.owner)
        modifiers.set_dish_groups(self.burger, [doneness, extras], self.owner)
        self.medium, self.cheese_opt = doneness.options.get(), extras.options.get()
        self.tables_t1()
        self.vat(True)

    def vat(self, on):
        set_capability_enabled(ClientProfile.get_active(), "vat", on, user=self.owner)

    def burgers(self, table=None, customer=None):
        order = services.open_order(self.owner, table=table or self.a_t1, guests=2, customer=customer)
        services.add_item(order, self.burger, self.owner, 2, options=[self.medium.pk, self.cheese_opt.pk])
        return order

    def split_from_screen(self, order, rows, user=None, lang="ar", **extra):
        data = {"lang": lang, "action": "pay", "cashbox": rows[0][0].pk if rows and rows[0][0] else "", "tendered": "0", "split": "1", "print": "0"}
        for index, (box, amount) in enumerate(rows):
            data[f"tender_cashbox_{index}"] = box.pk if box is not None else ""
            data[f"tender_amount_{index}"] = amount
        data.update(extra)
        self.client.force_login(user or self.owner)
        return self.client.post(reverse("restaurant:order", args=[order.pk]), data, follow=True)

    def everything(self):
        Projector().rebuild()
        return {"moved": self.moved(), "payments": CustomerPayment.objects.count(), "settlements": InvoiceSettlement.objects.count(),
                "journal": JournalLine.objects.count(), "ledger": walk_in_balance()}

    def assert_refused(self, order, rows, text, **extra):
        before = self.everything()
        response = self.split_from_screen(order, rows, **extra)
        self.assertContains(response, text)
        self.assertEqual(self.everything(), before)
        order.refresh_from_db()
        self.assertEqual(order.status, OrderStatus.OPEN)


class SplitBillTests(SplitBillSetup):
    def test_307_80_as_200_into_the_till_and_107_80_into_the_card_machine(self):
        order = self.burgers()
        response = self.split_from_screen(order, [(self.cashbox, "200.00"), (self.card, "107.80")])
        self.assertEqual(response.status_code, 200)
        order.refresh_from_db()
        invoice = order.invoice
        self.assertEqual((order.status, invoice.total_amount, invoice.tax_amount, invoice.paid_now, invoice.cashbox, invoice.selling_location),
                         (OrderStatus.PAID, D("307.80"), D("37.80"), D("200.00"), self.cashbox, self.location))
        self.assertEqual([(line.item.item_code, line.quantity, line.line_total_amount) for line in invoice.lines.order_by("pk")],
                         [("D-BURG", D("2.000"), D("240.00")), ("A-CHEESE", D("2.000"), D("30.00"))])  # R1a's add-on row is unchanged
        self.assertEqual((get_cashbox_balance(self.cashbox), get_cashbox_balance(self.card)), (D("200.00"), D("107.80")))
        self.assertEqual(list(InvoiceSettlement.objects.values_list("invoice_id", "amount", "source")), [(invoice.pk, D("107.80"), "split")])
        self.assertEqual(walk_in_balance(), D("0.00"))
        figures = summary(open_shift_for(self.owner))  # the owner's shift is on the till; the card machine is in no shift (AN8)
        self.assertEqual(figures["groups"], {"sales": "200.00"})
        self.assertEqual(D(figures["expected"]), open_shift_for(self.owner).opening_float + D("200.00"))
        Projector().rebuild()
        self.assertTrue(gl.trial_balance()["balanced"])
        self.assertEqual({key: gl.control_balance(key, entity=self.main) for key in ("cash", "receivable", "sales", "vat_out")},
                         {"cash": D("307.80"), "receivable": D("0.00"), "sales": D("-270.00"), "vat_out": D("-37.80")})

    def test_270_with_vat_off_as_150_and_120(self):
        self.vat(False)
        order = self.burgers()
        self.split_from_screen(order, [(self.cashbox, "150"), (self.card, "120.00")])
        order.refresh_from_db()
        self.assertEqual((order.invoice.total_amount, order.invoice.paid_now), (D("270.00"), D("150.00")))
        self.assertEqual((get_cashbox_balance(self.cashbox), get_cashbox_balance(self.card)), (D("150.00"), D("120.00")))
        self.assertEqual(walk_in_balance(), D("0.00"))

    def test_three_ways_and_a_discount(self):
        bank = make_cashbox(cashbox_code="BANK", name_ar="البنك", kind=CashboxKind.BANK)
        order = self.burgers()
        # 307.80 less a 7.80 discount (after tax) = 300.00, as 100.00 three ways.
        self.split_from_screen(order, [(self.cashbox, "100"), (self.card, "100"), (bank, "100")], discount="7.80")
        order.refresh_from_db()
        self.assertEqual((order.invoice.total_amount, order.invoice.discount_amount), (D("300.00"), D("7.80")))
        self.assertEqual([get_cashbox_balance(box) for box in (self.cashbox, self.card, bank)], [D("100.00")] * 3)
        self.assertEqual(InvoiceSettlement.objects.count(), 2)

    def test_a_branch_bill_paid_two_ways_posts_to_that_branchs_cashboxes_and_books(self):
        Projector().rebuild()
        main_books = gl.trial_balance(entity=self.main)
        order = self.burgers(table=self.b_t1)
        self.work_in(None)
        self.split_from_screen(order, [(self.b_cashbox, "207.80"), (self.b_card, "100.00")])
        order.refresh_from_db()
        invoice = order.invoice
        self.assertEqual((invoice.selling_location, invoice.cashbox, invoice.total_amount), (self.b_location, self.b_cashbox, D("307.80")))
        self.assertEqual((get_cashbox_balance(self.b_cashbox), get_cashbox_balance(self.b_card)), (D("207.80"), D("100.00")))
        self.assertEqual((get_cashbox_balance(self.cashbox), get_cashbox_balance(self.card)), (D("0.00"), D("0.00")))
        Projector().rebuild()
        self.assertEqual(gl.trial_balance(entity=self.main), main_books)  # the main entity's books did not move
        self.assertTrue(gl.trial_balance(entity=self.branch)["balanced"])
        self.assertEqual({key: gl.control_balance(key, entity=self.branch) for key in ("cash", "receivable", "sales")},
                         {"cash": D("307.80"), "receivable": D("0.00"), "sales": D("-270.00")})

    def test_with_split_off_the_rows_are_not_read_and_the_bill_is_the_one_cashbox_bill(self):
        order = self.burgers()
        response = self.split_from_screen(order, [(self.cashbox, "200.00"), (self.card, "107.80")], split="", tendered="400")
        order.refresh_from_db()
        self.assertEqual((order.invoice.cashbox, order.invoice.paid_now), (self.cashbox, D("307.80")))
        self.assertFalse(CustomerPayment.objects.exists())
        self.assertContains(response, "الباقي للعميل: 92.20")  # the one-cashbox bill's change, as before

    def test_cancelling_the_paid_bills_invoice_puts_both_cashboxes_back(self):
        order = self.burgers()
        self.split_from_screen(order, [(self.cashbox, "200.00"), (self.card, "107.80")])
        order.refresh_from_db()
        cancel_posted_sales_invoice(order.invoice.pk, self.owner, "غلط")
        self.assertEqual((get_cashbox_balance(self.cashbox), get_cashbox_balance(self.card), walk_in_balance()), (D("0.00"), D("0.00"), D("0.00")))
        self.assertFalse(InvoiceSettlement.objects.exists())


class RefusedSplitBillTests(SplitBillSetup):
    def test_short_of_the_bill_for_a_walk_in(self):
        self.assert_refused(self.burgers(), [(self.cashbox, "200.00"), (self.card, "100.00")], "البيع الآجل محتاج عميل باسمه")

    def test_short_of_the_bill_for_a_named_customer_too(self):
        customer = make_customer(customer_code="C-T1", name="أستاذ سامح")
        self.assert_refused(self.burgers(customer=customer), [(self.cashbox, "200.00"), (self.card, "100.00")], "لازم يساوي الحساب بالظبط: 307.80")

    def test_more_than_the_bill(self):
        self.assert_refused(self.burgers(), [(self.cashbox, "200.00"), (self.card, "107.81")], "مجموع طرق الدفع أكبر من المطلوب")

    def test_one_row_only(self):
        self.assert_refused(self.burgers(), [(self.cashbox, "307.80")], "محتاج مبلغين على الأقل")

    def test_a_zero_amount_and_a_repeated_cashbox(self):
        order = self.burgers()
        self.assert_refused(order, [(self.cashbox, "307.80"), (self.card, "0")], "لازم يكون مبلغها أكبر من صفر")
        self.assert_refused(order, [(self.cashbox, "200.00"), (self.cashbox, "107.80")], "نفس الخزنة متكررة")
        self.assert_refused(order, [(self.cashbox, "407.80"), (self.card, "-100")], "اكتب رقم صحيح")

    def test_another_branchs_an_inactive_or_a_held_cashbox(self):
        order = self.burgers()
        self.assert_refused(order, [(self.cashbox, "200.00"), (self.b_card, "107.80")], "اختار لكل مبلغ خزنة من خزن فرع الطلب")
        cheques = make_cashbox(cashbox_code="CHQ", kind=CashboxKind.CHEQUES_IN)
        self.assert_refused(order, [(self.cashbox, "200.00"), (cheques, "107.80")], "اختار لكل مبلغ خزنة من خزن فرع الطلب")
        self.card.active = False
        self.card.save(update_fields=["active"])
        self.assert_refused(order, [(self.cashbox, "200.00"), (self.card, "107.80")], "اختار لكل مبلغ خزنة من خزن فرع الطلب")

    def test_another_branchs_cashbox_through_the_service(self):
        from django.core.exceptions import ValidationError

        order = self.burgers()
        before = self.everything()
        with self.assertRaisesMessage(ValidationError, "الخزنة دي تبع فرع تاني"):
            services.pay(order, self.owner, cashbox=self.cashbox, tenders=[(self.cashbox, D("200.00")), (self.b_card, D("107.80"))])
        self.assertEqual(self.everything(), before)

    def test_without_a_shift_nothing_is_made_or_posted(self):
        order = self.burgers()
        before = self.everything()
        response = self.split_from_screen(order, [(self.cashbox, "200.00"), (self.card, "107.80")], user=self.manager_a)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.everything(), before)
        self.assertFalse(Order.objects.get(pk=order.pk).invoice_id)


class SplitBoxScreenTests(SplitBillSetup):
    def page(self, order, lang="ar"):
        self.client.force_login(self.owner)
        return self.client.get(reverse("restaurant:order", args=[order.pk]), {"lang": lang}).content.decode()

    def test_the_bill_box_offers_the_split_rows_with_the_bill_including_vat(self):
        order = self.burgers()
        page = self.page(order)
        self.assertIn('data-split data-bill="307.80"', page)
        self.assertIn("ادفع بأكتر من طريقة", page)
        self.assertIn('name="split" value="1" data-split-toggle', page)
        self.assertEqual(len(re.findall(r'name="tender_amount_\d"', page)), 2)  # two cashboxes in the main hall: two rows
        rows = re.findall(r'<select name="tender_cashbox_(\d)">(.*?)</select>', page, re.S)
        self.assertIn(f'<option value="{self.cashbox.pk}" selected>', rows[0][1])  # the till (the owner's shift) first
        self.assertIn(f'<option value="{self.card.pk}" selected>', rows[1][1])  # then the card machine
        self.assertFalse(any(f'<option value="{self.b_card.pk}"' in options for _index, options in rows))  # never another branch's
        self.assertIn("الباقي", page)
        self.assertIn("?v=resto-r2b", page)
        english = self.page(order, "en")
        self.assertIn("Pay several ways", english)
        self.assertIn("Left", english)

    def test_one_cashbox_means_no_split_box(self):
        self.card.active = False
        self.card.save(update_fields=["active"])
        self.assertNotIn("data-split", self.page(self.burgers()))

    def test_the_script_and_styles_use_tokens_and_keep_44px_targets(self):
        script = open(finders.find("hesba/js/restaurant.js"), encoding="utf-8").read()
        self.assertIn("[data-split]", script)
        self.assertIn("pay.disabled = on && rest !== 0", script)
        styles = open(finders.find("hesba/css/restaurant.css"), encoding="utf-8").read()
        block = styles[styles.index("/* R2b (HG-110)"):]
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgb\(", block))  # tokens only, no new colour literal
        self.assertIn(".rs-split-toggle{min-height:44px}", block)

    def test_a_delivery_bill_shows_its_fee_with_vat_before_the_fee_item_exists(self):
        from master_data.models import Item

        from .models import OrderKind

        order = services.open_order(self.owner, kind=OrderKind.DELIVERY, address="المعادي", delivery_fee="20")
        services.add_item(order, self.tea, self.owner, 2)  # 30.00 + a 20.00 fee; VAT 4.20 + 2.80
        self.assertFalse(Item.objects.filter(item_code=services.FEE_ITEM_CODE).exists())
        self.assertIn('data-bill="57.00"', self.page(order))
        self.assertFalse(Item.objects.filter(item_code=services.FEE_ITEM_CODE).exists())  # the page wrote nothing
        self.split_from_screen(order, [(self.cashbox, "50.00"), (self.card, "7.00")])
        order.refresh_from_db()
        self.assertEqual((order.status, order.invoice.total_amount), (OrderStatus.PAID, D("57.00")))
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertEqual(CashboxMovement.objects.filter(cashbox=self.card).count(), 1)
