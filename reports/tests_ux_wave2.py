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
        # UX Wave 4 (lists): the search box sits outside the fold, so the badge counts only what is folded (the status)
        html = self.client.get(reverse("sales:list"), {"lang": "ar", "q": "SI", "status": "posted"}).content.decode()
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


class ReviewFixesTests(TestCase):
    """PR #344 review: the paged export, the chip that forgets a switched party, inactive parties, print, item edit,
    phone cost words, the till's «مرتجع», the empty «⋯» menu and the payment forms' balance line."""

    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "ux2_review")
        self.client.force_login(self.owner)

    def test_only_the_paged_table_is_gathered_from_every_page(self):
        location = make_location(location_code="SHOP", is_default=True, is_receiving_location=True)
        item = make_item(item_code="PAGED2", item_name="Paged two")
        for n in range(3):
            make_stock_movement(item, location, StockMovementType.PURCHASE_IN, "1", "5.00", movement_date=TODAY - timedelta(days=n))
        html = self.client.get(reverse("inventory:item_detail", args=[item.pk]), {"lang": "ar"}).content.decode()
        tables = re.findall(r"<table[^>]*>", html)
        paged = [tag for tag in tables if "data-paged" in tag]
        self.assertEqual(len(paged), 1)  # the movements, not the stock by location (nor the batches)
        self.assertLess(html.index("المخزون حسب الموقع"), html.index(paged[0]))
        cashbox = make_cashbox(cashbox_code="PAGED-BOX")
        box = self.client.get(reverse("cashboxes:detail", args=[cashbox.pk]), {"lang": "ar"}).content.decode()
        self.assertEqual(len([tag for tag in re.findall(r"<table[^>]*>", box) if "data-paged" in tag]), 1)
        tools = read_static("hesba/js/report_tools.js")
        self.assertIn("table[data-paged]", tools)
        self.assertIn("if (pagedOnly && !table.hasAttribute('data-paged')) return;", tools)

    def test_the_chip_amount_is_cleared_when_the_party_changes(self):
        script = read_static("hesba/js/party_due.js")
        self.assertIn("filled = amount.value;", script)
        self.assertIn("if (filled !== null && amount.value === filled)", script)

    def test_an_inactive_party_in_the_address_shows_no_balance(self):
        gone = make_customer(customer_code="GONE", name="Gone Co", opening_balance=D("900.00"), active=False)
        html = self.client.get(reverse("sales:payment_create"), {"lang": "ar", "customer": gone.pk}).content.decode()
        self.assertNotIn("900.00", html)
        self.assertRegex(html, r"data-party-due [^>]*hidden")
        supplier = make_supplier(supplier_code="GONE-S", name="Gone Supplier", opening_balance=D("800.00"), active=False)
        page = self.client.get(reverse("purchases:payment_create"), {"lang": "ar", "supplier": supplier.pk}).content.decode()
        self.assertNotIn("800.00", page)

    def test_the_supplier_payment_form_shows_what_is_owed_to_the_supplier(self):
        make_cashbox(cashbox_code="TILL", is_default=True)
        supplier = make_supplier(supplier_code="OWED-P", name="Owed Supplier", opening_balance=D("320.50"))
        html = self.client.get(reverse("purchases:payment_create"), {"lang": "ar", "supplier": supplier.pk}).content.decode()
        self.assertIn('data-kind="supplier"', html)
        self.assertIn("مستحق ليه", html)
        self.assertIn("320.50", html)
        self.assertIn('data-due-fill data-amount="320.50"', html)
        self.assertIn(f'data-url="{reverse("parties:balance", args=["supplier", 0])}"', html)
        self.assertIn("سجّل الدفعة", html)

    def test_a_credit_balance_reads_in_their_favour_and_hides_the_chip(self):
        make_cashbox(cashbox_code="TILL", is_default=True)
        customer = make_customer(customer_code="CREDIT", name="Credit Co", opening_balance=D("-150.00"))
        html = self.client.get(reverse("sales:payment_create"), {"lang": "ar", "customer": customer.pk}).content.decode()
        due = re.search(r"<div class=\"hs-due\" data-party-due.*?</div>", html, re.S).group(0)
        self.assertNotRegex(due, r"data-party-due [^>]*hidden")
        self.assertIn("ليه عندك <b><bdi>150.00", due)
        self.assertNotIn("مستحق عليه <b>", due)
        self.assertNotIn("-150", due)
        self.assertRegex(due, r'data-due-fill data-amount=""\s*hidden')
        supplier = make_supplier(supplier_code="CREDIT-S", name="Credit Supplier", opening_balance=D("-40.00"))
        page = self.client.get(reverse("purchases:payment_create"), {"lang": "ar", "supplier": supplier.pk}).content.decode()
        self.assertIn("ليك عنده <b><bdi>40.00", page)
        self.assertRegex(page, r'data-due-fill data-amount=""\s*hidden')

    def test_paper_hides_the_menu_the_filter_button_and_the_header_actions(self):
        css = read_static("hesba/css/report_tools.css")
        hidden = css[css.index("@media print"):].split("{display:none !important}")[0]
        for selector in (".hs-more", ".hs-filter>summary", ".hs-head__actions", "[data-pick-return]"):
            self.assertIn(selector, hidden)

    def test_empty_means_zero_on_a_new_item_only(self):
        item = make_item(item_code="EDIT-P", item_name="Priced", unit="قطعة", default_sale_price=D("12.00"), default_purchase_price=D("7.00"))
        url = reverse("master_data:edit", kwargs={"entity": "items", "pk": item.pk}) + "?lang=ar"
        page = self.client.get(url).content.decode()
        sale = re.search(r'<input[^>]*name="default_sale_price"[^>]*>', page).group(0)
        self.assertIn("required", sale)  # on edit the price is required, as before
        response = self.client.post(url, {"item_code": "EDIT-P", "item_name": "Priced", "unit": "قطعة", "default_sale_price": "",
                                          "default_purchase_price": "", "min_stock": "", "is_stock_tracked": "on", "active": "on"})
        self.assertEqual(response.status_code, 200)
        form = response.context["form"]
        self.assertIn("default_sale_price", form.errors)
        self.assertIn("default_purchase_price", form.errors)
        self.assertNotIn("min_stock", form.errors)
        item.refresh_from_db()
        self.assertEqual((item.default_sale_price, item.default_purchase_price), (D("12.00"), D("7.00")))
        created = self.client.post(reverse("master_data:create", kwargs={"entity": "items"}) + "?lang=ar",
                                   {"item_name": "Fresh UX2", "unit": "قطعة", "default_sale_price": "", "default_purchase_price": "",
                                    "is_stock_tracked": "on", "active": "on"})
        self.assertEqual(created.status_code, 302)
        fresh = Item.objects.get(item_name="Fresh UX2")
        self.assertEqual((fresh.default_sale_price, fresh.default_purchase_price), (D("0"), D("0")))

    def test_sales_lines_keep_their_cost_on_a_phone_for_cost_readers(self):
        Period.objects.create(period_code="P", name="p", start_date=TODAY - timedelta(days=30), end_date=TODAY + timedelta(days=5))
        location = make_location(location_code="SHOP", is_default=True, is_receiving_location=True)
        cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        item = make_item(item_code="RICE", item_name="Rice", default_sale_price=D("100.00"))
        stock_in(item, location, 20, "60.00", movement_date=TODAY - timedelta(days=10))
        recalculate_item_average_cost(item)
        invoice = create_sales_draft({"invoice_number": "SI-COST", "invoice_date": TODAY, "customer": make_customer(customer_code="K"), "selling_location": location,
                                      "cashbox": cashbox, "paid_now": D("0")}, [{"item": item, "quantity": D("2"), "unit_sale_price": D("100.00")}], self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        html = self.client.get(reverse("sales:detail", args=[invoice.pk]), {"lang": "ar"}).content.decode()
        self.assertIn('<td data-row="meta"><span class="hs-phone-only">تكلفة الوحدة</span>60', html)
        self.assertIn('<td data-row="meta"><span class="hs-phone-only">تكلفة</span>120.00</td>', html)
        seller = granted("ux2_no_cost", "sales.view_sales_invoices")
        self.client.force_login(seller)
        self.assertNotContains(self.client.get(reverse("sales:detail", args=[invoice.pk]), {"lang": "ar"}), "تكلفة الوحدة")

    def test_the_till_return_link_needs_the_sales_list_too(self):
        make_location(location_code="SHOP", is_default=True, is_selling_location=True)
        make_cashbox(cashbox_code="TILL", is_default=True)
        returner = granted("ux2_till_return", "sales.create_sales_invoice", "sales.return_sale")
        self.client.force_login(returner)
        till = self.client.get(reverse("sales:pos"), {"lang": "ar"})
        self.assertEqual(till.status_code, 200)
        self.assertNotContains(till, "data-pos-return")  # the link would open a list this cashier may not read
        both = granted("ux2_till_return_list", "sales.create_sales_invoice", "sales.return_sale", "sales.view_sales_invoices")
        self.client.force_login(both)
        till = self.client.get(reverse("sales:pos"), {"lang": "ar"})
        self.assertContains(till, "data-pos-return")
        self.assertContains(till, "pick=return")

    def test_a_menu_with_only_print_and_excel_waits_hidden_for_the_script(self):
        for name in ("customers", "suppliers", "categories", "cashboxes"):
            with self.subTest(name=name):
                html = self.client.get(reverse("master_data:list", kwargs={"entity": name}), {"lang": "ar"}).content.decode()
                self.assertIn('<details class="hs-more" data-more-actions hidden>', html)
        self.assertIn("more.hidden = false", read_static("hesba/js/report_tools.js"))
        self.assertIn(".hs-more[hidden]{display:none}", read_static("hesba/css/lists.css"))
        sales = self.client.get(reverse("sales:list"), {"lang": "ar"}).content.decode()
        self.assertIn('<details class="hs-more" data-more-actions>', sales)  # it has server links (the till, return)

    def test_messages_say_final_not_posted(self):
        from settings_core.ui_messages import EXACT

        for english in ("A sales invoice with posted return documents cannot be cancelled.", "Only posted sales invoices can be cancelled.",
                        "Sales returns require a posted source invoice.", "Purchase returns require a posted source invoice."):
            self.assertIn("نهائية", EXACT[english])
            self.assertNotIn("مرحّل", EXACT[english])


class ListFiguresAtOneQueryTests(TestCase):
    """PR #344 review: the balance and stock columns are read for the whole page at once, so a bigger page costs no more queries."""

    def setUp(self):
        prepared_client()
        self.client.force_login(person(RoleCode.OWNER, "ux2_queries"))
        location = make_location(location_code="SHOP", is_default=True, is_receiving_location=True)
        for n in range(15):
            make_customer(customer_code=f"QC-{n:02}", name=f"Customer {n:02}", opening_balance=D(n))
            make_supplier(supplier_code=f"QS-{n:02}", name=f"Supplier {n:02}", opening_balance=D(n))
            stock_in(make_item(item_code=f"QI-{n:02}", item_name=f"Item {n:02}"), location, n + 1, "2.00")

    def count(self, url, per):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        self.client.get(url, {"lang": "ar", "per": per})  # the page size is remembered once; measure the next look
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(url, {"lang": "ar", "per": per})
        return len(queries.captured_queries), len(response.context["rows"])

    def test_the_same_queries_for_ten_rows_or_a_hundred(self):
        for name, column in (("customers", "show_balance"), ("suppliers", "show_balance"), ("items", "show_stock")):
            with self.subTest(name=name):
                url = reverse("master_data:list", kwargs={"entity": name})
                self.assertTrue(self.client.get(url, {"lang": "ar"}).context[column])
                small, small_rows = self.count(url, 10)
                large, large_rows = self.count(url, 100)
                self.assertEqual(small_rows, 10)
                self.assertGreaterEqual(large_rows, 15)
                self.assertEqual(small, large)


from entities.tests_scope import _Group  # noqa: E402  (the two-entity group: a main shop and a branch)


class BranchListFiguresTests(_Group):
    """PR #344 review: the list figures are the account card's and the stock page's, entity by entity."""

    def in_entity(self, entity, fn):
        from entities import current

        token = current._current.set(entity)
        try:
            return fn()
        finally:
            current._current.reset(token)

    def test_page_balances_equal_the_card_balance_in_each_entity(self):
        from master_data.models import Customer, Supplier
        from parties.services import balance, balances

        idle = make_customer(customer_code="C-IDLE", name="بدون حركة", opening_balance=D("7.00"))
        for entity in (None, self.main, self.branch):
            with self.subTest(entity=entity):
                for kind, parties in (("customer", list(Customer.objects.all())), ("supplier", list(Supplier.objects.all()))):
                    page = self.in_entity(entity, lambda: balances(kind, parties))
                    one_by_one = self.in_entity(entity, lambda: {party.pk: balance(kind, party) for party in parties})
                    self.assertEqual(page, one_by_one)
                self.assertIn(idle.pk, page if kind == "customer" else self.in_entity(entity, lambda: balances("customer", [idle])))
        self.assertEqual(self.in_entity(self.branch, lambda: balances("customer", [self.customer]))[self.customer.pk], D("40.00"))

    def test_page_stock_equals_the_stock_read_in_each_entity(self):
        from inventory.models import StockMovement
        from inventory.services import get_item_location_stock_quantity, get_item_stock_quantity, get_items_stock_quantities
        from entities import scope as entity_scope

        spare = make_item(item_code="IT-NONE", item_name="بدون حركة")
        group = self.in_entity(None, lambda: get_items_stock_quantities([self.item, spare], entity_scope.scope(StockMovement.objects.all(), entity_scope.STOCK_MOVEMENT)))
        self.assertEqual(group, {self.item.pk: get_item_stock_quantity(self.item), spare.pk: get_item_stock_quantity(spare)})
        self.assertEqual(group[self.item.pk], D("40"))
        for key in ("main", "branch"):
            side = self.side[key]
            here = self.in_entity(side["entity"], lambda: get_items_stock_quantities([self.item], entity_scope.scope(StockMovement.objects.all(), entity_scope.STOCK_MOVEMENT)))
            self.assertEqual(here[self.item.pk], get_item_location_stock_quantity(self.item, side["location"]))
            self.assertEqual(here[self.item.pk], D("20"))

    def test_a_branch_user_sees_the_branch_balance_and_stock_on_the_lists(self):
        self.as_manager()
        items = self.client.get(reverse("master_data:items"), {"lang": "ar"})
        self.assertTrue(items.context["show_stock"])
        row = next(row for row in items.context["rows"] if row["object"].pk == self.item.pk)
        self.assertEqual(row["stock"], D("20"))  # the branch's store only, as on the item's stock page
        customers = self.client.get(reverse("master_data:customers"), {"lang": "ar"})
        self.assertTrue(customers.context["show_balance"])
        row = next(row for row in customers.context["rows"] if row["object"].pk == self.customer.pk)
        self.assertEqual(row["balance"], D("40.00"))  # the branch's ledger rows, no opening balance
        self.as_owner()
        items = self.client.get(reverse("master_data:items"), {"lang": "ar"})
        row = next(row for row in items.context["rows"] if row["object"].pk == self.item.pk)
        self.assertEqual(row["stock"], D("40"))  # the whole group
        self.assertEqual(next(row for row in self.client.get(reverse("master_data:customers"), {"lang": "ar"}).context["rows"]
                              if row["object"].pk == self.customer.pk)["balance"], D("100.00") + D("40.00") + D("60.00") - D("10.00") - D("5.00") - D("25.00"))
        self.as_owner(self.branch)
        row = next(row for row in self.client.get(reverse("master_data:customers"), {"lang": "ar"}).context["rows"] if row["object"].pk == self.customer.pk)
        self.assertEqual(row["balance"], D("40.00"))
