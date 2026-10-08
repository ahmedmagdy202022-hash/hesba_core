"""HG-034: the entity being worked in is the data scope.

A group with a main shop and a second branch. Each has its own store and
cashbox and its own posted sale, purchase, collection and supplier payment.
A user who belongs to the branch only sees, prints, picks and acts on the
branch's records; the main entity's documents answer 404 to them, exactly as
a document that does not exist. The owner sees the whole group or, after
choosing an entity, that entity alone.
"""

from decimal import Decimal as D

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from entities import current
from entities.models import Entity, EntityMembership
from hesba_testing.factories import (
    add_purchase_line,
    add_sales_line,
    make_cashbox,
    make_customer,
    make_item,
    make_location,
    make_supplier,
    recalculate_invoice_totals,
    recalculate_purchase_totals,
    stock_in,
)
from inventory.services import recalculate_item_average_cost
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice, SupplierPayment
from purchases.services import post_purchase_invoice, record_supplier_payment
from reports.selectors import customer_report, profit_totals
from reports.tests_dashboard import prepared_client
from sales.forms import SalesDraftForm
from sales.models import CustomerPayment, SalesInvoice
from sales.services import create_sales_return, post_sales_invoice, record_customer_payment

from . import services
from .tests import person

ALL = "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,expenses,reports"


class _Group(TestCase):
    @classmethod
    def setUpTestData(cls):
        prepared_client("commercial", "retail", ALL)
        cls.owner = person(RoleCode.OWNER, "scope_owner")
        cls.main = Entity.objects.get(is_main=True)
        cls.branch = services.save_entity({"code": "BR2", "name_ar": "فرع المعادي", "activity_slug": "commercial", "sub_activity_slug": "retail"}, cls.owner)
        cls.today = timezone.localdate()
        cls.customer = make_customer(customer_code="C-SHARED", name="عميل مشترك", opening_balance=D("100.00"))
        cls.supplier = make_supplier(supplier_code="S-SHARED", name="مورد مشترك")
        cls.item = make_item(item_code="IT-1", item_name="صنف")
        cls.side = {}
        for key, entity, price in (("main", cls.main, "30.00"), ("branch", cls.branch, "50.00")):
            location = make_location(location_code=f"L-{key}", name_ar=f"مخزن {key}", entity=entity, is_selling_location=True)
            cashbox = make_cashbox(cashbox_code=f"C-{key}", name_ar=f"خزنة {key}", entity=entity)
            stock_in(cls.item, location, 20, "10.00")
            recalculate_item_average_cost(cls.item)
            sale = cls._sell(location, cashbox, price, f"SI-{key}")
            draft = SalesInvoice.objects.create(invoice_number=f"SD-{key}", invoice_date=cls.today, customer=cls.customer, selling_location=location, created_by=cls.owner)
            add_sales_line(draft, cls.item, 1, price)
            purchase = PurchaseInvoice.objects.create(invoice_number=f"PI-{key}", invoice_date=cls.today, supplier=cls.supplier, receiving_location=location, cashbox=cashbox, created_by=cls.owner)
            add_purchase_line(purchase, cls.item, 1, "10.00")
            recalculate_purchase_totals(purchase, paid_now=D("0.00"))
            post_purchase_invoice(purchase.pk, cls.owner)
            collection = record_customer_payment(f"CP-{key}", cls.today, cls.customer, cashbox, D("5.00"), user=cls.owner)
            payment = record_supplier_payment(f"SP-{key}", cls.today, cls.supplier, cashbox, D("4.00"), user=cls.owner)
            sales_return = create_sales_return(f"SR-{key}", cls.today, sale.pk, [{"source_line": sale.lines.get(), "quantity": D("1")}], "تالف", cls.owner)
            cls.side[key] = {
                "entity": entity, "location": location, "cashbox": cashbox, "sale": sale, "draft": draft,
                "purchase": purchase, "collection": collection, "payment": payment, "return": sales_return,
            }
        cls.manager = person(RoleCode.MANAGER, "scope_branch_manager")
        EntityMembership.objects.create(user=cls.manager, entity=cls.branch, is_default=True)

    @classmethod
    def _sell(cls, location, cashbox, price, number):
        invoice = SalesInvoice.objects.create(invoice_number=number, invoice_date=cls.today, customer=cls.customer, selling_location=location, cashbox=cashbox, paid_now=D("0.00"), created_by=cls.owner)
        add_sales_line(invoice, cls.item, 2, price)
        recalculate_invoice_totals(invoice, paid_now=D("10.00"))
        post_sales_invoice(invoice.pk, user=cls.owner)
        invoice.refresh_from_db()
        return invoice

    def as_manager(self):
        self.client.force_login(self.manager)

    def as_owner(self, entity=None):
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": entity.pk if entity else "", "next": "/dashboard/"})


class ListsTests(_Group):
    def test_a_branch_member_lists_only_the_branch(self):
        self.as_manager()
        for url, mine, theirs in (
            ("/sales/", "SI-branch", "SI-main"),
            ("/purchases/", "PI-branch", "PI-main"),
            ("/sales/collections/", "CP-branch", "CP-main"),
            ("/cashboxes/", "C-branch", "C-main"),
            ("/master-data/cashboxes/", "C-branch", "C-main"),
            ("/master-data/locations/", "L-branch", "L-main"),
        ):
            with self.subTest(url=url):
                page = self.client.get(url)
                self.assertEqual(page.status_code, 200)
                self.assertContains(page, mine)
                self.assertNotContains(page, theirs)

    def test_the_owner_sees_the_group_then_one_entity(self):
        self.as_owner()
        page = self.client.get("/sales/")
        self.assertContains(page, "SI-main")
        self.assertContains(page, "SI-branch")
        self.as_owner(self.main)
        page = self.client.get("/sales/")
        self.assertContains(page, "SI-main")
        self.assertNotContains(page, "SI-branch")


class TamperingTests(_Group):
    """Every document of another entity answers 404 — reading, printing or acting."""

    def assert_out_of_reach(self, mine, theirs):
        # Where the role may open its own entity's document, the other entity's
        # is a 404; where the role may not, both are a 403 and nothing leaks.
        self.assertIn(mine, (200, 403))
        self.assertEqual(theirs, 404 if mine == 200 else 403)

    def test_the_owner_working_in_the_branch_gets_404_for_the_main_shop(self):
        self.as_owner(self.branch)
        for name, key in (("printing:sales_invoice", "sale"), ("printing:purchase_invoice", "purchase"),
                          ("printing:sales_return", "return"), ("printing:customer_payment", "collection"),
                          ("printing:supplier_payment", "payment"), ("purchases:payments", None)):
            with self.subTest(view=name):
                if key is None:
                    page = self.client.get(reverse(name))
                    self.assertContains(page, "SP-branch")
                    self.assertNotContains(page, "SP-main")
                    continue
                self.assert_out_of_reach(self.client.get(reverse(name, args=[self.side["branch"][key].pk])).status_code,
                                         self.client.get(reverse(name, args=[self.side["main"][key].pk])).status_code)
        response = self.client.post(reverse("purchases:payment_cancel", args=[self.side["main"]["payment"].pk]), {"reason": "x"})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(SupplierPayment.objects.get(pk=self.side["main"]["payment"].pk).status, "posted")

    def test_reading_and_printing_another_entitys_documents(self):
        self.as_manager()
        theirs, mine = self.side["main"], self.side["branch"]
        for name, key in (
            ("sales:detail", "sale"), ("sales:return_create", "sale"), ("sales:return_detail", "return"),
            ("purchases:detail", "purchase"), ("purchases:return_create", "purchase"),
            ("printing:sales_invoice", "sale"), ("printing:purchase_invoice", "purchase"),
            ("printing:sales_return", "return"), ("printing:customer_payment", "collection"),
            ("printing:supplier_payment", "payment"), ("cashboxes:detail", "cashbox"),
        ):
            with self.subTest(view=name):
                self.assert_out_of_reach(self.client.get(reverse(name, args=[mine[key].pk])).status_code,
                                         self.client.get(reverse(name, args=[theirs[key].pk])).status_code)

    def test_acting_on_another_entitys_documents_changes_nothing(self):
        self.as_manager()
        theirs = self.side["main"]
        for name, key in (
            ("sales:post", "draft"), ("sales:cancel", "sale"), ("sales:payment_cancel", "collection"),
            ("sales:return_cancel", "return"), ("purchases:cancel", "purchase"),
            ("purchases:payment_cancel", "payment"),
        ):
            with self.subTest(view=name):
                response = self.client.post(reverse(name, args=[theirs[key].pk]), {"reason": "x", "reversal_date": self.today.isoformat()})
                self.assertIn(response.status_code, (403, 404))
        self.assertEqual(SalesInvoice.objects.get(pk=theirs["draft"].pk).status, "draft")
        self.assertEqual(SalesInvoice.objects.get(pk=theirs["sale"].pk).status, "posted")
        self.assertEqual(PurchaseInvoice.objects.get(pk=theirs["purchase"].pk).status, "posted")
        self.assertEqual(CustomerPayment.objects.get(pk=theirs["collection"].pk).status, "posted")
        self.assertEqual(SupplierPayment.objects.get(pk=theirs["payment"].pk).status, "posted")
        self.assertEqual(theirs["return"].__class__.objects.get(pk=theirs["return"].pk).status, "posted")

    def test_editing_another_entitys_cashbox_or_store(self):
        self.as_manager()
        for kind, key in (("cashboxes", "cashbox"), ("locations", "location")):
            with self.subTest(kind=kind):
                url = reverse("master_data:edit", args=[kind, self.side["main"][key].pk])
                self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_restricted_user_cannot_ask_for_the_consolidated_or_another_entitys_books(self):
        self.as_manager()
        for query in ("", f"?entity={self.main.pk}", "?entity="):
            with self.subTest(query=query):
                page = self.client.get(reverse("ledger:trial_balance") + query)
                if page.status_code == 403:
                    continue  # a role without the books never gets this far
                self.assertEqual(page.context["entity"], self.branch)
                self.assertNotContains(page, "كل الكيانات")


class FormChoicesTests(_Group):
    def test_document_forms_offer_and_accept_the_branchs_stores_and_cashboxes_only(self):
        token = current._current.set(self.branch)
        try:
            form = SalesDraftForm(lang="ar")
            self.assertEqual(list(form.fields["selling_location"].queryset), [self.side["branch"]["location"]])
            self.assertEqual(list(form.fields["cashbox"].queryset), [self.side["branch"]["cashbox"]])
            tampered = SalesDraftForm(
                {"invoice_number": "SI-X", "invoice_date": self.today.isoformat(), "customer": self.customer.pk,
                 "selling_location": self.side["main"]["location"].pk, "cashbox": self.side["main"]["cashbox"].pk, "paid_now": "0"},
                lang="ar",
            )
            self.assertFalse(tampered.is_valid())
            self.assertIn("selling_location", tampered.errors)
            self.assertIn("cashbox", tampered.errors)
        finally:
            current._current.reset(token)

    def test_a_new_cashbox_made_inside_the_branch_belongs_to_it(self):
        self.as_owner(self.branch)
        url = reverse("master_data:create", args=["cashboxes"])
        data = {"cashbox_code": "C-NEW", "name_ar": "خزنة جديدة", "name_en": "", "opening_balance": "0", "currency": "EGP", "active": "on"}
        # Naming another entity is refused; leaving it out files it under the branch.
        self.client.post(url, {**data, "entity": self.main.pk})
        self.assertFalse(Cashbox.objects.filter(cashbox_code="C-NEW").exists())
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Cashbox.objects.get(cashbox_code="C-NEW").entity, self.branch)


class FiguresTests(_Group):
    """Report and dashboard figures add up per entity, and to the group."""

    def in_entity(self, entity, fn):
        token = current._current.set(entity)
        try:
            return fn()
        finally:
            current._current.reset(token)

    def test_customer_balance_per_entity_and_group(self):
        def balance():
            return customer_report(self.customer)[0]["balance"]

        # Each sale: 2×price with 10 paid, a 5 collection, and one unit returned
        # (the return takes back due net of the share already paid in cash:
        # 45 of 50 in the branch, 25 of 30 in the main shop).
        branch = D("100.00") - D("10.00") - D("5.00") - D("45.00")
        main = D("100.00") + D("60.00") - D("10.00") - D("5.00") - D("25.00")  # opening balance lives here
        self.assertEqual(self.in_entity(self.branch, balance), branch)
        self.assertEqual(self.in_entity(self.main, balance), main)
        self.assertEqual(self.in_entity(None, balance), branch + main)

    def test_profit_per_entity_adds_up_to_the_group(self):
        def sales():
            return profit_totals(self.today, self.today)["sales"]

        branch, main, group = (self.in_entity(e, sales) for e in (self.branch, self.main, None))
        self.assertEqual(branch, D("50.00"))  # 2×50 sold, one returned
        self.assertEqual(main, D("30.00"))
        self.assertEqual(branch + main, group)

    def test_dashboard_cards_follow_the_entity(self):
        self.as_owner(self.branch)
        cards = {card["key"]: card["raw"] for card in self.client.get("/dashboard/").context["cards"]}
        self.assertEqual(cards["sales_today"], D("100.00"))
        self.as_owner()
        cards = {card["key"]: card["raw"] for card in self.client.get("/dashboard/").context["cards"]}
        self.assertEqual(cards["sales_today"], D("160.00"))

    def test_customer_report_screen_for_the_branch_member(self):
        self.as_manager()
        page = self.client.get(reverse("reports:customers") + f"?customer={self.customer.pk}")
        if page.status_code == 403:
            self.skipTest("this role has no customer report")
        row = page.context["rows"][0]
        self.assertEqual(row["opening_balance"], D("0"))
        self.assertEqual(row["balance"], D("40.00"))
        numbers = {entry.sales_invoice.invoice_number for entry in page.context["entries"] if entry.sales_invoice}
        self.assertEqual(numbers, {"SI-branch"})


class RoleMatrixTests(_Group):
    """Every seeded role, working in the branch: the main entity's sale is never theirs."""

    def test_no_role_inside_the_branch_reaches_the_main_entitys_sale(self):
        theirs, mine = self.side["main"]["sale"], self.side["branch"]["sale"]
        for code in (RoleCode.MANAGER, RoleCode.CASHIER, RoleCode.STOCK_KEEPER, RoleCode.ACCOUNTANT, RoleCode.SUPPORT):
            with self.subTest(role=code):
                user = person(code, f"matrix_{code}")
                EntityMembership.objects.create(user=user, entity=self.branch, is_default=True)
                self.client.force_login(user)
                for name in ("sales:detail", "printing:sales_invoice"):
                    allowed = self.client.get(reverse(name, args=[mine.pk])).status_code
                    blocked = self.client.get(reverse(name, args=[theirs.pk])).status_code
                    self.assertIn(allowed, (200, 403))
                    self.assertEqual(blocked, 404 if allowed == 200 else 403)
                self.assertNotContains(self.client.get("/sales/"), "SI-main", status_code=self.client.get("/sales/").status_code)


class OfflineSyncTests(_Group):
    """POS-003: a queued sale lands in the entity it was rung up in."""

    def sync(self, side):
        import json
        import uuid

        body = {
            "key": str(uuid.uuid4()), "recorded_at": timezone.now().isoformat(), "customer": "",
            "location": str(self.side[side]["location"].pk), "cashbox": str(self.side[side]["cashbox"].pk),
            "discount": "0", "tendered": "30", "lines": [{"id": self.item.pk, "qty": "1", "price": "30.00", "unit": None, "serial": None}],
        }
        return self.client.post(reverse("sales:pos_sync"), json.dumps(body), content_type="application/json").json()

    def test_the_owner_syncs_after_switching_entity_and_a_branch_cashier_cannot_use_the_main_till(self):
        self.as_owner(self.branch)
        self.assertEqual(self.sync("main")["status"], "posted")
        cashier = person(RoleCode.CASHIER, "scope_branch_cashier")
        EntityMembership.objects.create(user=cashier, entity=self.branch, is_default=True)
        self.client.force_login(cashier)
        self.assertEqual(self.sync("main")["status"], "refused")
        self.assertEqual(self.sync("branch")["status"], "posted")
