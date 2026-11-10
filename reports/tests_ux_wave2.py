"""UX Wave 2: the compact list rows, the «⋯» headers, the «فلترة» panels, the collection form that shows what is owed,
the item save that lands on the item, plain choice labels and status words, and the paged movement tables.
Presentation only; these pin the structure, while the figures, permissions and posting stay covered by their own suites."""

import re
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import CashboxDirection
from closing.models import Period
from hesba_testing.factories import (make_cashbox, make_cashbox_movement, make_customer, make_draft_sales_invoice, make_item, make_location,
                                     make_seeded_role, make_stock_movement, make_supplier, make_user, make_user_profile, stock_in)
from inventory.models import StockMovementType
from inventory.services import recalculate_item_average_cost
from master_data.models import Item
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.services import create_sales_draft, post_sales_invoice

TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def granted(username, *codes):
    """A user whose role allows exactly these permissions (seeded codes), nothing else."""

    from hesba_testing.factories import grant, make_role
    from permissions.models import Permission

    role = make_role(code=f"UX2-{username}")
    for code in codes:
        grant(role, Permission.objects.get(code=code))
    user = make_user(username=username)
    make_user_profile(user=user, role=role)
    return user


def read_static(path):
    with open(finders.find(path), encoding="utf-8") as handle:
        return handle.read()


class SharedLayerTests(TestCase):
    def test_the_list_sheet_is_loaded_everywhere_and_uses_tokens_only(self):
        css = read_static("hesba/css/lists.css")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css), [])
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", read_static("hesba/css/tokens.css")))
        self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])
        # the phone row: two lines, the whole row one tap, every link a 44px target
        self.assertIn('table[data-list-rows] td[data-row="title"] a::after{content:"";position:absolute;inset:0}', css)
        self.assertIn("table[data-list-rows] td a,.op-table tbody td a,.md-table tbody td a{display:inline-flex;align-items:center;min-height:var(--hs-control-height)}", css)

    def test_card_tables_leave_list_rows_alone_and_print_tools_go_into_the_menu(self):
        self.assertIn("table.op-table:not([data-list-rows])", read_static("hesba/js/table_cards.js"))
        tools = read_static("hesba/js/report_tools.js")
        self.assertIn("[data-report-tools-slot]", tools)
        self.assertIn("querySelectorAll('.hs-phone-only')", tools)  # the Excel file never carries «باقي» / «عليه»


class SalesScreensTests(TestCase):
    def setUp(self):
        prepared_client()
        Period.objects.create(period_code="P", name="p", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=5))
        self.owner = person(RoleCode.OWNER, "ux2_owner")
        self.client.force_login(self.owner)
        self.location = make_location(location_code="SHOP", is_default=True, is_receiving_location=True)
        self.cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        self.item = make_item(item_code="RICE", item_name="Rice", default_sale_price=D("100.00"))
        stock_in(self.item, self.location, 20, "60.00", movement_date=TODAY - timedelta(days=10))
        recalculate_item_average_cost(self.item)
        self.customer = make_customer(customer_code="KARIM", name="Karim Store")

    def posted(self, number="SI-UX2-1", paid="0.00"):
        invoice = create_sales_draft({"invoice_number": number, "invoice_date": TODAY, "customer": self.customer, "selling_location": self.location,
                                      "cashbox": self.cashbox, "paid_now": D(paid)}, [{"item": self.item, "quantity": D("2"), "unit_sale_price": D("100.00")}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        return invoice

    def test_the_compact_list_shows_number_customer_total_status_and_what_is_left(self):
        invoice = self.posted()
        html = self.client.get(reverse("sales:list"), {"lang": "ar"}).content.decode()
        row = re.search(r"<tr data-invoice-row>.*?</tr>", html, re.S).group(0)
        self.assertIn(f'<td data-row="title"><a href="{reverse("sales:detail", args=[invoice.pk])}?lang=ar"><bdi>SI-UX2-1</bdi></a></td>', row)
        self.assertIn('<td data-row="sub">Karim Store</td>', row)
        self.assertIn('<td data-row="amount">200.00</td>', row)
        self.assertIn('<span class="op-status op-status-posted">نهائية</span>', row)  # «نهائية», not «مُرحّل»
        self.assertIn(f'<td data-row="meta">{TODAY.day} ', row)  # «9 نوفمبر 2026»
        self.assertIn('data-invoice-outstanding data-row="meta"', row)  # still to collect: on the phone's second line
        self.assertIn("data-list-rows", html)
        self.assertNotIn("مُرحّل", html)

    def test_one_primary_the_rest_in_the_menu_and_filters_folded(self):
        html = self.client.get(reverse("sales:list"), {"lang": "ar", "q": "SI"}).content.decode()
        head = re.search(r'<section class="op-title-row hs-head">.*?</section>', html, re.S).group(0)
        self.assertEqual(head.count('class="op-primary"'), 1)
        self.assertIn("data-sales-new", head)
        menu = re.search(r'<div class="hs-more__menu">.*?</details>', head, re.S).group(0)
        self.assertIn(reverse("sales:pos"), menu)
        self.assertIn("pick=return", menu)
        self.assertIn("data-report-tools-slot", menu)
        self.assertIn('<details class="hs-filter" data-filter-fold data-filter-active>', html)
        self.assertIn('<span class="hs-filter__count" data-filter-count>1</span>', html)
        self.assertIn('<form class="op-filters" method="get">', html)  # the Excel export still reads the filters

    def test_a_search_with_no_match_says_so_and_offers_to_clear_it(self):
        html = self.client.get(reverse("sales:list"), {"lang": "ar", "q": "zzzz"}).content.decode()
        self.assertIn("مفيش نتايج للبحث ده", html)
        self.assertIn("امسح البحث", html)
        self.assertNotIn("لا توجد فواتير بيع مطابقة", html)

    def test_return_from_the_menu_picks_a_posted_invoice_then_opens_the_existing_return_form(self):
        invoice = self.posted()
        make_draft_sales_invoice(customer=self.customer, location=self.location, invoice_number="SI-DRAFT-9")
        html = self.client.get(reverse("sales:list"), {"lang": "ar", "pick": "return"}).content.decode()
        self.assertIn("data-pick-return", html)
        self.assertIn(f'href="{reverse("sales:return_create", args=[invoice.pk])}?lang=ar" data-pick-invoice', html)
        self.assertNotIn("SI-DRAFT-9", html)  # only posted invoices take a return
        # without sales.return_sale: no «مرتجع» anywhere, and ?pick=return is ignored (rows open the invoice)
        seller = granted("ux2_no_return", "sales.view_sales_invoices", "sales.create_sales_invoice")
        self.client.force_login(seller)
        page = self.client.get(reverse("sales:list"), {"lang": "ar", "pick": "return"}).content.decode()
        self.assertNotIn("data-pick-return", page)
        self.assertNotIn("data-more-return", page)
        self.assertNotIn("data-pick-invoice", page)
        self.assertIn(f'href="{reverse("sales:detail", args=[invoice.pk])}?lang=ar"', page)
        self.assertIn("SI-DRAFT-9", page)  # no posted-only filter either
        till = self.client.get(reverse("sales:pos"), {"lang": "ar"})
        if till.status_code == 200:
            self.assertNotContains(till, "data-pos-return")

    def test_the_till_menu_offers_return(self):
        self.assertContains(self.client.get(reverse("sales:pos"), {"lang": "ar"}), "data-pos-return")

    def test_invoice_page_totals_in_one_card_and_cancel_folded_at_the_bottom(self):
        invoice = self.posted()
        html = self.client.get(reverse("sales:detail", args=[invoice.pk]), {"lang": "ar"}).content.decode()
        self.assertIn('<dl class="hs-totals" data-invoice-totals>', html)
        head = re.search(r'<section class="op-title-row hs-head">.*?</section>', html, re.S).group(0)
        self.assertEqual(head.count('class="op-primary"'), 1)
        self.assertIn("data-new-return", head)
        self.assertIn("data-share-whatsapp", head)  # share and print in «⋯»
        self.assertNotIn("op-danger", head)
        zone = re.search(r'<details class="hs-danger-zone" data-cancel-invoice>.*?</details>', html, re.S).group(0)
        self.assertIn('name="reason" required', zone)
        self.assertIn(reverse("sales:cancel", args=[invoice.pk]), zone)
        self.assertGreater(html.index("data-cancel-invoice"), html.index("data-invoice-totals"))


class CollectionFormTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "ux2_collector")
        self.client.force_login(self.owner)
        make_cashbox(cashbox_code="TILL", is_default=True)
        self.customer = make_customer(customer_code="DUE", name="Due Co", opening_balance=D("5234.87"))

    def test_the_form_shows_what_the_customer_owes_and_a_chip_for_the_whole_amount(self):
        html = self.client.get(reverse("sales:payment_create"), {"lang": "ar", "customer": self.customer.pk}).content.decode()
        self.assertIn("مستحق عليه", html)
        self.assertIn("5,234.87", html)
        self.assertIn('data-due-fill data-amount="5234.87"', html)  # the raw figure goes into the box
        self.assertIn("المبلغ كله", html)
        self.assertIn(f'data-url="{reverse("parties:balance", args=["customer", 0])}"', html)
        self.assertIn("سجّل التحصيل", html)
        self.assertNotIn("تسجيل الحركة", html)
        self.assertIn("hesba/js/party_due.js", html)

    def test_without_a_customer_the_line_waits_hidden_and_the_choice_reads_choose(self):
        html = self.client.get(reverse("sales:payment_create"), {"lang": "ar"}).content.decode()
        self.assertRegex(html, r"data-party-due [^>]*hidden")
        self.assertIn('<option value="" selected>اختار …</option>', html)
        self.assertNotIn("---------", html)

    def test_a_reader_who_may_not_see_balances_gets_no_figure(self):
        collector = granted("ux2_collect_only", "sales.receive_customer_payment")
        self.client.force_login(collector)
        response = self.client.get(reverse("sales:payment_create"), {"customer": self.customer.pk})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertNotIn("5,234.87", html)
        self.assertNotIn("data-party-due", html)
        self.assertNotIn("party_due.js", html)
        self.assertEqual(self.client.get(reverse("parties:balance", args=["customer", self.customer.pk])).status_code, 403)  # the endpoint is not widened
        reader = granted("ux2_collect_and_report", "sales.receive_customer_payment", "reports.view_customer_report")
        self.client.force_login(reader)
        self.assertContains(self.client.get(reverse("sales:payment_create"), {"customer": self.customer.pk}), "5,234.87")

    def test_plain_errors_and_a_toast_that_names_the_collection(self):
        page = self.client.post(reverse("sales:payment_create") + "?lang=ar", {"lang": "ar", "customer": "", "amount": "", "cashbox": "", "payment_date": TODAY.isoformat()})
        self.assertContains(page, "اختار العميل اللي هتحصّل منه.")
        self.assertContains(page, "اكتب المبلغ.")
        from cashboxes.models import Cashbox

        response = self.client.post(reverse("sales:payment_create") + "?lang=ar", {"lang": "ar", "customer": self.customer.pk, "amount": "100.00",
                                                                                  "cashbox": Cashbox.objects.get(cashbox_code="TILL").pk, "payment_date": TODAY.isoformat()}, follow=True)
        html = response.content.decode()
        self.assertIn("data-saved-toast", html)
        self.assertIn("اتسجل تحصيل 100.00", html)
        self.assertIn("«Due Co»", html)
        self.assertIn("تحصيل تاني", html)
        self.assertRegex(response.redirect_chain[-1][0], r"saved=\d+")


class ItemsAndPartiesTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "ux2_items")
        self.client.force_login(self.owner)

    def test_new_item_form_title_empty_prices_and_choose_label(self):
        html = self.client.get(reverse("master_data:create", kwargs={"entity": "items"}), {"lang": "ar"}).content.decode()
        self.assertIn(">صنف جديد</h1>", html)
        sale = re.search(r'<input[^>]*name="default_sale_price"[^>]*>', html).group(0)
        self.assertNotIn('value="0', sale)
        self.assertNotIn("required", sale)  # empty counts as 0: no asterisk
        self.assertIn('<option value="" selected>اختار …</option>', html)
        self.assertNotIn("---------", html)

    def test_new_titles_follow_the_word_and_its_gender(self):
        from master_data.views import new_title

        self.assertEqual(new_title("items", "صنف / خدمة", "ar"), "صنف جديد")
        self.assertEqual(new_title("items", "صنف / خدمة", "en"), "New item")
        self.assertEqual(new_title("customers", "مريض", "ar"), "مريض جديد")
        self.assertEqual(new_title("cashboxes", "خزنة", "ar"), "خزنة جديدة")
        self.assertEqual(new_title("locations", "مخزن", "ar"), "مخزن جديد")

    def test_saving_an_item_names_it_and_lands_on_it(self):
        response = self.client.post(reverse("master_data:create", kwargs={"entity": "items"}) + "?lang=ar",
                                    {"item_name": "Tea UX2", "unit": "قطعة", "is_stock_tracked": "on", "active": "on"}, follow=True)
        item = Item.objects.get(item_name="Tea UX2")
        self.assertEqual(item.default_sale_price, D("0"))  # left empty: 0, as before
        self.assertRegex(response.redirect_chain[-1][0], rf"saved={item.pk}")
        html = response.content.decode()
        self.assertIn("اتسجل «Tea UX2»", html)
        self.assertIn("data-saved-toast", html)
        self.assertRegex(html, r"<tr data-saved>\s*<td data-row=\"meta\"><bdi>IT-")
        self.assertIn("+ صنف جديد", html)

    def test_the_quick_item_dialog_has_one_star_a_close_cross_and_empty_prices(self):
        make_location(location_code="SHOP", is_default=True)
        html = self.client.get(reverse("sales:create"), {"lang": "ar"}).content.decode()
        dialog = re.search(r'<dialog class="hs-quick-item".*?</dialog>', html, re.S).group(0)
        self.assertNotIn('<span aria-hidden="true">*</span>', dialog)  # form_required.css adds the one star
        self.assertIn("hs-quick-item__x", dialog)
        self.assertNotIn('value="0"', dialog)
        self.assertLess(dialog.index('name="barcode"'), dialog.index('name="default_sale_price"'))  # the full form's order

    def test_customers_list_shows_what_each_owes_and_names_link_to_the_account(self):
        customer = make_customer(customer_code="OWE", name="Owing Co", opening_balance=D("750.00"))
        html = self.client.get(reverse("master_data:customers"), {"lang": "ar"}).content.decode()
        self.assertIn("<th>عليه</th>", html)
        self.assertRegex(html, r'data-party-balance><span class="hs-phone-only">عليه</span>750.00</td>')
        self.assertIn(f'<td data-row="title"><a href="{reverse("parties:card", args=["customer", customer.pk])}?lang=ar" data-row-link>Owing Co</a></td>', html)
        self.assertIn("data-party-card", html)

    def test_a_filtered_list_with_no_match_says_so(self):
        html = self.client.get(reverse("master_data:customers"), {"lang": "ar", "q": "zzzz"}).content.decode()
        self.assertIn("مفيش نتايج للبحث ده", html)
        self.assertIn("امسح البحث", html)
        self.assertNotIn("لا توجد بيانات حتى الآن", html)

    def test_the_customer_page_leads_with_collect_and_icon_contacts(self):
        customer = make_customer(customer_code="CALL", name="Call Co", phone="01001234567")
        html = self.client.get(reverse("parties:card", args=["customer", customer.pk]), {"lang": "ar"}).content.decode()
        actions = re.search(r'<div class="hs-head__actions">.*?</div>', html, re.S).group(0)
        self.assertLess(actions.index("data-party-collect"), actions.index("data-party-new-invoice"))
        self.assertIn('class="op-primary" href="/sales/collections/new/', actions)
        self.assertIn('<div class="hs-icon-row" data-party-contact>', html)
        self.assertIn('<span class="hs-sr">', html)
        self.assertIn("op-summary-grid--compact", html)

    def test_suppliers_list_shows_what_each_is_owed(self):
        make_supplier(supplier_code="OWED", name="Owed Supplier", opening_balance=D("320.00"))
        html = self.client.get(reverse("master_data:suppliers"), {"lang": "ar"}).content.decode()
        self.assertIn("<th>ليه</th>", html)
        self.assertIn("320.00", html)


class ChoiceLabelAndStatusTests(TestCase):
    def setUp(self):
        prepared_client(modules="customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports")
        self.client.force_login(person(RoleCode.OWNER, "ux2_labels"))
        make_location(location_code="SHOP", is_default=True, is_receiving_location=True)
        make_cashbox(cashbox_code="TILL", is_default=True)

    def test_the_forms_the_audit_listed_read_choose_not_dashes(self):
        for name in ("sales:create", "purchases:create", "expenses:create", "sales:payment_create", "purchases:payment_create"):
            with self.subTest(route=name):
                html = self.client.get(reverse(name), {"lang": "ar"}).content.decode()
                self.assertNotIn(">---------<", html)
                self.assertIn("اختار …", html)
        for entity in ("items", "customers"):
            with self.subTest(entity=entity):
                html = self.client.get(reverse("master_data:create", kwargs={"entity": entity}), {"lang": "ar"}).content.decode()
                self.assertNotIn(">---------<", html)
        english = self.client.get(reverse("sales:payment_create"), {"lang": "en"}).content.decode()
        self.assertIn("Choose …", english)

    def test_status_words_are_plain_labels_only(self):
        from settings_core.display_labels import AR_LABELS

        self.assertEqual(AR_LABELS[("sales.salesinvoice", "status")]["posted"], "نهائية")
        self.assertEqual(AR_LABELS[("sales.salesinvoice", "status")]["draft"], "مسودة")
        self.assertEqual(AR_LABELS[("sales.customerpayment", "status")]["posted"], "نهائي")
        self.assertEqual(AR_LABELS[("purchases.purchaseinvoice", "status")]["posted"], "نهائية")
        from sales.models import SalesInvoiceStatus

        self.assertEqual(SalesInvoiceStatus.POSTED, "posted")  # the stored value is unchanged


class PagedMovementsTests(TestCase):
    def setUp(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "ux2_pages"))

    def test_item_stock_movements_come_twenty_at_a_time(self):
        location = make_location(location_code="SHOP", is_default=True, is_receiving_location=True)
        item = make_item(item_code="PAGED", item_name="Paged")
        for n in range(25):
            make_stock_movement(item, location, StockMovementType.PURCHASE_IN, "1", "5.00", movement_date=TODAY - timedelta(days=n))
        html = self.client.get(reverse("inventory:item_detail", args=[item.pk]), {"lang": "ar"}).content.decode()
        self.assertIn("data-pager", html)
        self.assertIn("1–20 من 25 سطر", html)
        second = self.client.get(reverse("inventory:item_detail", args=[item.pk]), {"lang": "ar", "page": 2}).content.decode()
        self.assertIn("21–25 من 25 سطر", second)

    def test_cashbox_movements_come_twenty_at_a_time(self):
        cashbox = make_cashbox(cashbox_code="PAGEBOX")
        for n in range(23):
            make_cashbox_movement(cashbox, CashboxDirection.IN, "10.00")
        html = self.client.get(reverse("cashboxes:detail", args=[cashbox.pk]), {"lang": "ar"}).content.decode()
        self.assertIn("1–20 من 23 سطر", html)
        self.assertIn("page=2", html)
