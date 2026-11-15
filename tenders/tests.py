"""Contracting C8 (HG-126): the tender study, its price analysis, «اترسّت علينا» and the prints. Nothing here moves money.

The reference tender (the plan's figures): concrete 400 m³ at materials 900 + labour 200 + equipment 100 = 1,200.00 direct,
overhead 10% → 1,320.00, profit 12% → 1,478.40, total 591,360.00; masonry 2,000 m² at 150 + 40 = 190.00 → 209.00 → 234.08,
total 468,160.00; the tender 1,059,520.00. Won, the project's budget is materials 660,000.00, labour 160,000.00 and
equipment 40,000.00."""

import pathlib
import re
from datetime import date
from decimal import Decimal as D
from unittest import mock

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import router
from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_customer, make_seeded_role, make_user, make_user_profile
from ledger.projector import ensure_fresh
from permissions.models import RoleCode
from projects import contract
from projects.models import BoqLine, BudgetLine, Project
from reports.tests_dashboard import prepared_client

from . import services
from .models import Tender, TenderCost, TenderItem, TenderStatus

MODULES = "customers,suppliers,items_services,sales_operations,purchases,cashboxes,reports,inventory,expenses,projects"
#: Rows a tender may add: its own, the audit trail, sessions, and once won the project, its bill of quantities and budget.
OWN = {"tenders.Tender", "tenders.TenderItem", "tenders.TenderCost", "audit.AuditLog", "sessions.Session",
       "projects.Project", "projects.BoqLine", "projects.BudgetLine"}


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def counts():
    """Every table's row count except the tender's own and the won project's: cashbox movements, the journal, the ledgers, stock."""

    ensure_fresh()  # the journal is built lazily; build it before counting so a page view cannot change it
    return {model._meta.label: model.objects.count() for model in apps.get_models()
            if model._meta.managed and not model._meta.proxy and router.db_for_read(model) == "default" and model._meta.label not in OWN}


class TenderSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="contracting", sub_activity="general", modules=MODULES)
        self.owner = person(RoleCode.OWNER, "c8_owner")
        self.customer = make_customer(customer_code="C-NILE", name="شركة النيل للتطوير")

    def reference(self, user=None):
        """The plan's tender: concrete and masonry, overhead 10%, profit 12%."""

        user = user or self.owner
        tender = services.save_tender({"name": "عمارة النيل السكنية", "customer": self.customer, "due_on": date(2026, 12, 20),
                                       "overhead_percent": "10", "profit_percent": "12"}, user)
        self.concrete = services.save_item(tender, user, {"code": "1", "description": "خرسانة مسلحة للأساسات", "unit": "م³", "quantity": "400"})
        for heading, cost in (("materials", "900"), ("labour", "200"), ("equipment", "100")):
            services.save_cost(self.concrete, user, {"heading": heading, "unit_cost": cost})
        self.masonry = services.save_item(tender, user, {"code": "2", "description": "مباني طوب أحمر", "unit": "م²", "quantity": "2000"})
        for heading, cost in (("materials", "150"), ("labour", "40")):
            services.save_cost(self.masonry, user, {"heading": heading, "unit_cost": cost})
        tender.refresh_from_db()
        return tender


class PriceAnalysisTests(TenderSetup):
    def test_the_reference_tender_prices_to_the_piastre(self):
        figures = services.analysis(self.reference())
        concrete, masonry = figures["rows"]
        self.assertEqual((concrete["direct"], concrete["with_overhead"], concrete["rate"], concrete["total"]),
                         (D("1200.00"), D("1320.00"), D("1478.40"), D("591360.00")))
        self.assertEqual((masonry["direct"], masonry["with_overhead"], masonry["rate"], masonry["total"]),
                         (D("190.00"), D("209.00"), D("234.08"), D("468160.00")))
        self.assertEqual(figures["total"], D("1059520.00"))
        self.assertEqual(figures["by_heading"], {"materials": D("660000.00"), "labour": D("160000.00"), "equipment": D("40000.00")})
        self.assertEqual(figures["direct"], D("860000.00"))
        self.assertEqual(figures["overhead"], D("86000.00"))
        self.assertEqual(figures["profit"], D("113520.00"))  # 946,000.00 × 12%
        self.assertEqual(figures["unpriced"], [])

    def test_the_rate_is_rounded_once_from_the_unrounded_product(self):
        tender = services.save_tender({"name": "عطاء تقريب", "prospect_name": "جهة حكومية", "overhead_percent": "7.5", "profit_percent": "11"}, self.owner)
        item = services.save_item(tender, self.owner, {"description": "دهانات", "unit": "م²", "quantity": "3.333"})
        services.save_cost(item, self.owner, {"heading": "materials", "unit_cost": "33.33"})
        row = services.analysis(tender)["rows"][0]
        # 33.33 × 1.075 = 35.82975 → shown 35.83; × 1.11 = 39.7710225 → 39.77; × 3.333 = 132.55341 → 132.55
        self.assertEqual((row["with_overhead"], row["rate"], row["total"]), (D("35.83"), D("39.77"), D("132.55")))
        self.assertEqual(services.analysis(tender)["by_heading"], {"materials": D("111.09")})  # 33.33 × 3.333 = 111.08889

    def test_an_item_without_cost_is_unpriced_and_rates_zero(self):
        tender = self.reference()
        bare = services.save_item(tender, self.owner, {"description": "أعمال متنوعة", "unit": "مقطوعية", "quantity": "1"})
        figures = services.analysis(tender)
        self.assertEqual([row["item"] for row in figures["unpriced"]], [bare])
        self.assertEqual(figures["rows"][2]["rate"], D("0.00"))
        self.assertEqual(figures["total"], D("1059520.00"))

    def test_refusals_in_words(self):
        tender = self.reference()
        cases = (
            (lambda: services.save_tender({"name": "", "customer": self.customer}, self.owner), "اكتب اسم العطاء"),
            (lambda: services.save_tender({"name": "عطاء"}, self.owner), "اسم الجهة"),
            (lambda: services.save_tender({"name": "عطاء", "customer": self.customer, "overhead_percent": "101"}, self.owner), "من 0 لـ 100"),
            (lambda: services.save_tender({"name": "عطاء", "customer": self.customer, "profit_percent": "-1"}, self.owner), "من 0 لـ 100"),
            (lambda: services.save_tender({"name": "عطاء", "customer": self.customer, "due_on": "2026-13-40"}, self.owner), "ميعاد التقديم"),
            (lambda: services.save_item(tender, self.owner, {"description": "", "quantity": "1"}), "وصف البند"),
            (lambda: services.save_item(tender, self.owner, {"description": "بند", "quantity": "0"}), "أكبر من صفر"),
            (lambda: services.save_item(tender, self.owner, {"description": "بند", "quantity": "0.0004"}), "أكبر من صفر"),
            (lambda: services.save_item(tender, self.owner, {"description": "بند", "quantity": "abc"}), "أكبر من صفر"),
            (lambda: services.save_cost(self.concrete, self.owner, {"heading": "fuel", "unit_cost": "5"}), "بند التكلفة"),
            (lambda: services.save_cost(self.concrete, self.owner, {"heading": "other", "unit_cost": "-5"}), "مش سالب"),
        )
        for call, words in cases:
            with self.subTest(words=words), self.assertRaisesMessage(ValidationError, words):
                call()
        with self.assertRaisesMessage(ValidationError, "Type the reason"):
            services.close(tender, self.owner, TenderStatus.LOST, " ", lang="en")
        self.assertEqual(services.analysis(tender)["total"], D("1059520.00"))

    def test_editing_and_removing_items_and_costs_reprices(self):
        tender = self.reference()
        concrete_costs = list(self.concrete.costs.order_by("pk"))
        services.save_cost(self.concrete, self.owner, {"heading": "materials", "description": "أسمنت وزلط ورمل وحديد", "unit_cost": "1000"}, concrete_costs[0])
        self.assertEqual(services.analysis(tender)["rows"][0]["direct"], D("1300.00"))
        services.delete_cost(concrete_costs[2], self.owner)
        self.assertEqual(services.analysis(tender)["rows"][0]["direct"], D("1200.00"))
        services.save_item(tender, self.owner, {"code": "2", "description": "مباني طوب أحمر", "unit": "م²", "quantity": "1000"}, self.masonry)
        self.assertEqual(services.analysis(tender)["rows"][1]["total"], D("234080.00"))
        services.delete_item(tender, self.masonry, self.owner)
        self.assertFalse(TenderCost.objects.filter(item_id=self.masonry.pk).exists())
        self.assertEqual([item.line_number for item in tender.items.all()], [1])
        third = services.save_item(tender, self.owner, {"description": "عزل", "quantity": "10"})
        self.assertEqual(third.line_number, 2)

    def test_the_percents_and_costs_need_the_profit_report(self):
        manager = person(RoleCode.MANAGER, "c8_manager")  # sales.create_sales_invoice, no reports.view_profit_report
        tender = self.reference()
        self.assertTrue(services.can_manage(manager))
        self.assertFalse(services.can_see_costs(manager))
        with self.assertRaises(PermissionDenied):
            services.save_cost(self.concrete, manager, {"heading": "other", "unit_cost": "5"})
        with self.assertRaises(PermissionDenied):
            services.delete_cost(self.concrete.costs.first(), manager)
        # The manager renames the tender; the markups he cannot see are kept, not blanked.
        services.save_tender({"name": "عمارة النيل (معدّل)", "customer": self.customer, "overhead_percent": "0", "profit_percent": "0"}, manager, tender)
        tender.refresh_from_db()
        self.assertEqual((tender.name, tender.overhead_percent, tender.profit_percent), ("عمارة النيل (معدّل)", D("10.00"), D("12.00")))
        services.save_item(tender, manager, {"description": "عزل أسطح", "unit": "م²", "quantity": "300"})
        self.assertEqual(tender.items.count(), 3)
        for role, name in ((RoleCode.SITE_ENGINEER, "c8_engineer"), (RoleCode.ACCOUNTANT, "c8_accountant"), (RoleCode.STOCK_KEEPER, "c8_keeper")):
            with self.subTest(role=role), self.assertRaises(PermissionDenied):
                services.save_tender({"name": "عطاء", "customer": self.customer}, person(role, name))

    def test_nothing_but_the_tender_and_its_audit_is_written(self):
        before = counts()
        tender = self.reference()
        services.submit(tender, self.owner)
        self.assertEqual(counts(), before)
        self.assertEqual(Tender.objects.get().status, TenderStatus.SUBMITTED)


class WinTests(TenderSetup):
    def test_won_makes_the_project_its_bill_of_quantities_and_its_budget(self):
        tender = self.reference()
        before = counts()
        projects_before = Project.objects.count()
        project = services.win(tender, self.owner, on=date(2027, 1, 5))
        self.assertEqual(counts(), before)  # no journal, cashbox, ledger or stock row
        self.assertEqual(Project.objects.count(), projects_before + 1)
        self.assertEqual(project.customer, self.customer)
        self.assertEqual(project.name, "عمارة النيل السكنية")
        self.assertEqual(project.contract_value, D("1059520.00"))
        self.assertEqual(contract.boq_total(project), D("1059520.00"))
        self.assertEqual(contract.contract_value(project), D("1059520.00"))
        lines = list(project.boq.order_by("line_number"))
        self.assertEqual([(line.code, line.description, line.unit, line.quantity, line.rate) for line in lines],
                         [("1", "خرسانة مسلحة للأساسات", "م³", D("400.000"), D("1478.40")), ("2", "مباني طوب أحمر", "م²", D("2000.000"), D("234.08"))])
        budget = {line.heading: line.amount for line in BudgetLine.objects.filter(project=project)}
        self.assertEqual(budget, {"materials": D("660000.00"), "labour": D("160000.00"), "equipment": D("40000.00")})
        tender.refresh_from_db()
        self.assertEqual((tender.status, tender.project, tender.status_on), (TenderStatus.WON, project, date(2027, 1, 5)))

    def test_a_won_tender_is_read_only(self):
        tender = self.reference()
        services.win(tender, self.owner)
        tender.refresh_from_db()
        cost = self.concrete.costs.first()
        attempts = (
            lambda: services.save_tender({"name": "تعديل", "customer": self.customer}, self.owner, tender),
            lambda: services.save_item(tender, self.owner, {"description": "بند", "quantity": "1"}),
            lambda: services.save_item(tender, self.owner, {"description": "خرسانة", "quantity": "500"}, self.concrete),
            lambda: services.delete_item(tender, self.masonry, self.owner),
            lambda: services.save_cost(self.concrete, self.owner, {"heading": "other", "unit_cost": "1"}),
            lambda: services.save_cost(self.concrete, self.owner, {"heading": "materials", "unit_cost": "1"}, cost),
            lambda: services.delete_cost(cost, self.owner),
            lambda: services.submit(tender, self.owner),
            lambda: services.close(tender, self.owner, TenderStatus.LOST, "سعر أقل"),
            lambda: services.win(tender, self.owner),
        )
        for attempt in attempts:
            with self.assertRaisesMessage(ValidationError, "اترسّت علينا"):
                attempt()
        self.assertEqual(Project.objects.filter(pk=tender.project_id).count(), 1)
        self.assertEqual(services.analysis(tender)["total"], D("1059520.00"))
        self.assertEqual(TenderCost.objects.count(), 5)

    def test_the_whole_win_is_undone_if_any_service_refuses(self):
        tender = self.reference()
        before = (Project.objects.count(), BoqLine.objects.count(), BudgetLine.objects.count())
        with mock.patch("projects.costs.save_budget", side_effect=ValidationError("refused")):
            with self.assertRaisesMessage(ValidationError, "refused"):
                services.win(tender, self.owner)
        self.assertEqual((Project.objects.count(), BoqLine.objects.count(), BudgetLine.objects.count()), before)
        tender.refresh_from_db()
        self.assertEqual((tender.status, tender.project_id), (TenderStatus.STUDYING, None))
        # A refusal from save_boq_line on the second line also leaves nothing behind.
        real = contract.save_boq_line
        calls = []

        def second_refuses(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise ValidationError("line refused")
            return real(*args, **kwargs)

        with mock.patch("projects.contract.save_boq_line", side_effect=second_refuses):
            with self.assertRaisesMessage(ValidationError, "line refused"):
                services.win(tender, self.owner)
        self.assertEqual((Project.objects.count(), BoqLine.objects.count(), BudgetLine.objects.count()), before)
        # Then it wins for real.
        project = services.win(tender, self.owner)
        self.assertEqual(contract.boq_total(project), D("1059520.00"))

    def test_winning_needs_a_customer_items_and_a_price_for_each(self):
        tender = services.save_tender({"name": "عطاء مدرسة", "prospect_name": "هيئة الأبنية التعليمية", "overhead_percent": "10"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "اختار العميل اللي هيتعمل له المشروع"):
            services.win(tender, self.owner)
        with self.assertRaisesMessage(ValidationError, "مافيهوش بنود"):
            services.win(tender, self.owner, customer=self.customer)
        item = services.save_item(tender, self.owner, {"description": "خرسانة عادية", "unit": "م³", "quantity": "50"})
        with self.assertRaisesMessage(ValidationError, "مالوش تكلفة"):
            services.win(tender, self.owner, customer=self.customer)
        services.save_cost(item, self.owner, {"heading": "materials", "unit_cost": "800"})
        project = services.win(tender, self.owner, customer=self.customer)
        tender.refresh_from_db()
        self.assertEqual((tender.customer, tender.prospect_name, project.contract_value), (self.customer, "", D("44000.00")))  # 50 × 880.00
        self.assertEqual({line.heading: line.amount for line in project.budget.all()}, {"materials": D("40000.00")})

    def test_lost_and_withdrawn_need_a_reason_and_close_the_tender(self):
        lost = self.reference()
        services.close(lost, self.owner, TenderStatus.LOST, "منافس سعره أقل 4%", on=date(2027, 1, 10))
        lost.refresh_from_db()
        self.assertEqual((lost.status, lost.close_reason, lost.status_on), (TenderStatus.LOST, "منافس سعره أقل 4%", date(2027, 1, 10)))
        with self.assertRaisesMessage(ValidationError, "مارسيتش علينا"):
            services.save_item(lost, self.owner, {"description": "بند", "quantity": "1"})
        withdrawn = services.save_tender({"name": "عطاء تاني", "customer": self.customer}, self.owner)
        with self.assertRaisesMessage(ValidationError, "اكتب السبب"):
            services.close(withdrawn, self.owner, TenderStatus.WITHDRAWN, "")
        services.close(withdrawn, self.owner, TenderStatus.WITHDRAWN, "المدة قصيرة")
        withdrawn.refresh_from_db()
        self.assertEqual(withdrawn.status, TenderStatus.WITHDRAWN)
        self.assertEqual(Project.objects.count(), 0)


class ScreenTests(TenderSetup):
    def url(self, name, *args):
        return reverse(f"tenders:{name}", args=args)

    def test_building_the_reference_tender_from_the_screens_then_winning_it(self):
        before = counts()
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url("list")).status_code, 200)
        made = self.client.post(self.url("new"), {"lang": "ar", "name": "عمارة النيل السكنية", "customer": str(self.customer.pk), "due_on": "2026-12-20",
                                                  "overhead_percent": "10", "profit_percent": "12"})
        tender = Tender.objects.get()
        self.assertRedirects(made, self.url("detail", tender.pk) + "?lang=ar", fetch_redirect_response=False)
        detail = self.url("detail", tender.pk)
        self.client.post(detail, {"lang": "ar", "action": "add_item", "code": "1", "description": "خرسانة مسلحة", "unit": "م³", "quantity": "400"})
        self.client.post(detail, {"lang": "ar", "action": "add_item", "code": "2", "description": "مباني طوب", "unit": "م²", "quantity": "2,000"})
        concrete, masonry = tender.items.all()
        for item, costs in ((concrete, (("materials", "900"), ("labour", "200"), ("equipment", "100"))), (masonry, (("materials", "150"), ("labour", "40")))):
            for heading, cost in costs:
                self.client.post(self.url("item", tender.pk, item.pk), {"lang": "ar", "action": "add_cost", "heading": heading, "unit_cost": cost})
        page = self.client.get(detail)
        self.assertContains(page, '<bdi data-figure="total">1,059,520.00</bdi>', html=False)
        self.assertContains(page, '<bdi data-item-rate="1">1,478.40</bdi>', html=False)
        self.assertContains(page, '<bdi data-item-overhead="1">1,320.00</bdi>', html=False)
        self.assertContains(page, '<bdi data-item-total="2">468,160.00</bdi>', html=False)
        self.assertContains(page, '<bdi data-heading-amount="materials">660,000.00</bdi>', html=False)
        self.assertContains(page, "data-print-analysis")
        item_page = self.client.get(self.url("item", tender.pk, concrete.pk))
        self.assertContains(item_page, '<bdi data-figure="direct">1,200.00</bdi>', html=False)
        self.assertContains(item_page, "عمالة")
        self.client.post(detail, {"lang": "ar", "action": "submit"})
        tender.refresh_from_db()
        self.assertEqual(tender.status, TenderStatus.SUBMITTED)
        won = self.client.post(detail, {"lang": "ar", "action": "win", "customer": str(self.customer.pk)}, follow=True)
        tender.refresh_from_db()
        self.assertEqual(tender.status, TenderStatus.WON)
        self.assertContains(won, f"اتعمل المشروع {tender.project.code}")
        self.assertContains(won, "data-won-note")
        self.assertContains(won, reverse("projects:detail", args=[tender.project_id]))
        self.assertNotContains(won, "data-add-item")
        self.assertNotContains(won, 'data-action="win"')
        self.assertNotContains(won, "data-edit-tender")
        self.assertEqual(contract.boq_total(tender.project), D("1059520.00"))
        self.assertEqual(counts(), before)
        # Read-only from every screen: posts change nothing.
        self.assertRedirects(self.client.get(self.url("edit", tender.pk)), detail + "?lang=ar", fetch_redirect_response=False)
        self.client.post(self.url("edit", tender.pk), {"name": "تعديل", "customer": str(self.customer.pk)})
        self.client.post(detail, {"action": "add_item", "description": "بند", "quantity": "1"})
        self.client.post(self.url("item", tender.pk, concrete.pk), {"action": "save_item", "description": "خرسانة", "quantity": "999"})
        self.client.post(self.url("item", tender.pk, concrete.pk), {"action": "add_cost", "heading": "other", "unit_cost": "1"})
        self.client.post(self.url("item", tender.pk, concrete.pk), {"action": "delete_cost", "cost": str(concrete.costs.first().pk)})
        self.client.post(self.url("item", tender.pk, masonry.pk), {"action": "delete_item"})
        self.client.post(detail, {"action": "lost", "reason": "x"})
        tender.refresh_from_db()
        self.assertEqual((tender.name, tender.status, tender.items.count(), TenderCost.objects.count()), ("عمارة النيل السكنية", TenderStatus.WON, 2, 5))
        self.assertEqual(Project.objects.count(), 1)
        item_page = self.client.get(self.url("item", tender.pk, concrete.pk))
        self.assertNotContains(item_page, "data-add-cost")
        self.assertNotContains(item_page, "data-delete-item")

    def test_a_refusal_shows_in_words_and_keeps_what_was_typed(self):
        self.client.force_login(self.owner)
        page = self.client.post(self.url("new"), {"lang": "ar", "name": "عطاء مدرسة", "overhead_percent": "10"})
        self.assertContains(page, "data-tender-error")
        self.assertContains(page, "اسم الجهة")
        self.assertContains(page, 'value="عطاء مدرسة"')
        tender = self.reference()
        refused = self.client.post(self.url("detail", tender.pk), {"lang": "en", "action": "add_item", "description": "Item", "quantity": "-3"}, follow=True)
        self.assertContains(refused, "The quantity must be a number above zero.")
        self.assertEqual(tender.items.count(), 2)
        lost = self.client.post(self.url("detail", tender.pk), {"lang": "ar", "action": "lost", "reason": ""}, follow=True)
        self.assertContains(lost, "اكتب السبب")
        self.client.post(self.url("detail", tender.pk), {"lang": "ar", "action": "lost", "reason": "منافس أرخص"})
        tender.refresh_from_db()
        self.assertEqual(tender.status, TenderStatus.LOST)
        page = self.client.get(self.url("detail", tender.pk))
        self.assertContains(page, "data-closed-note")
        self.assertContains(page, "منافس أرخص")
        listing = self.client.get(self.url("list") + "?status=lost")
        self.assertContains(listing, f'data-tender="{tender.pk}"')
        self.assertNotContains(self.client.get(self.url("list") + "?status=studying"), f'data-tender="{tender.pk}"')

    def test_editing_and_removing_from_the_item_page(self):
        tender = self.reference()
        self.client.force_login(self.owner)
        url = self.url("item", tender.pk, self.concrete.pk)
        labour = self.concrete.costs.get(heading="labour")
        self.client.post(url, {"lang": "ar", "action": "save_cost", "cost": str(labour.pk), "heading": "labour", "description": "نجار ومسلّح", "unit_cost": "250"})
        self.assertEqual(services.analysis(tender)["rows"][0]["rate"], D("1540.00"))  # 1,250.00 × 1.1 × 1.12
        self.client.post(url, {"lang": "ar", "action": "delete_cost", "cost": str(labour.pk)})
        self.assertEqual(services.analysis(tender)["rows"][0]["direct"], D("1000.00"))
        self.client.post(url, {"lang": "ar", "action": "save_item", "code": "1", "description": "خرسانة مسلحة", "unit": "م³", "quantity": "500"})
        self.concrete.refresh_from_db()
        self.assertEqual(self.concrete.quantity, D("500.000"))
        other = self.client.post(url, {"lang": "ar", "action": "delete_cost", "cost": str(self.masonry.costs.first().pk)})
        self.assertEqual(other.status_code, 404)  # another item's cost line
        self.client.post(url, {"lang": "ar", "action": "delete_item"})
        self.assertFalse(TenderItem.objects.filter(pk=self.concrete.pk).exists())
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(self.url("item", tender.pk, 999999)).status_code, 404)

    def test_english_and_arabic(self):
        tender = self.reference()
        self.client.force_login(self.owner)
        english = self.client.get(self.url("detail", tender.pk) + "?lang=en")
        self.assertContains(english, 'dir="ltr"')
        for words in ("Bill of quantities", "Unit rate", "Direct unit cost", "Cost by heading", "Materials", "Won", "Lost", "Withdrawn", "Tender total"):
            self.assertContains(english, words)
        self.assertNotContains(english, "اترسّت علينا")
        arabic = self.client.get(self.url("detail", tender.pk))
        self.assertContains(arabic, 'dir="rtl"')
        for words in ("بنود المقايسة", "سعر الوحدة", "التكلفة المباشرة للوحدة", "التكلفة حسب البند", "خامات", "اترسّت علينا", "مارسيتش علينا", "انسحبنا"):
            self.assertContains(arabic, words)
        self.assertNotContains(arabic, "Unit rate")
        self.assertContains(self.client.get(self.url("list") + "?lang=en"), "Under study")
        self.assertContains(self.client.get(self.url("list")), "تحت الدراسة")
        self.assertContains(self.client.get(self.url("item", tender.pk, self.concrete.pk) + "?lang=en"), "Unit price analysis")
        self.assertContains(self.client.get(self.url("new") + "?lang=en"), "Inviting party")


class PrintTests(TenderSetup):
    def test_the_submission_print_shows_no_cost(self):
        tender = self.reference()
        self.client.force_login(self.owner)
        sheet = self.client.get(reverse("tenders:boq_print", args=[tender.pk]))
        self.assertEqual(sheet.status_code, 200)
        html = sheet.content.decode()
        self.assertIn('data-kind="tender_boq"', html)
        for figure in ("1,478.40", "591,360.00", "234.08", "468,160.00", "1,059,520.00", "شركة النيل للتطوير"):
            self.assertIn(figure, html)
        for cost in ("900.00", "1,200.00", "1,320.00", "190.00", "209.00", "660,000.00", "160,000.00", "40,000.00", "86,000.00", "113,520.00",
                     "خامات", "عمالة", "معدات", "12%", "10%", "المصاريف العامة", "الربح", "التكلفة"):
            self.assertNotIn(cost, html, cost)
        english = self.client.get(reverse("tenders:boq_print", args=[tender.pk]) + "?lang=en").content.decode()
        self.assertIn("Priced bill of quantities", english)
        for cost in ("Materials", "Labour", "Overhead", "Profit", "1,200.00"):
            self.assertNotIn(cost, english, cost)

    def test_the_internal_analysis_shows_costs_and_needs_the_profit_report(self):
        tender = self.reference()
        self.client.force_login(self.owner)
        sheet = self.client.get(reverse("tenders:analysis_print", args=[tender.pk]))
        self.assertEqual(sheet.status_code, 200)
        for figure in ("900.00", "1,200.00", "1,320.00", "1,478.40", "209.00", "234.08", "660,000.00", "160,000.00", "40,000.00", "860,000.00",
                       "86,000.00", "113,520.00", "1,059,520.00", "داخلي"):
            self.assertContains(sheet, figure)
        manager = person(RoleCode.MANAGER, "print_manager")  # manages tenders, no profit report
        self.client.force_login(manager)
        self.assertEqual(self.client.get(reverse("tenders:analysis_print", args=[tender.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("tenders:boq_print", args=[tender.pk])).status_code, 200)
        page = self.client.get(reverse("tenders:detail", args=[tender.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-print-analysis")
        self.assertNotContains(page, "data-item-costs")
        self.assertNotContains(page, "data-by-heading")
        self.assertNotContains(page, "1,200.00")
        self.assertNotContains(page, "12%")
        self.assertContains(page, "data-costs-hidden")
        self.assertContains(page, "1,478.40")  # the rates and totals are the tender's price, not its cost
        item_page = self.client.get(reverse("tenders:item", args=[tender.pk, self.concrete.pk]))
        self.assertNotContains(item_page, "data-costs>")
        self.assertContains(item_page, "data-costs-hidden")
        self.assertNotContains(item_page, "data-add-cost")
        self.assertNotContains(item_page, "1,200.00")
        form = self.client.get(reverse("tenders:edit", args=[tender.pk]))
        self.assertNotContains(form, 'name="overhead_percent"')
        self.assertNotContains(form, 'name="profit_percent"')
        refused = self.client.post(reverse("tenders:item", args=[tender.pk, self.concrete.pk]), {"action": "add_cost", "heading": "other", "unit_cost": "5"}, follow=True)
        self.assertContains(refused, "صلاحية تقرير الأرباح")
        self.assertEqual(TenderCost.objects.count(), 5)
        # A holder of the profit report without the tender office (a custom role: the accountant's rights plus the profit
        # report) gets 403 too, from the analysis' own check after the decorator lets it through.
        from permissions.role_services import create_role, role_codes
        from permissions.services import user_has_permission

        codes = role_codes(make_seeded_role(RoleCode.ACCOUNTANT)) + ["reports.view_profit_report"]
        analyst = make_user(username="print_analyst")
        make_user_profile(user=analyst, role=create_role(name_ar="محاسب بأرباح", name_en="Accountant with profit", codes=codes, actor=self.owner))
        self.assertTrue(user_has_permission(analyst, "reports.view_profit_report"))
        self.assertFalse(user_has_permission(analyst, "sales.create_sales_invoice"))
        self.client.force_login(analyst)
        self.assertEqual(self.client.get(reverse("tenders:analysis_print", args=[tender.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("tenders:detail", args=[tender.pk])).status_code, 403)


class PermissionAndEntityTests(TenderSetup):
    def test_the_office_opens_tenders_and_others_get_403(self):
        tender = self.reference()
        urls = (reverse("tenders:list"), reverse("tenders:new"), reverse("tenders:detail", args=[tender.pk]), reverse("tenders:edit", args=[tender.pk]),
                reverse("tenders:item", args=[tender.pk, self.concrete.pk]), reverse("tenders:boq_print", args=[tender.pk]))
        for role, name in ((RoleCode.OWNER, "pp_owner"), (RoleCode.MANAGER, "pp_manager"), (RoleCode.CASHIER, "pp_cashier")):
            self.client.force_login(person(role, name))
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 200, (role, url))
        for role, name in ((RoleCode.SITE_ENGINEER, "pp_engineer"), (RoleCode.ACCOUNTANT, "pp_accountant"), (RoleCode.STOCK_KEEPER, "pp_keeper")):
            self.client.force_login(person(role, name))
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 403, (role, url))
            self.assertEqual(self.client.post(reverse("tenders:detail", args=[tender.pk]), {"action": "win", "customer": str(self.customer.pk)}).status_code, 403)
            self.assertEqual(self.client.post(reverse("tenders:item", args=[tender.pk, self.concrete.pk]), {"action": "delete_item"}).status_code, 403)
        tender.refresh_from_db()
        self.assertEqual((tender.status, tender.items.count()), (TenderStatus.STUDYING, 2))

    def test_the_menu_item_sits_next_to_projects_for_the_office_only(self):
        from reports.navigation import nav_items
        from settings_core.setup_services import enabled_modules

        keys = [item["key"] for item in nav_items(self.owner, "ar", enabled_modules())]
        self.assertIn("tenders", keys)
        self.assertEqual(keys.index("tenders"), keys.index("projects") + 1)
        engineer = [item["key"] for item in nav_items(person(RoleCode.SITE_ENGINEER, "nav_engineer"), "ar", enabled_modules())]
        self.assertIn("projects", engineer)
        self.assertNotIn("tenders", engineer)
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("tenders:list")), 'href="#hs-i-tenders"')

    def test_another_entitys_tender_is_a_404(self):
        from entities.current import SESSION_KEY
        from entities.models import Entity
        from entities.services import main_entity

        other = Entity.objects.create(code="E2", name_ar="شركة تانية")
        mine = self.reference()
        far = Tender.objects.create(name="عطاء الشركة التانية", prospect_name="جهة", entity=other, created_by=self.owner)
        far_item = TenderItem.objects.create(tender=far, line_number=1, description="بند", quantity=D("1"))
        self.client.force_login(self.owner)
        session = self.client.session
        session[SESSION_KEY] = main_entity().pk
        session.save()
        for url in (reverse("tenders:detail", args=[far.pk]), reverse("tenders:edit", args=[far.pk]), reverse("tenders:item", args=[far.pk, far_item.pk]),
                    reverse("tenders:boq_print", args=[far.pk]), reverse("tenders:analysis_print", args=[far.pk])):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        self.assertEqual(self.client.post(reverse("tenders:detail", args=[far.pk]), {"action": "lost", "reason": "x"}).status_code, 404)
        self.assertEqual(self.client.post(reverse("tenders:item", args=[far.pk, far_item.pk]), {"action": "delete_item"}).status_code, 404)
        self.assertEqual(self.client.get(reverse("tenders:item", args=[mine.pk, far_item.pk])).status_code, 404)  # another tender's item
        far.refresh_from_db()
        self.assertEqual((far.status, far.items.count()), (TenderStatus.STUDYING, 1))
        listing = self.client.get(reverse("tenders:list"))
        self.assertContains(listing, f'data-tender="{mine.pk}"')
        self.assertNotContains(listing, f'data-tender="{far.pk}"')
        # A tender made while working in an entity belongs to it.
        session = self.client.session
        session[SESSION_KEY] = other.pk
        session.save()
        self.client.post(reverse("tenders:new"), {"name": "عطاء جديد", "prospect_name": "جهة"})
        self.assertEqual(Tender.objects.get(name="عطاء جديد").entity, other)


class ScannerTests(TestCase):
    ROOT = pathlib.Path(settings.BASE_DIR)
    TEMPLATES = sorted((ROOT / "templates/tenders").glob("*.html"))

    def test_tokens_only_in_the_stylesheet(self):
        css = (self.ROOT / "static/hesba/css/tenders.css").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsl\(", css))
        self.assertIn("var(--hs-", css)

    def test_templates_carry_no_raw_labels_or_banned_words(self):
        self.assertTrue(self.TEMPLATES)
        sources = [(path.name, path.read_text(encoding="utf-8")) for path in self.TEMPLATES]
        sources.append(("views.py", (self.ROOT / "tenders/views.py").read_text(encoding="utf-8")))
        sources.append(("services.py", (self.ROOT / "tenders/services.py").read_text(encoding="utf-8")))
        for name, text in sources:
            with self.subTest(name=name):
                self.assertIsNone(re.search(r"<td[^>]*data-label=", text))
                self.assertNotIn("_display", text)
                for word in ("ورشة", "الأسطى", "بتاع", "دوس", "لينك"):
                    self.assertNotIn(word, text)
