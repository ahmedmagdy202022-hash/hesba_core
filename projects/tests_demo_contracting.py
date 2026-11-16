"""Contracting C13: the contracting demo company shows every merged contracting feature with realistic data.

The demo is built the way ``prepare_demo`` builds it (``SampleBusinessPerActivityTests.fill``), once for the whole class.
Every figure below is stated in full; each one is what the screen reads from the service that made it, and nothing here
checks a number the demo wrote by itself (the demo only calls services). The clock is the real one: every date is counted
back or forward from ``timezone.localdate()``.
"""

from datetime import timedelta
from decimal import Decimal as D
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import contract, demo, equipment, insights, labour, returns, schedule, site_reports, subcontracting
from .models import (
    BudgetLine, EquipmentLog, LabourDay, LabourPaySheet, Project, PurchaseRequest, PurchaseRequestStatus, SiteWorker, Subcontract, VariationOrder,
)

BANNED = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")


class ContractingDemo(TestCase):
    @classmethod
    def setUpTestData(cls):
        from reports.tests_demo_catalogs import SampleBusinessPerActivityTests

        SampleBusinessPerActivityTests("fill").fill("contracting", "general")
        cls.today = timezone.localdate()
        cls.owner = get_user_model().objects.get(username="owner")
        cls.main = Project.objects.get(name__startswith="عمارة")

    def money(self, value):
        return D(value).quantize(D("0.01"))


class ProjectsTests(ContractingDemo):
    def test_three_projects_the_third_made_by_the_won_tender_and_all_with_documents_and_diaries(self):
        self.assertEqual(list(Project.objects.order_by("pk").values_list("name", flat=True)),
                         ["عمارة سكنية - التجمع", "مبنى مدرسة - المرحلة الأولى", "فيلا سكنية - الشيخ زايد"])
        for project in Project.objects.all():
            self.assertEqual(site_reports.reports_for(project).count(), 4, project.name)
        # C2b: two daily reports with photos (three photos in all) on each project, and today's is still to write
        for project in Project.objects.all():
            reports = list(site_reports.reports_for(project))
            photos = site_reports.photo_counts(reports)
            self.assertEqual(sorted(count for count in photos.values()), [1, 2], project.name)
            self.assertNotIn(self.today, [report.report_date for report in reports])
        self.assertEqual(VariationOrder.objects.get().project, self.main)  # the variation order is still on the project with the contract

    def test_the_projects_list_opens_for_the_owner_and_for_the_site_engineer_in_both_languages(self):
        engineer = get_user_model().objects.get(username="engineer")
        for user in (self.owner, engineer):
            self.client.force_login(user)
            for lang in ("ar", "en"):
                with self.subTest(user=user.username, lang=lang):
                    page = self.client.get(f"{reverse('projects:list')}?lang={lang}")
                    self.assertEqual(page.status_code, 200)
                    self.assertContains(page, self.main.code)


class FeatureTests(ContractingDemo):
    def test_c3_the_schedule_has_four_tasks_and_one_of_them_is_late(self):
        data = schedule.snapshot(self.main, self.today)
        rows = data["rows"]
        self.assertEqual([row["task"].name for row in rows], ["تنفيذ خرسانة عادية للأساسات", "تنفيذ خرسانة مسلحة للهيكل", "تنفيذ مباني طوب أحمر", "تنفيذ لياسة داخلية وخارجية"])
        self.assertEqual([row["actual"] for row in rows], [D("100.0"), D("80.0"), D("0.0"), D("0.0")])
        self.assertEqual([alert["kind"] for alert in data["alerts"]], ["late"])
        self.assertEqual((data["alerts"][0]["task"].name, data["alerts"][0]["days"]), ("تنفيذ خرسانة مسلحة للهيكل", 4))
        self.assertEqual(rows[1]["task"].end_date, self.today - timedelta(days=4))
        self.assertTrue(data["behind"])

    def test_c9_a_cheque_cleared_and_one_in_hand_leave_the_rest_of_the_second_certificate_due(self):
        from cheques.models import Cheque

        cleared = Cheque.objects.get(number="300871")
        waiting = Cheque.objects.get(number="300872")
        self.assertEqual((cleared.status, cleared.amount, cleared.cleared_on), ("cleared", D("125000.00"), self.today - timedelta(days=3)))
        self.assertEqual((waiting.status, waiting.amount, waiting.due_date), ("in_hand", D("175000.00"), self.today + timedelta(days=14)))
        figures = contract.position(self.main)
        self.assertEqual(figures["under_collection"], D("175000.00"))
        self.assertEqual(figures["due_now"], D("200310.00"))
        second = self.main.certificates.get(number=2)
        # the second certificate's net payable = what the two cheques paid + what is still due (the cheque in hand is not yet money)
        self.assertEqual(contract.net_payable(second), D("125000.00") + D("175000.00") + D("200310.00"))
        overdue, total, _ = insights.overdue_certificates()
        self.assertEqual([(row["certificate"].number, row["owed"]) for row in overdue], [(2, D("200310.00"))])
        self.assertEqual(total, D("200310.00"))

    def test_c12_an_issue_voucher_of_three_lines_and_a_return_of_the_surplus(self):
        voucher = returns.voucher_operations(self.main, returns.ISSUE, 2)
        self.assertEqual([(op.item.item_name, op.quantity, op.unit_cost, op.operation_date) for op in voucher],
                         [("أسمنت (شيكارة 50 كجم)", D("40"), D("200"), self.today - timedelta(days=9)), ("رمل (متر مكعب)", D("12"), D("280"), self.today - timedelta(days=9)),
                          ("طوب أحمر (ألف)", D("8"), D("1900"), self.today - timedelta(days=9))])
        back = returns.voucher_operations(self.main, returns.RETURN, 1)
        self.assertEqual([(op.item.item_name, op.quantity, op.unit_cost, op.operation_date) for op in back],
                         [("أسمنت (شيكارة 50 كجم)", D("8"), D("200"), self.today - timedelta(days=2)), ("رمل (متر مكعب)", D("3"), D("280"), self.today - timedelta(days=2))])
        # issued 5 + 40 cement, 12 sand, 8 bricks = 1,000 + 8,000 + 3,360 + 15,200; back 8 cement and 3 sand = 1,600 + 840
        self.assertEqual(returns.materials_cost(self.main), D("25120.00"))
        self.client.force_login(self.owner)
        for kind, number in (("issue", 2), ("return", 1)):
            self.assertEqual(self.client.get(reverse("projects:voucher_print", args=[self.main.pk, kind, number])).status_code, 200)

    def test_c11_a_subcontract_by_quantities_with_its_advance_and_one_posted_bill(self):
        sub = Subcontract.objects.filter(project=self.main, lines__isnull=False).distinct().get()
        self.assertEqual((sub.supplier.name, sub.value, sub.retention_rate, sub.advance_recovery_rate), ("شركة الدلتا لمواد البناء", D("327000.00"), D("5.00"), D("10.00")))
        self.assertEqual([(line.quantity, line.rate, subcontracting.line_amount(line)) for line in sub.lines.all()],
                         [(D("1100"), D("208.00"), D("228800.00")), (D("440"), D("221.00"), D("97240.00"))])
        bill = sub.bills.get()
        self.assertEqual((bill.invoice.status, bill.invoice.total_amount, bill.retention_amount, bill.recovery_amount, bill.bill_date),
                         ("posted", D("114400.00"), D("5720.00"), D("11440.00"), self.today - timedelta(days=6)))
        self.assertEqual(bill.lines.get().quantity, D("550"))
        figures = subcontracting.figures(sub)
        self.assertEqual((figures["advance"], figures["recovered"], figures["advance_left"]), (D("32700.00"), D("11440.00"), D("21260.00")))
        self.assertEqual(subcontracting.bill_warnings(bill), [])  # the owner was certified for more than the 550 billed
        self.assertIsNone(subcontracting.check(sub))  # the bills recover no more advance than was paid
        # the lump-sum subcontract the demo always had is still there, with its one bill
        self.assertEqual(Subcontract.objects.filter(project=self.main).count(), 2)

    def test_c4_a_performance_bond_expires_in_twenty_days_and_an_advance_guarantee_in_a_hundred_and_seventy(self):
        from guarantees import services as guarantees
        from guarantees.models import Guarantee

        bond = Guarantee.objects.get(kind="performance")
        self.assertEqual((bond.amount, bond.margin_percent, bond.commission, bond.project, bond.status), (D("152000.00"), D("10.00"), D("1500.00"), self.main, "active"))
        self.assertEqual(((bond.expires_on - self.today).days, (self.today - bond.issued_on).days), (20, 345))
        advance = Guarantee.objects.get(kind="advance")
        self.assertEqual((advance.amount, (advance.expires_on - self.today).days), (D("424500.00"), 170))
        self.assertEqual(list(guarantees.expiring(Guarantee.objects.all())), [bond])  # only the bond is inside the 30-day alert
        self.assertEqual(guarantees.counts(), {"expiring": 1, "expired": 0, "active": 2})

    def test_c7_one_approved_request_with_its_draft_purchase_order_and_one_waiting(self):
        from orders.models import PurchaseOrder

        approved = PurchaseRequest.objects.get(project=self.main, number=1)
        self.assertEqual((approved.status, approved.requested_by.username, approved.decided_by, approved.needed_by), (PurchaseRequestStatus.ORDERED, "engineer", self.owner, self.today + timedelta(days=7)))
        self.assertEqual([(line.description or line.item.item_name, line.quantity, line.requested_quantity) for line in approved.lines.order_by("position")],
                         [("أسمنت (شيكارة 50 كجم)", D("120"), None), ("رمل (متر مكعب)", D("25"), D("30")), ("مواسير تغذية مياه قطر بوصة", D("150"), None)])
        order = approved.purchase_order
        self.assertEqual((order.status, order.supplier.name), ("draft", "شركة الدلتا لمواد البناء"))
        self.assertEqual([(line.item.item_name, line.quantity) for line in order.lines.all()], [("أسمنت (شيكارة 50 كجم)", D("120")), ("رمل (متر مكعب)", D("25"))])
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        waiting = PurchaseRequest.objects.get(project=self.main, number=2)
        self.assertEqual((waiting.status, waiting.purchase_order, [(line.item.item_name, line.quantity) for line in waiting.lines.all()]),
                         (PurchaseRequestStatus.SUBMITTED, None, [("طوب أحمر (ألف)", D("40"))]))

    def test_c5_a_paid_labour_week_and_two_days_left_for_the_next_sheet(self):
        self.assertEqual(sorted(SiteWorker.objects.values_list("name", "day_rate")),
                         sorted([("محمود عبد الحميد", D("450.00")), ("سيد فتحي", D("450.00")), ("ياسر عطية", D("400.00")), ("عادل منصور", D("250.00")), ("رمضان سعيد", D("250.00"))]))
        sheet = LabourPaySheet.objects.get()
        # six working days (a week from 9 days ago to 3 days ago, less its Friday) x 1,800 a day for the five, plus the carpenter's
        # 2 + 3 hours of overtime: 5 x 450 / 8 = 281.25
        self.assertEqual((sheet.number, sheet.total, sheet.period_from, sheet.period_to), (1, D("11081.25"), self.today - timedelta(days=9), self.today - timedelta(days=3)))
        self.assertEqual(LabourDay.objects.filter(pay_sheet=sheet).count(), 30)
        self.assertEqual(sheet.expense.cashbox_operation.status, "posted")
        self.assertEqual(sheet.expense.cashbox_operation.operation_date, self.today - timedelta(days=2))
        unpaid = labour.unpaid_days(self.main, self.today - timedelta(days=2), self.today)
        self.assertEqual((unpaid.count(), sum(day.days * day.rate for day in unpaid)), (10, D("3600.00")))
        self.assertEqual(labour.labour_cost(self.main), D("11081.25"))

    def test_c6_an_owned_and_a_hired_machine_with_their_hours(self):
        from .models import Equipment

        owned, hired = Equipment.objects.get(kind="owned"), Equipment.objects.get(kind="hired")
        self.assertEqual((owned.name, owned.hour_rate, hired.name, hired.hour_rate, hired.supplier.name), ("حفار شوفل", D("350.00"), "ونش برجي 40 متر", D("650.00"), "مؤسسة الوادي للتوريدات"))
        self.assertEqual(sum(log.hours for log in EquipmentLog.objects.filter(equipment=owned)), D("19.00"))
        self.assertEqual(sum(log.hours for log in EquipmentLog.objects.filter(equipment=hired)), D("22.00"))
        memo = equipment.memo(self.main)
        self.assertEqual((memo["hours"], memo["amount"]), (D("19.00"), D("6650.00")))  # 19 x 350, a memo only
        waiting = equipment.unbilled_hire(self.main)
        self.assertEqual((waiting.count(), sum(log.hours * log.rate for log in waiting)), (3, D("14300.00")))  # 22 x 650, not yet billed

    def test_c8_one_tender_won_with_its_project_and_one_lost_for_a_prospect(self):
        from tenders import services as tenders
        from tenders.models import Tender

        won, lost = Tender.objects.get(status="won"), Tender.objects.get(status="lost")
        self.assertEqual((won.name, won.project.name, won.customer.name, won.status_on), ("فيلا سكنية - الشيخ زايد", "فيلا سكنية - الشيخ زايد", "م. أحمد عبد الله", self.today - timedelta(days=12)))
        self.assertEqual(tenders.analysis(won)["total"], D("2013897.60"))
        self.assertEqual((contract.contract_value(won.project), won.project.boq.count()), (D("2013897.60"), 5))
        self.assertEqual(BudgetLine.objects.filter(project=won.project).count(), 4)  # materials, subcontract, labour, equipment
        self.assertEqual((lost.prospect_name, lost.customer, lost.project, lost.close_reason, lost.status_on),
                         ("شركة الأفق للتطوير العمراني", None, None, "عرضنا أعلى من أقل عطاء بنسبة 6%", self.today - timedelta(days=8)))
        self.assertEqual(tenders.analysis(lost)["total"], D("6733016.00"))

    def test_the_demo_moves_money_and_stock_only_through_the_services_so_the_books_balance(self):
        from ledger.reports import reconciliation
        from reports import selectors

        result = reconciliation()
        self.assertTrue(result["balanced"] and result["ok"], result["rows"])
        alerts = selectors.stock_alert_counts()
        self.assertEqual((alerts["out_of_stock"], alerts["low_stock"]), (1, 1))  # the planted pair, not a third raised by the issue

    def test_building_it_again_adds_nothing(self):
        from cheques.models import Cheque
        from guarantees.models import Guarantee
        from tenders.models import Tender

        before = (Project.objects.count(), Cheque.objects.count(), Guarantee.objects.count(), Tender.objects.count(), PurchaseRequest.objects.count(),
                  LabourDay.objects.count(), EquipmentLog.objects.count(), LabourPaySheet.objects.count())
        from reports.demo_catalogs import active_catalog
        from reports.management.commands.seed_demo_business import Command as Seed

        master = Seed()._seed_master_data(active_catalog())
        self.assertEqual(demo.seed_site_life(master, self.owner, list(Project.objects.order_by("pk"))), [])
        self.assertIsNone(demo.seed_tenders(self.owner, master["customers"]))
        self.assertEqual(demo.seed_bank_side(master, self.owner), [])
        after = (Project.objects.count(), Cheque.objects.count(), Guarantee.objects.count(), Tender.objects.count(), PurchaseRequest.objects.count(),
                 LabourDay.objects.count(), EquipmentLog.objects.count(), LabourPaySheet.objects.count())
        self.assertEqual(after, before)


class TourTests(ContractingDemo):
    def card(self, lang):
        from settings_core.models import ClientProfile

        from config import demo_first_run

        request = RequestFactory().get("/dashboard/")
        request.user = self.owner
        return demo_first_run.tour_card(request, lang, ClientProfile.get_active())

    @override_settings(DEMO_MODE=True)
    def test_the_card_has_twelve_steps_and_every_url_resolves_for_the_owner_in_both_languages(self):
        self.client.force_login(self.owner)
        for lang in ("ar", "en"):
            card = self.card(lang)
            self.assertIsNotNone(card)
            self.assertEqual(len(card["steps"]), 12)
            for step in card["steps"]:
                with self.subTest(lang=lang, step=step["n"], url=step["url"]):
                    self.assertEqual(self.client.get(step["url"]).status_code, 200)
        urls = [step["url"] for step in self.card("ar")["steps"]]
        main = self.main.pk
        self.assertEqual(urls[0], f"/projects/{main}/boq/?lang=ar")
        self.assertEqual(urls[1], f"/projects/{main}/certificates/3/?lang=ar")
        self.assertEqual(urls[3], f"/projects/{main}/payments/?lang=ar")
        self.assertEqual(urls[5], f"/projects/{main}/schedule/?lang=ar")
        self.assertEqual(urls[8], "/guarantees/?lang=ar")
        self.assertEqual(urls[10], "/tenders/?lang=ar")
        self.assertEqual(urls[11], "/projects/report/?lang=ar")
        self.assertEqual(len(set(urls)), 12)

    def test_the_diary_step_opens_the_newest_report_that_has_photos(self):
        path = demo.tour_path("projects:diary_day")
        report = site_reports.reports_for(self.main)[0]
        self.assertEqual(path, reverse("projects:diary_day", args=[self.main.pk, report.pk]))
        self.assertEqual(site_reports.photo_counts([report])[report.pk], 2)  # yesterday's, with two photos

    @override_settings(DEMO_MODE=True)
    def test_an_emptied_demo_drops_the_project_steps_and_the_card_still_renders(self):
        with mock.patch("projects.demo._main_project", return_value=None):
            self.assertIsNone(demo.tour_path("projects:boq"))
            card = self.card("ar")
        self.assertEqual([step["url"] for step in card["steps"]], ["/guarantees/?lang=ar", "/tenders/?lang=ar", "/projects/report/?lang=ar"])


class ToneTests(ContractingDemo):
    def test_no_banned_word_in_any_text_the_demo_wrote_or_in_the_tour(self):
        from cheques.models import Cheque
        from guarantees.models import Guarantee
        from tenders.models import Tender, TenderCost, TenderItem

        from config.demo import DEMO_TOURS
        from reports.tests_demo_names import SLANG, SLANG_FRAGMENTS, UNWANTED_WORDS

        from .models import Equipment, ProjectTask, PurchaseRequestLine, SubcontractLine

        texts = []
        texts += [t.name for t in ProjectTask.objects.all()]
        texts += [value for sub in Subcontract.objects.all() for value in (sub.scope,)] + [line.description for line in SubcontractLine.objects.all()]
        texts += [value for r in PurchaseRequest.objects.all() for value in (r.note, r.decision_note)] + [line.description + line.note for line in PurchaseRequestLine.objects.all()]
        texts += [value for w in SiteWorker.objects.all() for value in (w.name, w.trade)]
        texts += [value for e in Equipment.objects.all() for value in (e.name,)] + [log.operator for log in EquipmentLog.objects.all()]
        texts += [value for t in Tender.objects.all() for value in (t.name, t.notes, t.prospect_name, t.close_reason)]
        texts += [i.description for i in TenderItem.objects.all()] + [c.description for c in TenderCost.objects.all()]
        texts += [g.notes for g in Guarantee.objects.all()] + [c.bank_name for c in Cheque.objects.all()]
        texts += [text for step in DEMO_TOURS["contracting"] for text in (step[0], step[2])]
        self.assertGreater(len([text for text in texts if text]), 60)
        for text in texts:
            for word in BANNED + UNWANTED_WORDS:
                self.assertNotIn(word, text)
            for pattern, word in zip(SLANG_FRAGMENTS, SLANG):
                self.assertIsNone(pattern.search(text), f"{word}: {text}")
