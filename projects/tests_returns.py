"""Contracting C12 (HG-127): materials back from the site at the project's own net issued cost, and a multi-line issue
voucher. Every amount is written out."""

import re
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from cashboxes.models import CashboxMovement, CashboxOperation
from hesba_testing.factories import make_item, make_supplier, stock_in
from inventory.models import StockAdjustmentDirection, StockMovement, StockOperation, StockOperationStatus
from inventory.services import adjust_stock, cancel_stock_operation, get_item_authoritative_average_cost, get_item_location_stock_quantity, get_item_stock_value
from permissions.models import RoleCode
from purchases.models import SupplierLedgerEntry
from purchases.services import create_purchase_draft, post_purchase_invoice
from sales.models import CustomerLedgerEntry, CustomerPayment, SalesInvoice, SalesLine

from . import costs, returns, services
from .models import CostHeading, ProjectIssue, ProjectStatus
from .tests_contract import TODAY, ContractSetup, gl, ledger_ok, person

BANNED = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")


def money_rows():
    """Every row a return must never write: cash, the customer and supplier ledgers, and sales."""

    return {model.__name__: model.objects.count() for model in (CashboxMovement, CashboxOperation, CustomerLedgerEntry, SupplierLedgerEntry,
                                                                  CustomerPayment, SalesInvoice, SalesLine)}


class ReturnSetup(ContractSetup):
    def setUp(self):
        super().setUp()
        self.cement = make_item(item_code="CEM", item_name="أسمنت (شكارة)", is_stock_tracked=True)
        self.supplier = make_supplier(supplier_code="S-CEM", name="مصنع الأسمنت")

    def buy(self, number, quantity, price):
        invoice = create_purchase_draft({"invoice_number": number, "invoice_date": TODAY, "supplier": self.supplier, "receiving_location": self.store},
                                        [{"item": self.cement, "quantity": D(quantity), "unit_purchase_price": D(price)}], self.owner)
        post_purchase_invoice(invoice.pk, self.owner)

    def issue(self, quantity, user=None):
        return services.issue_materials(self.project, user or self.owner, item=self.cement, location=self.store, quantity=quantity)

    def give_back(self, quantity, user=None, item=None):
        return returns.return_materials(self.project, user or self.owner, location=self.store,
                                        lines=[{"item": item or self.cement, "quantity": quantity}])[0]

    def materials(self):
        return services.summary(self.project)["materials"]


class ReturnCostTests(ReturnSetup):
    def test_a_return_comes_back_at_the_projects_issue_cost_not_todays_average(self):
        self.buy("PI-CEM-1", "100", "80.00")
        self.issue("100")
        self.assertEqual(self.materials(), D("8000.00"))
        self.assertEqual(gl("project_cost"), D("8000.00"))
        self.buy("PI-CEM-2", "100", "90.00")
        self.assertEqual(get_item_authoritative_average_cost(self.cement), D("90.0000"))  # today's average is 90.00
        before = money_rows()

        back = self.give_back("20")
        self.assertEqual((back.reference_number, back.adjustment_direction, back.quantity, back.unit_cost),
                         ("MR-P-0001-001", StockAdjustmentDirection.IN, D("20.000"), D("80.0000")))
        movement = back.movements.get()
        self.assertEqual((movement.movement_type, movement.quantity, movement.unit_cost), ("adjustment_in", D("20.000"), D("80.0000")))
        self.assertEqual(movement.quantity * movement.unit_cost, D("1600.0000000"))  # 1,600.00, not 1,800.00 at today's average
        self.assertEqual(get_item_location_stock_quantity(self.cement, self.store), D("120"))
        self.assertEqual(get_item_stock_value(self.cement), D("10600.00"))  # 9,000.00 bought at 90 + 1,600.00 back at 80
        self.assertTrue(ProjectIssue.objects.filter(project=self.project, operation=back).exists())

        self.assertEqual(self.materials(), D("6400.00"))
        self.assertEqual(costs.actual_by_heading(self.project)[CostHeading.MATERIALS], D("6400.00"))
        figures = services.summary(self.project)
        self.assertEqual((figures["cost"], figures["profit"]), (D("6400.00"), D("-6400.00")))
        self.assertEqual(gl("project_cost"), D("6400.00"))  # 5103: issued 8,000.00 debit, returned 1,600.00 credit
        ledger_ok(self)  # the stock valuation and every control account reconcile
        self.assertEqual(money_rows(), before)  # no cash, ledger party or sales row

        with self.assertRaisesMessage(ValidationError, "المتاح للارتجاع 80"):
            self.give_back("81")
        self.assertEqual(self.materials(), D("6400.00"))
        self.assertEqual(self.project.issues.count(), 2)

    def test_two_issues_at_different_costs_return_at_their_weighted_cost(self):
        stock_in(self.cement, self.store, 60, "80.00", movement_date=TODAY)
        self.issue("60")
        stock_in(self.cement, self.store, 40, "95.00", movement_date=TODAY)
        second = self.issue("40")
        self.assertEqual(second.unit_cost, D("95.0000"))
        self.assertEqual(self.materials(), D("8600.00"))
        self.assertEqual(returns.return_cost(self.project, self.cement), D("86.0000"))
        back = self.give_back("10")
        self.assertEqual((back.unit_cost, back.quantity * back.unit_cost), (D("86.0000"), D("860.0000000")))
        self.assertEqual(self.materials(), D("7740.00"))
        self.assertEqual(gl("project_cost"), D("7740.00"))
        self.assertEqual(returns.return_cost(self.project, self.cement), D("86.0000"))  # what is left keeps the same cost
        ledger_ok(self)

    def test_a_reversed_issue_drops_out_of_the_cost_and_of_the_next_returns_cost(self):
        stock_in(self.cement, self.store, 60, "80.00", movement_date=TODAY)
        self.issue("60")
        stock_in(self.cement, self.store, 40, "95.00", movement_date=TODAY)
        second = self.issue("40")
        cancel_stock_operation(second.pk, TODAY, "صرف بالغلط", self.owner)
        self.assertEqual(self.materials(), D("4800.00"))
        self.assertEqual(returns.return_cost(self.project, self.cement), D("80.0000"))
        back = self.give_back("10")
        self.assertEqual(back.unit_cost, D("80.0000"))
        self.assertEqual(self.materials(), D("4000.00"))
        self.assertEqual(gl("project_cost"), D("4000.00"))
        with self.assertRaisesMessage(ValidationError, "المتاح للارتجاع 50"):
            self.give_back("51")
        # A reversed return drops out too, and its quantity can come back again.
        cancel_stock_operation(back.pk, TODAY, "ارتجاع بالغلط", self.owner)
        self.assertEqual(self.materials(), D("4800.00"))
        self.assertEqual(self.give_back("60").unit_cost, D("80.0000"))
        self.assertEqual(self.materials(), D("0.00"))
        ledger_ok(self)

    def test_the_engine_itself_still_reverses_an_issue_after_its_return_and_the_books_agree(self):
        """The engine is not changed (a run's own cancellation calls it directly): reversing the issue there leaves the
        project reading the return alone (negative), the ledger agreeing, and nothing more can come back. The screens
        that reverse an operation a person names refuse it first (the next test)."""

        stock_in(self.cement, self.store, 100, "80.00", movement_date=TODAY)
        issued = self.issue("100")
        self.give_back("20")
        cancel_stock_operation(issued.pk, TODAY, "x", self.owner)
        self.assertEqual(self.materials(), D("-1600.00"))
        self.assertEqual(gl("project_cost"), D("-1600.00"))
        ledger_ok(self)
        with self.assertRaisesMessage(ValidationError, "ما اتصرفش للمشروع"):
            self.give_back("1")

    def test_the_stock_screen_refuses_an_issue_something_came_back_from_until_the_return_is_reversed(self):
        from manufacturing.guards import before_stock_operation_reversal
        from settings_core.ui_messages import translate

        stock_in(self.cement, self.store, 100, "80.00", movement_date=TODAY)
        issued = self.issue("100")
        back = self.give_back("20")
        with self.assertRaises(ValidationError) as caught:
            before_stock_operation_reversal(issued.pk)
        message = caught.exception.messages[0]
        self.assertEqual(message, f"This operation is a material issue to project {self.project.code} and 20 of it came back from the site after it. "
                                  "Reverse those returns first, then the issue.")
        self.assertEqual(translate(message), f"الحركة دي صرف خامات لمشروع {self.project.code}، ورجع من الصنف ده 20 من الموقع بعدها. اعكس الارتجاع ده الأول، وبعدين الصرف.")
        self.client.force_login(self.owner)
        cancel = lambda operation: self.client.post(reverse("inventory:operation_cancel", args=[operation.pk]),
                                                    {"lang": "ar", "reversal_date": TODAY.isoformat(), "reason": "اتصرف بالغلط"}, follow=True)
        response = cancel(issued)
        self.assertContains(response, "اعكس الارتجاع ده الأول")
        self.assertEqual((self.materials(), gl("project_cost"), StockOperation.objects.exclude(status=StockOperationStatus.POSTED).count()), (D("6400.00"), D("6400.00"), 0))
        cancel(back)  # the return first: the 20 leave the store again and the project's cost goes back to 8,000.00
        self.assertEqual(self.materials(), D("8000.00"))
        cancel(issued)  # then the issue
        self.assertEqual((self.materials(), gl("project_cost"), get_item_location_stock_quantity(self.cement, self.store)), (D("0.00"), D("0.00"), D("100")))
        ledger_ok(self)

    def test_an_issue_a_later_return_was_priced_from_is_refused_even_when_the_quantity_balances(self):
        """Codex #434: 100 at 80.00 and 100 at 100.00, 100 back at 90.00; reversing the 100.00 issue alone would leave
        −1,000.00 in the project and in 5103 with nothing left to return."""
        from manufacturing.guards import before_stock_operation_reversal

        stock_in(self.cement, self.store, 100, "80.00", movement_date=TODAY)
        self.issue("100")
        stock_in(self.cement, self.store, 100, "100.00", movement_date=TODAY)
        dearer = self.issue("100")
        back = self.give_back("100")
        self.assertEqual((back.unit_cost, self.materials()), (D("90.00"), D("9000.00")))
        with self.assertRaisesMessage(ValidationError, "100 of it came back from the site after it"):
            before_stock_operation_reversal(dearer.pk)
        cancel_stock_operation(back.pk, TODAY, "x", self.owner)  # the return first
        before_stock_operation_reversal(dearer.pk)
        cancel_stock_operation(dearer.pk, TODAY, "x", self.owner)
        self.assertEqual((self.materials(), gl("project_cost")), (D("8000.00"), D("8000.00")))
        ledger_ok(self)

    def test_an_issue_made_after_the_returns_and_a_return_are_reversed_as_before(self):
        from manufacturing.guards import before_stock_operation_reversal

        stock_in(self.cement, self.store, 100, "80.00", movement_date=TODAY)
        self.issue("60")
        back = self.give_back("30")
        later = self.issue("40")
        before_stock_operation_reversal(later.pk)  # no return was priced from it: allowed
        before_stock_operation_reversal(back.pk)  # a return is never held back here
        cancel_stock_operation(later.pk, TODAY, "x", self.owner)
        self.assertEqual(self.materials(), D("2400.00"))  # 60 × 80.00 − 30 × 80.00

    def test_refusals_in_both_languages(self):
        stock_in(self.cement, self.store, 10, "80.00", movement_date=TODAY)
        steel = make_item(item_code="STL", item_name="حديد تسليح (طن)", is_stock_tracked=True)
        stock_in(steel, self.store, 5, "30000.00", movement_date=TODAY)
        with self.assertRaisesMessage(ValidationError, "ما اتصرفش للمشروع"):
            self.give_back("1")
        self.issue("10")
        with self.assertRaisesMessage(ValidationError, "was not issued to this project"):
            returns.return_materials(self.project, self.owner, location=self.store, lines=[{"item": steel, "quantity": "1"}], lang="en")
        with self.assertRaisesMessage(ValidationError, "net quantity issued to this project: 10 can come back"):
            returns.return_materials(self.project, self.owner, location=self.store, lines=[{"item": self.cement, "quantity": "11"}], lang="en")
        with self.assertRaisesMessage(ValidationError, "الكمية لازم أكبر من صفر"):
            self.give_back("0")
        with self.assertRaisesMessage(ValidationError, "اختار المخزن"):
            returns.return_materials(self.project, self.owner, location=None, lines=[{"item": self.cement, "quantity": "1"}])
        with self.assertRaisesMessage(ValidationError, "Enter at least one item"):
            returns.return_materials(self.project, self.owner, location=self.store, lines=[], lang="en")
        # Two lines of one item cannot together exceed what was issued: the second reads the first.
        with self.assertRaisesMessage(ValidationError, "السطر 2"):
            returns.return_materials(self.project, self.owner, location=self.store,
                                     lines=[{"item": self.cement, "quantity": "6"}, {"item": self.cement, "quantity": "5"}])
        self.assertEqual(self.project.issues.count(), 1)  # nothing of the refused voucher stayed
        self.assertEqual(self.materials(), D("800.00"))

    def test_surplus_comes_back_after_the_project_is_done(self):
        stock_in(self.cement, self.store, 10, "80.00", movement_date=TODAY)
        self.issue("10")
        services.save_project({"name": self.project.name, "customer": self.customer, "status": ProjectStatus.DONE}, self.owner, self.project)
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            self.issue("1")  # no new issue on a closed project
        self.assertEqual(self.give_back("4").unit_cost, D("80.0000"))
        self.assertEqual(self.materials(), D("480.00"))


class LedgerTests(ReturnSetup):
    def test_5103_takes_issues_and_returns_and_a_general_adjustment_never_touches_it(self):
        """The projector books an operation linked to a project against 5103 (``hasattr(op, "project_issue")``): an issue
        (out) debits it and a return (in) credits it. A stock adjustment in that is not linked goes to stock gain."""

        stock_in(self.cement, self.store, 50, "80.00", movement_date=TODAY)
        self.issue("50")
        self.assertEqual(gl("project_cost"), D("4000.00"))
        self.give_back("5")
        self.assertEqual(gl("project_cost"), D("3600.00"))
        adjust_stock("ADJ-GEN", TODAY, self.cement, self.store, StockAdjustmentDirection.IN, D("3"), "جرد", self.owner)
        self.assertEqual(gl("project_cost"), D("3600.00"))  # unlinked: stock gain, never the project
        self.assertEqual(self.materials(), D("3600.00"))
        ledger_ok(self)


class VoucherTests(ReturnSetup):
    def setUp(self):
        super().setUp()
        self.steel = make_item(item_code="STL", item_name="حديد تسليح (طن)", is_stock_tracked=True)
        self.sand = make_item(item_code="SND", item_name="رمل (م³)", is_stock_tracked=True)
        stock_in(self.cement, self.store, 100, "80.00", movement_date=TODAY)
        stock_in(self.steel, self.store, 10, "30000.00", movement_date=TODAY)
        stock_in(self.sand, self.store, 5, "200.00", movement_date=TODAY)

    def lines(self, *pairs):
        return [{"item": item, "quantity": quantity} for item, quantity in pairs]

    def test_a_three_line_voucher_writes_three_operations_under_one_reference(self):
        operations = returns.issue_voucher(self.project, self.owner, location=self.store,
                                           lines=self.lines((self.cement, "10"), (self.steel, "2"), (self.sand, "5")))
        self.assertEqual([op.reference_number for op in operations], ["PI-P-0001-001/1", "PI-P-0001-001/2", "PI-P-0001-001/3"])
        self.assertEqual([(op.item, op.quantity, op.unit_cost) for op in operations],
                         [(self.cement, D("10.000"), D("80.0000")), (self.steel, D("2.000"), D("30000.0000")), (self.sand, D("5.000"), D("200.0000"))])
        self.assertEqual(self.materials(), D("61800.00"))  # 800 + 60,000 + 1,000
        self.assertEqual(gl("project_cost"), D("61800.00"))
        self.assertEqual(self.issue("1").reference_number, "PI-P-0001-002")  # a single line keeps the plain reference
        again = returns.issue_voucher(self.project, self.owner, location=self.store, lines=self.lines((self.cement, "1"), (self.steel, "1")))
        self.assertEqual([op.reference_number for op in again], ["PI-P-0001-003/1", "PI-P-0001-003/2"])
        back = returns.return_materials(self.project, self.owner, location=self.store, lines=self.lines((self.cement, "2"), (self.steel, "1")))
        self.assertEqual([(op.reference_number, op.unit_cost) for op in back], [("MR-P-0001-001/1", D("80.0000")), ("MR-P-0001-001/2", D("30000.0000"))])
        self.assertEqual(self.give_back("1").reference_number, "MR-P-0001-002")
        self.assertEqual([op.reference_number for op in returns.voucher_operations(self.project, returns.ISSUE, 1)],
                         ["PI-P-0001-001/1", "PI-P-0001-001/2", "PI-P-0001-001/3"])
        ledger_ok(self)

    def test_one_failing_line_rolls_back_the_whole_voucher(self):
        before = (StockOperation.objects.count(), StockMovement.objects.count(), ProjectIssue.objects.count())
        stock = {item.pk: (get_item_location_stock_quantity(item, self.store), get_item_authoritative_average_cost(item))
                 for item in (self.cement, self.steel, self.sand)}
        # Lines 1 and 2 reach the engine and are written; line 3 asks for 6 of the 5 in stock and the engine refuses it.
        with self.assertRaisesMessage(ValidationError, "السطر 3 (رمل (م³)): الكمية مش كفاية: المتاح 5"):
            returns.issue_voucher(self.project, self.owner, location=self.store,
                                  lines=self.lines((self.cement, "10"), (self.steel, "2"), (self.sand, "6")))
        self.assertEqual((StockOperation.objects.count(), StockMovement.objects.count(), ProjectIssue.objects.count()), before)
        self.assertEqual({item.pk: (get_item_location_stock_quantity(item, self.store), get_item_authoritative_average_cost(item))
                          for item in (self.cement, self.steel, self.sand)}, stock)
        self.assertEqual(self.materials(), D("0.00"))
        self.assertEqual(self.issue("1").reference_number, "PI-P-0001-001")  # the refused voucher used no number

    def test_line_refusals_name_the_line(self):
        service = make_item(item_code="SRV", item_name="نقل", is_stock_tracked=False)
        for lines, text in ((self.lines((self.cement, "1"), (service, "1")), "السطر 2 (نقل): الصنف ده مش متتبع"),
                            (self.lines((self.cement, "1"), (self.steel, "")), "السطر 2 (حديد تسليح (طن)): الكمية لازم أكبر من صفر"),
                            (self.lines((self.cement, "1"), (None, "3")), "السطر 2 (—): اختار الصنف")):
            with self.subTest(text=text), self.assertRaisesMessage(ValidationError, text):
                returns.issue_voucher(self.project, self.owner, location=self.store, lines=lines)
        with self.assertRaisesMessage(ValidationError, "up to 30 lines"):
            returns.issue_voucher(self.project, self.owner, location=self.store, lines=self.lines(*[(self.cement, "1")] * 31), lang="en")
        self.assertFalse(ProjectIssue.objects.exists())


class ScreenTests(ReturnSetup):
    def setUp(self):
        super().setUp()
        self.steel = make_item(item_code="STL", item_name="حديد تسليح (طن)", is_stock_tracked=True)
        stock_in(self.cement, self.store, 100, "80.00", movement_date=TODAY)
        stock_in(self.steel, self.store, 10, "30000.00", movement_date=TODAY)
        self.engineer = person(RoleCode.SITE_ENGINEER, "c12_eng")
        self.url = reverse("projects:detail", args=[self.project.pk])

    def post_voucher(self, action, rows, lang="ar"):
        items = [str(item.pk) if item else "" for item, _ in rows]
        quantities = [quantity for _, quantity in rows]
        return self.client.post(self.url, {"action": action, "lang": lang, "location": str(self.store.pk), "item": items, "quantity": quantities})

    def test_an_engineer_issues_a_voucher_and_returns_from_the_screens(self):
        self.client.force_login(self.engineer)
        page = self.client.get(self.url)
        self.assertContains(page, "data-issue-form")
        self.assertContains(page, 'data-voucher-line="5"')
        self.assertContains(page, "data-nothing-out")  # nothing issued yet, so nothing to return
        made = self.post_voucher("issue", [(self.cement, "30"), (self.steel, "2"), (None, ""), (None, ""), (None, "")])
        self.assertRedirects(made, f"{self.url}?lang=ar", fetch_redirect_response=False)
        self.assertEqual(list(self.project.issues.values_list("operation__reference_number", "operation__created_by")),
                         [("PI-P-0001-001/1", self.engineer.pk), ("PI-P-0001-001/2", self.engineer.pk)])
        back = self.post_voucher("return", [(self.cement, "5"), (None, "")])
        self.assertEqual(back.status_code, 302)
        operation = self.project.issues.get(operation__reference_number="MR-P-0001-001").operation
        self.assertEqual((operation.quantity, operation.unit_cost, operation.created_by), (D("5.000"), D("80.0000"), self.engineer))
        self.assertEqual(get_item_location_stock_quantity(self.cement, self.store), D("75"))
        page = self.client.get(self.url)
        self.assertContains(page, 'data-project-issue="MR-P-0001-001" data-direction="return"')
        self.assertContains(page, 'data-project-issue="PI-P-0001-001/1" data-direction="issue"')
        self.assertContains(page, reverse("projects:voucher_print", args=[self.project.pk, "issue", 1]))
        self.assertContains(page, reverse("projects:voucher_print", args=[self.project.pk, "return", 1]))
        self.assertContains(page, "data-return-form")
        self.assertContains(page, "data-returnable")
        self.assertNotContains(page, 'data-figure="cost"')  # the engineer still sees no cost
        refused = self.post_voucher("return", [(self.cement, "26")])
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, "المتاح للارتجاع 25")
        self.assertEqual(self.project.issues.count(), 3)

    def test_a_stock_keeper_without_the_stand_in_can_neither_issue_nor_return(self):
        services.issue_materials(self.project, self.owner, item=self.cement, location=self.store, quantity="10")
        keeper = person(RoleCode.STOCK_KEEPER, "c12_keeper")
        self.client.force_login(keeper)
        before = (StockOperation.objects.count(), get_item_location_stock_quantity(self.cement, self.store))
        self.assertEqual(self.post_voucher("issue", [(self.cement, "1")]).status_code, 403)
        self.assertEqual(self.post_voucher("return", [(self.cement, "1")]).status_code, 403)
        self.assertEqual(self.client.get(reverse("projects:voucher_print", args=[self.project.pk, "issue", 1])).status_code, 403)
        self.assertEqual((StockOperation.objects.count(), get_item_location_stock_quantity(self.cement, self.store)), before)
        self.assertEqual(self.materials(), D("800.00"))
        # Someone holding neither the stand-in nor general stock adjustment is refused by the engine itself.
        cashier = person(RoleCode.CASHIER, "c12_cashier")
        with self.assertRaises(PermissionDenied):
            self.give_back("1", user=cashier)
        self.assertEqual(self.project.issues.count(), 1)

    def test_a_three_line_voucher_from_the_screen_rolls_back_whole(self):
        self.client.force_login(self.owner)
        before = StockOperation.objects.count()
        page = self.post_voucher("issue", [(self.cement, "10"), (self.steel, "2"), (self.steel, "9")])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "السطر 3")
        self.assertEqual(StockOperation.objects.count(), before)
        self.assertFalse(self.project.issues.exists())

    def test_the_old_single_issue_post_still_works(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {"action": "issue", "item": str(self.cement.pk), "location": str(self.store.pk), "quantity": "12"})
        self.assertEqual(self.project.issues.get().operation.reference_number, "PI-P-0001-001")


class PrintTests(ReturnSetup):
    def setUp(self):
        super().setUp()
        self.steel = make_item(item_code="STL", item_name="حديد تسليح (طن)", is_stock_tracked=True)
        stock_in(self.cement, self.store, 100, "80.00", movement_date=TODAY)
        stock_in(self.steel, self.store, 10, "30000.00", movement_date=TODAY)
        returns.issue_voucher(self.project, self.owner, location=self.store,
                              lines=[{"item": self.cement, "quantity": "20"}, {"item": self.steel, "quantity": "2"}])
        self.give_back("4")

    def page(self, kind, number, lang):
        return self.client.get(reverse("projects:voucher_print", args=[self.project.pk, kind, number]) + f"?lang={lang}")

    def test_the_issue_voucher_prints_in_arabic_and_english_with_both_signatures(self):
        self.client.force_login(self.owner)
        arabic = self.page("issue", 1, "ar")
        self.assertContains(arabic, '<html lang="ar" dir="rtl">', html=False)
        for text in ("إذن صرف خامات", "PI-P-0001-001", "أمين المخزن", "مهندس الموقع", 'data-sign="storekeeper"', 'data-sign="engineer"',
                     'data-print-line="2"', "أسمنت (شكارة)", "حديد تسليح (طن)", 'data-format="a4"', "عمارة النور"):
            self.assertContains(arabic, text)
        self.assertContains(arabic, 'data-print-total>61,600.00')  # 20 × 80 + 2 × 30,000, for a manager
        self.assertNotContains(arabic, "PI-P-0001-001/")  # the voucher's number, not one line's
        english = self.page("issue", 1, "en")
        self.assertContains(english, '<html lang="en" dir="ltr">', html=False)
        for text in ("Materials issue voucher", "Storekeeper", "Site engineer", "Voucher no."):
            self.assertContains(english, text)
        self.assertNotContains(english, "إذن صرف")

    def test_the_same_print_serves_the_return_voucher(self):
        self.client.force_login(self.owner)
        arabic = self.page("return", 1, "ar")
        for text in ("إذن ارتجاع خامات", "MR-P-0001-001", "إلى مخزن", 'data-print-note', 'data-sign="storekeeper"', 'data-sign="engineer"',
                     'data-voucher-kind="return"'):
            self.assertContains(arabic, text)
        self.assertContains(arabic, 'data-print-total>320.00')
        self.assertContains(self.page("return", 1, "en"), "Materials return voucher")

    def test_the_engineer_prints_quantities_without_cost_and_unknown_vouchers_are_404(self):
        self.client.force_login(person(RoleCode.SITE_ENGINEER, "c12_print_eng"))
        page = self.page("issue", 1, "ar")
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-print-total")
        self.assertNotContains(page, "30,000")
        for kind, number in (("issue", 9), ("return", 2), ("other", 1)):
            self.assertEqual(self.page(kind, number, "ar").status_code, 404, (kind, number))


class ScannerTests(ReturnSetup):
    def test_no_banned_word_no_colour_literal_and_no_prefilled_data_label(self):
        from .views import VOUCHER_WORDS, WORDS

        templates = [Path(settings.BASE_DIR, "templates/projects", name).read_text(encoding="utf-8") for name in ("voucher_print.html", "detail.html")]
        texts = templates + [Path(settings.BASE_DIR, "projects/returns.py").read_text(encoding="utf-8"),
                             str(WORDS["ar"]), str(WORDS["en"]), str(VOUCHER_WORDS["ar"]), str(VOUCHER_WORDS["en"])]
        for word in BANNED:
            for text in texts:
                with self.subTest(word=word):
                    self.assertNotIn(word, text)
        for source in templates:
            self.assertNotRegex(source, r"#[0-9a-fA-F]{3,8}\b")
            self.assertNotRegex(source, r"rgba?\(")
            self.assertNotRegex(source, r"<t[dh][^>]*\sdata-label=")
            self.assertIn("projects.css' %}?v=c1-vo1", source)  # the pinned stylesheet, unchanged
        self.assertTrue(re.search(r"tokens\.css", templates[0]))
