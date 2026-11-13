"""Contracting C1 (HG-103): variation orders and extra works: the rules and the money, to the piastre.

Every test runs on the reference contract of ``tests_variation_baseline`` (1,000,000.00; retention 5%, advance recovery
10%, advance 100,000.00) and on its fixed clock (Wednesday 11 November 2026, 10:00), so no figure and no date depends
on the day the suite runs.

The order used most, VO-1 (after certificates 1 and 2):

* +20 m³ on A at 1,500.00 = +30,000.00;
* −100 m² on B at 200.00 = −20,000.00;
* a new item C, roof waterproofing, 1,000 m² × 80.00 = +80,000.00;

value +90,000.00: original 1,000,000.00, revised 1,090,000.00.
"""

from datetime import timedelta
from decimal import Decimal as D

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from attachments import services as attachments
from attachments.models import Attachment, Kind, Stage
from attachments.tests import JPEG, PDF, upload
from audit.models import AuditLog
from hesba_testing.factories import make_cashbox, make_location
from permissions.models import RoleCode
from sales.services import post_sales_invoice

from . import contract, insights, services, variations
from .models import BoqLine, Project, VariationOrder, VariationStatus
from .tests_contract import customer_balance, gl, ledger_ok, person
from .tests_documents import view_only
from .tests_variation_baseline import DAY, ReferenceContract, certificate_figures, money_footprint

NEW_C = {"code": "C", "description": "عزل مائي للسطح", "unit": "م²", "quantity": "1000", "rate": "80"}


def footprint():
    """``money_footprint`` with the ledger projected first: the projector is lazy, so journal rows only appear once it has run.
    Without this the journal counts on both sides would be the same zero and would prove nothing."""

    from ledger.projector import ensure_fresh

    ensure_fresh()
    return money_footprint()


def boq_state(project):
    return [(line.code, line.quantity, line.rate, contract.line_amount(line), line.variation_id) for line in project.boq.all()]


class VariationSetup(ReferenceContract):
    def setUp(self):
        super().setUp()
        self.engineer = person(RoleCode.SITE_ENGINEER, "c1_engineer")
        self.keeper = person(RoleCode.STOCK_KEEPER, "c1_keeper")  # holds neither projects.view_projects nor sales.view_sales_invoices
        self.viewer = view_only(self.owner, username="c1_viewer")

    def draft(self, user=None, project=None, **over):
        """VO-1 as a draft, unless told otherwise."""

        data = {"order_date": DAY, "title": "تعديلات الدور الأخير", "reason": "طلب المالك في محضر الاجتماع", "extension_days": "0",
                "changes": [(self.a.pk, "20"), (self.b.pk, "-100")], "new_items": [dict(NEW_C)]}
        data.update(over)
        return variations.save_order(project or self.project, user or self.owner, **data)

    def sign(self, order, user=None, raw=PDF, name="signed.pdf", mime="application/pdf"):
        """Attach the copy the owner signed."""

        kind = Kind.DOCUMENT if mime == "application/pdf" else Kind.PHOTO
        return attachments.add_attachment(order, upload(raw, name, mime), user or self.owner, kind=kind, stage=Stage.OTHER)

    def approve(self, order, user=None, signed=True, **over):
        if signed:
            self.sign(order)
        return variations.approve(self.project if order.project_id == self.project.pk else order.project, order, user or self.owner, **over)

    def new_item(self, code="C"):
        return self.project.boq.get(code=code)


class DraftTests(VariationSetup):
    def test_a_draft_holds_its_figures_and_changes_nothing(self):
        first, second = self.two_certificates()
        before = (footprint(), boq_state(self.project), contract.position(self.project), customer_balance(self.customer),
                  certificate_figures(first), certificate_figures(second))
        order = self.draft()
        self.assertEqual((order.number, order.status, order.order_date, order.extension_days, order.lump_amount), (1, "draft", DAY, 0, None))
        self.assertEqual([(line.code, line.boq_line_id, line.quantity_change, line.rate, line.amount) for line in order.lines.all()],
                         [("A", self.a.pk, D("20.000"), D("1500.00"), D("30000.00")), ("B", self.b.pk, D("-100.000"), D("200.00"), D("-20000.00")),
                          ("C", None, D("1000.000"), D("80.00"), D("80000.00"))])
        self.assertEqual(variations.order_value(order), D("90000.00"))
        after = (footprint(), boq_state(self.project), contract.position(self.project), customer_balance(self.customer),
                 certificate_figures(first), certificate_figures(second))
        self.assertEqual(after, before)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.original_value)
        figures = variations.figures(self.project)
        self.assertEqual((figures["original"], figures["approved"], figures["revised"], figures["contract"], figures["pending"]),
                         (D("1000000.00"), D("0.00"), D("1000000.00"), D("1000000.00"), D("90000.00")))
        self.assertEqual((figures["approved_count"], figures["pending_count"], figures["reconciled"]), (0, 1, True))

    def test_a_draft_item_is_not_in_the_bill_of_quantities_so_no_certificate_can_bill_it(self):
        self.two_certificates()
        self.draft()
        self.assertEqual([line.code for line in self.project.boq.all()], ["A", "B"])
        with self.assertRaisesMessage(ValidationError, contract.MESSAGES["ar"]["no_work"]):
            contract.create_certificate(self.project, self.owner, quantities={10 ** 9: "500"})  # there is no item to carry the quantity
        self.assertEqual(self.project.certificates.count(), 2)

    def test_a_draft_is_edited_in_place_and_its_lines_are_replaced_as_typed(self):
        order = self.draft(user=self.engineer)
        edited = self.draft(order=order, title="تعديل العزل", changes=[(self.a.pk, "5")], new_items=[], extension_days="10")
        self.assertEqual(edited.pk, order.pk)
        self.assertEqual((edited.number, edited.title, edited.extension_days, edited.created_by, edited.updated_by), (1, "تعديل العزل", 10, self.engineer, self.owner))
        self.assertEqual([(line.code, line.quantity_change, line.amount) for line in edited.lines.all()], [("A", D("5.000"), D("7500.00"))])
        self.assertEqual(VariationOrder.objects.count(), 1)

    def test_numbers_run_per_project_and_the_database_refuses_a_duplicate(self):
        other = services.save_project({"name": "فيلا الشروق", "customer": self.customer, "contract_value": "300000"}, self.owner)
        self.assertEqual([self.draft().number, self.draft().number, self.draft(project=other, changes=[], new_items=[], amount="5000").number], [1, 2, 1])
        with self.assertRaises(IntegrityError), transaction.atomic():
            VariationOrder.objects.create(project=self.project, number=2, order_date=DAY, title="مكرر", created_by=self.owner)

    def test_what_is_typed_is_checked_in_words(self):
        words = variations.MESSAGES["ar"]
        cases = (
            ({"title": " "}, words["title"]),
            ({"order_date": ""}, words["date"]),
            ({"order_date": DAY + timedelta(days=1)}, words["future"]),
            ({"extension_days": "-3"}, words["days"].format(max=variations.MAX_EXTENSION)),
            ({"extension_days": "4000"}, words["days"].format(max=variations.MAX_EXTENSION)),
            ({"changes": [(self.a.pk, "كتير")]}, words["change"].format(code="A")),
            ({"changes": [(self.a.pk, "5"), (self.a.pk, "7")]}, words["twice"].format(code="A")),
            ({"changes": [(10 ** 9, "5")]}, words["unknown_item"]),
            ({"changes": [(self.a.pk, "-401")]}, words["below_zero"].format(code="A")),
            ({"new_items": [{"code": "C", "description": "", "unit": "م²", "quantity": "10", "rate": "5"}]}, words["new_description"].format(n=1)),
            ({"new_items": [dict(NEW_C, quantity="0")]}, words["new_qty"].format(n=1)),
            ({"new_items": [dict(NEW_C, rate="")]}, words["new_rate"].format(n=1)),
            ({"new_items": [dict(NEW_C, rate="-1")]}, words["new_rate"].format(n=1)),
            ({"changes": [], "new_items": []}, words["empty"]),
            ({"amount": "5000"}, words["boq_mode"]),
        )
        for over, message in cases:
            with self.subTest(over=str(over)[:60]):
                with self.assertRaises(ValidationError) as caught:
                    self.draft(**over)
                self.assertEqual(caught.exception.messages, [message])
        self.assertFalse(VariationOrder.objects.exists())
        self.assertEqual(variations.MESSAGES["en"]["change"].format(code="A"), "The change in the quantity of item A must be a number, up or down (for example 20 or -100).")

    def test_blank_and_zero_rows_are_dropped(self):
        order = self.draft(changes=[(self.a.pk, ""), (self.b.pk, "0")], new_items=[{}, dict(NEW_C), {"code": "", "description": " ", "unit": "", "quantity": "", "rate": ""}])
        self.assertEqual([(line.code, line.quantity_change) for line in order.lines.all()], [("C", D("1000.000"))])

    def test_who_may_draft(self):
        self.assertEqual(self.draft(user=self.engineer).created_by, self.engineer)  # the site engineer drafts
        for user in (self.keeper, self.viewer):
            with self.subTest(user=user.username):
                with self.assertRaises(PermissionDenied):
                    self.draft(user=user)
        self.assertEqual(VariationOrder.objects.count(), 1)

    def test_a_closed_project_takes_no_order(self):
        Project.objects.filter(pk=self.project.pk).update(status="done")
        with self.assertRaisesMessage(ValidationError, services.MESSAGES["ar"]["closed"]):
            self.draft()

    def test_the_audit_row_keeps_the_figures_and_never_the_text(self):
        order = self.draft()
        row = AuditLog.objects.get(module="projects", action="save_variation_order", object_id=str(order.pk))
        self.assertEqual((row.object_type, row.actor, row.after_data["value"], row.after_data["number"]), ("projects.VariationOrder", self.owner, "90000.00", 1))
        self.assertEqual([(line["item"], line["new"], line["amount"]) for line in row.after_data["lines"]],
                         [("A", False, "30000.00"), ("B", False, "-20000.00"), ("C", True, "80000.00")])
        stored = str(row.after_data) + str(row.before_data)
        self.assertNotIn("محضر الاجتماع", stored)  # the reason
        self.assertNotIn("تعديلات الدور الأخير", stored)  # the title


class ApprovalTests(VariationSetup):
    def test_approval_writes_into_the_bill_of_quantities_and_keeps_the_original_value(self):
        first, second = self.two_certificates()
        one, two = certificate_figures(first), certificate_figures(second)
        ledger = {control: gl(control) for control in ("receivable", "retention_receivable", "customer_advances", "sales", "vat_out", "cash")}
        money, owner_account = footprint(), customer_balance(self.customer)  # the journal rows of the two posted certificates are in it
        self.assertGreater(money["journal_lines"], 0)  # so comparing them after the approval means something
        order = self.draft()
        approved = self.approve(order, approved_on=DAY, owner_reference="خطاب 114")
        self.assertEqual((approved.status, approved.value, approved.approved_on, approved.owner_reference, approved.decided_by), ("approved", D("90000.00"), DAY, "خطاب 114", self.owner))
        self.assertEqual(boq_state(self.project), [
            ("A", D("420.000"), D("1500.00"), D("630000.00"), None), ("B", D("1900.000"), D("200.00"), D("380000.00"), None),
            ("C", D("1000.000"), D("80.00"), D("80000.00"), order.pk)])
        self.project.refresh_from_db()
        self.assertEqual(self.project.original_value, D("1000000.00"))
        self.assertEqual((variations.original_value(self.project), variations.approved_total(self.project), variations.revised_value(self.project)),
                         (D("1000000.00"), D("90000.00"), D("1090000.00")))
        self.assertEqual((contract.boq_total(self.project), contract.contract_value(self.project), services.summary(self.project)["contract"]),
                         (D("1090000.00"), D("1090000.00"), D("1090000.00")))
        # Certificates 1 and 2 are exactly what they were: lines, rates, amounts, retention, recovery, net.
        self.assertEqual((certificate_figures(first), certificate_figures(second)), (one, two))
        self.assertEqual((one["gross"], two["gross"]), (D("200000.00"), D("250000.00")))
        # No money moved: the same rows everywhere, the same account, the same ledger.
        self.assertEqual(footprint(), money)
        self.assertEqual(customer_balance(self.customer), owner_account)
        self.assertEqual({control: gl(control) for control in ledger}, ledger)
        figures = contract.position(self.project)
        self.assertEqual((figures["contract"], figures["certified"], figures["certified_progress"]), (D("1090000.00"), D("450000.00"), 41))
        self.assertEqual((figures["retention_held"], figures["recovered"], figures["advance_left"], figures["due_now"]),
                         (D("22500.00"), D("45000.00"), D("55000.00"), D("382500.00")))
        ledger_ok(self)

    def test_certificate_three_bills_the_new_item_at_the_projects_rates(self):
        self.two_certificates()
        self.approve(self.draft())
        third = self.certify(post=False, **{str(self.new_item().pk): "500"})
        self.assertEqual(certificate_figures(third), {
            "number": 3, "status": "draft", "gross": D("40000.00"), "retention_rate": D("5.00"), "retention": D("2000.00"), "recovery": D("4000.00"),
            "tax": D("0.00"), "invoice": D("40000.00"), "net": D("34000.00"), "lines": [("C", D("0.000"), D("500.000"), D("80.00"), D("40000.00"))]})
        post_sales_invoice(third.invoice_id, self.owner)
        figures = contract.position(self.project)
        self.assertEqual((figures["contract"], figures["certified"], figures["certified_progress"]), (D("1090000.00"), D("490000.00"), 44))
        self.assertEqual((figures["retention_held"], figures["recovered"], figures["advance_left"], figures["due_now"]),
                         (D("24500.00"), D("49000.00"), D("51000.00"), D("416500.00")))  # 382,500 + 34,000
        self.assertEqual(customer_balance(self.customer), D("390000.00"))  # 490,000 − 100,000
        self.assertEqual(customer_balance(self.customer), figures["due_now"] + figures["retention_held"] - figures["advance_left"])
        self.assertEqual((gl("receivable"), gl("retention_receivable"), gl("customer_advances"), gl("sales")),
                         (D("416500.00"), D("24500.00"), D("-51000.00"), D("-490000.00")))
        ledger_ok(self)

    def test_the_raised_quantity_and_the_cut_one_are_billed_to_their_new_limits(self):
        self.two_certificates()
        self.approve(self.draft())
        third = self.certify(a="420", b="1900", **{str(self.new_item().pk): "1000"})  # everything that is left
        # 220 × 1,500 + 1,150 × 200 + 1,000 × 80 = 330,000 + 230,000 + 80,000
        self.assertEqual((third.gross, third.retention_amount, third.recovery_amount, contract.net_payable(third)),
                         (D("640000.00"), D("32000.00"), D("55000.00"), D("553000.00")))  # the recovery is what was left of the advance, not 64,000
        figures = contract.position(self.project)
        self.assertEqual((figures["certified"], figures["certified_progress"], figures["recovered"], figures["advance_left"]), (D("1090000.00"), 100, D("100000.00"), D("0.00")))
        self.assertEqual((figures["retention_held"], figures["due_now"]), (D("54500.00"), D("935500.00")))
        self.assertEqual(customer_balance(self.customer), D("990000.00"))
        self.assertEqual((gl("receivable"), gl("retention_receivable"), gl("customer_advances"), gl("sales")),
                         (D("935500.00"), D("54500.00"), D("0.00"), D("-1090000.00")))
        ledger_ok(self)

    def test_a_variation_above_the_original_contract_never_restarts_or_overshoots_the_recovery(self):
        """Billed to the original 1,000,000.00, the advance is recovered in full. The 90,000.00 of extra work that follows
        recovers nothing more, holds retention at the project's rate and is otherwise due in full."""

        self.two_certificates()
        self.certify(a="400", b="2000")
        self.assertEqual((contract.advance_recovered(self.project), contract.advance_left(self.project)), (D("100000.00"), D("0.00")))
        self.approve(self.draft(changes=[], new_items=[dict(NEW_C, rate="90")]))
        self.assertEqual((variations.original_value(self.project), variations.revised_value(self.project), contract.contract_value(self.project)),
                         (D("1000000.00"), D("1090000.00"), D("1090000.00")))
        fourth = self.certify(post=False, **{str(self.new_item().pk): "1000"})
        self.assertEqual((fourth.gross, fourth.retention_rate, fourth.retention_amount, fourth.recovery_amount, fourth.invoice.total_amount, contract.net_payable(fourth)),
                         (D("90000.00"), D("5.00"), D("4500.00"), D("0.00"), D("90000.00"), D("85500.00")))
        post_sales_invoice(fourth.invoice_id, self.owner)
        figures = contract.position(self.project)
        self.assertEqual((figures["advance"], figures["recovered"], figures["advance_left"]), (D("100000.00"), D("100000.00"), D("0.00")))
        self.assertEqual((figures["certified"], figures["retention_held"], figures["due_now"]), (D("1090000.00"), D("54500.00"), D("935500.00")))
        self.assertEqual(customer_balance(self.customer), D("990000.00"))
        self.assertEqual(customer_balance(self.customer), figures["due_now"] + figures["retention_held"] - figures["advance_left"])
        self.assertEqual((gl("receivable"), gl("retention_receivable"), gl("customer_advances"), gl("sales")),
                         (D("935500.00"), D("54500.00"), D("0.00"), D("-1090000.00")))
        ledger_ok(self)

    def test_an_omission_below_the_certified_quantity_is_refused_when_drafted(self):
        self.two_certificates()  # B is certified to 750
        with self.assertRaises(ValidationError) as caught:
            self.draft(changes=[(self.b.pk, "-1300")], new_items=[])  # would leave 700
        self.assertEqual(caught.exception.messages, [variations.MESSAGES["ar"]["below_certified"].format(code="B", after="700", qty="750")])
        self.assertFalse(VariationOrder.objects.exists())
        self.assertEqual(self.draft(changes=[(self.b.pk, "-1250")], new_items=[]).lines.get().amount, D("-250000.00"))  # exactly down to 750 is allowed

    def test_an_omission_is_checked_again_at_approval_and_a_refusal_leaves_everything_as_it_was(self):
        self.two_certificates()
        order = self.draft(changes=[(self.b.pk, "-1200")], new_items=[dict(NEW_C)])  # would leave 800: fine while 750 is certified
        self.certify(b="850")  # a certificate made in between
        self.sign(order)
        state, money = boq_state(self.project), footprint()
        with self.assertRaises(ValidationError) as caught:
            variations.approve(self.project, order, self.owner)
        self.assertEqual(caught.exception.messages, [variations.MESSAGES["ar"]["below_certified"].format(code="B", after="800", qty="850")])
        order.refresh_from_db()
        self.project.refresh_from_db()
        self.assertEqual((order.status, order.value, self.project.original_value), ("draft", D("0.00"), None))
        self.assertEqual((boq_state(self.project), footprint()), (state, money))  # the new item was not created either

    def test_a_rate_is_never_changed_work_at_another_rate_is_a_new_item(self):
        first, _ = self.two_certificates()
        with self.assertRaisesMessage(ValidationError, contract.MESSAGES["ar"]["rate_locked"]):
            contract.save_boq_line(self.project, self.owner, {"code": "A", "description": "خرسانة مسلحة", "quantity": "400", "rate": "1650"}, self.a)
        order = self.approve(self.draft(changes=[(self.a.pk, "10")], new_items=[{"code": "A-2", "description": "خرسانة مسلحة بفئة معدّلة", "unit": "م³", "quantity": "50", "rate": "1650"}]))
        self.assertEqual([(line.code, line.rate, line.amount) for line in order.lines.all()], [("A", D("1500.00"), D("15000.00")), ("A-2", D("1650.00"), D("82500.00"))])
        self.a.refresh_from_db()
        self.assertEqual((self.a.rate, self.a.quantity), (D("1500.00"), D("410.000")))  # the certified item keeps its rate
        third = self.certify(post=False, **{str(self.new_item("A-2").pk): "50"})
        self.assertEqual((third.gross, [(line.boq_line.code, line.rate, line.amount) for line in third.lines.all()]), (D("82500.00"), [("A-2", D("1650.00"), D("82500.00"))]))
        self.assertEqual(certificate_figures(first)["lines"][0], ("A", D("0.000"), D("100.000"), D("1500.00"), D("150000.00")))

    def test_the_items_always_add_up_to_the_contract_value_whatever_the_decimals(self):
        """A line's value is its amount after less its amount before, so original + approved = the bill of quantities, to the piastre."""

        awkward = contract.save_boq_line(self.project, self.owner, {"code": "X", "description": "بند بكسور", "unit": "م", "quantity": "10.005", "rate": "0.99"})
        self.assertEqual(contract.line_amount(awkward), D("9.90"))  # 9.90495
        order = self.approve(self.draft(changes=[(awkward.pk, "0.005")], new_items=[{"code": "Y", "description": "بند جديد بكسور", "unit": "م", "quantity": "3.333", "rate": "3.33"}]))
        self.assertEqual([(line.code, line.amount) for line in order.lines.all()], [("X", D("0.01")), ("Y", D("11.10"))])  # 10.010 × 0.99 = 9.91; 0.005 × 0.99 alone rounds to 0.00
        self.assertEqual((variations.original_value(self.project), order.value, variations.revised_value(self.project), contract.boq_total(self.project)),
                         (D("1000009.90"), D("11.11"), D("1000021.01"), D("1000021.01")))

    def test_the_owners_approval_needs_the_signed_copy_and_a_sensible_date(self):
        order = self.draft()
        words = variations.MESSAGES["ar"]
        with self.assertRaises(ValidationError) as caught:
            self.approve(order, signed=False)
        self.assertEqual(caught.exception.messages, [words["signed_copy"]])
        self.sign(order, raw=JPEG, name="signed.jpg", mime="image/jpeg")  # a phone photo of the signed page is a copy too
        for day, message in ((DAY + timedelta(days=1), words["approved_future"]), ("مش تاريخ", words["approved_on"]),
                             (DAY - timedelta(days=1), words["approved_before"].format(date="11 نوفمبر 2026"))):
            with self.subTest(day=str(day)):
                with self.assertRaises(ValidationError) as caught:
                    self.approve(order, signed=False, approved_on=day)
                self.assertEqual(caught.exception.messages, [message])
        order.refresh_from_db()
        self.assertEqual((order.status, boq_state(self.project)[-1][0]), ("draft", "B"))
        self.assertEqual(self.approve(order, signed=False).approved_on, DAY)  # no date typed: today

    def test_an_approved_order_is_never_edited_approved_again_rejected_or_cancelled(self):
        order = self.approve(self.draft())
        not_draft = variations.MESSAGES["ar"]["not_draft"]
        for attempt in (lambda: self.draft(order=order, title="تعديل بعد الاعتماد"), lambda: variations.approve(self.project, order, self.owner),
                        lambda: variations.close(self.project, order, self.owner, status=VariationStatus.REJECTED),
                        lambda: variations.close(self.project, order, self.owner, status=VariationStatus.CANCELLED)):
            with self.assertRaises(ValidationError) as caught:
                attempt()
            self.assertEqual(caught.exception.messages, [not_draft])
        order.refresh_from_db()
        self.assertEqual((order.status, order.title, contract.boq_total(self.project)), ("approved", "تعديلات الدور الأخير", D("1090000.00")))

    def test_the_signed_copy_is_added_and_removed_only_while_the_order_is_a_draft(self):
        order = self.draft(user=self.engineer)
        first = self.sign(order, user=self.engineer)
        attachments.delete_attachment(first.pk, self.engineer)  # a wrong file goes, while it is a draft
        kept = self.sign(order, user=self.engineer)
        variations.approve(self.project, order, self.owner)
        with self.assertRaisesMessage(ValidationError, attachments.MESSAGES["ar"]["variation_closed"]):
            self.sign(order)
        with self.assertRaisesMessage(ValidationError, attachments.MESSAGES["ar"]["variation_kept"]):
            attachments.delete_attachment(kept.pk, self.owner)
        rows = Attachment.objects.filter(content_type=ContentType.objects.get_for_model(VariationOrder), object_id=order.pk)
        self.assertEqual([(row.pk, row.kind, row.stage, row.entity_id) for row in rows], [(kept.pk, "document", "other", None)])
        with self.assertRaises(PermissionDenied):
            attachments.attachments_for(order, self.keeper)

    def test_only_the_office_records_the_owners_decision(self):
        order = self.draft(user=self.engineer)
        self.sign(order, user=self.engineer)
        for user in (self.engineer, self.viewer, self.keeper):
            with self.subTest(user=user.username):
                with self.assertRaises(PermissionDenied):
                    variations.approve(self.project, order, user)
                with self.assertRaises(PermissionDenied):
                    variations.close(self.project, order, user, status=VariationStatus.REJECTED)
        order.refresh_from_db()
        self.assertEqual((order.status, contract.boq_total(self.project)), ("draft", D("1000000.00")))
        with self.assertRaises(PermissionDenied):
            variations.close(self.project, order, self.viewer, status=VariationStatus.CANCELLED)
        cancelled = variations.close(self.project, order, self.engineer, status=VariationStatus.CANCELLED, note="اتكتب بالغلط")  # whoever drafts may withdraw a draft
        self.assertEqual((cancelled.status, cancelled.decided_by, cancelled.decision_note), ("cancelled", self.engineer, "اتكتب بالغلط"))

    def test_a_rejected_order_stays_on_the_register_and_changes_nothing(self):
        self.two_certificates()
        state, money = boq_state(self.project), footprint()
        order = variations.close(self.project, self.draft(), self.owner, status=VariationStatus.REJECTED, note="المالك اعتذر عن البند")
        self.assertEqual((order.status, order.value, variations.order_value(order)), ("rejected", D("0.00"), D("90000.00")))  # what it asked for is kept
        figures = variations.figures(self.project)
        self.assertEqual((figures["count"], figures["approved"], figures["pending"], figures["revised"]), (1, D("0.00"), D("0.00"), D("1000000.00")))
        self.assertEqual((boq_state(self.project), footprint()), (state, money))
        self.assertEqual(self.draft().number, 2)  # its number is not reused
        row = AuditLog.objects.get(module="projects", action="reject_variation_order")
        self.assertEqual((row.object_id, row.after_data["status"]), (str(order.pk), "rejected"))

    def test_the_approval_is_audited_with_the_contract_before_and_after(self):
        order = self.approve(self.draft(), approved_on=DAY)
        row = AuditLog.objects.get(module="projects", action="approve_variation_order", object_id=str(order.pk))
        self.assertEqual({key: row.after_data[key] for key in ("status", "value", "contract_before", "contract_after", "original_value", "approved_on", "signed_copies")},
                         {"status": "approved", "value": "90000.00", "contract_before": "1000000.00", "contract_after": "1090000.00", "original_value": "1000000.00",
                          "approved_on": "2026-11-11", "signed_copies": 1})

    def test_a_second_order_starts_from_the_revised_value_and_the_original_never_moves(self):
        first = self.approve(self.draft())
        second = self.approve(self.draft(title="تخفيض العزل", changes=[(self.new_item().pk, "-250")], new_items=[]))  # −250 × 80
        self.assertEqual((second.number, second.value), (2, D("-20000.00")))
        self.project.refresh_from_db()
        self.assertEqual((self.project.original_value, variations.approved_total(self.project), variations.revised_value(self.project), contract.boq_total(self.project)),
                         (D("1000000.00"), D("70000.00"), D("1070000.00"), D("1070000.00")))
        self.assertEqual((variations.contract_before(first), variations.contract_before(second)), (D("1000000.00"), D("1090000.00")))
        self.assertEqual(variations.order_numbers(self.project), {self.new_item().pk: 1})

    def test_another_entity_cannot_draft_or_approve_on_a_project_whose_money_is_elsewhere(self):
        from entities.current import working_in
        from entities.models import Entity

        self.two_certificates()  # the project's money now lives in the main entity
        order = self.draft()
        self.sign(order)
        branch = Entity.objects.create(code="E9", name_ar="فرع الإسكندرية")
        make_location(location_code="E9-LOC", entity=branch)
        make_cashbox(cashbox_code="E9-BOX", entity=branch)
        state = boq_state(self.project)
        with working_in(branch):
            for attempt in (lambda: self.draft(title="من فرع تاني"), lambda: self.draft(order=order, title="تعديل من فرع تاني"),
                            lambda: variations.approve(self.project, order, self.owner),
                            lambda: variations.close(self.project, order, self.owner, status=VariationStatus.REJECTED),
                            lambda: variations.close(self.project, order, self.owner, status=VariationStatus.CANCELLED)):
                with self.assertRaises(ValidationError) as caught:
                    attempt()
                self.assertEqual(caught.exception.messages, [contract.MESSAGES["ar"]["entity"]])
            # The signed copy of a draft follows the order: another entity neither adds one nor takes one away.
            rules = attachments.OWNERS["projects.variationorder"]
            self.assertFalse(rules["can_attach"](self.owner, order))
            self.assertFalse(rules["can_delete"](self.owner, order))
            with self.assertRaises(PermissionDenied):
                attachments.add_attachment(order, upload(PDF, "b.pdf", "application/pdf"), self.owner, kind=Kind.DOCUMENT, stage=Stage.OTHER)
            with self.assertRaises(PermissionDenied):
                attachments.delete_attachment(Attachment.objects.get(object_id=order.pk, content_type=ContentType.objects.get_for_model(VariationOrder)).pk, self.owner)
        order.refresh_from_db()
        self.assertEqual((VariationOrder.objects.count(), order.status, order.title, boq_state(self.project)), (1, "draft", "تعديلات الدور الأخير", state))
        self.assertEqual(Attachment.objects.filter(object_id=order.pk, content_type=ContentType.objects.get_for_model(VariationOrder)).count(), 1)


class ApprovalWithVatTests(VariationSetup):
    vat = True

    def test_certificate_three_with_vat_at_14_percent(self):
        first, second = self.two_certificates()
        one, two = certificate_figures(first), certificate_figures(second)
        self.approve(self.draft())
        self.assertEqual((certificate_figures(first), certificate_figures(second)), (one, two))
        self.assertEqual((one["invoice"], one["net"], two["invoice"], two["net"]), (D("228000.00"), D("198000.00"), D("285000.00"), D("247500.00")))
        third = self.certify(post=False, **{str(self.new_item().pk): "500"})
        self.assertEqual((third.gross, third.retention_amount, third.recovery_amount, third.invoice.tax_amount, third.invoice.total_amount, contract.net_payable(third)),
                         (D("40000.00"), D("2000.00"), D("4000.00"), D("5600.00"), D("45600.00"), D("39600.00")))  # 45,600 − 2,000 − 4,000
        post_sales_invoice(third.invoice_id, self.owner)
        figures = contract.position(self.project)
        self.assertEqual((figures["certified"], figures["retention_held"], figures["recovered"], figures["advance_left"], figures["due_now"]),
                         (D("490000.00"), D("24500.00"), D("49000.00"), D("51000.00"), D("485100.00")))  # 445,500 + 39,600
        self.assertEqual(customer_balance(self.customer), D("458600.00"))  # 558,600 − 100,000
        self.assertEqual(customer_balance(self.customer), figures["due_now"] + figures["retention_held"] - figures["advance_left"])
        self.assertEqual((gl("receivable"), gl("retention_receivable"), gl("customer_advances"), gl("sales"), gl("vat_out")),
                         (D("485100.00"), D("24500.00"), D("-51000.00"), D("-490000.00"), D("-68600.00")))
        ledger_ok(self)


class BillOfQuantitiesLockTests(VariationSetup):
    def edit(self, line, **over):
        data = {"code": line.code, "description": line.description, "unit": line.unit, "quantity": str(line.quantity), "rate": str(line.rate)}
        data.update(over)
        return contract.save_boq_line(self.project, self.owner, data, line)

    def test_before_the_first_certificate_the_bill_of_quantities_is_edited_freely(self):
        self.assertFalse(contract.under_way(self.project))
        self.edit(self.a, quantity="450", rate="1400")
        extra = contract.save_boq_line(self.project, self.owner, {"code": "Z", "description": "أعمال موقع عام", "unit": "مقطوعية", "quantity": "1", "rate": "5000"})
        contract.delete_boq_line(self.project, extra, self.owner)
        self.assertEqual(contract.boq_total(self.project), D("1030000.00"))  # 450 × 1,400 + 400,000

    def test_after_the_first_certificate_value_changes_go_through_a_variation_order(self):
        draft = self.certify(post=False, a="100")  # a draft certificate already counts: its quantities are spoken for
        self.assertTrue(contract.under_way(self.project))
        locked = contract.MESSAGES["ar"]["boq_locked"]
        for attempt in (lambda: self.edit(self.b, quantity="2100"), lambda: self.edit(self.b, quantity="1900"), lambda: self.edit(self.b, rate="210"),
                        lambda: contract.save_boq_line(self.project, self.owner, {"code": "Z", "description": "بند جديد", "quantity": "1", "rate": "1"}),
                        lambda: contract.delete_boq_line(self.project, self.b, self.owner)):
            with self.assertRaises(ValidationError) as caught:
                attempt()
            self.assertEqual(caught.exception.messages, [locked])
        self.assertEqual(self.edit(self.b, description="مباني طوب أحمر", unit="متر مربع").description, "مباني طوب أحمر")  # words are not value
        self.assertEqual(contract.boq_total(self.project), D("1000000.00"))
        contract.withdraw_certificate(self.project, draft, self.owner)  # no certificate any more: free again
        self.assertEqual(self.edit(self.b, quantity="2100").quantity, D("2100.000"))

    def test_an_approved_order_locks_it_too_even_without_a_certificate(self):
        self.approve(self.draft())
        with self.assertRaisesMessage(ValidationError, contract.MESSAGES["ar"]["boq_locked"]):
            self.edit(self.a, quantity="500")
        self.assertEqual(contract.boq_total(self.project), D("1090000.00"))

    def test_an_item_named_on_an_order_is_not_deleted(self):
        self.draft()  # no certificate, nothing approved: the bill of quantities is still free, but B is on a draft order
        with self.assertRaisesMessage(ValidationError, contract.MESSAGES["ar"]["boq_in_order"]):
            contract.delete_boq_line(self.project, self.b, self.owner)
        self.assertEqual(self.project.boq.count(), 2)

    def test_a_quantity_changed_under_a_draft_is_read_as_it_stands_at_approval(self):
        order = self.draft(changes=[(self.a.pk, "20")], new_items=[])
        self.edit(self.a, quantity="500", rate="1600")  # still free: no certificate, nothing approved
        self.assertEqual(variations.order_value(order), D("32000.00"))  # 20 × 1,600 now
        approved = self.approve(order)
        self.assertEqual((approved.value, approved.lines.get().rate), (D("32000.00"), D("1600.00")))
        self.assertEqual((variations.original_value(self.project), variations.revised_value(self.project), contract.boq_total(self.project)),
                         (D("1200000.00"), D("1232000.00"), D("1232000.00")))  # 500 × 1,600 + 400,000, then + 32,000


class LumpSumTests(VariationSetup):
    def setUp(self):
        super().setUp()
        self.villa = services.save_project({"name": "تشطيب فيلا الشروق", "customer": self.customer, "contract_value": "300000"}, self.owner)
        contract.save_terms(self.villa, self.owner, retention_rate="10", advance_recovery_rate="0")

    def order(self, amount="50000", **over):
        return self.draft(project=self.villa, changes=[], new_items=[], amount=amount, **over)

    def test_an_amount_order_raises_the_contract_value_and_is_billed_as_a_lump_sum(self):
        order = self.order()
        self.assertEqual((order.lump_amount, order.lines.count(), variations.order_value(order), contract.contract_value(self.villa)), (D("50000.00"), 0, D("50000.00"), D("300000.00")))
        self.sign(order)
        approved = variations.approve(self.villa, order, self.owner)
        self.villa.refresh_from_db()
        self.assertEqual((approved.value, self.villa.original_value, self.villa.contract_value), (D("50000.00"), D("300000.00"), D("350000.00")))
        self.assertEqual((variations.original_value(self.villa), variations.revised_value(self.villa), contract.contract_value(self.villa)),
                         (D("300000.00"), D("350000.00"), D("350000.00")))
        invoice = services.bill_progress(self.villa, self.owner, amount="50000", description="أعمال أمر التغيير رقم 1")
        certificate = invoice.project_certificate
        self.assertEqual((certificate.gross, certificate.retention_amount, certificate.recovery_amount, contract.net_payable(certificate)),
                         (D("50000.00"), D("5000.00"), D("0.00"), D("45000.00")))

    def test_an_omission_never_takes_the_contract_below_what_is_billed(self):
        post_sales_invoice(services.bill_progress(self.villa, self.owner, amount="280000", description="أعمال الشهر الأول").pk, self.owner)
        with self.assertRaises(ValidationError) as caught:
            self.order(amount="-30000")
        self.assertEqual(caught.exception.messages, [variations.MESSAGES["ar"]["below_billed"].format(after=D("270000.00"), billed=D("280000.00"))])
        order = self.order(amount="-20000")
        self.sign(order)
        self.assertEqual(variations.approve(self.villa, order, self.owner).value, D("-20000.00"))
        self.assertEqual((contract.contract_value(self.villa), variations.original_value(self.villa)), (D("280000.00"), D("300000.00")))

    def test_items_are_refused_on_a_project_without_a_bill_of_quantities_and_an_amount_on_one_with(self):
        with self.assertRaisesMessage(ValidationError, variations.MESSAGES["ar"]["lump_mode"]):
            self.draft(project=self.villa, changes=[], new_items=[dict(NEW_C)])
        with self.assertRaisesMessage(ValidationError, variations.MESSAGES["ar"]["empty_lump"]):
            self.order(amount="")
        with self.assertRaisesMessage(ValidationError, variations.MESSAGES["ar"]["amount"]):
            self.order(amount="خمسين ألف")

    def test_once_an_order_is_approved_the_typed_value_and_a_late_bill_of_quantities_are_locked(self):
        order = self.order()
        self.sign(order)
        variations.approve(self.villa, order, self.owner)
        data = {"name": "تشطيب فيلا الشروق", "customer": self.customer, "contract_value": "400000"}
        with self.assertRaisesMessage(ValidationError, services.MESSAGES["ar"]["value_locked"]):
            services.save_project(data, self.owner, self.villa)
        self.assertEqual(services.save_project(dict(data, contract_value="350000", site="الشروق"), self.owner, self.villa).site, "الشروق")  # the rest stays editable
        with self.assertRaisesMessage(ValidationError, contract.MESSAGES["ar"]["boq_locked"]):
            contract.save_boq_line(self.villa, self.owner, {"description": "بند", "quantity": "1", "rate": "1"})
        self.assertEqual((contract.contract_value(self.villa), variations.revised_value(self.villa)), (D("350000.00"), D("350000.00")))


class ExtensionOfTimeTests(VariationSetup):
    def test_an_approved_extension_moves_the_planned_end_the_calendar_share_and_the_behind_flag(self):
        self.two_certificates()  # 45% billed; the project runs from 100 days ago to 100 days ahead
        later = DAY + timedelta(days=30)
        self.assertEqual((insights.time_share(self.project, later), variations.planned_end(self.project)), (65, DAY + timedelta(days=100)))  # 130 of 200 days
        rows, totals = insights.portfolio(today=later)
        self.assertEqual((rows[0]["behind"], totals["behind"]), (True, 1))  # 45 + 15 < 65
        order = self.draft(title="تمديد بسبب تأخر تسليم الموقع", changes=[], new_items=[], extension_days="30")
        self.assertEqual((order.lines.count(), variations.order_value(order)), (0, D("0.00")))
        self.assertEqual(insights.time_share(self.project, later), 65)  # a draft extends nothing
        approved = self.approve(order)
        self.assertEqual((approved.value, approved.extension_days, variations.extension_days(self.project)), (D("0.00"), 30, 30))
        self.assertEqual(variations.planned_end(self.project), DAY + timedelta(days=130))
        self.assertEqual(insights.time_share(self.project, later), 57)  # 130 of 230 days
        rows, totals = insights.portfolio(today=later)
        self.assertEqual((rows[0]["time_pct"], rows[0]["behind"], totals["behind"]), (57, False, 0))  # 45 + 15 is not below 57
        self.project.refresh_from_db()
        self.assertEqual((self.project.end_date, self.project.original_value, contract.contract_value(self.project)), (DAY + timedelta(days=100), D("1000000.00"), D("1000000.00")))

    def test_a_rejected_extension_and_a_project_without_dates_move_nothing(self):
        variations.close(self.project, self.draft(changes=[], new_items=[], extension_days="30"), self.owner, status=VariationStatus.REJECTED)
        self.assertEqual((variations.extension_days(self.project), insights.time_share(self.project)), (0, 50))
        undated = services.save_project({"name": "مشروع بدون تواريخ", "customer": self.customer, "contract_value": "1000"}, self.owner)
        self.assertEqual((variations.planned_end(undated), insights.time_share(undated)), (None, None))


class MigrationTests(TransactionTestCase):
    """0004 adds two tables and two NULL columns after 0003; a project, its bill of quantities and its site reports are untouched."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_follows_0003_and_leaves_existing_projects_as_they_were(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("projects", "0004_variation_orders")]
        self.assertIn(("projects", "0003_site_reports"), node.dependencies)
        self.assertEqual([type(op).__name__ for op in node.operations], ["AddField", "CreateModel", "CreateModel", "AddField", "AddConstraint"])  # no data migration
        others = [n for n in executor.loader.graph.leaf_nodes() if n[0] != "projects"]
        executor.migrate([("projects", "0003_site_reports")])
        old = executor.loader.project_state(others + [("projects", "0003_site_reports")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_c1")
        customer = old.get_model("master_data", "Customer").objects.create(customer_code="P-1", name="شركة النور للتطوير العقاري")
        project = old.get_model("projects", "Project").objects.create(code="P-0001", name="برج النور الإداري", customer=customer, created_by=user, contract_value=1000000)
        old.get_model("projects", "BoqLine").objects.create(project=project, line_number=1, code="A", description="خرسانة مسلحة", unit="م³", quantity="400", rate="1500", created_by=user)
        self.assertNotIn("projects_variationorder", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("projects", "0004_variation_orders")])
        names = connection.introspection.table_names()
        self.assertIn("projects_variationorder", names)
        self.assertIn("projects_variationline", names)
        migrated = Project.objects.get()
        self.assertEqual((migrated.code, migrated.contract_value, migrated.original_value), ("P-0001", D("1000000.00"), None))  # NULL: no order was approved yet
        self.assertEqual([(line.code, line.quantity, line.rate, line.variation_id) for line in BoqLine.objects.all()], [("A", D("400.000"), D("1500.00"), None)])
        self.assertEqual((variations.original_value(migrated), variations.revised_value(migrated), contract.contract_value(migrated)),
                         (D("600000.00"), D("600000.00"), D("600000.00")))
        self.assertEqual(VariationOrder.objects.count(), 0)
