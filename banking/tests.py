"""T3 (MARKET-PARITY): bank accounts, bank fees and matching the bank's statement (HG-055)."""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox, CashboxKind, CashboxMovement, CashboxOperationType
from cashboxes.services import cancel_cashbox_operation, create_cashbox_operation, get_cashbox_balance
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from ledger import reports
from ledger.models import Account, JournalLine
from ledger.projector import Projector
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import services
from .models import StatementLine

TODAY = timezone.localdate()
D1, D2, D3 = TODAY - timedelta(days=6), TODAY - timedelta(days=5), TODAY - timedelta(days=3)


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def bal(code):
    agg = JournalLine.objects.filter(account__code=code).aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or D("0")) - (agg["c"] or D("0"))


def csv_file(text, name="statement.csv"):
    return SimpleUploadedFile(name, text.encode("utf-8"), content_type="text/csv")


STATEMENT = ("التاريخ,البيان,المرجع,مدين,دائن,الرصيد\n"
             f"{D1 + timedelta(days=1):%d/%m/%Y},إيداع نقدي,DEP-9,,\"2,000.00\",\"12,000.00\"\n"
             f"{D3:%d/%m/%Y},عمولة تحويل,,15.00,,\"11,985.00\"\n"
             f"{D3:%d/%m/%Y},فوائد,,,3.00,\"11,988.00\"\n")


class BankSetup(TestCase):
    def setUp(self):
        prepared_client("commercial", "wholesale")
        self.owner = person(RoleCode.OWNER, "bk_owner")
        self.accountant = person(RoleCode.ACCOUNTANT, "bk_accountant")
        self.cashier = person(RoleCode.CASHIER, "bk_cashier")
        self.cash = Cashbox.objects.create(cashbox_code="CASH-BK", name_ar="الخزنة", opening_balance=D("5000.00"))
        self.bank = Cashbox.objects.create(cashbox_code="BANK-1", name_ar="البنك الأهلي", kind=CashboxKind.BANK, bank_name="البنك الأهلي",
                                           account_number="1234567", opening_balance=D("10000.00"))

    def transfer(self, amount, day, source=None, destination=None, ref="TR-1"):
        return create_cashbox_operation(ref, day, CashboxOperationType.TRANSFER, D(amount), "إيداع في البنك", self.owner,
                                        source_cashbox=source or self.cash, destination_cashbox=destination or self.bank)

    def fee(self, amount, day, ref="FEE-1", cashbox=None):
        return create_cashbox_operation(ref, day, CashboxOperationType.BANK_FEE, D(amount), "عمولة", self.owner, source_cashbox=cashbox or self.bank)


class BankInTheBooksTests(BankSetup):
    def test_a_new_cashbox_is_cash_unless_chosen_bank(self):
        self.assertEqual(Cashbox.objects.create(cashbox_code="NEW-1", name_ar="جديدة").kind, CashboxKind.CASH)

    def test_cash_to_bank_and_a_fee_land_in_their_accounts_and_the_books_reconcile(self):
        self.transfer("2000.00", D1)
        fee = self.fee("15.00", D3)
        self.assertEqual(get_cashbox_balance(self.bank), D("11985.00"))
        Projector().rebuild()
        self.assertEqual(bal("1101"), D("3000.00"))   # cash on hand: 5000 - 2000
        self.assertEqual(bal("1102"), D("11985.00"))  # banks: 10000 + 2000 - 15
        self.assertEqual(bal("5212"), D("15.00"))     # bank charges, not owner drawings
        self.assertEqual(bal("1199"), D("0"))         # nothing in suspense
        result = reports.reconciliation()
        self.assertTrue(result["ok"], result["rows"])
        self.assertTrue(result["balanced"])
        self.assertTrue(reports.trial_balance()["balanced"])
        # Cancelling the fee reverses it in the same accounts.
        cancel_cashbox_operation(fee.pk, TODAY, "اتردت", self.owner)
        Projector().rebuild()
        self.assertEqual(bal("5212"), D("0"))
        self.assertEqual(bal("1102"), D("12000.00"))
        self.assertTrue(reports.reconciliation()["ok"])

    def test_a_cashbox_turned_bank_moves_between_accounts_and_nothing_else_changes(self):
        other = Cashbox.objects.create(cashbox_code="SAFE-2", name_ar="خزنة تانية", opening_balance=D("700.00"))
        self.transfer("300.00", D1, destination=other)
        Projector().rebuild()
        before = reports.trial_balance()["totals"]
        money_before = bal("1101") + bal("1102")
        Cashbox.objects.filter(pk=other.pk).update(kind=CashboxKind.BANK)
        Projector().rebuild()
        self.assertEqual(bal("1101") + bal("1102"), money_before)
        self.assertEqual(bal("1102"), D("10000.00") + D("1000.00"))  # the bank + the old cashbox's 700 + 300
        self.assertEqual(reports.trial_balance()["totals"], before)
        self.assertTrue(reports.reconciliation()["ok"])

    def test_a_bank_fee_counts_in_the_months_expenses_until_cancelled(self):
        from expenses.services import expense_total

        fee = self.fee("15.00", D3)
        self.assertEqual(expense_total(D3, D3), D("15.00"))
        self.assertEqual(expense_total(TODAY, TODAY), D("0.00"))
        cancel_cashbox_operation(fee.pk, TODAY, "اتردت", self.owner)
        self.assertEqual(expense_total(D3, D3), D("0.00"))

    def test_another_entitys_bank_fee_is_not_this_entitys_expense(self):
        from entities.current import working_in
        from entities.models import Entity
        from entities.services import main_entity
        from expenses.services import expense_total

        branch = Entity.objects.create(code="E-FEE", name_ar="فرع")
        theirs = Cashbox.objects.create(cashbox_code="BANK-F", name_ar="بنك الفرع", kind=CashboxKind.BANK, bank_name="QNB", entity=branch,
                                        opening_balance=D("100.00"))
        self.fee("15.00", D3)
        self.fee("7.00", D3, ref="FEE-B", cashbox=theirs)
        with working_in(main_entity()):
            self.assertEqual(expense_total(D3, D3), D("15.00"))
        with working_in(branch):
            self.assertEqual(expense_total(D3, D3), D("7.00"))
        self.assertEqual(expense_total(D3, D3), D("22.00"))  # the whole group

    def test_a_fee_is_only_taken_from_a_bank_and_never_below_zero(self):
        with self.assertRaisesMessage(ValidationError, "bank account only"):
            self.fee("10.00", D3, cashbox=self.cash)
        with self.assertRaisesMessage(ValidationError, "negative"):
            self.fee("99999.00", D3)

    def test_the_bank_charges_account_takes_its_key_on_upgraded_installs(self):
        from importlib import import_module

        from django.apps import apps

        migration = import_module("ledger.migrations.0003_bank_charges_control")
        Projector().rebuild()
        Account.objects.filter(control="bank_charges").update(control="", is_system=False)
        migration.forwards(apps, None)
        self.assertEqual(Account.objects.get(control="bank_charges").code, "5212")
        Account.objects.filter(code="5212").update(control="", name_en="Our own bank costs")
        migration.forwards(apps, None)
        self.assertFalse(Account.objects.filter(control="bank_charges").exists())  # renamed by the user: left alone


class StatementTests(BankSetup):
    def setUp(self):
        super().setUp()
        self.transfer("2000.00", D1)
        self.cheque = create_cashbox_operation("OUT-1", D2, CashboxOperationType.DIRECT_OUT, D("500.00"), "شيك لمورد", self.owner, source_cashbox=self.bank)

    def test_reading_a_bank_file(self):
        lines, closing = services.read_statement(csv_file(STATEMENT))
        self.assertEqual([line["amount"] for line in lines], [D("2000.00"), D("-15.00"), D("3.00")])
        self.assertEqual(closing, D("11988.00"))
        self.assertEqual(services.parse_amount("(1,250.50)"), D("-1250.50"))
        self.assertEqual(services.parse_amount("٣٠٠٫٥٠ ج.م"), D("300.50"))
        self.assertEqual(services.parse_amount("1,000.00-"), D("-1000.00"))
        with self.assertRaisesMessage(ValidationError, "سطر 2"):
            services.read_statement(csv_file("date,amount\nyesterday,10\n"))
        with self.assertRaisesMessage(ValidationError, "عمود التاريخ"):
            services.read_statement(csv_file("when,how much\n2026-01-01,10\n"))

    def test_a_newest_first_file_takes_the_last_days_balance_and_a_bad_cell_is_refused(self):
        newest_first = ("date,description,debit,credit,balance\n"
                        f"{D3:%Y-%m-%d},interest,,3.00,11988.00\n{D3:%Y-%m-%d},fee,15.00,,11985.00\n{D2:%Y-%m-%d},deposit,,2000.00,12000.00\n")
        _, closing = services.read_statement(csv_file(newest_first))
        self.assertEqual(closing, D("11988.00"))
        with self.assertRaisesMessage(ValidationError, "المبلغ مش مفهوم"):
            services.read_statement(csv_file(f"date,debit,credit\n{D3:%Y-%m-%d},abc,\n"))
        with self.assertRaisesMessage(ValidationError, "المبلغ مش مفهوم"):
            services.read_statement(csv_file(f"date,debit,credit\n{D3:%Y-%m-%d},n/a,50.00\n"))

    def test_an_older_uncleared_movement_carries_into_the_next_statement(self):
        old_day = TODAY - timedelta(days=40)
        services.import_statement(self.bank, csv_file(f"date,amount\n{old_day:%Y-%m-%d},1.00\n"), self.accountant)  # last month's statement
        create_cashbox_operation("OLD-1", old_day + timedelta(days=1), CashboxOperationType.DIRECT_OUT, D("70.00"), "شيك قديم", self.owner,
                                 source_cashbox=self.bank)
        statement, _ = services.import_statement(self.bank, csv_file(STATEMENT), self.accountant)
        outstanding = services.reconciliation(statement)["outstanding"]
        self.assertIn("OLD-1", [movement.cashbox_operation.reference_number for movement in outstanding])

    def test_import_matches_by_itself_and_the_fee_is_recorded_from_its_line(self):
        balance_before = get_cashbox_balance(self.bank)
        statement, matched = services.import_statement(self.bank, csv_file(STATEMENT), self.accountant)
        self.assertEqual(matched, 1)  # the deposit, one day after the books
        self.assertEqual(get_cashbox_balance(self.bank), balance_before)  # matching moves no money
        deposit = statement.lines.get(amount=D("2000.00"))
        self.assertEqual(deposit.movement.cashbox_operation.reference_number, "TR-1")
        fee_line = statement.lines.get(amount=D("-15.00"))
        services.record_fee(fee_line, self.accountant)
        fee_line.refresh_from_db()
        self.assertEqual(fee_line.movement.movement_type, "bank_fee")
        self.assertEqual(get_cashbox_balance(self.bank), balance_before - D("15.00"))
        report = services.reconciliation(statement)
        self.assertEqual(report["book"], D("11485.00"))              # 10000 + 2000 - 500 - 15
        self.assertEqual(report["outstanding_total"], D("-500.00"))  # the cheque the bank has not paid yet
        self.assertEqual(report["unrecorded_total"], D("3.00"))      # interest not in the books
        self.assertEqual(report["expected"], D("11988.00"))
        self.assertEqual(report["difference"], D("0.00"))
        Projector().rebuild()
        self.assertEqual(bal("5212"), D("15.00"))
        self.assertTrue(reports.reconciliation()["ok"])

    def test_each_statement_is_read_at_its_own_cutoff(self):
        # Statement A: the deposit cleared, the cheque not yet. 10000 + 2000 = 12000 at the bank.
        a, _ = services.import_statement(self.bank, csv_file(f"date,amount\n{D2:%Y-%m-%d},2000.00\n"), self.accountant, closing_balance="12000.00")
        self.assertEqual(services.reconciliation(a)["difference"], D("0.00"))
        # Statement B clears the cheque; A's figures do not move.
        b, _ = services.import_statement(self.bank, csv_file(f"date,amount\n{D3:%Y-%m-%d},-500.00\n"), self.accountant, closing_balance="11500.00")
        self.assertIsNotNone(b.lines.get().movement_id)
        self.assertEqual(services.reconciliation(a)["difference"], D("0.00"))
        self.assertEqual(services.reconciliation(b)["difference"], D("0.00"))

    def test_a_line_matched_to_a_movement_booked_after_the_cutoff_still_counts(self):
        create_cashbox_operation("LATE-1", D3 + timedelta(days=1), CashboxOperationType.DIRECT_OUT, D("70.00"), "سحب", self.owner, source_cashbox=self.bank)
        statement, matched = services.import_statement(self.bank, csv_file(f"date,amount\n{D3:%Y-%m-%d},-70.00\n"), self.accountant,
                                                       closing_balance="9930.00")  # the bank saw only the 70
        self.assertEqual(matched, 1)
        report = services.reconciliation(statement)
        self.assertEqual(report["unrecorded_total"], D("-70.00"))  # matched, but booked the day after the statement ends
        self.assertEqual(report["difference"], D("0.00"))

    def test_an_excel_statement_keeps_every_digit(self):
        import io

        from openpyxl import Workbook

        book = Workbook()
        sheet = book.active
        sheet.append(["date", "description", "amount", "balance"])
        sheet.append([D3, "big transfer", 1234567.89, 1244567.89])
        data = io.BytesIO()
        book.save(data)
        lines, closing = services.read_statement(SimpleUploadedFile("statement.xlsx", data.getvalue()))
        self.assertEqual((lines[0]["amount"], closing), (D("1234567.89"), D("1244567.89")))
        self.assertEqual(services.parse_amount("1.23456789e+06"), D("1234567.89"))

    def test_a_statement_uploaded_twice_or_overlapping_brings_each_line_once(self):
        services.import_statement(self.bank, csv_file(STATEMENT), self.accountant)
        with self.assertRaisesMessage(ValidationError, "اترفعت قبل كده"):
            services.import_statement(self.bank, csv_file(STATEMENT), self.accountant)
        later = STATEMENT + f"{TODAY:%d/%m/%Y},إيداع,,,50.00,\"12,038.00\"\n"
        statement, _ = services.import_statement(self.bank, csv_file(later), self.accountant)
        self.assertEqual([line.amount for line in statement.lines.all()], [D("50.00")])
        self.assertEqual(StatementLine.objects.filter(amount=D("-15.00")).count(), 1)

    def test_a_reversal_after_the_cutoff_leaves_the_statement_as_it_was(self):
        a, _ = services.import_statement(self.bank, csv_file(f"date,amount\n{D2:%Y-%m-%d},2000.00\n"), self.accountant, closing_balance="12000.00")
        self.assertEqual(services.reconciliation(a)["difference"], D("0.00"))  # the 500 cheque outstanding
        cancel_cashbox_operation(self.cheque.pk, TODAY, "اتلغى بعدين", self.owner)
        self.assertEqual(services.reconciliation(a)["difference"], D("0.00"))

    def test_an_overlapping_upload_keeps_the_files_own_last_day(self):
        first, _ = services.import_statement(self.bank, csv_file(f"date,amount\n{D3:%Y-%m-%d},-500.00\n"), self.accountant, closing_balance="9500.00")
        self.assertEqual(services.reconciliation(first)["difference"], D("0.00"))
        second, _ = services.import_statement(self.bank, csv_file(f"date,amount\n{D2:%Y-%m-%d},2000.00\n{D3:%Y-%m-%d},-500.00\n"), self.accountant,
                                              closing_balance="11500.00")
        self.assertEqual((second.lines.count(), second.ends_on), (1, D3))  # the repeated line is not kept twice
        self.assertEqual(services.reconciliation(second)["difference"], D("0.00"))

    def test_the_refund_of_a_cleared_fee_stays_outstanding_until_the_bank_shows_it(self):
        fee = create_cashbox_operation("FEE-X", D2, CashboxOperationType.BANK_FEE, D("15.00"), "عمولة", self.owner, source_cashbox=self.bank)
        a, _ = services.import_statement(self.bank, csv_file(f"date,amount\n{D2:%Y-%m-%d},2000.00\n{D2:%Y-%m-%d},-500.00\n{D2:%Y-%m-%d},-15.00\n"),
                                         self.accountant, closing_balance="11485.00")
        self.assertEqual(services.reconciliation(a)["difference"], D("0.00"))
        cancel_cashbox_operation(fee.pk, D3, "البنك هيرجعها", self.owner)
        b, _ = services.import_statement(self.bank, csv_file(f"date,amount\n{TODAY:%Y-%m-%d},1.00\n"), self.accountant, closing_balance="11486.00")
        report = services.reconciliation(b)
        self.assertEqual(report["outstanding_total"], D("15.00"))  # the refund the bank has not made yet
        self.assertEqual(report["difference"], D("0.00"))

    def test_manual_match_unmatch_and_the_refusals(self):
        statement, _ = services.import_statement(self.bank, csv_file(STATEMENT), self.accountant)
        deposit = statement.lines.get(amount=D("2000.00"))
        movement = deposit.movement
        services.unmatch(deposit, self.accountant)
        deposit.refresh_from_db()
        self.assertIsNone(deposit.movement)
        services.match(deposit, movement, self.accountant)
        interest = statement.lines.get(amount=D("3.00"))
        with self.assertRaisesMessage(ValidationError, "المبلغ مش زي الكشف"):
            services.match(interest, self.cheque.movements.get(), self.accountant)
        cash_movement = CashboxMovement.objects.get(cashbox=self.cash)
        with self.assertRaisesMessage(ValidationError, "حساب تاني"):
            services.match(interest, cash_movement, self.accountant)
        with self.assertRaisesMessage(ValidationError, "متطابق بالفعل"):
            services.match(deposit, movement, self.accountant)
        with self.assertRaisesMessage(ValidationError, "مش مبلغ اتخصم"):
            services.record_fee(interest, self.accountant)
        with self.assertRaises(PermissionDenied):
            services.import_statement(self.bank, csv_file(STATEMENT), self.cashier)
        services.delete_statement(statement, self.accountant)
        self.assertFalse(StatementLine.objects.exists())
        self.assertTrue(CashboxMovement.objects.filter(pk=movement.pk).exists())

    def test_a_cancelled_operation_is_neither_offered_nor_outstanding(self):
        cancel_cashbox_operation(self.cheque.pk, D3, "اتلغى", self.owner)
        statement, _ = services.import_statement(self.bank, csv_file(STATEMENT), self.accountant)
        self.assertEqual(services.reconciliation(statement)["outstanding"], [])


class ScreenTests(BankSetup):
    def test_the_accountant_uploads_and_matches_from_the_screen(self):
        self.transfer("2000.00", D1)
        self.client.force_login(self.accountant)
        for lang in ("ar", "en"):
            page = self.client.get(reverse("banking:list") + f"?lang={lang}")
            self.assertContains(page, f'data-bank="{self.bank.pk}"')
            self.assertNotContains(page, f'data-bank="{self.cash.pk}"')
        url = reverse("banking:account", args=[self.bank.pk])
        response = self.client.post(url, {"lang": "ar", "action": "import", "file": csv_file(STATEMENT)})
        self.assertEqual(response.status_code, 302)
        page = self.client.get(response["Location"])
        self.assertContains(page, "data-matched-line")
        fee_line = StatementLine.objects.get(amount=D("-15.00"))
        self.assertContains(page, f'data-fee="{fee_line.pk}"')
        self.client.post(url, {"lang": "ar", "action": "fee", "line": fee_line.pk, "statement": fee_line.statement_id})
        fee_line.refresh_from_db()
        self.assertIsNotNone(fee_line.movement_id)
        self.assertContains(self.client.get(url + "?lang=en"), "The statement should say")
        bad = self.client.post(url, {"lang": "ar", "action": "import", "file": csv_file("x,y\n1,2\n")})
        self.assertContains(bad, "عمود التاريخ")

    def test_an_older_statement_opens_by_its_id(self):
        self.client.force_login(self.accountant)
        url = reverse("banking:account", args=[self.bank.pk])
        old, _ = services.import_statement(self.bank, csv_file(f"date,amount\n{D1:%Y-%m-%d},1.00\n"), self.accountant)
        for day in range(25):
            services.import_statement(self.bank, csv_file(f"date,amount\n{D3:%Y-%m-%d},{day + 2}.00\n"), self.accountant)
        page = self.client.get(url + f"?lang=ar&statement={old.pk}")
        self.assertContains(page, f'data-line="{old.lines.get().pk}"')
        self.assertEqual(self.client.get(url + "?lang=ar&statement=999999").status_code, 404)
        self.client.post(url, {"lang": "ar", "action": "delete", "statement": old.pk})
        self.assertFalse(services.statements(self.bank).filter(pk=old.pk).exists())
        self.assertEqual(services.statements(self.bank).count(), 25)  # the old one went, not the newest

    def test_who_sees_the_banks(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("banking:list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("banking:account", args=[self.bank.pk])).status_code, 403)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard_snapshot") + "?lang=ar"), 'href="/banks/?lang=ar"')
        self.assertEqual(self.client.get(reverse("banking:account", args=[self.cash.pk])).status_code, 404)  # a cashbox is not a bank account

    def test_the_cashbox_form_takes_the_bank_details(self):
        self.client.force_login(self.owner)
        create = reverse("master_data:create", kwargs={"entity": "cashboxes"})
        page = self.client.get(create + "?lang=ar&kind=bank")
        self.assertContains(page, 'value="bank" selected')
        response = self.client.post(create + "?lang=ar", {"lang": "ar", "cashbox_code": "BANK-2", "name_ar": "بنك مصر", "kind": "bank", "bank_name": "",
                                                          "currency": "EGP", "active": "on"})
        self.assertContains(response, "اكتب اسم البنك")
        response = self.client.post(create + "?lang=ar", {"lang": "ar", "cashbox_code": "BANK-2", "name_ar": "بنك مصر", "kind": "bank", "bank_name": "بنك مصر",
                                                          "iban": "EG380019000500000000263180002", "currency": "EGP", "active": "on"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Cashbox.objects.get(cashbox_code="BANK-2").kind, CashboxKind.BANK)
        self.assertContains(self.client.get(reverse("cashboxes:list") + "?lang=ar"), "data-bank-kind")

    def test_another_entitys_bank_account_is_not_found(self):
        from entities.models import Entity

        branch = Entity.objects.create(code="E-BK", name_ar="فرع")
        theirs = Cashbox.objects.create(cashbox_code="BANK-B", name_ar="بنك الفرع", kind=CashboxKind.BANK, bank_name="QNB", entity=branch)
        self.client.force_login(self.owner)
        session = self.client.session
        session["hesba_entity"] = Entity.objects.get(is_main=True).pk
        session.save()
        self.assertEqual(self.client.get(reverse("banking:account", args=[theirs.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("banking:account", args=[self.bank.pk])).status_code, 200)
