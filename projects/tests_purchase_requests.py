"""Contracting C7 (HG-125): purchase requests from the site, with approval: the rules, the draft purchase order, the screens.

The reference contract of ``tests_variation_baseline`` (its clock fixed on Wednesday 11 November 2026) with two catalogue
items: Portland cement in bags (شيكارة) and reinforcing steel in tonnes (طن). The site engineer asks for 200 bags of
cement and 5 t of steel; the owner, who buys, approves.
"""

import re
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from audit.models import AuditLog
from hesba_testing.factories import make_cashbox, make_item, make_location, make_supplier
from orders import purchasing
from orders.models import PurchaseOrder, PurchaseOrderLine
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice, PurchaseLine, SupplierLedgerEntry, SupplierPayment

from . import contract, purchase_requests as prq, services
from .models import Project, PurchaseRequest, PurchaseRequestLine, PurchaseRequestStatus as Status
from .tests_c2_ui import PRE_LABELLED
from .tests_contract import person
from .tests_documents import view_only
from .tests_variation_baseline import DAY, ReferenceContract, money_footprint

BANNED = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")


def footprint():
    """``money_footprint`` (sales, payments, customer ledger, stock, cashbox, journal, certificates) and the purchases side."""

    data = money_footprint()
    data.update({"purchase_invoices": PurchaseInvoice.objects.count(), "purchase_lines": PurchaseLine.objects.count(),
                 "supplier_payments": SupplierPayment.objects.count(), "supplier_ledger": SupplierLedgerEntry.objects.count()})
    return data


class RequestSetup(ReferenceContract):
    def setUp(self):
        super().setUp()
        self.engineer = person(RoleCode.SITE_ENGINEER, "c7_engineer")
        self.manager = person(RoleCode.MANAGER, "c7_manager")
        self.keeper = person(RoleCode.STOCK_KEEPER, "c7_keeper")
        self.cement = make_item(item_code="CEM-50", item_name="أسمنت بورتلاندي 50 كجم", unit="شيكارة")
        self.steel = make_item(item_code="STL-16", item_name="حديد تسليح 16 مم", unit="طن")
        self.supplier = make_supplier(supplier_code="S-BUILD", name="شركة مواد البناء المتحدة")
        self.before = footprint()

    def lines(self, cement="200", steel="5", **extra):
        rows = [{"item": str(self.cement.pk), "quantity": cement, "boq_line": str(self.a.pk), "note": "خرسانة الدور الثالث"},
                {"item": str(self.steel.pk), "quantity": steel, "boq_line": str(self.a.pk)}]
        return rows + extra.get("more", [])

    def draft(self, user=None, **kwargs):
        data = {"needed_by": (DAY + timedelta(days=10)).isoformat(), "note": "التوريد على الموقع", "lines": kwargs.pop("rows", None) or self.lines(**kwargs)}
        return prq.save_request(self.project, user or self.engineer, data)

    def submitted(self, user=None, **kwargs):
        request = self.draft(user, **kwargs)
        return prq.submit(self.project, request, user or self.engineer)

    def url(self, name, *args, project=None, lang=None):
        link = reverse(f"projects:{name}", args=[(project or self.project).pk, *args])
        return f"{link}?lang={lang}" if lang else link

    def assertNoMoneyMoved(self):
        self.assertEqual(footprint(), self.before)


# ---- the rules ----

class RuleTests(RequestSetup):
    def test_an_engineer_requests_200_bags_of_cement_and_5_tonnes_of_steel(self):
        request = self.draft()
        self.assertEqual((prq.label(request), request.status, request.requested_by, request.needed_by), (f"PR-{self.project.code}-01", Status.DRAFT, self.engineer, DAY + timedelta(days=10)))
        self.assertEqual(list(request.lines.values_list("item__item_code", "quantity", "unit", "boq_line__code", "requested_quantity")),
                         [("CEM-50", D("200.000"), "شيكارة", "A", None), ("STL-16", D("5.000"), "طن", "A", None)])
        prq.submit(self.project, request, self.engineer)
        request.refresh_from_db()
        self.assertEqual(request.status, Status.SUBMITTED)
        self.assertIsNotNone(request.submitted_at)
        self.assertEqual(prq.label(self.draft()), f"PR-{self.project.code}-02")
        self.assertEqual(list(AuditLog.objects.filter(object_type="projects.PurchaseRequest", object_id=str(request.pk)).values_list("action", flat=True).order_by("pk")),
                         ["save_purchase_request", "submit_purchase_request"])
        self.assertNoMoneyMoved()

    def test_a_catalogue_line_takes_the_items_own_unit(self):
        request = self.draft(rows=[{"item": str(self.steel.pk), "quantity": "5", "unit": "كجم"}])
        self.assertEqual(request.lines.get().unit, "طن")  # the purchase order counts in the item's unit, never a typed one
        free = self.draft(rows=[{"description": "خشب موسكي للشدات", "quantity": "3", "unit": "م³"}])
        self.assertEqual(list(free.lines.values_list("item", "description", "unit")), [(None, "خشب موسكي للشدات", "م³")])

    def test_the_requester_cannot_approve_their_own_request(self):
        """The manager holds both rights: he drafts, submits, and is still refused; someone else then approves."""

        request = self.submitted(self.manager)
        with self.assertRaisesMessage(ValidationError, "مينفعش توافق على طلب انت اللي طلبته"):
            prq.approve(self.project, request, self.manager, supplier=self.supplier)
        with self.assertRaisesMessage(ValidationError, "ده طلبك انت"):
            prq.reject(self.project, request, self.manager, "مش محتاجينه")
        request.refresh_from_db()
        self.assertEqual((request.status, request.purchase_order, request.decided_by), (Status.SUBMITTED, None, None))
        self.assertEqual(PurchaseOrder.objects.count(), 0)
        done = prq.approve(self.project, request, self.owner, supplier=self.supplier)
        self.assertEqual((done.status, done.decided_by), (Status.ORDERED, self.owner))

    def test_approving_makes_a_draft_purchase_order_with_exactly_those_quantities(self):
        request = self.submitted()
        done = prq.approve(self.project, request, self.owner, supplier=self.supplier)
        order = done.purchase_order
        self.assertEqual((done.status, done.decided_by, done.decision_note), (Status.ORDERED, self.owner, ""))
        self.assertEqual((order.status, order.supplier, order.created_by, order.expected_on), ("draft", self.supplier, self.owner, DAY + timedelta(days=10)))
        self.assertIn(f"PR-{self.project.code}-01", order.notes)
        self.assertEqual(list(PurchaseOrderLine.objects.filter(order=order).values_list("item__item_code", "quantity").order_by("pk")),
                         [("CEM-50", D("200.000")), ("STL-16", D("5.000"))])
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(prq.state(done), Status.ORDERED)
        self.assertTrue(AuditLog.objects.filter(action="approve_purchase_request", object_id=str(request.pk), after_data__purchase_order=order.number).exists())
        self.assertNoMoneyMoved()  # a purchase order posts nothing (HG-054)

    def test_changing_a_quantity_to_180_keeps_the_note(self):
        request = self.submitted()
        cement = request.lines.get(item=self.cement)
        done = prq.approve(self.project, request, self.owner, supplier=self.supplier, quantities={cement.pk: "180"}, note="الباقي موجود في المخزن")
        cement.refresh_from_db()
        self.assertEqual((cement.quantity, cement.requested_quantity), (D("180.000"), D("200.000")))
        self.assertEqual(done.decision_note, "«أسمنت بورتلاندي 50 كجم»: اتطلب 200 واتوافق على 180 شيكارة\nالباقي موجود في المخزن")
        self.assertEqual(PurchaseOrderLine.objects.get(order=done.purchase_order, item=self.cement).quantity, D("180.000"))
        self.assertEqual(PurchaseOrderLine.objects.get(order=done.purchase_order, item=self.steel).quantity, D("5.000"))
        self.assertIsNone(request.lines.get(item=self.steel).requested_quantity)  # unchanged, so nothing kept

    def test_rejecting_needs_a_reason_and_leaves_no_purchase_order(self):
        request = self.submitted()
        with self.assertRaisesMessage(ValidationError, "اكتب سبب الرفض."):
            prq.reject(self.project, request, self.owner, "  ")
        done = prq.reject(self.project, request, self.owner, "الكمية موجودة في مخزن الموقع")
        self.assertEqual((done.status, done.decision_note, done.purchase_order, done.decided_by), (Status.REJECTED, "الكمية موجودة في مخزن الموقع", None, self.owner))
        self.assertEqual(PurchaseOrder.objects.count(), 0)
        with self.assertRaisesMessage(ValidationError, "مش مستني موافقة"):
            prq.approve(self.project, done, self.owner, supplier=self.supplier)
        self.assertNoMoneyMoved()

    def test_cancelling_the_purchase_order_brings_the_request_back_to_approved(self):
        done = prq.approve(self.project, self.submitted(), self.owner, supplier=self.supplier)
        first = done.purchase_order
        purchasing.move(first, "cancelled", self.owner)
        done = PurchaseRequest.objects.select_related("purchase_order").get(pk=done.pk)
        self.assertEqual((done.status, prq.state(done), done.purchase_order_id), (Status.ORDERED, Status.APPROVED, first.pk))  # read, not written
        self.assertIn(done.pk, list(PurchaseRequest.objects.filter(prq.TO_ORDER).values_list("pk", flat=True)))
        with self.assertRaisesMessage(ValidationError, "اختار المورد"):
            prq.make_order(self.project, done, self.owner, supplier=None)
        again = prq.make_order(self.project, done, self.owner, supplier=self.supplier)
        self.assertNotEqual(again.purchase_order_id, first.pk)
        self.assertEqual((again.status, prq.state(again), again.purchase_order.status), (Status.ORDERED, Status.ORDERED, "draft"))
        first.refresh_from_db()
        self.assertEqual(first.status, "cancelled")
        with self.assertRaisesMessage(ValidationError, "مش مستني أمر شراء"):
            prq.make_order(self.project, again, self.owner, supplier=self.supplier)
        with self.assertRaisesMessage(ValidationError, "الغي أمر الشراء الأول"):
            prq.cancel(self.project, again, self.owner)
        purchasing.move(again.purchase_order, "cancelled", self.owner)
        cancelled = prq.cancel(self.project, PurchaseRequest.objects.get(pk=done.pk), self.owner, "المشروع اتأجل")
        self.assertEqual((cancelled.status, prq.state(cancelled)), (Status.CANCELLED, Status.CANCELLED))
        self.assertNoMoneyMoved()

    def test_free_text_lines_join_the_order_only_once_an_item_is_chosen(self):
        wood = {"description": "خشب موسكي للشدات", "quantity": "3", "unit": "م³"}
        sand = {"description": "رمل", "quantity": "10", "unit": "م³"}
        gravel = {"description": "زلط", "quantity": "12", "unit": "م³"}
        request = self.submitted(rows=[{"item": str(self.cement.pk), "quantity": "200"}, wood, sand, gravel])
        free = request.lines.get(description="رمل")
        washed = make_item(item_code="SND-1", item_name="رمل مغسول", unit="م³")
        graded = make_item(item_code="GRV-1", item_name="زلط فرز أول", unit="طن")
        done = prq.approve(self.project, request, self.owner, supplier=self.supplier,
                           items={free.pk: str(washed.pk), request.lines.get(description="زلط").pk: str(graded.pk)})
        self.assertEqual(list(PurchaseOrderLine.objects.filter(order=done.purchase_order).values_list("item__item_code", "quantity").order_by("pk")),
                         [("CEM-50", D("200.000")), ("SND-1", D("10.000")), ("GRV-1", D("12.000"))])
        self.assertIn("«رمل»: اتحدد له الصنف «رمل مغسول»\n", done.decision_note)  # same unit: nothing more to say
        # Another unit: the quantity now counts in tonnes, and the note says so instead of swapping the unit silently.
        self.assertIn("«زلط» (م³): اتحدد له الصنف «زلط فرز أول» (طن)، والكمية بقت بوحدة الصنف", done.decision_note)
        self.assertEqual(request.lines.get(description="زلط").unit, "طن")
        self.assertIn("مش في أمر الشراء (من غير صنف): خشب موسكي للشدات", done.decision_note)
        only_free = self.submitted(rows=[wood])
        approved = prq.approve(self.project, only_free, self.owner)
        self.assertEqual((approved.status, approved.purchase_order), (Status.APPROVED, None))
        with self.assertRaisesMessage(ValidationError, "مفيش ولا سطر عليه صنف"):
            prq.make_order(self.project, approved, self.owner, supplier=self.supplier)

    def test_refusals_in_words_in_both_languages(self):
        other = services.save_project({"name": "مشروع تاني", "customer": self.customer}, self.owner)
        foreign = contract.save_boq_line(other, self.owner, {"code": "Z", "description": "دهانات", "unit": "م²", "quantity": "100", "rate": "50"})
        cases = [
            ([], "ضيف سطر واحد على الأقل", "Add at least one line"),
            ([{"item": str(self.cement.pk), "quantity": "0"}], "السطر 1: الكمية لازم تكون رقم أكبر من صفر.", "Line 1: the quantity must be a number above zero."),
            ([{"item": str(self.cement.pk), "quantity": "كتير"}], "السطر 1: الكمية", "Line 1: the quantity"),
            ([{"quantity": "5", "note": "بسرعة"}], "السطر 1: اختار الصنف أو اكتب وصفه.", "Line 1: choose the item or describe it."),
            ([{"item": "999999", "quantity": "5"}], "السطر 1: الصنف ده مش موجود", "Line 1: that item does not exist"),
            ([{"item": str(self.cement.pk), "quantity": "5", "boq_line": str(foreign.pk)}], "السطر 1: اختار بند مقايسة", "Line 1: choose an item of this project's"),
            ([{"item": str(self.cement.pk), "quantity": "5"}, {"item": str(self.cement.pk), "quantity": "1"}], "موجود في سطرين", "is on two lines"),
            ([{"description": f"صنف {n}", "quantity": "1"} for n in range(16)], "15 سطر بالكتير", "15 lines at most"),
        ]
        for rows, arabic, english in cases:
            for lang, text in (("ar", arabic), ("en", english)):
                with self.subTest(text=text), self.assertRaisesMessage(ValidationError, text):
                    prq.save_request(self.project, self.engineer, {"lines": rows}, lang=lang)
        with self.assertRaisesMessage(ValidationError, "مش مظبوط"):
            prq.save_request(self.project, self.engineer, {"needed_by": "بكرة", "lines": self.lines()})
        self.assertEqual(PurchaseRequest.objects.count(), 0)
        request = self.submitted()
        with self.assertRaisesMessage(ValidationError, "رجّعه مسودة الأول"):
            prq.save_request(self.project, self.engineer, {"lines": self.lines()}, request)
        prq.reopen(self.project, request, self.engineer)
        edited = prq.save_request(self.project, self.engineer, {"lines": self.lines(cement="150")}, request)
        self.assertEqual(edited.lines.get(item=self.cement).quantity, D("150.000"))
        with self.assertRaisesMessage(ValidationError, "مش مستني موافقة"):
            prq.approve(self.project, edited, self.owner, supplier=self.supplier)

    def test_a_refusal_from_orders_undoes_the_whole_approval(self):
        request = self.submitted()
        cement = request.lines.get(item=self.cement)
        with self.assertRaisesMessage(ValidationError, "اختار المورد"):
            prq.approve(self.project, request, self.owner, supplier=None, quantities={cement.pk: "180"})
        self.steel.active = False
        self.steel.save(update_fields=["active"])
        with self.assertRaisesMessage(ValidationError, "اختار الصنف."):  # orders.purchasing.add_line refuses an inactive item
            prq.approve(self.project, request, self.owner, supplier=self.supplier, quantities={cement.pk: "180"})
        request.refresh_from_db()
        cement.refresh_from_db()
        self.assertEqual((request.status, request.purchase_order, request.decision_note), (Status.SUBMITTED, None, ""))
        self.assertEqual((cement.quantity, cement.requested_quantity), (D("200.000"), None))
        self.assertEqual((PurchaseOrder.objects.count(), PurchaseOrderLine.objects.count()), (0, 0))

    def test_cancelling_a_draft_or_a_submitted_request_and_a_closed_project(self):
        draft = self.draft()
        self.assertEqual(prq.cancel(self.project, draft, self.engineer).status, Status.CANCELLED)
        request = self.submitted()
        with self.assertRaises(PermissionDenied):
            prq.cancel(self.project, request, self.keeper)
        self.project.status = "done"
        self.project.save(update_fields=["status"])
        for call in (lambda: self.draft(), lambda: prq.approve(self.project, request, self.owner, supplier=self.supplier),
                     lambda: prq.reject(self.project, request, self.owner, "لا")):
            with self.subTest(call=call), self.assertRaisesMessage(ValidationError, "المشروع ده خلص أو اتلغى."):
                call()

    def test_who_may_do_what(self):
        self.assertEqual([prq.can_view(u) for u in (self.engineer, self.owner, self.manager, self.keeper)], [True, True, True, True])
        self.assertEqual([prq.can_draft(u) for u in (self.engineer, self.owner, self.manager, self.keeper)], [True, True, True, False])
        # The stock keeper holds purchases.create_purchase_invoice but not the project right: he reads only.
        self.assertEqual([prq.can_decide(u) for u in (self.engineer, self.owner, self.manager, self.keeper)], [False, True, True, False])
        request = self.submitted()
        for call in (lambda: prq.approve(self.project, request, self.keeper, supplier=self.supplier), lambda: prq.reject(self.project, request, self.keeper, "لا"),
                     lambda: prq.approve(self.project, request, self.engineer, supplier=self.supplier), lambda: self.draft(self.keeper)):
            with self.subTest(call=call), self.assertRaises(PermissionDenied):
                call()
        self.assertEqual(PurchaseRequest.objects.get(pk=request.pk).status, Status.SUBMITTED)

    def test_the_counts_of_money_and_stock_are_unchanged_throughout(self):
        request = self.submitted()
        approved = prq.approve(self.project, request, self.owner, supplier=self.supplier, quantities={request.lines.first().pk: "180"})
        purchasing.move(approved.purchase_order, "cancelled", self.owner)
        prq.make_order(self.project, PurchaseRequest.objects.get(pk=request.pk), self.owner, supplier=self.supplier)
        prq.reject(self.project, self.submitted(), self.owner, "مش وقته")
        prq.cancel(self.project, self.draft(), self.engineer)
        self.assertEqual(PurchaseOrder.objects.count(), 2)
        self.assertNoMoneyMoved()


# ---- the screens ----

class ScreenTests(RequestSetup):
    def test_the_engineer_drafts_and_submits_and_the_owner_approves_through_the_screens(self):
        self.client.force_login(self.engineer)
        tab = self.client.get(self.url("purchase_requests"))
        self.assertContains(tab, "data-new-request")
        self.assertContains(tab, "data-empty-none")
        self.assertEqual(self.client.get(self.url("purchase_request_new")).status_code, 200)
        response = self.client.post(self.url("purchase_request_new"), {
            "lang": "ar", "needed_by": (DAY + timedelta(days=7)).isoformat(), "note": "للدور الثالث",
            "item": [str(self.cement.pk), str(self.steel.pk), ""], "description": ["", "", ""], "unit": ["", "", ""],
            "quantity": ["200", "5", ""], "boq_line": [str(self.a.pk), "", ""], "note": ["", "", ""]})
        request = PurchaseRequest.objects.get()
        self.assertRedirects(response, self.url("purchase_request", request.pk, lang="ar"), fetch_redirect_response=False)
        self.assertEqual(request.lines.count(), 2)
        page = self.client.get(self.url("purchase_request", request.pk)).content.decode()
        for marker in ("data-submit", "data-request-edit", "data-cancel-form", "data-no-money"):
            self.assertIn(marker, page)
        self.assertNotIn("data-approve-form", page)
        self.assertEqual(self.client.post(self.url("purchase_request", request.pk), {"lang": "ar", "action": "submit"}).status_code, 302)
        page = self.client.get(self.url("purchase_request", request.pk)).content.decode()
        self.assertIn('data-request-state="submitted"', page)
        self.assertIn("data-decide-only", page)
        self.assertEqual(self.client.post(self.url("purchase_request", request.pk), {"action": "approve", "supplier": str(self.supplier.pk)}).status_code, 403)
        self.assertEqual(self.client.post(self.url("purchase_request", request.pk), {"action": "reject", "reason": "لا"}).status_code, 403)
        self.client.force_login(self.owner)
        page = self.client.get(self.url("purchase_request", request.pk)).content.decode()
        self.assertIn("data-approve-form", page)
        self.assertIn("data-reject-form", page)
        cement = request.lines.get(item=self.cement)
        steel = request.lines.get(item=self.steel)
        response = self.client.post(self.url("purchase_request", request.pk), {"lang": "ar", "action": "approve", "supplier": str(self.supplier.pk),
                                                                             f"q_{cement.pk}": "180", f"q_{steel.pk}": "5", "note": ""})
        self.assertEqual(response.status_code, 302)
        request.refresh_from_db()
        self.assertEqual(request.status, Status.ORDERED)
        page = self.client.get(self.url("purchase_request", request.pk)).content.decode()
        self.assertIn(request.purchase_order.number, page)
        self.assertIn("data-request-order-link", page)
        self.assertIn("اتطلب 200", page)
        self.assertNoMoneyMoved()

    def test_a_refusal_is_shown_in_words_with_what_was_typed(self):
        self.client.force_login(self.engineer)
        page = self.client.post(self.url("purchase_request_new"), {"lang": "ar", "item": [""], "description": ["سقالات معدن"], "unit": ["عدد"],
                                                                   "quantity": ["صفر"], "boq_line": [""], "note": [""]})
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-form-error")
        self.assertContains(page, "السطر 1: الكمية لازم تكون رقم أكبر من صفر.")
        self.assertContains(page, 'value="سقالات معدن"')
        request = self.submitted()
        self.client.force_login(self.owner)
        page = self.client.post(self.url("purchase_request", request.pk), {"lang": "ar", "action": "approve", "supplier": ""})
        self.assertContains(page, "اختار المورد اللي هيتعمل له أمر الشراء.")
        page = self.client.post(self.url("purchase_request", request.pk), {"lang": "ar", "action": "reject", "reason": ""})
        self.assertContains(page, "اكتب سبب الرفض.")

    def test_the_requester_with_both_rights_sees_no_approval_form(self):
        request = self.submitted(self.manager)
        self.client.force_login(self.manager)
        page = self.client.get(self.url("purchase_request", request.pk)).content.decode()
        self.assertIn("data-not-yours", page)
        self.assertNotIn("data-approve-form", page)
        page = self.client.post(self.url("purchase_request", request.pk), {"lang": "ar", "action": "approve", "supplier": str(self.supplier.pk)})
        self.assertContains(page, "مينفعش توافق على طلب انت اللي طلبته")
        self.assertEqual(PurchaseRequest.objects.get(pk=request.pk).status, Status.SUBMITTED)

    def test_a_stock_keeper_reads_only(self):
        request = self.submitted()
        self.client.force_login(self.keeper)
        tab = self.client.get(self.url("purchase_requests"))
        self.assertEqual(tab.status_code, 200)
        self.assertContains(tab, "data-view-only")
        for absent in ("data-new-request", "data-my-requests", "data-all-requests"):
            self.assertNotContains(tab, absent)
        page = self.client.get(self.url("purchase_request", request.pk)).content.decode()
        for form in ("data-approve-form", "data-reject-form", "data-submit-form", "data-cancel-form", "data-order-form"):
            self.assertNotIn(form, page)
        self.assertEqual(self.client.get(self.url("purchase_request_new")).status_code, 403)
        for action in ("approve", "reject", "submit", "cancel", "order", "reopen"):
            with self.subTest(action=action):
                self.assertEqual(self.client.post(self.url("purchase_request", request.pk), {"action": action, "supplier": str(self.supplier.pk), "reason": "لا"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("projects:purchase_requests_all")).status_code, 200)
        self.assertEqual(PurchaseRequest.objects.get(pk=request.pk).status, Status.SUBMITTED)
        self.assertEqual(PurchaseOrder.objects.count(), 0)

    def test_a_reader_reads_and_a_user_without_rights_is_refused(self):
        request = self.submitted()
        reader = view_only(self.owner, "c7_reader")
        self.client.force_login(reader)
        self.assertContains(self.client.get(self.url("purchase_requests")), "data-view-only")
        self.assertEqual(self.client.post(self.url("purchase_request", request.pk), {"action": "submit"}).status_code, 403)
        self.assertEqual(self.client.post(self.url("purchase_request_new"), {"item": [str(self.cement.pk)], "quantity": ["1"]}).status_code, 403)
        nobody = view_only(self.owner, "c7_nobody", codes=("inventory.view_stock",))
        self.client.force_login(nobody)
        for url in (self.url("purchase_requests"), self.url("purchase_request", request.pk), self.url("purchase_request_new"), reverse("projects:purchase_requests_all")):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_another_projects_request_an_unknown_one_and_an_unknown_action_are_404(self):
        request = self.submitted()
        other = services.save_project({"name": "مشروع تاني", "customer": self.customer}, self.owner)
        self.client.force_login(self.owner)
        for name in ("purchase_request", "purchase_request_edit"):
            with self.subTest(name=name):
                self.assertEqual(self.client.get(self.url(name, request.pk, project=other)).status_code, 404)
        self.assertEqual(self.client.post(self.url("purchase_request", request.pk, project=other), {"action": "reject", "reason": "لا"}).status_code, 404)
        self.assertEqual(self.client.get(self.url("purchase_request", 10 ** 9)).status_code, 404)
        self.assertEqual(self.client.post(self.url("purchase_request", request.pk), {"action": "nonsense"}).status_code, 404)
        self.assertEqual(PurchaseRequest.objects.get(pk=request.pk).status, Status.SUBMITTED)

    def test_arabic_and_english_pages(self):
        request = self.submitted()
        self.client.force_login(self.owner)
        checks = {
            "ar": ("طلبات الشراء", "الموافقة وعمل أمر الشراء", "ارفض الطلب", "مستني موافقة", 'dir="rtl"'),
            "en": ("Purchase requests", "Approve and make the purchase order", "Reject the request", "Waiting for approval", 'dir="ltr"'),
        }
        for lang, expected in checks.items():
            other = "en" if lang == "ar" else "ar"
            pages = [self.client.get(self.url("purchase_requests", lang=lang)), self.client.get(self.url("purchase_request", request.pk, lang=lang)),
                     self.client.get(self.url("purchase_request_new", lang=lang)), self.client.get(f"{reverse('projects:purchase_requests_all')}?lang={lang}")]
            for page in pages:
                self.assertEqual(page.status_code, 200)
            detail = pages[1].content.decode()
            for text in expected:
                with self.subTest(lang=lang, text=text):
                    self.assertIn(text, detail)
            for text in checks[other][1:4]:
                with self.subTest(lang=lang, absent=text):
                    self.assertNotIn(text, detail)
        english = self.client.get(self.url("purchase_request_new", lang="en")).content.decode()
        self.assertIn("Not in the catalogue (describe it)", english)
        self.assertNotIn("مش في الأصناف", english)

    def test_the_engineer_and_the_office_find_the_lists(self):
        mine = reverse("projects:purchase_requests_mine")
        self.client.force_login(self.engineer)
        tab = self.client.get(self.url("purchase_requests")).content.decode()
        self.assertIn(f'href="{mine}?lang=ar" data-my-requests', tab)  # «طلباتي»
        self.assertNotIn("data-all-requests", tab)
        self.assertIn(f'href="{reverse("projects:purchase_requests_all")}?lang=ar" data-purchase-requests-link', self.client.get(reverse("projects:list")).content.decode())
        self.client.force_login(self.owner)
        tab = self.client.get(self.url("purchase_requests")).content.decode()
        self.assertIn("data-all-requests", tab)
        self.assertIn("data-my-requests", tab)
        nobody = view_only(self.owner, "c7_lister", codes=("inventory.view_stock",))
        self.client.force_login(nobody)
        self.assertEqual(self.client.get(reverse("projects:list")).status_code, 403)

    def test_every_project_page_links_to_the_tab(self):
        self.client.force_login(self.engineer)
        for name in ("detail", "schedule", "variations", "purchase_requests"):
            page = self.client.get(reverse(f"projects:{name}", args=[self.project.pk])).content.decode()
            with self.subTest(page=name):
                self.assertIn('data-tab="purchase_requests"', page)
        self.assertIn('aria-current="page" data-tab="purchase_requests"', self.client.get(self.url("purchase_requests")).content.decode())

    def test_the_lists_across_projects_waiting_mine_and_to_order(self):
        waiting = self.submitted()
        mine_draft = self.draft()
        cancelled_order = prq.approve(self.project, self.submitted(), self.owner, supplier=self.supplier)
        purchasing.move(cancelled_order.purchase_order, "cancelled", self.owner)
        self.client.force_login(self.owner)
        page = self.client.get(reverse("projects:purchase_requests_all")).content.decode()  # whoever decides lands on «waiting»
        self.assertIn('data-request-inbox="waiting"', page)
        self.assertIn(f'data-purchase-request="{prq.label(waiting)}"', page)
        self.assertNotIn(f'data-purchase-request="{prq.label(mine_draft)}"', page)
        page = self.client.get(f"{reverse('projects:purchase_requests_all')}?show=to_order").content.decode()
        self.assertIn(f'data-purchase-request="{prq.label(cancelled_order)}"', page)
        self.assertIn("data-order-was-cancelled", page)
        self.assertNotIn(f'data-purchase-request="{prq.label(waiting)}"', page)
        self.client.force_login(self.engineer)
        page = self.client.get(reverse("projects:purchase_requests_all")).content.decode()  # the engineer lands on «mine»
        self.assertIn('data-request-inbox="mine"', page)
        for request in (waiting, mine_draft, cancelled_order):
            self.assertIn(f'data-purchase-request="{prq.label(request)}"', page)
        self.assertEqual(self.client.get(reverse("projects:purchase_requests_mine")).status_code, 200)

    def test_a_cancelled_order_reads_approved_on_the_page_and_a_new_one_can_be_made(self):
        done = prq.approve(self.project, self.submitted(), self.owner, supplier=self.supplier)
        purchasing.move(done.purchase_order, "cancelled", self.owner)
        self.client.force_login(self.manager)
        page = self.client.get(self.url("purchase_request", done.pk)).content.decode()
        self.assertIn('data-request-state="approved"', page)
        self.assertIn("data-order-cancelled", page)
        self.assertIn("data-order-form", page)
        self.assertEqual(self.client.post(self.url("purchase_request", done.pk), {"lang": "ar", "action": "order", "supplier": str(self.supplier.pk)}).status_code, 302)
        self.assertEqual(PurchaseRequest.objects.get(pk=done.pk).purchase_order.status, "draft")
        self.assertEqual(PurchaseOrder.objects.count(), 2)

    def test_the_default_unit_reads_in_the_pages_language(self):
        nails = make_item(item_code="NL-1", item_name="مسامير 3 بوصة")  # Item.unit stays the stored default "unit"
        request = self.submitted(rows=[{"item": str(nails.pk), "quantity": "40"}])
        self.client.force_login(self.owner)
        arabic = self.client.get(self.url("purchase_request", request.pk)).content.decode()
        self.assertIn("<bdi>قطعة</bdi>", arabic)
        self.assertIn("مسامير 3 بوصة (قطعة)", self.client.get(self.url("purchase_request_new")).content.decode())
        self.assertIn("<bdi>piece</bdi>", self.client.get(self.url("purchase_request", request.pk, lang="en")).content.decode())
        done = prq.approve(self.project, request, self.owner, supplier=self.supplier, quantities={request.lines.get().pk: "30"})
        self.assertEqual(done.decision_note, "«مسامير 3 بوصة»: اتطلب 40 واتوافق على 30 قطعة")

    def test_a_closed_project_is_read_only(self):
        request = self.draft()
        self.project.status = "done"
        self.project.save(update_fields=["status"])
        self.client.force_login(self.engineer)
        tab = self.client.get(self.url("purchase_requests"))
        self.assertContains(tab, "data-project-closed")
        self.assertNotContains(tab, "data-new-request")
        self.assertNotContains(self.client.get(self.url("purchase_request", request.pk)), "data-submit-form")
        self.assertContains(self.client.get(self.url("purchase_request_new")), "data-project-closed")


class DashboardTests(RequestSetup):
    def test_the_office_sees_the_requests_waiting_and_the_tile_opens_the_list(self):
        from reports.activity_panel import WORDS, _projects

        def tile(user):
            tiles, _ = _projects(WORDS["ar"], DAY, user)
            return next((t for t in tiles if t["key"] == "requests_waiting"), None)

        self.assertEqual((tile(self.owner)["value"], tile(self.owner)["tone"]), (0, "good"))
        self.submitted()
        self.draft()
        found = tile(self.owner)
        self.assertEqual((found["value"], found["label"], found["tone"], found["url"]), (1, "طلبات شراء مستنية موافقة", "bad", reverse("projects:purchase_requests_all")))
        self.assertIsNone(tile(self.engineer))  # the engineer does not approve
        self.assertIsNone(tile(self.keeper))
        self.assertNotIn("requests_waiting", [t["key"] for t in _projects(WORDS["ar"], DAY)[0]])  # no viewer, no tile
        self.client.force_login(self.owner)
        page = self.client.get(reverse("dashboard_snapshot")).content.decode()
        self.assertRegex(page, r'<a class="dash-atile[^"]*" href="/projects/purchase-requests/\?lang=ar" data-tile="requests_waiting">')
        self.assertIn('<article class="dash-atile', page)  # the other tiles stay plain


class EntityTests(RequestSetup):
    def test_another_entity_sees_none_of_the_requests_and_changes_nothing(self):
        from entities.current import working_in
        from entities.models import Entity, EntityMembership

        request = self.submitted()  # the project's money (its advance) is in the main entity
        branch = Entity.objects.create(code="E7", name_ar="فرع المنصورة")
        make_location(location_code="E7-LOC", entity=branch)
        make_cashbox(cashbox_code="E7-BOX", entity=branch)
        manager = person(RoleCode.MANAGER, "c7_branch_mgr")
        EntityMembership.objects.create(user=manager, entity=branch, is_default=True)
        with working_in(branch):
            self.assertIsNone(prq.get_request(self.project, request.pk))
            self.assertEqual(prq.visible(self.owner).count(), 0)
            with self.assertRaisesMessage(ValidationError, "كيان تاني"):
                prq.approve(self.project, request, self.owner, supplier=self.supplier)
            with self.assertRaisesMessage(ValidationError, "كيان تاني"):
                self.draft()
        self.client.force_login(manager)
        tab = self.client.get(self.url("purchase_requests"))
        self.assertContains(tab, "data-other-entity")
        self.assertNotContains(tab, "data-purchase-request=")
        self.assertNotContains(tab, "data-new-request")
        for name in ("purchase_request", "purchase_request_edit"):
            self.assertEqual(self.client.get(self.url(name, request.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("purchase_request", request.pk), {"action": "approve", "supplier": str(self.supplier.pk)}).status_code, 404)
        self.assertEqual(self.client.get(self.url("purchase_request_new")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("projects:purchase_requests_all")), "data-purchase-request=")
        request.refresh_from_db()
        self.assertEqual((request.status, request.purchase_order), (Status.SUBMITTED, None))
        self.assertEqual(PurchaseOrder.objects.count(), 0)


# ---- the scanners ----

class ScannerTests(TestCase):
    TEMPLATES = ("purchase_requests.html", "purchase_request_form.html", "purchase_request.html", "purchase_requests_inbox.html", "_request_line.html")

    def test_the_stylesheet_uses_tokens_only_and_every_token_is_defined(self):
        css = Path(settings.BASE_DIR, "static/hesba/css/projects_requests.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(css, r"rgba?\(")
        self.assertNotRegex(css, r"(?<![-\w])(left|right)\s*:")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", Path(settings.BASE_DIR, "static/hesba/css/tokens.css").read_text(encoding="utf-8")))
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertTrue(used)
        self.assertEqual(used - defined, set())

    def test_no_template_pre_fills_a_data_label_and_each_pins_its_stylesheets(self):
        folder = Path(settings.BASE_DIR, "templates/projects")
        for name in self.TEMPLATES:
            source = (folder / name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertNotRegex(source, PRE_LABELLED)
                if not name.startswith("_"):
                    self.assertIn("projects.css' %}?v=c1-vo1", source)
                    self.assertIn("projects_requests.css' %}?v=c7-1", source)

    def test_the_words_never_use_the_banned_terms(self):
        from .views_purchase_requests import WORDS

        texts = [Path(settings.BASE_DIR, "templates/projects", name).read_text(encoding="utf-8") for name in self.TEMPLATES]
        texts.append(Path(settings.BASE_DIR, "projects/purchase_requests.py").read_text(encoding="utf-8"))
        texts += [str(WORDS["ar"]), str(WORDS["en"]), str(prq.MESSAGES["ar"]), str(prq.MESSAGES["en"])]
        for word in BANNED:
            for text in texts:
                with self.subTest(word=word):
                    self.assertNotIn(word, text)

    def test_both_languages_carry_the_same_keys(self):
        from .views_purchase_requests import WORDS

        self.assertEqual(set(WORDS["ar"]), set(WORDS["en"]))
        self.assertEqual(set(prq.MESSAGES["ar"]), set(prq.MESSAGES["en"]))


class MigrationTests(TransactionTestCase):
    """0006 adds two tables after 0005; a project, its bill of quantities and a purchase order are untouched."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_follows_0005_and_adds_two_tables_without_touching_data(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("projects", "0006_purchase_requests")]
        self.assertIn(("projects", "0005_schedule"), node.dependencies)
        self.assertEqual({type(op).__name__ for op in node.operations}, {"CreateModel", "AddConstraint"})  # no data step, no column on an existing table
        self.assertEqual([op.name for op in node.operations if type(op).__name__ == "CreateModel"], ["PurchaseRequest", "PurchaseRequestLine"])
        others = [n for n in executor.loader.graph.leaf_nodes() if n[0] != "projects"]
        executor.migrate([("projects", "0005_schedule")])
        old = executor.loader.project_state(others + [("projects", "0005_schedule")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_c7")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="P-1", name="شركة النور للتطوير العقاري")
        project = old.get_model("projects", "Project").objects.create(code="P-0001", name="برج النور", customer=customer, created_by=user, contract_value=1000000)
        old.get_model("projects", "BoqLine").objects.create(project=project, line_number=1, code="A", description="خرسانة مسلحة", unit="م³", quantity="400", rate="1500", created_by=user)
        self.assertNotIn("projects_purchaserequest", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("projects", "0006_purchase_requests")])
        tables = connection.introspection.table_names()
        self.assertIn("projects_purchaserequest", tables)
        self.assertIn("projects_purchaserequestline", tables)
        migrated = Project.objects.get()
        self.assertEqual((migrated.code, migrated.contract_value), ("P-0001", D("1000000.00")))
        self.assertEqual(list(migrated.boq.values_list("code", "quantity", "rate")), [("A", D("400.000"), D("1500.00"))])
        self.assertEqual((PurchaseRequest.objects.count(), PurchaseRequestLine.objects.count()), (0, 0))
