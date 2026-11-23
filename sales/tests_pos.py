"""POS-001: the cashier screen posts real invoices through the sales services."""

import re
from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.services import get_cashbox_balance
from hesba_testing.factories import open_test_shift, make_cashbox, make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.models import StockMovement
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from .models import CustomerLedgerEntry, SalesInvoice
from .pos import WALK_IN_CODE, walk_in_customer


POS = reverse("sales:pos")


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class PosTests(TestCase):
    def setUp(self):
        prepared_client()
        self.cashier = person(RoleCode.CASHIER, "pos_cashier")
        self.client.force_login(self.cashier)
        self.location = make_location(location_code="SHOP", is_default=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        open_test_shift(self.cashier, self.cashbox)  # AN4: the till sells inside an open shift
        self.shirt = make_item(item_code="SHIRT", barcode="5901234123457", default_sale_price="240.00")
        self.belt = make_item(item_code="BELT", default_sale_price="120.00")
        for item in (self.shirt, self.belt):
            stock_in(item, self.location, 10, "100.00", movement_date=timezone.localdate())
            recalculate_item_average_cost(item)

    def sell(self, lines, tendered, customer=None, discount="0", print_flag="0", follow=False):
        data = {
            "line_count": str(len(lines)),
            "customer": str((customer or walk_in_customer()).pk),
            "location": str(self.location.pk),
            "cashbox": str(self.cashbox.pk),
            "discount": discount,
            "tendered": tendered,
            "print": print_flag,
        }
        for index, (item, qty, price) in enumerate(lines):
            data.update({f"item_{index}": str(item.pk), f"qty_{index}": qty, f"price_{index}": price})
        return self.client.post(POS, data, follow=follow)

    def test_cash_sale_posts_moves_stock_and_cash_and_gives_change(self):
        response = self.sell([(self.shirt, "2", "240.00"), (self.belt, "1", "120.00")], tendered="700")
        invoice = SalesInvoice.objects.get()
        self.assertRedirects(response, f"/sales/pos/?lang=ar&last={invoice.pk}", fetch_redirect_response=False)
        self.assertEqual(invoice.status, "posted")
        self.assertEqual(invoice.invoice_number, f"POS-{timezone.localdate():%Y%m%d}-0001")
        self.assertEqual((invoice.total_amount, invoice.paid_now, invoice.remaining_due), (D("600.00"), D("600.00"), D("0.00")))
        self.assertEqual(invoice.customer.customer_code, WALK_IN_CODE)
        self.assertEqual(invoice.created_by, self.cashier)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("600.00"))
        self.assertEqual(StockMovement.objects.filter(sales_invoice=invoice).count(), 2)
        page = self.client.get(response.url)
        self.assertContains(page, "الباقي للعميل 100.00")
        self.assertContains(page, invoice.invoice_number)

    def test_numbers_follow_each_other_within_the_day(self):
        self.sell([(self.belt, "1", "120.00")], tendered="120")
        self.sell([(self.belt, "1", "120.00")], tendered="120")
        numbers = sorted(SalesInvoice.objects.values_list("invoice_number", flat=True))
        self.assertTrue(numbers[1].endswith("-0002"))

    def test_discount_and_too_large_discount(self):
        self.sell([(self.shirt, "1", "240.00")], tendered="200", discount="40")
        self.assertEqual(SalesInvoice.objects.get().total_amount, D("200.00"))
        self.sell([(self.shirt, "1", "240.00")], tendered="0", discount="500")
        self.assertEqual(SalesInvoice.objects.count(), 1)

    def test_walk_in_customer_cannot_buy_on_credit(self):
        response = self.sell([(self.shirt, "1", "240.00")], tendered="100", follow=True)
        self.assertContains(response, "البيع الآجل محتاج عميل باسمه")
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual(get_cashbox_balance(self.cashbox), D("0.00"))

    def test_named_customer_credit_sale_goes_to_their_account(self):
        customer = make_customer(customer_code="C-POS", name="Ali")
        self.sell([(self.shirt, "1", "240.00")], tendered="100", customer=customer)
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.paid_now, invoice.remaining_due, invoice.payment_status), (D("100.00"), D("140.00"), "partial"))
        self.assertEqual(CustomerLedgerEntry.objects.get(sales_invoice=invoice).due_increase, D("140.00"))

    def test_short_stock_keeps_nothing(self):
        before = StockMovement.objects.count()
        response = self.sell([(self.shirt, "50", "240.00")], tendered="12000", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SalesInvoice.objects.exists())
        self.assertEqual(StockMovement.objects.count(), before)
        self.assertEqual(get_cashbox_balance(self.cashbox), D("0.00"))
        self.assertContains(response, "op-message-error")

    def test_bad_lines_are_refused(self):
        for qty, price in (("0", "10"), ("-1", "10"), ("abc", "10"), ("1", "-5"), ("1", "NaN")):
            with self.subTest(qty=qty, price=price):
                self.sell([(self.belt, qty, price)], tendered="10")
        self.shirt.active = False
        self.shirt.save()
        self.sell([(self.shirt, "1", "240")], tendered="240")
        self.assertFalse(SalesInvoice.objects.exists())

    def test_empty_cart_is_refused(self):
        response = self.client.post(POS, {"line_count": "0", "location": self.location.pk, "cashbox": self.cashbox.pk, "tendered": "0"}, follow=True)
        self.assertContains(response, "السلة فاضية")
        self.assertFalse(SalesInvoice.objects.exists())

    def test_pay_and_print_opens_the_receipt_that_returns_to_the_till(self):
        from settings_core.models import ClientProfile
        from settings_core.setup_services import set_module_enabled

        set_module_enabled(ClientProfile.get_active(), "pdf_printing", True)
        response = self.sell([(self.belt, "1", "120.00")], tendered="120", print_flag="1")
        invoice = SalesInvoice.objects.get()
        self.assertEqual(response.url, f"/print/sales/{invoice.pk}/?lang=ar&format=receipt&autoprint=1&next=pos")
        receipt = self.client.get(response.url)
        self.assertContains(receipt, f'href="/sales/pos/?lang=ar&amp;last={invoice.pk}"')  # UX Wave 1: back to the after-sale banner
        self.assertContains(receipt, "afterprint")

    def test_with_printing_off_the_sale_returns_to_the_till(self):
        response = self.sell([(self.belt, "1", "120.00")], tendered="120", print_flag="1")
        self.assertTrue(response.url.startswith("/sales/pos/?lang=ar&last="))
        self.assertNotContains(self.client.get(POS), "pay_print")

    def test_refused_sale_restores_a_clean_cart_only(self):
        payload = '[{"id": %d, "qty": "1", "price": "240"}, {"id": "</script><script>alert(1)</script>", "qty": "1", "price": "1"}]' % self.shirt.pk
        response = self.client.post(POS, {"line_count": "1", "item_0": str(self.shirt.pk), "qty_0": "1", "price_0": "240", "customer": walk_in_customer().pk, "location": self.location.pk, "cashbox": self.cashbox.pk, "tendered": "10", "cart_json": payload})
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertEqual(response.context["cart_back"], [{"id": self.shirt.pk, "qty": "1.000", "price": "240.00"}])

    def test_screen_ships_catalog_without_cost_and_today_summary(self):
        self.sell([(self.belt, "2", "120.00")], tendered="240")
        page = self.client.get(POS)
        self.assertContains(page, "5901234123457")
        self.assertContains(page, "hesba/js/pos.js")
        self.assertNotContains(page, "100.0000")
        self.assertEqual(page.context["summary"]["count"], 1)
        self.assertEqual(page.context["summary"]["total"], D("240.00"))
        other = person(RoleCode.CASHIER, "pos_other")
        self.client.force_login(other)
        self.assertEqual(self.client.get(POS).context["summary"]["count"], 0)

    def test_permissions_and_module_gate(self):
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "pos_keeper"))
        self.assertEqual(self.client.get(POS).status_code, 403)
        owner = person(RoleCode.OWNER, "pos_owner")
        self.client.force_login(owner)
        self.assertContains(self.client.get(reverse("sales:list")), reverse("sales:pos"))
        # sales_operations is required for a commercial activity, so it cannot be switched off;
        # the gate still covers /sales/pos/ through the /sales/ prefix.
        from settings_core.module_gate import module_for_path

        self.assertEqual(module_for_path(POS), "sales_operations")


class EasierTillTests(TestCase):
    """AN6 (Ahmed: "شاشة الكاشير محتاجة تكون أسهل وأسلس"): tap-to-add tiles, one-tap cash, details tucked away."""

    setUp = PosTests.setUp
    sell = PosTests.sell

    def test_the_best_sellers_come_first_as_tiles(self):
        self.sell([(self.belt, "3", "120.00")], "360")
        page = self.client.get(POS, {"lang": "ar"})
        picks = page.context["quick_picks"]
        self.assertEqual(picks[0]["id"], self.belt.pk)  # sold the most lately
        self.assertIn(self.shirt.pk, [pick["id"] for pick in picks])  # topped up from the catalog
        html = page.content.decode()
        self.assertIn("data-pos-quick", html)
        self.assertIn('id="hs-quick-picks"', html)
        self.assertIn('data-cash="exact"', html)
        self.assertIn('<details class="pos-more">', html)
        self.assertIn('name="cashbox"', html)  # still posted with the sale


class CalmTillTests(TestCase):
    """UX Wave 4, part 2 (Ahmed: «شاشة الكاشير تسهّلها»): the till as a selling mode with one search field, compact cart
    rows and a navy pay sheet. Nothing the server reads may move out of the form or change order or name."""

    setUp = PosTests.setUp

    # Every control the server reads, in the order the form sends them before and after this change.
    ORDER = ["csrfmiddlewaretoken", "lang", "line_count", "cart_json", "print", "customer", "coupon", "redeem_points",
             "points_seen", "gift_code", "gift_amount", "location", "cashbox", "discount", "promotion_seen", "tendered", "pay_method"]

    def till(self, lang="ar"):
        return self.client.get(POS, {"lang": lang}).content.decode()

    def form_parts(self, html):
        """The named controls and the buttons inside the till's form, in document order."""
        from html.parser import HTMLParser

        class Parts(HTMLParser):
            def __init__(self):
                super().__init__()
                self.depth, self.names, self.buttons = 0, [], []

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "form" and "data-pos" in attrs:
                    self.depth = 1
                    return
                if not self.depth:
                    return
                if tag == "form":
                    self.depth += 1
                if tag in ("input", "select", "textarea") and attrs.get("name") and attrs.get("form") is None:
                    self.names.append(attrs["name"])
                if tag == "button":
                    self.buttons.append(attrs)

            def handle_endtag(self, tag):
                if tag == "form" and self.depth:
                    self.depth -= 1

        parser = Parts()
        parser.feed(html)
        return parser

    def test_every_field_the_server_reads_is_inside_the_form_in_the_same_order(self):
        from .pos_buttons import save_card_cashbox

        save_card_cashbox(make_cashbox(cashbox_code="CARD", name_ar="ماكينة الفيزا"))
        names = self.form_parts(self.till()).names
        for needed in ("csrfmiddlewaretoken", "lang", "line_count", "cart_json", "print", "customer", "location", "cashbox", "discount", "tendered", "pay_method"):
            with self.subTest(field=needed):
                self.assertEqual(names.count(needed), 1)
        self.assertEqual(names, [name for name in self.ORDER if name in names])  # nothing else is sent, and the order is kept

    def test_the_default_button_is_the_primary_pay_and_every_other_button_says_type_button(self):
        from settings_core.models import ClientProfile
        from settings_core.setup_services import set_module_enabled

        only = [b for b in self.form_parts(self.till()).buttons if b.get("type") == "submit"]
        self.assertEqual([b.get("data-pos-pay") for b in only], ["only"])  # no printing: one pay button, the primary
        self.assertIn("op-primary", only[0]["class"])
        set_module_enabled(ClientProfile.get_active(), "pdf_printing", True)
        buttons = self.form_parts(self.till()).buttons
        self.assertTrue(all("type" in button for button in buttons), [b for b in buttons if "type" not in b])
        submits = [button for button in buttons if button["type"] == "submit"]
        self.assertEqual(submits[0].get("data-pos-pay"), "print")  # Enter in «المدفوع» and F9 take the primary
        self.assertIn("op-primary", submits[0]["class"])
        self.assertEqual(sorted(button["data-pos-pay"] for button in submits), ["only", "print"])

    def test_one_field_carries_the_scanner_and_the_name_search(self):
        html = self.till()
        scan = re.search(r"<input[^>]*data-pos-scan[^>]*>", html).group(0)
        for attribute in ('id="pos-scan"', 'role="combobox"', 'aria-controls="pos-find-list"', 'aria-expanded="false"', "autofocus",
                          'placeholder="امسح الباركود أو اكتب اسم الصنف"'):
            self.assertIn(attribute, scan)
        self.assertRegex(html, r'<ul class="pos-find__list" id="pos-find-list" role="listbox"[^>]*data-pos-results hidden>')
        search = re.search(r"<input[^>]*data-pos-search[^>]*>", html).group(0)  # the tiles' and «بدائل»' add path stays
        self.assertIn("hidden", search)
        self.assertNotIn("name=", search)
        self.assertNotIn('list="pos-items"', html)  # the native datalist is gone
        self.assertNotIn('id="pos-items"', html)
        self.assertIn("data-pos-status", html)
        self.assertIn('id="hs-item-catalog"', html)

    def test_the_single_fields_rules_in_the_script(self):
        from django.contrib.staticfiles import finders

        with open(finders.find("hesba/js/pos.js"), encoding="utf-8") as handle:
            script = handle.read()
        enter = script[script.index("if (event.key !== 'Enter') { return; }"):]
        enter = enter[:enter.index("});")]
        # a row is taken only for a person's Enter on a list drawn for this very text, never the camera's synthetic one
        self.assertIn("event.isTrusted && results && !results.hidden && shownFor === scan.value && found[active] && byHand()", enter)
        # the exact serial, unit barcode and code come first; then the row; then «not found»
        self.assertLess(enter.index("bySerial[code]"), enter.index("else if (pick)"))
        self.assertLess(enter.index("byCode[code]"), enter.index("else if (pick)"))
        self.assertLess(enter.index("else if (pick)"), enter.index("words.not_found"))
        self.assertIn("search.dispatchEvent(new Event('change'", enter)  # the name-search path, as the tiles use
        self.assertIn("var FIND_DELAY = 150;", script)  # a scanner's burst never draws the list
        # Android keyboards compose: every event carries the whole word so far, and that is still a person typing
        self.assertIn("var composing = event.inputType === 'insertCompositionText';", script)
        self.assertIn("(!composing && event.data && event.data.length > 1)", script)
        self.assertIn("document.addEventListener('DOMContentLoaded'", script)  # the field gets the focus back after camera_scan.js moves it
        # «سلة جديدة» (and an offline sale, which clicks it) sets the walk-in customer without a change event: the
        # customer's name on the cart button and the pay sheet must follow, or the next sale shows the last customer
        self.assertIn("customer.value = customer.getAttribute('data-walk-in'); showCustomer(); render();", script)

    def test_the_cart_lines_keep_the_hooks_the_alternatives_sheet_reads(self):
        from django.contrib.staticfiles import finders

        with open(finders.find("hesba/js/pos.js"), encoding="utf-8") as handle:
            script = handle.read()
        render = script[script.index("function render()"):script.index("// TAX-001")]
        for hook in ("'data-item': String(row.id)", "'data-unit'", "'data-field': 'qty'", "'data-field': 'price'", "'data-remove': '1'", "'data-step': '-1'", "'data-step': '1'"):
            self.assertIn(hook, render)
        self.assertLess(render.index("pos-line__name"), render.index("pos-line__qty"))  # the first cell is the name: «بدائل» goes in it
        self.assertIn('<table class="pos-table">', self.till())  # not op-table: table_cards.js no longer stacks the lines as tall cards

    def test_the_till_hides_the_app_bars_and_no_other_page_does(self):
        html = self.till()
        self.assertIn('<body class="operations-page hs-till"', html)
        css = read_static("hesba/css/pos.css")
        self.assertIn("body.hs-till .hs-topbar,body.hs-till .hs-tabbar{display:none}", css)
        self.assertNotIn("hs-till", self.client.get(reverse("sales:list"), {"lang": "ar"}).content.decode())
        # the way out and the app's menu stay one tap away
        self.assertIn("data-pos-back", html)
        self.assertIn('data-shell-open aria-controls="hs-sidebar"', html)
        self.assertIn("data-lang-toggle", html)

    def test_every_capability_is_still_on_the_page(self):
        html = self.till()
        for hook in ("data-pos-customer-find", "data-pos-customer", "data-pos-discount", 'name="location"', 'name="cashbox"', "data-pos-clear",
                     "data-pos-shift", "data-pos-invoices", "data-pos-open-details=\"customer\"", "data-pos-open-details=\"discount\"",
                     "data-pos-open-details=\"place\"", "data-pos-tendered", 'data-cash="exact"', "data-pos-offline", "data-pos-paybar",
                     "data-pos-bar-total", "data-pos-change-row", "data-pos-credit-row", "data-pos-quick", "data-pos-menu"):
            with self.subTest(hook=hook):
                self.assertIn(hook, html)
        self.assertEqual(html.count("data-pos-clear"), 1)  # pos_offline.js and pos_quick.js click the first one
        # the warehouse and cashbox pickers live inside the till's form (PR #350)
        form = html[html.index("<form class=\"pos\""):html.index("</form>", html.index("<form class=\"pos\""))]
        self.assertIn('name="location"', form)
        self.assertIn('name="cashbox"', form)

    def test_the_pay_panel_never_lies_over_the_details_on_wide_screens(self):
        html = self.till()
        start = html.index('<aside class="pos-side">')
        side = html[start:html.index("</aside>", start)]
        region = side.index("data-pos-side-scroll")
        self.assertLess(region, side.index("data-pos-sheet"))
        self.assertLess(side.index('class="pos-today"'), side.index("data-pos-paybar"))  # the figures scroll with the details
        wide = read_static("hesba/css/pos.css")
        wide = wide[wide.index("/* Wide screens: two columns beside the sidebar."):]
        self.assertIn(".pos-side__scroll{flex:1 1 auto;min-height:0;", wide)
        self.assertIn("overflow-y:auto", wide[wide.index(".pos-side__scroll{"):][:400])
        self.assertIn(".pos-paybar{flex:none;position:static;", wide)  # the last row of the column, not sticky over it

    def test_changed_static_files_are_cache_busted(self):
        from django.conf import settings
        import pathlib

        templates = pathlib.Path(settings.BASE_DIR) / "templates"
        till = (templates / "sales/pos.html").read_text(encoding="utf-8")
        for link in ("pos.css' %}?v=w4-2", "pos.js' %}?v=w4-2", "pos_quick.css' %}?v=w4-2"):
            self.assertIn(link, till)
        self.assertIn("pos_quick.css' %}?v=w4-2", (templates / "sales/pos_buttons.html").read_text(encoding="utf-8"))

    def test_no_new_colour_literals(self):
        for sheet in ("hesba/css/pos.css", "hesba/css/pos_quick.css"):
            css = re.sub(r"/\*.*?\*/", "", read_static(sheet), flags=re.S)
            with self.subTest(sheet=sheet):
                self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
                self.assertNotRegex(css, r"\b(rgba?|hsla?)\(")


def read_static(path):
    from django.contrib.staticfiles import finders

    with open(finders.find(path), encoding="utf-8") as handle:
        return handle.read()
