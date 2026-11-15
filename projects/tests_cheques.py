"""Contracting C9 (HG-122): cheques on the project.

A cheque the owner hands over for a certificate is received from the project's payments tab: the unchanged cheques engine
(``cheques.services.receive``) records the customer payment in the cheques-in-hand box, and the same transaction marks it
as a collection of the project. The project then reads the cheque's own status:

* in hand or deposited: «شيكات تحت التحصيل», off what is due now and shown as its own figure;
* cleared: collected;
* bounced: neither, so what is due now comes back by itself;
* cancelled: its payment is cancelled, so it drops out as any cancelled payment does.

An advance stays cleared-only (owner question 6), and a cheque linked as an advance can never bounce.

Figures on the reference contract of C1 (``tests_variation_baseline``): advance 100,000.00 received, certificate 1 posted
(gross 200,000.00, retention 10,000.00, recovery 20,000.00, net payable 170,000.00; VAT 14% on: invoice 228,000.00 and
net 198,000.00). The clock is fixed on Wednesday 11 November 2026.
"""

from datetime import timedelta
from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.urls import reverse

from cashboxes.models import Cashbox, CashboxKind
from cheques import services as cheques
from cheques.models import Cheque, ChequeStatus
from hesba_testing.factories import grant, make_cashbox, make_customer, make_role, make_user, make_user_profile
from ledger.projector import ensure_fresh
from permissions.models import Permission, RoleCode
from sales.models import CustomerPayment

from . import contract, insights
from .models import ProjectPayment, ProjectPaymentKind
from .tests_contract import customer_balance, ledger_ok, person
from .tests_variation_baseline import DAY, ReferenceContract

ZERO = D("0.00")


class ChequeSetup(ReferenceContract):
    """The reference contract with certificate 1 posted, and a bank to deposit cheques in."""

    net = D("170000.00")
    balance = D("100000.00")  # certificate 1's invoice less the 100,000.00 advance

    def setUp(self):
        super().setUp()
        self.first = self.certify(a="100", b="250")
        self.bank = Cashbox.objects.create(cashbox_code="BANK-C9", name_ar="البنك الأهلي", kind=CashboxKind.BANK, bank_name="البنك الأهلي")

    def data(self, number="100245", amount=None, due=None, **extra):
        return {"number": number, "bank_name": "بنك مصر", "due_date": (due or DAY + timedelta(days=30)).isoformat(),
                "amount": str(amount if amount is not None else self.net), "received_on": DAY.isoformat(), **extra}

    def receive(self, **kwargs):
        """The new path: the payments tab's «استلام شيك»."""

        return contract.receive_cheque(self.project, self.owner, data=self.data(**kwargs))

    def engine_linked(self, kind=ProjectPaymentKind.COLLECTION, **kwargs):
        """A cheque in hand linked to the project the way C9 links it, built from the engine alone (no C9 code), so the
        behaviour change in ``bounce`` and in the project's figures is shown against the code as it stood."""

        cheque = cheques.receive(self.customer, self.data(**kwargs), self.owner)
        ProjectPayment.objects.create(project=self.project, payment=cheque.customer_payment, kind=kind, created_by=self.owner)
        return cheque

    def figures(self):
        figures = contract.position(self.project)
        return figures["due_now"], figures["under_collection"], figures["collections"]

    def holds(self):
        """The owner's account = due now + retention held − the advance left (a bounced cheque counts as unpaid), and the
        projects list's grouped figure agrees with the project's own."""

        figures = contract.position(self.project)
        self.assertEqual(customer_balance(self.customer), figures["due_now"] + figures["retention_held"] - figures["advance_left"])
        self.assertEqual(contract.due_now_many([self.project]), {self.project.pk: figures["due_now"]})

    def overdue(self, days_late=1):
        from reports.aging import credit_days

        rows, total, _ = insights.overdue_certificates(today=DAY + timedelta(days=credit_days() + days_late))
        return [(row["certificate"].pk, row["owed"]) for row in rows], total


class ReceivingTests(ChequeSetup):
    def test_before_any_cheque(self):
        self.assertEqual(self.figures(), (self.net, ZERO, ZERO))
        self.assertEqual(customer_balance(self.customer), self.balance)
        self.assertEqual(self.overdue(), ([(self.first.pk, self.net)], self.net))
        self.holds()

    def test_a_cheque_in_hand_is_under_collection_and_the_certificate_is_not_overdue(self):
        cheque = self.receive()
        self.assertEqual((cheque.status, cheque.amount, cheque.due_date, cheque.box.kind), (ChequeStatus.IN_HAND, self.net, DAY + timedelta(days=30), CashboxKind.CHEQUES_IN))
        link = ProjectPayment.objects.get(payment=cheque.customer_payment)
        self.assertEqual((link.project, link.kind, link.created_by), (self.project, ProjectPaymentKind.COLLECTION, self.owner))
        self.assertEqual(customer_balance(self.customer), self.balance - self.net)  # the owner's account falls at once (T4)
        self.assertEqual(self.figures(), (ZERO, self.net, ZERO))
        self.assertEqual(insights.certificate_owed(self.project), {self.first.pk: ZERO})
        self.assertEqual(self.overdue(), ([], ZERO))
        self.holds()

    def test_a_deposited_cheque_is_still_under_collection(self):
        cheque = self.receive()
        cheques.deposit(cheque, self.bank, self.owner, on=DAY)
        self.assertEqual(self.figures(), (ZERO, self.net, ZERO))
        self.holds()

    def test_a_cleared_cheque_is_collected(self):
        cheque = self.receive()
        cheques.deposit(cheque, self.bank, self.owner, on=DAY)
        cheques.clear(cheque, self.owner, on=DAY)
        self.assertEqual(self.figures(), (ZERO, ZERO, self.net))
        self.assertEqual(self.overdue(), ([], ZERO))
        self.holds()
        ensure_fresh()
        ledger_ok(self)

    def test_a_part_cheque_leaves_the_rest_due(self):
        self.receive(amount="70000")
        self.assertEqual(self.figures(), (D("100000.00"), D("70000.00"), ZERO))
        self.assertEqual(self.overdue(), ([(self.first.pk, D("100000.00"))], D("100000.00")))
        self.holds()

    def test_the_engine_moves_what_it_moved_before(self):
        before = (CustomerPayment.objects.count(), Cheque.objects.count())
        cheque = self.receive()
        self.assertEqual((CustomerPayment.objects.count(), Cheque.objects.count()), (before[0] + 1, before[1] + 1))
        payment = cheque.customer_payment
        self.assertEqual((payment.cashbox, payment.amount, payment.status, payment.payment_date), (cheque.box, self.net, "posted", DAY))
        self.assertEqual(cheques.totals()["in_hand"], self.net)


class BouncingTests(ChequeSetup):
    def test_a_bounced_cheque_in_hand_puts_the_money_back_on_the_project(self):
        cheque = self.receive()
        cheques.bounce(cheque, "رصيد غير كافي", self.owner, on=DAY)
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, ChequeStatus.BOUNCED)
        self.assertEqual(customer_balance(self.customer), self.balance)  # back up by 170,000.00
        self.assertEqual(self.figures(), (self.net, ZERO, ZERO))
        self.assertEqual(insights.certificate_owed(self.project), {self.first.pk: self.net})
        self.assertEqual(self.overdue(), ([(self.first.pk, self.net)], self.net))  # overdue again after the credit days
        self.assertEqual(self.overdue(days_late=-1), ([], ZERO))  # and not before
        self.assertTrue(ProjectPayment.objects.filter(payment=cheque.customer_payment).exists())  # the link stays, read through the cheque
        self.holds()
        ensure_fresh()
        ledger_ok(self)

    def test_a_bounced_deposited_cheque_too(self):
        from cashboxes.services import get_cashbox_balance

        cheque = self.receive()
        cheques.deposit(cheque, self.bank, self.owner, on=DAY)
        cheques.bounce(cheque, "توقيع مختلف", self.owner, on=DAY)
        self.assertEqual(self.figures(), (self.net, ZERO, ZERO))
        self.assertEqual((get_cashbox_balance(self.bank), get_cashbox_balance(cheque.box)), (ZERO, ZERO))
        self.holds()
        ensure_fresh()
        ledger_ok(self)

    def test_bouncing_a_collection_linked_cheque_is_now_allowed(self):
        """Before C9 ``bounce`` refused any project-linked cheque with «الشيك ده مربوط بمشروع»."""

        cheque = self.engine_linked()
        cheques.bounce(cheque, "رجع", self.owner, on=DAY)
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, ChequeStatus.BOUNCED)

    def test_the_project_reads_the_cheque_status(self):
        """Before C9 the project counted an uncleared collection as collected, and a bounced one as collected for good."""

        cheque = self.engine_linked()
        figures = contract.position(self.project)
        self.assertEqual((figures["due_now"], figures["collections"], figures.get("under_collection")), (ZERO, ZERO, self.net))
        self.assertEqual(contract.due_now_many([self.project]), {self.project.pk: ZERO})
        cheque.status = ChequeStatus.BOUNCED  # as a bounce leaves it (the engine's bounce itself is pinned above)
        cheque.save(update_fields=["status"])
        figures = contract.position(self.project)
        self.assertEqual((figures["due_now"], figures["collections"], figures.get("under_collection")), (self.net, ZERO, ZERO))
        self.assertEqual(contract.due_now_many([self.project]), {self.project.pk: self.net})
        self.assertEqual(insights.certificate_owed(self.project), {self.first.pk: self.net})

    def test_an_advance_link_still_refuses_the_bounce(self):
        """The old refusal, kept for advances: a bounced advance after a certificate recovered it would leave more
        recovered than received. (Built from the engine alone; the services never link an uncleared advance.)"""

        cheque = self.engine_linked(kind=ProjectPaymentKind.ADVANCE, amount="5000")
        with self.assertRaisesMessage(ValidationError, "الشيك ده مربوط بمشروع"):
            cheques.bounce(cheque, "رجع", self.owner, on=DAY)
        with self.assertRaisesMessage(ValidationError, "linked to a project"):
            cheques.bounce(cheque, "returned", self.owner, lang="en", on=DAY)
        cheque.refresh_from_db()
        self.assertEqual(cheque.status, ChequeStatus.IN_HAND)

    def test_a_cleared_advance_cheque_cannot_bounce_at_all(self):
        cheque = cheques.receive(self.customer, self.data(number="ADV-1", amount="5000"), self.owner)
        cheques.deposit(cheque, self.bank, self.owner, on=DAY)
        cheques.clear(cheque, self.owner, on=DAY)
        cheque.refresh_from_db()
        contract.link_payment(self.project, cheque.customer_payment, self.owner, kind=ProjectPaymentKind.ADVANCE)
        self.assertEqual(contract.advance_received(self.project), D("105000.00"))
        with self.assertRaisesMessage(ValidationError, "حالته"):  # cleared: no bounce step, before the project guard is reached
            cheques.bounce(cheque, "رجع", self.owner, on=DAY)
        self.holds()

    def test_cancelling_a_cheque_in_hand_drops_it_from_the_project(self):
        cheque = self.receive()
        cheques.cancel(cheque, "اتسجل غلط", self.owner)
        cheque.customer_payment.refresh_from_db()
        self.assertEqual(cheque.customer_payment.status, "cancelled")
        self.assertEqual(customer_balance(self.customer), self.balance)
        self.assertEqual(self.figures(), (self.net, ZERO, ZERO))
        self.holds()
        ensure_fresh()
        ledger_ok(self)


class RefusalTests(ChequeSetup):
    def test_an_advance_by_an_uncleared_cheque_is_refused_and_nothing_is_written(self):
        before = (Cheque.objects.count(), CustomerPayment.objects.count(), ProjectPayment.objects.count(), customer_balance(self.customer))
        with self.assertRaisesMessage(ValidationError, "لسه ماتحصلش"):
            contract.receive_cheque(self.project, self.owner, data=self.data(), kind=ProjectPaymentKind.ADVANCE)
        self.assertEqual((Cheque.objects.count(), CustomerPayment.objects.count(), ProjectPayment.objects.count(), customer_balance(self.customer)), before)

    def test_linking_an_uncleared_cheque_by_hand_stays_refused(self):
        cheque = cheques.receive(self.customer, self.data(), self.owner)
        for kind in (ProjectPaymentKind.COLLECTION, ProjectPaymentKind.ADVANCE):
            with self.assertRaisesMessage(ValidationError, "لسه ماتحصلش"):
                contract.link_payment(self.project, cheque.customer_payment, self.owner, kind=kind)
        with self.assertRaisesMessage(ValidationError, "لسه ماتحصلش"):  # the flag never opens an advance
            contract.link_payment(self.project, cheque.customer_payment, self.owner, kind=ProjectPaymentKind.ADVANCE, uncleared_cheque=True)

    def test_the_cheque_form_is_checked_by_the_engine(self):
        for data, message in ((self.data(number=""), "رقم الشيك"), (self.data(amount="0"), "أكبر من صفر"), ({**self.data(), "due_date": ""}, "الاستحقاق")):
            with self.assertRaisesMessage(ValidationError, message):
                contract.receive_cheque(self.project, self.owner, data=data)
        self.receive()
        with self.assertRaisesMessage(ValidationError, "متسجل قبل كده"):
            self.receive()
        self.assertEqual(Cheque.objects.count(), 1)

    def test_a_closed_project_takes_no_cheque(self):
        from .models import ProjectStatus

        self.project.status = ProjectStatus.DONE
        self.project.save(update_fields=["status"])
        with self.assertRaises(ValidationError):
            self.receive()
        self.assertEqual(Cheque.objects.count(), 0)

    def test_receiving_needs_the_collection_right(self):
        from django.core.exceptions import PermissionDenied

        with self.assertRaises(PermissionDenied):
            contract.receive_cheque(self.project, person(RoleCode.SITE_ENGINEER, "c9_eng_svc"), data=self.data())
        self.assertEqual(Cheque.objects.count(), 0)


class EntityTests(ReferenceContract):
    def test_a_project_whose_money_is_in_another_entity_refuses_this_entitys_cheque_box(self):
        from entities.current import working_in
        from entities.models import Entity

        from . import services

        branch = Entity.objects.create(code="E9", name_ar="فرع الإسكندرية")
        branch_box = make_cashbox(cashbox_code="E9-BOX", entity=branch)
        customer = make_customer(customer_code="C-ALX", name="شركة الساحل")
        far = services.save_project({"name": "فيلا الساحل", "customer": customer}, self.owner)
        contract.receive_payment(far, self.owner, kind=ProjectPaymentKind.COLLECTION, cashbox=branch_box, amount="1000")  # its money is in the branch
        data = {"number": "55", "bank_name": "QNB", "due_date": (DAY + timedelta(days=10)).isoformat(), "amount": "5000"}
        with self.assertRaisesMessage(ValidationError, "من كيان تاني"):  # the whole group: the cheque would sit in the main entity's box
            contract.receive_cheque(far, self.owner, data=data)
        self.assertEqual(Cheque.objects.count(), 0)
        with working_in(Entity.objects.exclude(pk=branch.pk).order_by("pk").first()), self.assertRaisesMessage(ValidationError, "من كيان تاني"):
            contract.receive_cheque(far, self.owner, data=data)
        with working_in(branch):
            cheque = contract.receive_cheque(far, self.owner, data=data)
        self.assertEqual(cheque.box.entity, branch)
        self.assertEqual(contract.position(far)["under_collection"], D("5000.00"))


class ScreenTests(ChequeSetup):
    def url(self):
        return reverse("projects:payments", args=[self.project.pk])

    def post(self, **extra):
        return self.client.post(self.url(), {"action": "cheque", **self.data(), **extra})

    def test_the_owner_receives_a_cheque_from_the_payments_tab(self):
        self.client.force_login(self.owner)
        page = self.client.get(self.url())
        self.assertContains(page, "data-cheque-form")
        self.assertContains(page, "data-project-cheques")
        response = self.post()
        self.assertRedirects(response, self.url() + "?lang=ar", fetch_redirect_response=False)
        cheque = Cheque.objects.get()
        self.assertTrue(ProjectPayment.objects.filter(payment=cheque.customer_payment, kind=ProjectPaymentKind.COLLECTION).exists())
        page = self.client.get(self.url())
        self.assertContains(page, 'data-project-cheque="100245"')
        self.assertContains(page, 'data-cheque-status="in_hand"')
        self.assertContains(page, 'data-position="under_collection"')
        self.assertContains(page, "شيكات تحت التحصيل")
        self.assertEqual(page.context["position"]["under_collection"], self.net)
        self.assertNotContains(page, "data-cheques-due-soon")  # due in 30 days: not yet «مستحقة قريب»

    def test_a_cheque_due_within_the_week_is_due_soon_on_the_project_only(self):
        self.client.force_login(self.owner)
        self.receive(due=DAY + timedelta(days=3))
        stranger = make_customer(customer_code="C-OTHER", name="عميل تاني")
        cheques.receive(stranger, {"number": "900", "bank_name": "CIB", "due_date": DAY.isoformat(), "amount": "50"}, self.owner)
        page = self.client.get(self.url())
        self.assertContains(page, "data-cheques-due-soon")
        self.assertEqual([c.number for c in page.context["cheques_due_soon"]], ["100245"])
        self.assertEqual([row["cheque"].number for row in page.context["cheques"]], ["100245"])

    def test_a_bounced_cheque_reads_bounced_on_the_tab(self):
        self.client.force_login(self.owner)
        cheque = self.receive()
        cheques.bounce(cheque, "رجع", self.owner, on=DAY)
        page = self.client.get(self.url())
        self.assertContains(page, 'data-cheque-status="bounced"')
        self.assertContains(page, "مرتد")
        self.assertEqual(page.context["position"]["due_now"], self.net)

    def test_a_refusal_comes_back_in_words(self):
        self.client.force_login(self.owner)
        page = self.post(kind=ProjectPaymentKind.ADVANCE)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "لسه ماتحصلش")
        page = self.post(number="", lang="en")
        self.assertContains(page, "Type the cheque number.")
        self.assertEqual(Cheque.objects.count(), 0)

    def test_the_english_page(self):
        self.client.force_login(self.owner)
        self.receive()
        page = self.client.get(self.url() + "?lang=en")
        self.assertContains(page, "Cheques under collection")
        self.assertContains(page, "Receive a cheque from the owner")
        self.assertNotContains(page, "شيكات تحت التحصيل")

    def test_a_sales_only_user_and_an_engineer_get_403_on_receiving(self):
        role = make_role(code="C9-SALES")
        for code in ("sales.view_sales_invoices", "sales.create_sales_invoice"):
            grant(role, Permission.objects.get(code=code))
        seller = make_user(username="c9_seller")
        make_user_profile(user=seller, role=role)
        self.client.force_login(seller)
        page = self.client.get(self.url())
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-cheque-form")
        self.assertEqual(self.post().status_code, 403)
        self.client.force_login(person(RoleCode.SITE_ENGINEER, "c9_eng"))
        self.assertEqual(self.client.get(self.url()).status_code, 403)
        self.assertEqual(self.post().status_code, 403)
        self.assertEqual(Cheque.objects.count(), 0)

    def test_an_accountant_receives_it(self):
        self.client.force_login(person(RoleCode.ACCOUNTANT, "c9_acc"))
        self.assertEqual(self.post().status_code, 302)
        self.assertEqual(contract.position(self.project)["under_collection"], self.net)

    def test_the_stylesheet_uses_tokens_only(self):
        import re
        from pathlib import Path

        from django.conf import settings

        css = Path(settings.BASE_DIR, "static/hesba/css/project_cheques.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(css, r"rgba?\(")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", Path(settings.BASE_DIR, "static/hesba/css/tokens.css").read_text(encoding="utf-8")))
        self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])
        self.assertIn("project_cheques.css' %}?v=c9-1", Path(settings.BASE_DIR, "templates/projects/payments.html").read_text(encoding="utf-8"))


class VatTests(ChequeSetup):
    vat = True
    net = D("198000.00")
    balance = D("128000.00")  # 228,000.00 less the advance

    def test_the_same_figures_with_vat(self):
        self.assertEqual(self.figures(), (self.net, ZERO, ZERO))
        self.holds()
        cheque = self.receive()
        self.assertEqual(customer_balance(self.customer), self.balance - self.net)
        self.assertEqual(self.figures(), (ZERO, self.net, ZERO))
        self.assertEqual(self.overdue(), ([], ZERO))
        self.holds()
        cheques.bounce(cheque, "رجع", self.owner, on=DAY)
        self.assertEqual(customer_balance(self.customer), self.balance)
        self.assertEqual(self.figures(), (self.net, ZERO, ZERO))
        self.assertEqual(self.overdue(), ([(self.first.pk, self.net)], self.net))
        self.holds()
        second = self.receive(number="100246")
        cheques.deposit(second, self.bank, self.owner, on=DAY)
        cheques.clear(second, self.owner, on=DAY)
        self.assertEqual(self.figures(), (ZERO, ZERO, self.net))
        self.holds()
        ensure_fresh()
        ledger_ok(self)
