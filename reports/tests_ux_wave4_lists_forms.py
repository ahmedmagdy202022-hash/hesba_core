"""UX Wave 4 (lists and forms): the screens a shop uses every day, easier on a phone.

Lists: the search box stays in sight above the «فلترة» fold; purchases, collections and supplier payments read as the
shared two-line row instead of label:value cards; the usual state is quiet; an empty list offers its next step.
Forms: a summary at the top of a form that came back with errors, 48px inputs with 16px text on a phone, fields that
scroll clear of the sticky bars, and the invoice form in titled parts with its running total in reach.
Presentation only: these pin structure and the shared rules; posting, stock and money stay covered by their own suites.
"""

import pathlib
import re
from decimal import Decimal as D

from django import forms
from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from hesba_testing.factories import (make_cashbox, make_customer, make_draft_purchase_invoice, make_item, make_location, make_seeded_role,
                                     make_supplier, make_user, make_user_profile)
from permissions.models import RoleCode
from expenses.tests import COMMERCIAL_MODULES
from reports.tests_dashboard import prepared_client
from settings_core.templatetags.hesba_forms import error_fields, line_errors

TEMPLATES = pathlib.Path(settings.BASE_DIR) / "templates"
LISTS = ("master_data/list.html", "sales/list.html", "inventory/stock.html", "expenses/list.html", "purchases/list.html", "payments/list.html")


def read_static(path):
    with open(finders.find(path), encoding="utf-8") as handle:
        return handle.read()


def filter_form(html):
    """The list's GET form, from its opening tag to its close."""

    match = re.search(r'<form class="[^"]*op-filters[^"]*" method="get">.*?</form>', html, re.S)
    assert match, "no filter form"
    return match.group(0)


class OwnerTestCase(TestCase):
    def setUp(self):
        prepared_client(modules=COMMERCIAL_MODULES)  # expenses on
        user = make_user(username="ux4_lists_owner")
        make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(user)

    def page(self, name, **params):
        params.setdefault("lang", "ar")
        response = self.client.get(reverse(name), params)
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


class SearchInSightTests(OwnerTestCase):
    ROUTES = ("master_data:items", "master_data:customers", "master_data:suppliers", "sales:list", "inventory:stock",
              "purchases:list", "sales:payments", "purchases:payments", "expenses:list")

    def test_the_search_box_sits_before_the_fold_and_the_rest_inside_it(self):
        for name in self.ROUTES:
            with self.subTest(route=name):
                form = filter_form(self.page(name))
                fold = form.index('<details class="hs-filter" data-filter-fold')
                self.assertLess(form.index('name="q"'), fold)
                self.assertIn('enterkeyhint="search"', form[:fold])
                body = form[fold:]
                self.assertIn('<div class="hs-filter__body">', body)
                self.assertIn('type="submit"', body)
                # still one GET form: lang first, the same names as before
                self.assertLess(form.index('name="lang"'), form.index('name="q"'))

    def test_the_badge_counts_only_what_is_folded(self):
        searched = self.page("sales:list", q="SI")
        self.assertNotIn("data-filter-count", searched)
        self.assertNotIn("data-filter-active", searched)
        filtered = self.page("sales:list", q="SI", status="posted")
        self.assertIn('<span class="hs-filter__count" data-filter-count>1</span>', filtered)
        self.assertNotIn("data-filter-count", self.page("master_data:items", q="x"))
        self.assertIn("data-filter-count>1<", self.page("master_data:items", status="all"))
        self.assertIn("data-filter-count>2<", self.page("expenses:list", q="x", date_from="2026-01-01", date_to="2026-01-31"))

    def test_templates_never_label_cells_themselves(self):
        for template in LISTS:
            with self.subTest(template=template):
                source = (TEMPLATES / template).read_text(encoding="utf-8")
                self.assertNotRegex(source, r"<td[^>]*data-label")


class PurchasesAndPaymentsRowsTests(OwnerTestCase):
    def test_purchases_share_the_list_head_and_the_two_line_row(self):
        supplier = make_supplier(supplier_code="NILE", name="Nile Traders")
        make_draft_purchase_invoice(supplier=supplier, location=make_location(location_code="WH"), invoice_number="PI-UX4-1")
        html = self.page("purchases:list")
        head = re.search(r'<section class="op-title-row hs-head">.*?</section>', html, re.S).group(0)
        self.assertEqual(head.count('class="op-primary"'), 1)
        menu = re.search(r'<div class="hs-more__menu">.*?</details>', head, re.S).group(0)
        for hook in ("data-purchase-orders-link", "data-report-tools-slot"):
            self.assertIn(hook, menu)
        row = re.search(r"<tr data-purchase-row>.*?</tr>", html, re.S).group(0)
        self.assertIn('data-row="title"', row)
        self.assertIn('<td data-row="sub">Nile Traders</td>', row)
        self.assertIn('data-row="amount"', row)
        self.assertIn('<td data-row="meta"><span class="op-status op-status-draft">', row)
        self.assertIn("data-list-rows", html)

    def test_an_empty_purchase_list_offers_the_first_invoice(self):
        html = self.page("purchases:list")
        empty = re.search(r'<span class="hs-empty" data-empty-first>.*?</span>', html, re.S).group(0)
        self.assertIn(reverse("purchases:create"), empty)
        filtered = self.page("purchases:list", q="nothing-like-this")
        self.assertIn("data-empty-filtered", filtered)

    def test_collections_and_supplier_payments_read_as_rows(self):
        for name, create in (("sales:payments", "sales:payment_create"), ("purchases:payments", "purchases:payment_create")):
            with self.subTest(route=name):
                html = self.page(name)
                self.assertIn('<table class="op-table" data-list-rows>', html)
                head = re.search(r'<section class="op-title-row hs-head">.*?</section>', html, re.S).group(0)
                self.assertIn(reverse(create), head)
                self.assertIn("data-report-tools-slot", head)
                self.assertIn("data-empty-first", html)
        source = (TEMPLATES / "payments/list.html").read_text(encoding="utf-8")
        self.assertIn('<td class="op-cell-actions" data-row="action">', source)  # print and cancel stay together, 44px each

    def test_a_customer_who_owes_nothing_is_marked_quiet(self):
        make_customer(customer_code="ZERO", name="Zero Balance")
        html = self.page("master_data:customers")
        self.assertRegex(html, r'<td data-row="amount" data-party-balance data-zero>')


class ListSheetTests(SimpleTestCase):
    def test_the_shared_rules_are_there_and_use_tokens_only(self):
        css = read_static("hesba/css/lists.css")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css), [])
        for rule in (".op-filters:has(>.hs-filter){display:flex",
                     'table[data-list-rows] tr:has(>td[data-row="amount"])>td[data-row="title"]{max-width:calc(100% - 8em)}',
                     'table[data-list-rows] td[data-row="sub"]{flex:1 1 0}',
                     ".op-status:is(.op-status-posted,.op-status-completed){padding:0;background:transparent;color:var(--hs-muted)",
                     'table[data-list-rows] td[data-row="amount"][data-zero]{color:var(--hs-muted)',
                     ":is(.op-table,.md-table):not(.pos-table) thead th{background:transparent"):
            with self.subTest(rule=rule):
                self.assertIn(rule, css)
        # the phone row is still one tap: the title link's ::after covers the row
        self.assertIn('table[data-list-rows] td[data-row="title"] a::after{content:"";position:absolute;inset:0}', css)


class FormErrorSummaryTests(OwnerTestCase):
    def test_a_form_that_came_back_lists_what_to_fix_and_links_each_field(self):
        response = self.client.post(reverse("master_data:item_create") + "?lang=ar", {"item_name": "", "unit": ""})
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        summary = re.search(r'<div class="hs-error-summary" role="alert" data-error-summary>.*?</div>', html, re.S).group(0)
        self.assertIn('href="#id_item_name"', summary)
        self.assertIn('href="#id_unit"', summary)
        # the messages also stay beside their fields
        self.assertIn('<div class="md-field has-error">', html)

    def test_a_fresh_form_has_no_summary(self):
        for name in ("master_data:item_create", "master_data:customer_create", "sales:create", "purchases:create"):
            with self.subTest(route=name):
                self.assertNotIn("data-error-summary", self.page(name))

    def test_an_invoice_without_a_customer_says_so_at_the_top(self):
        make_location(location_code="SHOP", is_default=True)
        make_cashbox(cashbox_code="TILL", is_default=True)
        item = make_item(item_code="RICE", item_name="Rice", default_sale_price=D("10.00"))
        response = self.client.post(reverse("sales:create") + "?lang=en", {
            "lang": "en", "invoice_date": "2026-11-11", "customer": "", "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0",
            "lines-MIN_NUM_FORMS": "0", "lines-MAX_NUM_FORMS": "20", "lines-0-item": item.pk, "lines-0-quantity": "1",
            "lines-0-unit_sale_price": "10"})
        html = response.content.decode()
        self.assertIn("data-error-summary", html)
        self.assertIn('href="#id_customer"', html)


class ErrorFilterTests(SimpleTestCase):
    class Sample(forms.Form):
        name = forms.CharField()
        secret = forms.CharField(widget=forms.HiddenInput)
        note = forms.CharField(required=False)

    def test_only_visible_fields_with_errors_in_form_order(self):
        form = self.Sample(data={})
        form.is_valid()
        self.assertEqual([field.name for field in error_fields(form)], ["name"])
        self.assertEqual(error_fields(self.Sample()), [])  # unbound: nothing
        self.assertEqual(error_fields(None), [])

    def test_line_errors_number_lines_as_the_screen_does(self):
        LineSet = forms.formset_factory(self.Sample, extra=0)
        formset = LineSet(data={"form-TOTAL_FORMS": "2", "form-INITIAL_FORMS": "0", "form-0-name": "ok", "form-0-secret": "x",
                                "form-1-name": "", "form-1-secret": "x"})
        formset.is_valid()
        lines = line_errors(formset)
        self.assertEqual([(line["number"], line["field"].name) for line in lines], [(2, "name")])
        self.assertEqual(line_errors(None), [])


class InvoiceFormPartsTests(OwnerTestCase):
    def test_the_invoice_reads_in_titled_parts_and_draft_stays_the_first_submit(self):
        for name in ("sales:create", "purchases:create"):
            with self.subTest(route=name):
                html = self.page(name)
                form = re.search(r"<form class=\"op-form inv-form\".*?</form>", html, re.S).group(0)
                self.assertIn('data-inv-part="head"', form)
                self.assertIn('data-inv-part="lines"', form)
                dock = re.search(r'<div class="inv-dock" data-inv-dock>.*?</div>', form, re.S).group(0)
                self.assertIn('type="button" data-dock-review', dock)
                self.assertNotIn('type="submit"', dock)
                # Enter in a field submits with the first submit button: still «save draft», never «post»
                first_submit = re.search(r'<button[^>]*type="submit"[^>]*>', form).group(0)
                self.assertIn("data-save-draft", first_submit)

    def test_new_versions_are_linked(self):
        for template in ("sales/form.html", "purchases/form.html"):
            with self.subTest(template=template):
                source = (TEMPLATES / template).read_text(encoding="utf-8")
                self.assertIn("invoice_form.css' %}?v=w4-2", source)
                self.assertIn("invoice_form.js' %}?v=w4-2", source)
        js = read_static("hesba/js/invoice_form.js")
        self.assertIn("dockTotal.textContent = money(totalValue)", js)
        self.assertIn("[data-dock-review]", js)


class FormSheetTests(SimpleTestCase):
    def test_phone_inputs_are_large_and_focus_clears_the_bars(self):
        css = read_static("hesba/css/form_controls.css")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css[css.index("UX Wave 4 (forms)"):]), [])
        self.assertIn("html:has(:is(.hs-form,.inv-form)){scroll-padding-bottom:calc(var(--hs-tabbar-clear) + 96px", css)
        self.assertIn(".hs-section__grid :is(input:not([type=checkbox]):not([type=radio]),select,textarea){min-height:var(--hs-control-height-lg);font-size:16px}", css)
        self.assertIn(".hs-error-summary{", css)
        invoice = read_static("hesba/css/invoice_form.css")
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", invoice), [])
        self.assertIn(".inv-dock{bottom:calc(var(--hs-tabbar-clear) + env(safe-area-inset-bottom))}", invoice)
        self.assertIn(".inv-form .hs-pct-toggle{min-width:var(--hs-control-height);min-height:var(--hs-control-height)}", invoice)

    def test_the_summary_partial_is_shared(self):
        self.assertIn('{% include "partials/form_error_summary.html" with line_formset=None %}',
                      (TEMPLATES / "partials/form_sections.html").read_text(encoding="utf-8"))
        self.assertIn('{% include "partials/form_error_summary.html" %}', (TEMPLATES / "operations/_invoice_form.html").read_text(encoding="utf-8"))
