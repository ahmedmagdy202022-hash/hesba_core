"""R1: alternatives at the till (the «بدائل» sheet) and the demo pharmacy's reference list.

Unit tests use invented drug names; ``demo_data/drug_reference_demo.csv`` is the only place with real product names.
"""

import csv
import re
import tempfile
from collections import Counter
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

from django.db import connection, transaction
from django.forms.models import model_to_dict
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from barcode.services import item_catalog
from cashboxes.models import CashboxMovement
from hesba_testing.factories import make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, open_test_shift, stock_in
from inventory.models import StockMovement
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from permissions.services import user_has_permission
from reports.tests_dashboard import prepared_client
from sales.models import CustomerLedgerEntry, SalesInvoice
from settings_core import capabilities as caps

from . import services, till
from .demo import DEMO_FILE, DEMO_SOURCE, DEMO_VERSION, load_demo_reference
from .models import DrugReference
from .reference import fold

URL = reverse("pharmacy:till_alternatives")
POS = reverse("sales:pos")


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def reference(ref, name_en, name_ar, ingredient, strength, barcode="", active=True):
    return DrugReference.objects.create(source="test-list", source_ref=ref, name_en=name_en, name_ar=name_ar, name_key=fold(name_en or name_ar),
                                        active_ingredient=ingredient, ingredient_key=services.ingredient_key(ingredient), strength=strength,
                                        strength_key=services.strength_key(strength), barcode=barcode, is_active=active)


class TillSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client("commercial", "pharmacy")
        caps.set_capability_enabled(self.profile, "drugs", True)
        self.owner = person(RoleCode.OWNER, "till_owner")
        self.cashier = person(RoleCode.CASHIER, "till_cashier")
        self.shop = make_location(location_code="PH-SHOP", is_default=True)
        self.corner = make_location(location_code="PH-CORNER", name_ar="فرع الناصية")
        self.store = make_location(location_code="PH-STORE", name_ar="المخزن الخلفي", is_selling_location=False)
        self.cashbox = make_cashbox(cashbox_code="PH-TILL", is_default=True)
        self.customer = make_customer(customer_code="PH-CUS", name="عميل الصيدلية")
        # Invented drugs: one sold out at the shop, three alternatives, and one that is no item any more.
        self.wanted = self.drug("FAB-1", "فابرينول 500 مجم", "40.00", {}, "Fabrinol", "500 mg", "tablet", barcode="2950000009008")
        self.forte = self.drug("FAB-2", "فابرينول فورت 500 مجم", "30.00", {self.shop: "5"}, "fabrinol", "500mg", "tablet", rx=True)
        self.big = self.drug("FAB-3", "فابرينول 1000 مجم", "20.00", {self.shop: "2", self.store: "40"}, "Fabrinol", "1000 mg", "tablet",
                             barcode="2950000009015")
        self.syrup = self.drug("FAB-4", "فابرينول شراب", "18.00", {self.corner: "7", self.store: "9"}, "Fabrinol", "120 mg/5 ml", "syrup")
        self.gone = self.drug("FAB-5", "فابرينول قديم", "15.00", {self.shop: "3"}, "Fabrinol", "500 mg", "tablet")
        self.gone.active = False
        self.gone.save(update_fields=["active"])
        self.lonely = self.drug("HSZ-1", "هسبازول 20 مجم", "50.00", {self.shop: "4"}, "Hesbazole", "20 mg", "capsule")
        self.soap = make_item(item_code="SOAP-1", item_name="صابونة حسبة", default_sale_price=D("12.00"))
        stock_in(self.soap, self.shop, "10", "5.00")
        # The reference list: two of its rows are already items here (by name, by barcode); four are not.
        reference("R1", "Fabrinol Forte 500mg", "فابرينول فورت 500 مجم", "Fabrinol", "500 mg")
        reference("R2", "Bigfab 1000mg", "بيج فاب", "Fabrinol", "1000 mg", barcode="2950000009015")
        reference("R3", "Zentol 500mg", "زينتول 500 مجم", "Fabrinol", "500 mg")
        reference("R4", "Bexol 500mg", "بيكسول 500 مجم", "Fabrinol", "500 مجم")
        reference("R5", "Corvax 500mg", "كورفاكس 500 مجم", "Fabrinol", "500 mg")
        reference("R6", "Amberol 250mg", "أمبرول 250 مجم", "Fabrinol", "250 mg")
        reference("R7", "Dormol 500mg", "دورمول 500 مجم", "Fabrinol", "500 mg", active=False)
        reference("R8", "Otherol 500mg", "أوثيرول 500 مجم", "Otherine", "500 mg")
        self.client.force_login(self.cashier)

    def drug(self, code, name, price, stock, ingredient, strength, form, rx=False, barcode=""):
        item = make_item(item_code=code, item_name=name, default_sale_price=D(price), is_stock_tracked=True, barcode=barcode)
        for location, quantity in stock.items():
            stock_in(item, location, quantity, "10.00")
        recalculate_item_average_cost(item)
        services.save_drug(item, {"active_ingredient": ingredient, "strength": strength, "form": form, "prescription_only": rx}, self.owner)
        return item

    def ask(self, item, **params):
        return self.client.get(URL, {"item": item.pk if hasattr(item, "pk") else item, **params})


class GateTests(TillSetup):
    def test_the_till_permission_and_the_drug_database_are_both_needed(self):
        self.assertEqual(self.ask(self.wanted).status_code, 200)
        keeper = person(RoleCode.STOCK_KEEPER, "till_keeper")
        self.assertFalse(user_has_permission(keeper, till.PERMISSION))
        self.client.force_login(keeper)
        self.assertEqual(self.ask(self.wanted).status_code, 403)  # require_permission, like the till's other JSON endpoints
        self.client.logout()
        anonymous = self.ask(self.wanted)
        # The project's login middleware: the login page, which the till's script reads as a failed request (asked again).
        self.assertEqual(anonymous.status_code, 302)
        self.assertTrue(anonymous["Location"].startswith("/login/"), anonymous["Location"])
        self.client.force_login(self.cashier)
        caps.set_capability_enabled(self.profile, "drugs", False)
        closed = self.ask(self.wanted)
        # The shared capability gate answers first, with its HTML page: the till does not carry the sheet then anyway.
        self.assertEqual(closed.status_code, 403)
        self.assertNotIn("application/json", closed["Content-Type"])

    def test_only_get_and_only_an_active_item(self):
        self.assertEqual(self.client.post(URL, {"item": self.wanted.pk}).status_code, 405)
        self.assertEqual(self.ask(self.gone).status_code, 404)
        self.assertEqual(self.ask("abc").status_code, 404)
        self.assertEqual(self.client.get(URL).status_code, 404)

    def test_a_key_that_is_no_ascii_number_is_no_row_never_a_500(self):
        # "²" passes str.isdigit() but int() refuses it; "٣" would be read as 3; a long number overflows an integer key.
        for raw in ("²", "٣", "-1", "0", " 1", "99999999999", "2147483648"):
            with self.subTest(item=raw):
                self.assertEqual(self.ask(raw).status_code, 404)
        for name in ("location", "customer"):
            with self.subTest(name=name):
                answer = self.ask(self.wanted, **{name: "²"})
                self.assertEqual(answer.status_code, 200)
                self.assertEqual(answer.json()["location"], self.shop.pk)  # the till's default, as with no value
        self.assertEqual(self.ask(self.wanted, location="99999999999").json()["location"], self.shop.pk)
        self.assertEqual((till.as_pk("12"), till.as_pk(12), till.as_pk("²"), till.as_pk(None), till.as_pk("2147483647")), (12, 12, None, None, 2147483647))

    def test_the_till_carries_the_sheet_only_while_the_drug_database_is_on(self):
        open_test_shift(self.cashier, self.cashbox)
        page = self.client.get(POS)
        self.assertContains(page, 'data-pos-alt data-url="/pharmacy/till/alternatives/"')
        self.assertContains(page, "hesba/js/pos_alternatives.js")
        self.assertContains(page, "بدائل {name}")  # the sheet's words, in Arabic
        self.assertContains(page, 'data-pos-alt-title tabindex="-1"')  # the sheet opens on its heading, never on a row
        # The script asks about the items with drug data only: their ids, read in one query.
        drugs = page.context["alternatives_drugs"]
        self.assertEqual(drugs, sorted([self.wanted.pk, self.forte.pk, self.big.pk, self.syrup.pk, self.lonely.pk]))  # not the soap, not the inactive one
        self.assertContains(page, '<script id="hs-alt-drugs" type="application/json">')
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(till.drug_item_ids(), drugs)
        self.assertEqual(len(captured), 1)
        caps.set_capability_enabled(self.profile, "drugs", False)
        page = self.client.get(POS)
        self.assertNotContains(page, "data-pos-alt")
        self.assertNotContains(page, "pos_alternatives.js")
        self.assertNotContains(page, "hs-alt-drugs")


class ShapeTests(TillSetup):
    def test_stocked_alternatives_same_strength_first_with_price_stock_and_flags(self):
        answer = self.ask(self.wanted).json()
        self.assertEqual(answer["item"], {"id": self.wanted.pk, "name": "فابرينول 500 مجم", "drug": True, "on_hand": "0", "ingredient": "Fabrinol 500 mg"})
        self.assertEqual(answer["location"], self.shop.pk)
        # The same strength and form first although it is dearer; the syrup has nothing at the shop; the old one is no item.
        self.assertEqual([row["id"] for row in answer["stocked"]], [self.forte.pk, self.big.pk])
        self.assertEqual(answer["stocked"][0], {"id": self.forte.pk, "name": "فابرينول فورت 500 مجم", "strength": "500mg", "form": "tablet",
                                                "form_label": "أقراص", "price": "30.00", "on_hand": "5", "low": False, "same_strength": True,
                                                "prescription_only": True})
        self.assertEqual((answer["stocked"][1]["price"], answer["stocked"][1]["on_hand"], answer["stocked"][1]["low"], answer["stocked"][1]["same_strength"]),
                         ("20.00", "2", True, False))
        english = self.ask(self.wanted, lang="en").json()
        self.assertEqual(english["stocked"][0]["form_label"], "Tablets")

    def test_quantities_are_the_selling_locations_and_a_location_that_is_not_one_falls_back_to_the_till_default(self):
        corner = self.ask(self.wanted, location=self.corner.pk).json()
        self.assertEqual(corner["location"], self.corner.pk)
        self.assertEqual([(row["id"], row["on_hand"]) for row in corner["stocked"]], [(self.syrup.pk, "7")])  # 9 more in the back store do not count
        crafted = self.ask(self.wanted, location=self.store.pk).json()  # not a selling location: the till's default instead
        self.assertEqual(crafted["location"], self.shop.pk)
        self.assertEqual([(row["id"], row["on_hand"]) for row in crafted["stocked"]], [(self.forte.pk, "5"), (self.big.pk, "2")])
        self.assertEqual(self.ask(self.big, location=self.shop.pk).json()["item"]["on_hand"], "2")  # not 42: the store's 40 are elsewhere

    def test_the_unstocked_note_leaves_out_what_is_already_an_item_and_names_the_same_strength_first(self):
        answer = self.ask(self.wanted).json()
        # R1 has a stocked item's name, R2 a stocked item's barcode, R7 is switched off, R8 is another ingredient.
        self.assertEqual(answer["unstocked"], {"count": 4, "names": ["بيكسول 500 مجم", "زينتول 500 مجم", "كورفاكس 500 مجم"]})
        self.assertEqual(self.ask(self.wanted, lang="en").json()["unstocked"], {"count": 4, "names": ["Bexol 500mg", "Corvax 500mg", "Zentol 500mg"]})
        # The drug page's «بدائل مش عندك» goes by the same rule: the same rows, so the two screens never disagree.
        from master_data.models import Item

        from . import picker

        page = picker.unstocked_alternatives(Item.objects.select_related("drug").get(pk=self.wanted.pk))
        self.assertEqual(len(page), answer["unstocked"]["count"])
        self.assertEqual({row.name_en for row in page[:3]}, {"Bexol 500mg", "Corvax 500mg", "Zentol 500mg"})
        self.assertFalse({row.name_en for row in page} & {"Fabrinol Forte 500mg", "Bigfab 1000mg"})

    def test_an_item_with_no_drug_data_or_no_alternative(self):
        soap = self.ask(self.soap).json()
        self.assertEqual((soap["item"]["drug"], soap["stocked"], soap["unstocked"]), (False, [], {"count": 0, "names": []}))
        lonely = self.ask(self.lonely).json()
        self.assertEqual((lonely["item"]["drug"], lonely["stocked"], lonely["unstocked"]["count"]), (True, [], 0))

    def test_a_customer_on_a_price_list_sees_its_prices_and_the_queries_do_not_grow_with_the_alternatives(self):
        from pricing.models import CustomerPriceList, PriceList, PriceListItem

        caps.set_capability_enabled(self.profile, "price_lists", True)
        wholesale = PriceList.objects.create(code="WHOLE", name_ar="جملة", adjust_percent=D("-10"))
        PriceListItem.objects.create(price_list=wholesale, item=self.forte, price=D("25.50"))
        CustomerPriceList.objects.create(customer=self.customer, price_list=wholesale)
        answer = self.ask(self.wanted, customer=self.customer.pk).json()
        self.assertEqual([row["price"] for row in answer["stocked"]], ["25.50", "18.00"])  # named on the list; 10% off the rest

        def queries():
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.ask(self.wanted, customer=self.customer.pk).status_code, 200)
            return len(captured)

        few = queries()
        for number in range(8):
            self.drug(f"FAB-X{number}", f"فابرينول تجربة {number}", "33.00", {self.shop: "3"}, "Fabrinol", "500 mg", "tablet", barcode=f"29500000091{number}0")
            reference(f"RX{number}", f"Extrafab {number}", "", "Fabrinol", "500 mg", barcode=f"29500000092{number}0")
        self.assertEqual(len(self.ask(self.wanted, customer=self.customer.pk).json()["stocked"]), 10)
        self.assertEqual(queries(), few)


class EquivalenceTests(TillSetup):
    """Adding an alternative from the sheet is the till's ordinary add: the sale posts exactly what selling that item by hand posts."""

    def setUp(self):
        super().setUp()
        open_test_shift(self.cashier, self.cashbox)  # AN4: the till sells inside an open shift

    def sale_rows(self, item_id, price):
        data = {"line_count": "1", "customer": str(self.customer.pk), "location": str(self.shop.pk), "cashbox": str(self.cashbox.pk),
                "discount": "0", "tendered": "10.00", "print": "0", "item_0": str(item_id), "qty_0": "1", "price_0": str(price), "unit_0": "", "serial_0": ""}
        # Keys and links to the invoice differ between the two sales on PostgreSQL (a sequence does not roll back with
        # its savepoint), so the rows are compared by content: what was sold, moved, paid and owed.
        drop = {"id", "invoice", "sales_invoice", "sales_line", "created_at", "updated_at"}

        def clean(obj):
            return {key: value for key, value in model_to_dict(obj).items() if key not in drop}

        with transaction.atomic():
            savepoint = transaction.savepoint()
            response = self.client.post(POS, data)
            invoice = SalesInvoice.objects.get(status="posted")
            self.assertRedirects(response, f"/sales/pos/?lang=ar&last={invoice.pk}", fetch_redirect_response=False)
            rows = {
                "invoice": clean(invoice),
                "lines": [clean(line) for line in invoice.lines.order_by("pk")],
                "stock": [clean(row) for row in StockMovement.objects.filter(sales_invoice=invoice).order_by("pk")],
                "cash": [clean(row) for row in CashboxMovement.objects.filter(sales_invoice=invoice).order_by("pk")],
                "ledger": [clean(row) for row in CustomerLedgerEntry.objects.filter(sales_invoice=invoice).order_by("pk")],
            }
            transaction.savepoint_rollback(savepoint)
        self.assertFalse(SalesInvoice.objects.exists())
        return rows

    def test_selling_the_alternative_from_the_sheet_posts_the_same_rows_as_selling_it_by_hand(self):
        alternative = self.ask(self.wanted, customer=self.customer.pk, location=self.shop.pk).json()["stocked"][0]
        by_hand = next(row for row in item_catalog(sale_prices=True) if row["id"] == alternative["id"])  # what the till's own catalog holds
        self.assertEqual(D(alternative["price"]), D(by_hand["price"]))
        from_sheet = self.sale_rows(alternative["id"], alternative["price"])
        typed = self.sale_rows(by_hand["id"], by_hand["price"])
        self.assertEqual(from_sheet, typed)
        # And what those rows are: one box out of the shop, 10.00 into the drawer, 20.00 left on the customer's account.
        self.assertEqual([(row["item"], row["location"], row["movement_type"], row["quantity"]) for row in from_sheet["stock"]],
                         [(self.forte.pk, self.shop.pk, "sale_out", D("1.000"))])
        self.assertEqual([(row["cashbox"], row["direction"], row["amount"]) for row in from_sheet["cash"]], [(self.cashbox.pk, "in", D("10.00"))])
        self.assertEqual([(row["customer"], row["entry_type"], row["due_increase"]) for row in from_sheet["ledger"]], [(self.customer.pk, "sales_due", D("20.00"))])
        self.assertEqual((from_sheet["invoice"]["total_amount"], from_sheet["invoice"]["remaining_due"]), (D("30.00"), D("20.00")))


    def test_the_sheet_prices_are_what_the_till_itself_prices_the_item_at(self):
        """The sheet shows a price; the till adds the item at pos.js's own price (the page's price book, else the catalog)."""

        from pricing.models import CustomerPriceList, PriceList, PriceListItem
        from sales.pos import walk_in_customer

        caps.set_capability_enabled(self.profile, "price_lists", True)
        wholesale = PriceList.objects.create(code="WHOLE", name_ar="جملة", adjust_percent=D("-10"))
        PriceListItem.objects.create(price_list=wholesale, item=self.forte, price=D("25.50"))  # named on the list
        CustomerPriceList.objects.create(customer=self.customer, price_list=wholesale)  # the 1000 mg one: 10% off its price
        plain = make_customer(customer_code="PH-PLAIN", name="عميل من غير قايمة")
        page = self.client.get(POS).context
        book, catalog = page["price_book"], {row["id"]: D(row["price"]) for row in page["catalog"]}  # #hs-price-book, #hs-item-catalog

        def sheet(customer):
            return {row["id"]: D(row["price"]) for row in self.ask(self.wanted, customer=customer, location=self.shop.pk).json()["stocked"]}

        listed = book["lists"][book["customers"][str(self.customer.pk)]]["prices"]
        self.assertEqual(sheet(self.customer.pk), {self.forte.pk: D(listed[str(self.forte.pk)]), self.big.pk: D(listed[str(self.big.pk)])})
        self.assertEqual(sheet(self.customer.pk), {self.forte.pk: D("25.50"), self.big.pk: D("18.00")})
        # No list for this customer, the walk-in, or no customer at all: pos.js takes the catalog price, and so does the sheet.
        for who in (plain.pk, walk_in_customer().pk, ""):
            with self.subTest(customer=who):
                self.assertNotIn(str(who), book["customers"])
                self.assertEqual(sheet(who), {self.forte.pk: catalog[self.forte.pk], self.big.pk: catalog[self.big.pk]})
                self.assertEqual(sheet(who), {self.forte.pk: D("30.00"), self.big.pk: D("20.00")})

    def test_every_stocked_alternative_is_found_again_by_its_catalog_label(self):
        """The sheet adds an alternative by typing its catalog label into the till's name search (pos.js's byLabel)."""

        twin = self.drug("FAB-6", "فابرينول فورت 500 مجم", "31.00", {self.shop: "4"}, "Fabrinol", "500 mg", "tablet")  # forte's very name
        near = self.drug("FAB-7", "فابرينول فورت 500 مجم شريط", "32.00", {self.shop: "6"}, "Fabrinol", "500 mg", "tablet")
        catalog = self.client.get(POS).context["catalog"]
        self.assertEqual([label for label, count in Counter(row["label"] for row in catalog).items() if count > 1], [])  # whole catalog
        by_label = {row["label"]: row["id"] for row in catalog}
        label_of = {row["id"]: row["label"] for row in catalog}
        stocked = [row["id"] for row in self.ask(self.wanted, location=self.shop.pk).json()["stocked"]]
        self.assertTrue({self.forte.pk, twin.pk, near.pk, self.big.pk} <= set(stocked), stocked)
        self.assertNotEqual(label_of[self.forte.pk], label_of[twin.pk])  # the item code tells the two apart
        for item_id in stocked:
            with self.subTest(item=item_id):
                self.assertEqual(by_label[label_of[item_id]], item_id)


class SheetScriptTests(SimpleTestCase):
    """What only a browser shows (a USB scanner types then presses Enter), pinned in the script's code paths."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from django.contrib.staticfiles import finders

        with open(finders.find("hesba/js/pos_alternatives.js"), encoding="utf-8") as handle:
            cls.script = handle.read()

    def block(self, start):
        """The handler that starts at ``start``, up to its closing ``  });``."""

        at = self.script.index(start)
        return self.script[at:self.script.index("\n  });", at)]

    def test_the_sheet_retries_after_a_failure_and_counts_bigger_units(self):
        # Codex on PR #349: a network failure is remembered with its time and asked again, at once when back online
        self.assertIn("failed[key] = Date.now()", self.script)
        self.assertIn("window.addEventListener('online'", self.script)
        self.assertNotIn(".catch(function () { answers[key] = false; })", self.script)
        # a login page (HTML, after a signed-out redirect) makes .json() reject: caught as a failure that is asked again
        self.assertIn("return response.ok ? response.json() : null;", self.script)
        self.assertIn(".catch(function () { failed[key] = Date.now(); })", self.script)
        # a line in a bigger unit takes factor x quantity from the shelf (the till's own #hs-units catalog)
        self.assertIn("json('hs-units'", self.script)
        self.assertIn("num(qty.value) * factor > onHand", self.script)

    def test_the_focus_never_rests_where_a_scanners_enter_would_act(self):
        # Only the scan field and the sheet's heading are ever focused: never a row, «بدائل» or «شيله؟» (data-remove).
        self.assertEqual(set(re.findall(r"([\w.\]\[')]+)\.focus\(\)", self.script)), {"scan", "title"})
        self.assertNotIn("focusLine", self.script)
        self.assertIn("title.focus();", self.block("function show("))
        self.assertIn("focusScan();", self.block("sheet.addEventListener('close'"))  # Esc, the close button, a tap outside
        add = self.block("content.addEventListener('click'")
        self.assertTrue(add.rstrip().splitlines()[-1].strip().startswith("focusScan();"), add)  # after an add, the last word
        self.assertNotIn("data-remove", add)

    def test_no_key_reaches_the_till_behind_the_open_sheet(self):
        guard = self.block("window.addEventListener('keydown'")
        self.assertIn("event.stopPropagation();", guard)
        self.assertIn("event.key === 'F9' || event.key === 'F2'", guard)
        self.assertIn("}, true);", self.script[self.script.index(guard):][:len(guard) + 20])  # the capture phase, before pos.js's handler

    def test_only_drug_lines_are_asked_and_never_all_at_once(self):
        self.assertIn("json('hs-alt-drugs'", self.script)
        self.assertIn("if (!isDrug(id)) { return; }", self.script)
        self.assertIn("var MAX_IN_FLIGHT = 2;", self.script)
        self.assertIn("while (inFlight < MAX_IN_FLIGHT && queue.length)", self.script)
        # the customer's price list (not the customer) is in the key, and only when the page has a price book
        self.assertIn("function keyFor(id) { return placeKey(id) + '|' + priceList(); }", self.script)
        self.assertIn("book && book.customers && customer ? book.customers[customer.value] || '' : ''", self.script)

    def test_the_added_message_waits_for_the_cart_to_hold_the_item(self):
        add = self.block("content.addEventListener('click'")
        self.assertIn("var before = held(item.id);", add)
        self.assertIn("if (source.short && held(item.id) > before) {", add)
        self.assertLess(add.index("held(item.id) > before"), add.index("words.added"))


class DemoListTests(TestCase):

    def test_the_demo_list_imports_cleanly_and_every_drug_row_has_an_ingredient(self):
        with DEMO_FILE.open(encoding="utf-8") as handle:
            file_rows = list(csv.DictReader(handle))
        self.assertGreaterEqual(len(file_rows), 90)
        summary = load_demo_reference()
        self.assertEqual((summary.created, summary.skipped, summary.bad_barcodes, summary.bad_prices, summary.bad_dates, summary.ignored_columns),
                         (len(file_rows), 0, 0, 0, 0, []))
        rows = DrugReference.objects.filter(source=DEMO_SOURCE)
        self.assertEqual(rows.count(), len(file_rows))
        self.assertFalse(rows.exclude(source_version=DEMO_VERSION).exists())
        self.assertFalse(rows.filter(is_drug=True, ingredient_key="").exists())
        self.assertFalse(rows.filter(public_price__isnull=True).exists())
        self.assertTrue(all(code.startswith("2") for code in rows.exclude(barcode="").values_list("barcode", flat=True)))
        self.assertTrue(rows.filter(is_drug=False).exists())
        self.assertFalse(rows.filter(prescription_only=False).exists())  # only "1" (antibiotics) or unknown, never a guessed "no"
        again = load_demo_reference()
        self.assertEqual((again.created, rows.count()), (0, len(file_rows)))

    def test_the_demo_shelf_drugs_have_reference_siblings(self):
        from settings_core.management.commands.prepare_demo import Command as Prepare

        load_demo_reference()
        keys = set(DrugReference.objects.filter(source=DEMO_SOURCE).values_list("ingredient_key", flat=True))
        with_siblings = [name for name, (ingredient, *_rest) in Prepare.DRUGS.items() if services.ingredient_key(ingredient) in keys]
        self.assertTrue(6 <= len(with_siblings) <= 10, with_siblings)

    def test_only_the_pharmacy_demo_template_gets_the_list(self):
        from config.demo import DEMO_ACTIVITIES
        from config.demo_templates import demo_reference_list

        for activity, sub in DEMO_ACTIVITIES:
            with self.subTest(activity=activity, sub=sub):
                summary = demo_reference_list(activity, sub)
                count = DrugReference.objects.count()
                if (activity, sub) == ("commercial", "pharmacy"):
                    self.assertGreater(count, 0)
                    self.assertEqual(summary.source, DEMO_SOURCE)
                    DrugReference.objects.all().delete()
                else:
                    self.assertIsNone(summary)
                    self.assertEqual(count, 0)


class DemoTopUpTests(TestCase):
    def test_a_pharmacy_template_with_no_list_gets_it_on_its_next_top_up_once(self):
        from config import demo_templates as templates

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(templates, "base_template", return_value=Path(folder) / "demo.sqlite3"), \
                mock.patch.object(templates, "copy_database", side_effect=lambda source, work: Path(work).write_bytes(Path(source).read_bytes())), \
                mock.patch.object(templates, "_with_db", side_effect=lambda path, work: work()), \
                mock.patch.object(templates, "top_up_current_db") as today:
            pharmacy, grocery = templates.populated_template("commercial", "pharmacy"), templates.populated_template("commercial", "grocery")
            pharmacy.write_bytes(b"built before the list")
            grocery.write_bytes(b"built")
            self.assertEqual(templates.top_up_template("commercial", "grocery"), grocery)
            self.assertFalse(DrugReference.objects.exists())  # another business: no list
            self.assertEqual(templates.top_up_template("commercial", "pharmacy"), pharmacy)
            loaded = DrugReference.objects.filter(source=DEMO_SOURCE).count()
            self.assertGreaterEqual(loaded, 90)
            self.assertEqual(templates.top_up_template("commercial", "pharmacy"), pharmacy)  # idempotent: nothing more
            self.assertEqual(DrugReference.objects.count(), loaded)
            self.assertIsNone(templates.top_up_demo_reference_list("commercial", "pharmacy"))
            self.assertEqual(today.call_count, 3)  # today's trade and shift, on every top-up as before


class DemoCarPartsTests(TestCase):
    """The AI showcase's car parts (the invoice-photo sample) are kept off a pharmacy's shelf."""

    def test_a_pharmacy_demo_gets_no_car_parts_but_a_shop_still_does(self):
        from master_data.models import Item
        from settings_core import demo_ai

        profile = prepared_client("commercial", "pharmacy", modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports")
        caps.set_capability_enabled(profile, "ai_invoice_photo", True)
        owner = person(RoleCode.OWNER, "ai_owner")
        master = {"location": make_location(location_code="AI-SHOP", is_default=True), "cashboxes": [make_cashbox(cashbox_code="AI-TILL", is_default=True)]}
        self.assertFalse(demo_ai.seed(master, owner, modules=("purchases",)))
        self.assertFalse(Item.objects.filter(item_code__startswith="DEMO-AI-").exists())
        profile.sub_activity_slug = "retail"
        profile.save(update_fields=["sub_activity_slug"])
        self.assertTrue(demo_ai.seed(master, owner, modules=("purchases",)))
        self.assertEqual(Item.objects.filter(item_code__startswith="DEMO-AI-").count(), len(demo_ai.ITEMS))
