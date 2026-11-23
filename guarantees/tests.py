"""Contracting C4 (HG-123): letters of guarantee, their renewal dates and alerts. Nothing here moves money.

Dates are fixed (2027) and «today» is passed in, or ``guarantees.services.today`` is patched for the screens, so no test
depends on the real date."""

from datetime import date
from decimal import Decimal as D
from unittest import mock

from django.apps import apps
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import router
from django.urls import reverse

from hesba_testing.factories import make_cashbox, make_customer
from ledger.projector import ensure_fresh
from permissions.models import RoleCode
from projects import contract
from projects.models import ProjectPaymentKind
from projects.tests_contract import ContractSetup, person
from sales.services import post_sales_invoice

from . import services
from .models import EventKind, Guarantee, GuaranteeEvent, GuaranteeStatus

ISSUED = date(2026, 7, 1)
EXPIRY = date(2027, 6, 30)
#: Rows a guarantee may add: its own, the audit trail and the alert-days setting. Everything else must stay as it was.
OWN = {"guarantees.Guarantee", "guarantees.GuaranteeEvent", "audit.AuditLog", "settings_core.SystemSetting", "sessions.Session"}


def counts():
    """Every table's row count except the guarantee's own: cashbox movements, the journal, the customer ledger, ..."""

    ensure_fresh()  # the journal is built lazily; build it before counting so a page view cannot change it
    return {model._meta.label: model.objects.count() for model in apps.get_models()
            if model._meta.managed and not model._meta.proxy and router.db_for_read(model) == "default" and model._meta.label not in OWN}


def at(day):
    return mock.patch("guarantees.services.today", return_value=day)


class GuaranteeSetup(ContractSetup):
    def setUp(self):
        super().setUp()
        self.bank = make_cashbox(cashbox_code="BANK-NBE", name_ar="البنك الأهلي - جاري", name_en="NBE current", kind="bank", bank_name="البنك الأهلي")
        self.contract_value = D("1000000.00")

    def bond(self, **overrides):
        data = {"kind": "performance", "number": "LG-2026-0451", "bank": self.bank, "beneficiary": self.customer, "project": self.project,
                "amount": str(self.contract_value * D("0.10")), "issued_on": ISSUED, "expires_on": EXPIRY, "margin_percent": "25", "commission": "1500"}
        data.update(overrides)
        return services.save_guarantee(data, self.owner)


class PerformanceBondTests(GuaranteeSetup):
    def test_ten_percent_of_a_million_alerts_from_thirty_days_before_and_extends_reduces_releases(self):
        before = counts()
        bond = self.bond()
        self.assertEqual((bond.amount, bond.expires_on, bond.status, bond.currency), (D("100000.00"), EXPIRY, GuaranteeStatus.ACTIVE, "EGP"))
        self.assertEqual(services.margin_amount(bond), D("25000.00"))  # memo: 25% of 100,000.00
        self.assertEqual(services.alert_days(), 30)
        listed = lambda day: list(services.expiring(services.scoped(), day))  # noqa: E731
        self.assertEqual(listed(date(2027, 5, 30)), [])  # 31 days before
        self.assertEqual(listed(date(2027, 5, 31)), [bond])  # 30 days before
        self.assertEqual(listed(EXPIRY), [bond])  # the expiry day itself
        self.assertEqual((services.alert(bond, date(2027, 5, 30)), services.alert(bond, date(2027, 5, 31))), ("", "expiring"))

        services.extend(bond, self.owner, date(2027, 12, 31), event_date=date(2027, 6, 2), note="خطاب تمديد من البنك")
        bond.refresh_from_db()
        self.assertEqual(bond.expires_on, date(2027, 12, 31))
        self.assertEqual(listed(date(2027, 6, 2)), [])  # the alert clears
        self.assertEqual(services.alert(bond, date(2027, 6, 2)), "")
        event = bond.events.get()
        self.assertEqual((event.kind, event.previous_expiry, event.new_expiry), (EventKind.EXTEND, EXPIRY, date(2027, 12, 31)))
        self.assertEqual(services.first_expiry(bond), EXPIRY)

        services.reduce(bond, self.owner, "50,000.00", event_date=date(2027, 7, 1))
        bond.refresh_from_db()
        self.assertEqual(bond.amount, D("50000.00"))
        self.assertEqual(services.issued_amount(bond), D("100000.00"))
        services.release(bond, self.owner, event_date=date(2027, 9, 1), note="استلام الأعمال")
        bond.refresh_from_db()
        self.assertEqual(bond.status, GuaranteeStatus.RELEASED)
        self.assertEqual([(e.kind, e.previous_amount, e.new_amount) for e in bond.events.all()],
                         [("extend", D("100000.00"), None), ("reduce", D("100000.00"), D("50000.00")), ("release", D("50000.00"), None)])
        self.assertEqual(services.alert(bond, date(2028, 1, 5)), "")  # a released letter never alerts
        with self.assertRaisesMessage(ValidationError, "مش ساري"):
            services.extend(bond, self.owner, date(2028, 6, 30))
        self.assertEqual(counts(), before)  # no cashbox movement, journal entry, customer-ledger row or anything else

    def test_expired_but_active_is_its_own_alert(self):
        bond = self.bond()
        self.assertEqual(list(services.expired(services.scoped(), date(2027, 7, 1))), [bond])
        self.assertEqual(list(services.expired(services.scoped(), EXPIRY)), [])
        self.assertEqual(services.alert(bond, date(2027, 7, 1)), "expired")
        self.assertEqual(services.counts(date(2027, 7, 1)), {"expiring": 0, "expired": 1, "active": 1})
        services.liquidate(bond, self.owner, event_date=date(2027, 7, 3), note="طلب تسييل من المالك")
        bond.refresh_from_db()
        self.assertEqual((bond.status, services.alert(bond, date(2027, 7, 3))), (GuaranteeStatus.LIQUIDATED, ""))
        self.assertEqual(services.counts(date(2027, 7, 3))["expired"], 0)

    def test_the_alert_days_are_a_setting(self):
        bond = self.bond()
        services.save_alert_days("45", self.owner)
        self.assertEqual(services.alert_days(), 45)
        self.assertEqual(list(services.expiring(services.scoped(), date(2027, 5, 16))), [bond])
        with self.assertRaisesMessage(ValidationError, "من 1 لـ 365"):
            services.save_alert_days("0", self.owner)
        self.assertEqual(services.alert_days(), 45)

    def test_the_rules_refuse_what_would_break_the_history(self):
        bond = self.bond()
        with self.assertRaisesMessage(ValidationError, "بعد الحالي"):
            services.extend(bond, self.owner, date(2027, 6, 30))
        with self.assertRaisesMessage(ValidationError, "أقل من الحالي"):
            services.reduce(bond, self.owner, "100000")
        with self.assertRaisesMessage(ValidationError, "أقل من الحالي"):
            services.reduce(bond, self.owner, "0")
        with self.assertRaisesMessage(ValidationError, "مش قبل تاريخ الإصدار"):
            services.reduce(bond, self.owner, "1000", event_date=date(2026, 6, 1))
        with self.assertRaisesMessage(ValidationError, "سبب الإلغاء"):
            services.cancel(bond, self.owner, event_date=date(2026, 8, 1))
        with self.assertRaisesMessage(ValidationError, "with that number"):
            services.save_guarantee({"kind": "bid", "number": "lg-2026-0451", "bank": self.bank, "beneficiary": self.customer,
                                     "amount": "5000", "issued_on": ISSUED, "expires_on": EXPIRY}, self.owner, lang="en")
        with self.assertRaisesMessage(ValidationError, "after the issue date"):
            services.save_guarantee(
                {"kind": "bid", "number": "X-1", "bank": self.bank, "beneficiary": self.customer, "amount": "5000", "issued_on": ISSUED,
                 "expires_on": date(2026, 6, 1)}, self.owner, lang="en")
        cash = make_cashbox(cashbox_code="SAFE", name_ar="الخزنة")  # a cash box is not a bank
        with self.assertRaisesMessage(ValidationError, "bank account"):
            services.save_guarantee({"kind": "bid", "number": "X-2", "bank": cash, "beneficiary": self.customer, "amount": "5000",
                                     "issued_on": ISSUED, "expires_on": EXPIRY}, self.owner, lang="en")
        # Corrections are free until the first event; after it, the amount and expiry change only by an event.
        services.save_guarantee({"kind": "performance", "number": "LG-2026-0451", "bank": self.bank, "beneficiary": self.customer,
                                 "project": self.project, "amount": "100000", "issued_on": ISSUED, "expires_on": date(2027, 7, 31)}, self.owner, bond)
        bond.refresh_from_db()
        self.assertEqual(bond.expires_on, date(2027, 7, 31))
        services.extend(bond, self.owner, date(2027, 12, 31), event_date=date(2027, 7, 1))
        with self.assertRaisesMessage(ValidationError, "التمديد أو التخفيض"):
            services.save_guarantee({"kind": "performance", "number": "LG-2026-0451", "bank": self.bank, "beneficiary": self.customer,
                                     "amount": "90000", "issued_on": ISSUED, "expires_on": date(2027, 12, 31)}, self.owner, bond)
        services.cancel(bond, self.owner, event_date=date(2027, 7, 2), note="اتسجل غلط")
        bond.refresh_from_db()
        self.assertEqual(bond.status, GuaranteeStatus.CANCELLED)

    def test_events_are_append_only(self):
        bond = self.bond()
        event = services.extend(bond, self.owner, date(2027, 12, 31), event_date=date(2027, 6, 1))
        event.note = "edited"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        self.assertEqual(GuaranteeEvent.objects.get().note, "")

    def test_only_managers_may_write(self):
        engineer = person(RoleCode.SITE_ENGINEER, "lg_engineer")
        keeper = person(RoleCode.STOCK_KEEPER, "lg_keeper")
        for user in (engineer, keeper):
            with self.assertRaises(PermissionDenied):
                services.save_guarantee({"kind": "bid"}, user)
        bond = self.bond()
        with self.assertRaises(PermissionDenied):
            services.extend(bond, engineer, date(2027, 12, 31))
        self.assertTrue(services.can_manage(person(RoleCode.MANAGER, "lg_manager")))
        self.assertTrue(services.can_see_card(engineer))
        self.assertFalse(services.can_manage(engineer))
        self.assertFalse(services.can_see_card(keeper))


class AdvancePaymentTests(GuaranteeSetup):
    def test_after_two_certificates_the_page_suggests_the_advance_left(self):
        contract.save_terms(self.project, self.owner, retention_rate="5", advance_recovery_rate="30")
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="100000")
        guarantee = self.bond(kind="advance", number="LG-ADV-1", amount="100000")
        self.assertEqual(services.advance_suggestion(guarantee), {"received": D("100000.00"), "left": D("100000.00"), "action": "", "amount": None})
        post_sales_invoice(self.certify(concrete="40").invoice_id, self.owner)  # 40,000 × 30% = 12,000 recovered
        post_sales_invoice(self.certify(concrete="100", masonry="250").invoice_id, self.owner)  # 110,000 × 30% = 33,000
        self.assertEqual(contract.position(self.project)["advance_left"], D("55000.00"))
        before = counts()
        self.assertEqual(services.advance_suggestion(guarantee)["action"], "reduce")
        self.assertEqual(services.advance_suggestion(guarantee)["amount"], D("55000.00"))
        self.client.force_login(self.owner)
        with at(date(2027, 1, 10)):
            page = self.client.get(reverse("guarantees:detail", args=[guarantee.pk]) + "?lang=ar")
        self.assertContains(page, 'data-advance-suggestion="reduce"')
        self.assertContains(page, 'name="new_amount" value="55000.00"')
        guarantee.refresh_from_db()
        self.assertEqual(guarantee.amount, D("100000.00"))  # suggested, never applied by itself
        services.reduce(guarantee, self.owner, "55000.00", event_date=date(2027, 1, 10))
        guarantee.refresh_from_db()
        self.assertEqual(services.advance_suggestion(guarantee)["action"], "")
        self.assertEqual(counts(), before)

    def test_a_fully_recovered_advance_suggests_release(self):
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="4000")
        guarantee = self.bond(kind="advance", number="LG-ADV-2", amount="4000")
        post_sales_invoice(self.certify(concrete="40").invoice_id, self.owner)  # 10% of 40,000 = 4,000: all of it
        self.assertEqual(services.advance_suggestion(guarantee)["action"], "release")


class EntityTests(GuaranteeSetup):
    def test_another_entitys_guarantee_is_a_404(self):
        from entities.current import SESSION_KEY
        from entities.models import Entity
        from entities.services import main_entity

        other = Entity.objects.create(code="E2", name_ar="شركة تانية")
        far_bank = make_cashbox(cashbox_code="FAR-BANK", name_ar="بنك الشركة التانية", kind="bank", entity=other)
        far = services.save_guarantee({"kind": "bid", "number": "FAR-1", "bank": far_bank, "beneficiary": self.customer,
                                       "amount": "5000", "issued_on": ISSUED, "expires_on": EXPIRY}, self.owner)
        self.assertEqual(far.entity, other)
        mine = self.bond()
        self.client.force_login(self.owner)
        session = self.client.session
        session[SESSION_KEY] = main_entity().pk
        session.save()
        url = reverse("guarantees:detail", args=[far.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.post(url, {"action": "release"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("guarantees:edit", args=[far.pk])).status_code, 404)
        far.refresh_from_db()
        self.assertEqual(far.status, GuaranteeStatus.ACTIVE)
        listing = self.client.get(reverse("guarantees:list"))
        self.assertContains(listing, f'data-guarantee="{mine.pk}"')
        self.assertNotContains(listing, f'data-guarantee="{far.pk}"')
        # Its bank is not offered either, and naming it is refused.
        self.assertNotContains(self.client.get(reverse("guarantees:new")), "بنك الشركة التانية")
        made = self.client.post(reverse("guarantees:new"), {"kind": "bid", "number": "FAR-2", "bank": str(far_bank.pk), "beneficiary": str(self.customer.pk),
                                                            "amount": "100", "issued_on": "2026-07-01", "expires_on": "2027-06-30"})
        self.assertEqual(made.status_code, 200)
        self.assertFalse(Guarantee.objects.filter(number="FAR-2").exists())


class ScreenTests(GuaranteeSetup):
    def url(self, name, *args):
        return reverse(f"guarantees:{name}", args=args)

    def test_adding_from_the_form_then_extending_reducing_releasing_from_its_page(self):
        before = counts()
        self.client.force_login(self.owner)
        with at(date(2027, 5, 31)):
            self.assertContains(self.client.get(self.url("new") + f"?project={self.project.pk}"), f'<option value="{self.project.pk}" selected')
            made = self.client.post(self.url("new"), {"lang": "ar", "kind": "performance", "number": "LG-77", "bank": str(self.bank.pk),
                                                      "beneficiary": str(self.customer.pk), "project": str(self.project.pk), "amount": "100,000",
                                                      "issued_on": "2026-07-01", "expires_on": "2027-06-30", "margin_percent": "10", "commission": ""})
            bond = Guarantee.objects.get(number="LG-77")
            self.assertRedirects(made, self.url("detail", bond.pk) + "?lang=ar", fetch_redirect_response=False)
            page = self.client.get(self.url("detail", bond.pk))
            self.assertContains(page, 'data-guarantee-alert="expiring"')
            self.assertContains(page, "100,000.00")
            listing = self.client.get(self.url("list") + "?show=expiring")
            self.assertContains(listing, f'data-guarantee="{bond.pk}"')
            self.client.post(self.url("detail", bond.pk), {"lang": "ar", "action": "extend", "new_expiry": "2027-12-31", "event_date": "2027-06-01"})
            self.assertNotContains(self.client.get(self.url("list") + "?show=expiring"), f'data-guarantee="{bond.pk}"')
            history = self.client.get(self.url("detail", bond.pk))
            self.assertContains(history, 'data-event="extend"')
            for shown in ("2027-06-30", "2027-12-31"):
                self.assertContains(history, shown)
            self.client.post(self.url("detail", bond.pk), {"lang": "ar", "action": "reduce", "new_amount": "50000", "event_date": "2027-07-01"})
            refused = self.client.post(self.url("detail", bond.pk), {"lang": "en", "action": "reduce", "new_amount": "70000"}, follow=True)
            self.assertContains(refused, "below the current one")
            self.client.post(self.url("detail", bond.pk), {"lang": "ar", "action": "release", "event_date": "2027-09-01"})
        bond.refresh_from_db()
        self.assertEqual((bond.status, bond.amount, bond.expires_on), (GuaranteeStatus.RELEASED, D("50000.00"), date(2027, 12, 31)))
        self.assertEqual(list(bond.events.values_list("kind", flat=True)), ["extend", "reduce", "release"])
        self.assertEqual(counts(), before)

    def test_the_list_filters_by_alert_bank_and_project_in_both_languages(self):
        other_bank = make_cashbox(cashbox_code="BANK-CIB", name_ar="بنك CIB", kind="bank")
        soon = self.bond()
        late = self.bond(number="LG-OLD", expires_on=date(2027, 3, 31))
        bid = self.bond(kind="bid", number="LG-BID", bank=other_bank, project=None, expires_on=date(2028, 1, 31))
        self.client.force_login(self.owner)
        with at(date(2027, 6, 1)):
            page = self.client.get(self.url("list"))
            for row in (soon, late, bid):
                self.assertContains(page, f'data-guarantee="{row.pk}"')
            self.assertContains(page, f'data-guarantee-alert="expired" data-for="{late.pk}"')
            self.assertContains(page, f'data-guarantee-alert="expiring" data-for="{soon.pk}"')
            self.assertContains(page, "خطابات الضمان")
            expiring = self.client.get(self.url("list") + "?show=expiring")
            self.assertContains(expiring, f'data-guarantee="{soon.pk}"')
            self.assertNotContains(expiring, f'data-guarantee="{bid.pk}"')
            self.assertNotContains(expiring, f'data-guarantee="{late.pk}"')
            expired = self.client.get(self.url("list") + "?show=expired")
            self.assertContains(expired, f'data-guarantee="{late.pk}"')
            self.assertNotContains(expired, f'data-guarantee="{soon.pk}"')
            by_bank = self.client.get(self.url("list") + f"?bank={other_bank.pk}")
            self.assertContains(by_bank, f'data-guarantee="{bid.pk}"')
            self.assertNotContains(by_bank, f'data-guarantee="{soon.pk}"')
            by_project = self.client.get(self.url("list") + f"?project={self.project.pk}")
            self.assertContains(by_project, f'data-guarantee="{soon.pk}"')
            self.assertNotContains(by_project, f'data-guarantee="{bid.pk}"')
            english = self.client.get(self.url("list") + "?lang=en")
            self.assertContains(english, "Letters of guarantee")
            self.assertContains(english, "Performance bond")
            self.assertContains(english, "Expired, still active")
            detail_en = self.client.get(self.url("detail", late.pk) + "?lang=en")
            self.assertContains(detail_en, "Liquidate")
            self.assertContains(detail_en, "ordinary bank cash-out")
            self.assertContains(self.client.get(self.url("new") + "?lang=en"), "New letter of guarantee")

    def test_a_retention_guarantee_releases_nothing_by_itself(self):
        contract.receive_payment(self.project, self.owner, kind=ProjectPaymentKind.ADVANCE, cashbox=self.cashbox, amount="10000")
        post_sales_invoice(self.certify(concrete="40").invoice_id, self.owner)
        held = contract.position(self.project)["retention_held"]
        self.assertEqual(held, D("2000.00"))
        guarantee = self.bond(kind="retention", number="LG-RET", amount="2000")
        before = counts()
        self.client.force_login(self.owner)
        with at(date(2026, 9, 1)):
            page = self.client.get(self.url("detail", guarantee.pk))
            self.assertContains(page, "data-retention-note")
            self.client.post(self.url("detail", guarantee.pk), {"lang": "ar", "action": "release", "event_date": "2026-09-01"})
        self.assertEqual(contract.position(self.project)["retention_held"], held)
        self.assertEqual(counts(), before)

    def test_the_alert_days_form_is_the_owners_and_the_managers(self):
        cashier = person(RoleCode.CASHIER, "days_cashier")  # manages guarantees, but not a business-wide setting
        self.client.force_login(cashier)
        self.assertNotContains(self.client.get(self.url("list")), "data-alert-days")
        self.client.post(self.url("list"), {"lang": "ar", "action": "alert_days", "days": "60"})
        self.assertEqual(services.alert_days(), 30)
        with self.assertRaises(PermissionDenied):
            services.save_alert_days("60", cashier)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url("list")), "data-alert-days")
        self.client.post(self.url("list"), {"lang": "ar", "action": "alert_days", "days": "60"})
        self.assertEqual(services.alert_days(), 60)


class EditKeepsItsChoicesTests(GuaranteeSetup):
    """Codex: the form's pickers are limited (the newest projects, the first active customers); a letter whose project or
    beneficiary falls outside them must keep them when an unrelated field is corrected."""

    def test_a_correction_keeps_a_project_outside_the_picker_and_an_inactive_beneficiary(self):
        bond = self.bond(notes="")
        self.customer.active = False
        self.customer.save()
        self.client.force_login(self.owner)
        from . import views

        with mock.patch.object(views, "PROJECT_PICKER", 0), mock.patch.object(views, "CUSTOMER_PICKER", 0):
            page = self.client.get(reverse("guarantees:edit", args=[bond.pk]))
            self.assertContains(page, f'<option value="{self.project.pk}" selected')
            self.assertContains(page, f'<option value="{self.customer.pk}" selected')
            self.client.post(reverse("guarantees:edit", args=[bond.pk]), {
                "lang": "ar", "kind": bond.kind, "number": bond.number, "bank": str(bond.bank_id), "beneficiary": str(self.customer.pk),
                "project": str(self.project.pk), "amount": f"{bond.amount:.2f}", "issued_on": bond.issued_on.isoformat(),
                "expires_on": bond.expires_on.isoformat(), "margin_percent": "25", "commission": "1500", "notes": "تصحيح"})
        bond.refresh_from_db()
        self.assertEqual(bond.notes, "تصحيح")
        self.assertEqual(bond.project_id, self.project.pk)
        self.assertEqual(bond.beneficiary_id, self.customer.pk)


class PermissionScreenTests(GuaranteeSetup):
    def setUp(self):
        super().setUp()
        self.guarantee = self.bond()

    def test_managers_open_everything(self):
        for role, name in ((RoleCode.OWNER, "p_owner"), (RoleCode.MANAGER, "p_manager"), (RoleCode.CASHIER, "p_cashier")):
            self.client.force_login(person(role, name))
            for url in (reverse("guarantees:list"), reverse("guarantees:new"), reverse("guarantees:detail", args=[self.guarantee.pk]),
                        reverse("guarantees:edit", args=[self.guarantee.pk])):
                self.assertEqual(self.client.get(url).status_code, 200, (role, url))

    def test_others_get_403_and_cannot_write(self):
        for role, name in ((RoleCode.SITE_ENGINEER, "p_engineer"), (RoleCode.STOCK_KEEPER, "p_keeper"), (RoleCode.ACCOUNTANT, "p_accountant")):
            self.client.force_login(person(role, name))
            for url in (reverse("guarantees:list"), reverse("guarantees:new"), reverse("guarantees:detail", args=[self.guarantee.pk])):
                self.assertEqual(self.client.get(url).status_code, 403, (role, url))
            self.assertEqual(self.client.post(reverse("guarantees:detail", args=[self.guarantee.pk]), {"action": "release"}).status_code, 403)
        self.guarantee.refresh_from_db()
        self.assertEqual(self.guarantee.status, GuaranteeStatus.ACTIVE)

    def test_the_project_card_shows_amounts_to_managers_only(self):
        project_url = reverse("projects:detail", args=[self.project.pk])
        self.client.force_login(self.owner)
        with at(date(2027, 6, 10)):
            owner_page = self.client.get(project_url)
        self.assertContains(owner_page, "data-project-guarantees")
        self.assertContains(owner_page, f'data-guarantee-amount="{self.guarantee.pk}"')
        self.assertContains(owner_page, reverse("guarantees:detail", args=[self.guarantee.pk]))
        self.assertContains(owner_page, 'data-guarantee-alert="expiring"')
        self.client.force_login(person(RoleCode.SITE_ENGINEER, "card_engineer"))
        with at(date(2027, 6, 10)):
            engineer_page = self.client.get(project_url + "?lang=en")
        self.assertEqual(engineer_page.status_code, 200)
        self.assertContains(engineer_page, "data-project-guarantees")
        self.assertContains(engineer_page, "Performance bond")
        self.assertContains(engineer_page, 'data-guarantee-alert="expiring"')
        self.assertNotContains(engineer_page, "data-guarantee-amount")
        self.assertNotContains(engineer_page, "/guarantees/")  # no link the engineer could not open
        self.client.force_login(person(RoleCode.ACCOUNTANT, "card_accountant"))
        accountant = self.client.get(project_url)  # opens the project (sales.view_sales_invoices) but may not read guarantees
        self.assertEqual(accountant.status_code, 200)
        self.assertNotContains(accountant, "data-project-guarantees")


class DashboardTests(GuaranteeSetup):
    def test_the_tile_counts_expiring_and_expired_for_managers(self):
        from reports.dashboard_fit import Fit, option_tiles

        self.bond()
        self.bond(number="LG-OLD", expires_on=date(2027, 3, 31))
        fit = Fit.current(date(2027, 6, 1))
        tile = next(t for t in option_tiles(fit, self.owner, "ar") if t["key"] == "opt_guarantees")
        self.assertEqual((tile["value"], tile["tone"], tile["url"]), (2, "bad", reverse("guarantees:list")))
        self.assertIn("1", tile["sub"])
        english = next(t for t in option_tiles(fit, self.owner, "en") if t["key"] == "opt_guarantees")
        self.assertIn("expired", english["sub"])
        engineer = person(RoleCode.SITE_ENGINEER, "dash_engineer")
        self.assertFalse([t for t in option_tiles(fit, engineer, "ar") if t["key"] == "opt_guarantees"])

    def test_the_dashboard_shows_the_tile(self):
        from django.utils import timezone

        self.bond(expires_on=timezone.localdate())  # expiring today, whatever today is
        self.client.force_login(self.owner)
        page = self.client.get(reverse("dashboard_snapshot"))
        self.assertContains(page, 'data-tile="opt_guarantees"')
        self.assertContains(page, reverse("guarantees:list"))

    def test_no_guarantee_no_tile(self):
        from reports.dashboard_fit import Fit, option_tiles

        self.assertFalse([t for t in option_tiles(Fit.current(date(2027, 6, 1)), self.owner, "ar") if t["key"] == "opt_guarantees"])
