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
