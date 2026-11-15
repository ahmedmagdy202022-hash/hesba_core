"""Contracting C6 (HG-129): equipment, owned and hired, with operating hours.

Reference figures:
* the owned excavator: 600.00 / h × 40 h = 24,000.00, a memo on the project (never in the ledger, never in the company's
  profit); the project's own profit is 40,000.00 before it (one certificate of 40 m³ × 1,000.00) and 16,000.00 after;
* the hired loader: 450.00 / h × 30 h = 13,500.00, a draft purchase of 13,500.00 (15,390.00 with VAT on) that counts as
  project cost only once posted, before tax.
"""

import re
from datetime import timedelta
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse

from cashboxes.models import CashboxMovement, CashboxOperation
from hesba_testing.factories import make_cashbox, make_location, make_supplier
from ledger.models import JournalEntry
from ledger.projector import ensure_fresh
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice
from purchases.services import cancel_posted_purchase_invoice, post_purchase_invoice
from reports.selectors import profit_totals
from sales.services import post_sales_invoice

from . import costs, equipment, services
from .models import CostHeading, Equipment, EquipmentKind, EquipmentLog, ProjectPurchase
from .tests_c2_ui import PRE_LABELLED
from .tests_contract import TODAY, ContractSetup, gl, ledger_ok, person

BANNED = ("ورشة", "الأسطى", "بتاع", "دوس", "لينك")


def day(back):
    return TODAY - timedelta(days=back)


class EquipmentSetup(ContractSetup):
    def setUp(self):
        super().setUp()
        self.engineer = person(RoleCode.SITE_ENGINEER, "c6_engineer")
        self.supplier = make_supplier(supplier_code="S-HIRE", name="مؤسسة الشروق لتأجير المعدات")
        self.excavator = equipment.save_equipment(self.owner, {"name": "حفار كاتربيلر", "kind": "owned", "hour_rate": "600"})
        self.loader = equipment.save_equipment(self.owner, {"name": "لودر 950", "kind": "hired", "hour_rate": "450", "supplier": self.supplier})

    def log(self, machine, hours, back=0, user=None, **extra):
        return equipment.log_hours(self.project, user or self.engineer, equipment=machine, work_date=day(back), hours=hours, **extra)

    def log_loader_30_hours(self):
        return [self.log(self.loader, "10", back, operator="سيد") for back in (3, 2, 1)]

    def url(self, name, *args):
        return reverse(f"projects:{name}", args=[self.project.pk, *args])

    def invoices(self):
        return PurchaseInvoice.objects.count()


class OwnedEquipmentTests(EquipmentSetup):
    def test_the_excavator_is_a_memo_on_the_project_and_moves_nothing_in_the_books(self):
        certificate = self.certify(concrete="40")  # 40 m³ × 1,000.00: the project's revenue
        post_sales_invoice(certificate.invoice_id, self.owner)
        ensure_fresh()
        before = {"journal": JournalEntry.objects.count(), "operations": CashboxOperation.objects.count(), "movements": CashboxMovement.objects.count(),
                  "profit": profit_totals(None, None), "summary": services.summary(self.project), "headings": costs.actual_by_heading(self.project),
                  "project_cost": gl("project_cost")}

        for back, hours in ((4, "8"), (3, "8"), (2, "8"), (1, "8"), (0, "8")):
            self.log(self.excavator, hours, back)  # 40 hours in five days
        memo = equipment.memo(self.project)
        self.assertEqual(memo["hours"], D("40.00"))
        self.assertEqual(memo["amount"], D("24000.00"))  # 600.00 × 40
        self.assertEqual([(row["equipment"].name, row["hours"], row["amount"]) for row in memo["rows"]], [("حفار كاتربيلر", D("40.00"), D("24000.00"))])
        self.assertEqual((memo["profit_before"], memo["profit_after"]), (D("40000.00"), D("16000.00")))
        self.assertEqual(memo["profit_before"] - memo["profit_after"], D("24000.00"))

        # Nothing the books or the project's own figures read has moved.
        ensure_fresh()
        self.assertEqual(JournalEntry.objects.count(), before["journal"])
        self.assertEqual(CashboxOperation.objects.count(), before["operations"])
        self.assertEqual(CashboxMovement.objects.count(), before["movements"])
        self.assertEqual(profit_totals(None, None), before["profit"])
        self.assertEqual(services.summary(self.project), before["summary"])
        self.assertEqual(services.summary(self.project)["profit"], D("40000.00"))
        self.assertEqual(costs.actual_by_heading(self.project), before["headings"])
        self.assertEqual(gl("project_cost"), before["project_cost"])
        self.assertEqual(self.invoices(), 0)
        ledger_ok(self)

    def test_the_rate_is_copied_so_a_new_rate_moves_no_logged_hour(self):
        self.log(self.excavator, "10", 2)
        equipment.save_equipment(self.owner, {"name": "حفار كاتربيلر", "kind": "owned", "hour_rate": "700"}, self.excavator)
        self.log(self.excavator, "10", 1)
        self.assertEqual(sorted(EquipmentLog.objects.values_list("rate", flat=True)), [D("600.00"), D("700.00")])
        self.assertEqual(equipment.memo(self.project)["amount"], D("13000.00"))  # 6,000 + 7,000

    def test_the_project_pages_show_the_memo_labelled_and_beside_the_profit(self):
        post_sales_invoice(self.certify(concrete="40").invoice_id, self.owner)
        for back in (4, 3, 2, 1, 0):
            self.log(self.excavator, "8", back)
        self.client.force_login(self.owner)
        overview = self.client.get(self.url("detail")).content.decode()
        self.assertIn("data-equipment-memo", overview)
        self.assertIn("تحميل معدات ملك", overview)
        self.assertIn("24,000.00", overview)
        self.assertRegex(overview, r'data-figure="profit">\s*40,000\.00')  # the project's profit itself is untouched
        self.assertIn("16,000.00", overview)
        tab = self.client.get(self.url("equipment")).content.decode()
        for marker in ('data-memo="amount"', 'data-memo="profit_before"', 'data-memo="profit_after"', 'data-memo-machine="%d"' % self.excavator.pk):
            self.assertIn(marker, tab)
        self.assertIn("ربح المشروع قبل التحميل", tab)
        english = self.client.get(self.url("equipment") + "?lang=en").content.decode()
        self.assertIn("Owned-equipment charge", english)
        self.assertIn("Project profit after the charge", english)


class HiredEquipmentTests(EquipmentSetup):
    def test_the_hired_loader_is_billed_as_one_draft_and_costs_the_project_only_once_posted(self):
        self.log_loader_30_hours()
        ensure_fresh()
        journal = JournalEntry.objects.count()
        self.assertEqual(equipment.hire_groups(self.project)[0]["hours"], D("30.00"))
        self.assertEqual(equipment.hire_groups(self.project)[0]["amount"], D("13500.00"))  # 450.00 × 30

        (invoice,) = equipment.bill_hire(self.project, self.owner, self.supplier)
        self.assertEqual((invoice.status, invoice.invoice_number, invoice.supplier), ("draft", "EQ-P-0001-01", self.supplier))
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount), (D("13500.00"), D("0.00"), D("13500.00")))
        line = invoice.lines.get()  # one service line per machine
        self.assertEqual((line.quantity, line.unit_purchase_price, line.line_total_amount, line.item.item_code, line.item.is_stock_tracked),
                         (D("30.000"), D("450.00"), D("13500.00"), "PRJ-EQP", False))
        self.assertIn("لودر 950", line.description)
        link = ProjectPurchase.objects.get(invoice=invoice)
        self.assertEqual((link.project, link.heading), (self.project, CostHeading.EQUIPMENT))
        self.assertEqual(set(EquipmentLog.objects.values_list("purchase_line", flat=True)), {line.pk})

        # A draft is no project cost: the project's equipment cost stays 0.00 until the purchase is posted.
        self.assertEqual(costs.actual_by_heading(self.project).get(CostHeading.EQUIPMENT, D("0.00")), D("0.00"))
        self.assertEqual(services.summary(self.project)["purchases"], D("0.00"))
        ensure_fresh()
        self.assertEqual(JournalEntry.objects.count(), journal)  # making the draft books nothing

        post_purchase_invoice(invoice.pk, self.owner)
        self.assertEqual(costs.actual_by_heading(self.project)[CostHeading.EQUIPMENT], D("13500.00"))
        self.assertEqual(services.summary(self.project)["purchases"], D("13500.00"))
        self.assertEqual(gl("project_cost"), D("13500.00"))
        self.assertEqual(gl("payable"), D("-13500.00"))
        ledger_ok(self)

    def test_with_vat_the_draft_is_15390_and_the_project_cost_is_13500_before_tax(self):
        from settings_core.capabilities import _write

        _write("vat", True)
        self.log_loader_30_hours()
        (invoice,) = equipment.bill_hire(self.project, self.owner, self.supplier)
        self.assertEqual((invoice.subtotal, invoice.tax_amount, invoice.total_amount), (D("13500.00"), D("1890.00"), D("15390.00")))
        self.assertEqual(costs.actual_by_heading(self.project).get(CostHeading.EQUIPMENT, D("0.00")), D("0.00"))
        post_purchase_invoice(invoice.pk, self.owner)
        self.assertEqual(costs.actual_by_heading(self.project)[CostHeading.EQUIPMENT], D("13500.00"))
        self.assertEqual((gl("vat_in"), gl("project_cost"), gl("payable")), (D("1890.00"), D("13500.00"), D("-15390.00")))
        ledger_ok(self)

    def test_billing_the_same_hours_again_is_refused(self):
        self.log_loader_30_hours()
        equipment.bill_hire(self.project, self.owner, self.supplier)
        with self.assertRaisesMessage(ValidationError, "مفيش ساعات إيجار لسه ما اتفوترتش"):
            equipment.bill_hire(self.project, self.owner, self.supplier)
        with self.assertRaisesMessage(ValidationError, "no unbilled hire hours"):
            equipment.bill_hire(self.project, self.owner, self.supplier, lang="en")
        self.assertEqual(self.invoices(), 1)
        self.assertEqual(ProjectPurchase.objects.filter(heading=CostHeading.EQUIPMENT).count(), 1)
        # Only hours logged afterwards are billed next: 5 h × 450.00.
        self.log(self.loader, "5", 0)
        (second,) = equipment.bill_hire(self.project, self.owner, self.supplier)
        self.assertEqual((second.invoice_number, second.total_amount), ("EQ-P-0001-02", D("2250.00")))

    def test_cancelling_the_posted_bill_frees_the_hours_without_writing_anything_on_them(self):
        self.log_loader_30_hours()
        (invoice,) = equipment.bill_hire(self.project, self.owner, self.supplier)
        post_purchase_invoice(invoice.pk, self.owner)
        lines = set(EquipmentLog.objects.values_list("purchase_line", flat=True))
        self.assertEqual(equipment.hire_groups(self.project), [])
        cancel_posted_purchase_invoice(invoice.pk, self.owner, "خطأ")
        self.assertEqual(set(EquipmentLog.objects.values_list("purchase_line", flat=True)), lines)  # nothing was written on cancel
        groups = equipment.hire_groups(self.project)
        self.assertEqual((groups[0]["hours"], groups[0]["amount"]), (D("30.00"), D("13500.00")))
        self.assertEqual(costs.actual_by_heading(self.project).get(CostHeading.EQUIPMENT, D("0.00")), D("0.00"))
        (again,) = equipment.bill_hire(self.project, self.owner, self.supplier)  # the 30 hours bill again, once
        self.assertEqual(again.total_amount, D("13500.00"))
        post_purchase_invoice(again.pk, self.owner)
        self.assertEqual(costs.actual_by_heading(self.project)[CostHeading.EQUIPMENT], D("13500.00"))
        ledger_ok(self)

    def test_withdrawing_a_draft_bill_frees_its_hours_and_a_posted_one_cannot_be_withdrawn(self):
        self.log_loader_30_hours()
        (invoice,) = equipment.bill_hire(self.project, self.owner, self.supplier)
        with self.assertRaisesMessage(ValidationError, "دي فاتورة إيجار معدات"):
            costs.unlink_purchase(self.project, ProjectPurchase.objects.get(invoice=invoice), self.owner)  # the generic unlink would strand its hours
        equipment.withdraw_hire(self.project, invoice, self.owner)
        self.assertFalse(PurchaseInvoice.objects.exists())
        self.assertEqual(set(EquipmentLog.objects.values_list("purchase_line", flat=True)), {None})
        self.assertEqual(equipment.hire_groups(self.project)[0]["hours"], D("30.00"))
        (second,) = equipment.bill_hire(self.project, self.owner, self.supplier)
        post_purchase_invoice(second.pk, self.owner)
        with self.assertRaisesMessage(ValidationError, "اترحّلت"):
            equipment.withdraw_hire(self.project, second, self.owner)

    def test_a_draft_with_vat_is_withdrawn_too_and_its_hours_come_back(self):
        from settings_core.capabilities import _write

        _write("vat", True)
        self.log_loader_30_hours()
        (invoice,) = equipment.bill_hire(self.project, self.owner, self.supplier)
        self.assertEqual(invoice.total_amount, D("15390.00"))
        equipment.withdraw_hire(self.project, invoice, self.owner)
        self.assertFalse(PurchaseInvoice.objects.exists())
        self.assertEqual(equipment.hire_groups(self.project)[0]["amount"], D("13500.00"))

    def test_one_line_per_machine_and_a_changed_rate_is_its_own_line(self):
        crane = equipment.save_equipment(self.owner, {"name": "ونش 20 طن", "kind": "hired", "hour_rate": "800", "supplier": self.supplier})
        self.log(self.loader, "10", 3)
        equipment.save_equipment(self.owner, {"name": "لودر 950", "kind": "hired", "hour_rate": "500", "supplier": self.supplier}, self.loader)
        self.log(self.loader, "10", 2)
        self.log(crane, "4", 2)
        (invoice,) = equipment.bill_hire(self.project, self.owner, self.supplier)
        self.assertEqual([(line.quantity, line.unit_purchase_price, line.line_total_amount) for line in invoice.lines.order_by("line_number")],
                         [(D("10.000"), D("450.00"), D("4500.00")), (D("10.000"), D("500.00"), D("5000.00")), (D("4.000"), D("800.00"), D("3200.00"))])
        self.assertEqual(invoice.total_amount, D("12700.00"))

    def test_each_supplier_is_billed_on_its_own_invoice(self):
        other = make_supplier(supplier_code="S-CRANE", name="شركة الأمل للونشات")
        crane = equipment.save_equipment(self.owner, {"name": "ونش", "kind": "hired", "hour_rate": "800", "supplier": other})
        self.log(self.loader, "10", 1)
        self.log(crane, "5", 1)
        self.assertEqual([(g["supplier"], g["amount"]) for g in equipment.hire_groups(self.project)], [(other, D("4000.00")), (self.supplier, D("4500.00"))])
        (first,) = equipment.bill_hire(self.project, self.owner, self.supplier)
        self.assertEqual((first.supplier, first.total_amount), (self.supplier, D("4500.00")))
        self.assertEqual([g["supplier"] for g in equipment.hire_groups(self.project)], [other])
        with self.assertRaisesMessage(ValidationError, "اختار المورد"):
            equipment.bill_hire(self.project, self.owner, None)

    def test_owned_hours_are_never_billed(self):
        self.log(self.excavator, "10", 1)
        self.assertEqual(equipment.hire_groups(self.project), [])
        with self.assertRaisesMessage(ValidationError, "مفيش ساعات إيجار"):
            equipment.bill_hire(self.project, self.owner, self.supplier)
        self.assertEqual(self.invoices(), 0)


class RulesTests(EquipmentSetup):
    def test_the_register_rules(self):
        with self.assertRaisesMessage(ValidationError, "اختار المورد"):
            equipment.save_equipment(self.owner, {"name": "لودر", "kind": "hired", "hour_rate": "100"})
        with self.assertRaisesMessage(ValidationError, "سعر الساعة"):
            equipment.save_equipment(self.owner, {"name": "حفار", "kind": "owned", "hour_rate": "0"})
        with self.assertRaisesMessage(ValidationError, "اكتب اسم"):
            equipment.save_equipment(self.owner, {"name": " ", "kind": "owned", "hour_rate": "10"})
        owned = equipment.save_equipment(self.owner, {"name": "مولد", "kind": "owned", "hour_rate": "50", "supplier": self.supplier})
        self.assertIsNone(owned.supplier)  # an owned machine has no supplier
        self.assertEqual(owned.entity.is_main, True)
        self.log(self.loader, "1", 0)
        with self.assertRaisesMessage(ValidationError, "نوعها يتغيّر"):
            equipment.save_equipment(self.owner, {"name": "لودر 950", "kind": "owned", "hour_rate": "450"}, self.loader)

    def test_the_hours_rules(self):
        for bad in ("0", "-2", "abc", "24.5", ""):
            with self.assertRaisesMessage(ValidationError, "أكبر من صفر"):
                self.log(self.loader, bad, 0)
        self.log(self.loader, "20", 1)
        with self.assertRaisesMessage(ValidationError, "بتعدّي 24"):
            self.log(self.loader, "5", 1)  # the same machine, the same day: 25 h
        self.log(self.excavator, "20", 1)  # another machine the same day is fine
        with self.assertRaisesMessage(ValidationError, "تاريخ جاي"):
            equipment.log_hours(self.project, self.engineer, equipment=self.loader, work_date=TODAY + timedelta(days=1), hours="1")
        equipment.save_equipment(self.owner, {"name": "لودر 950", "kind": "hired", "hour_rate": "450", "supplier": self.supplier, "active": False}, self.loader)
        with self.assertRaisesMessage(ValidationError, "موقوفة"):
            self.log(self.loader, "1", 0)
        with self.assertRaisesMessage(ValidationError, "Choose the machine"):
            equipment.log_hours(self.project, self.engineer, equipment=None, hours="1", lang="en")

    def test_a_closed_project_takes_no_hours_and_no_bill(self):
        self.log(self.loader, "10", 1)
        self.project.status = "done"
        self.project.save()
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            self.log(self.loader, "1", 0)
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            equipment.bill_hire(self.project, self.owner, self.supplier)
        self.assertEqual(EquipmentLog.objects.count(), 1)

    def test_a_billed_hour_cannot_be_deleted_and_a_free_one_can(self):
        billed = self.log(self.loader, "10", 2)
        equipment.bill_hire(self.project, self.owner, self.supplier)
        free = self.log(self.loader, "2", 1)
        with self.assertRaisesMessage(ValidationError, "اتفوترت في فاتورة إيجار"):
            equipment.delete_log(self.project, billed, self.owner)
        equipment.delete_log(self.project, free, self.owner)
        self.assertEqual(list(EquipmentLog.objects.values_list("pk", flat=True)), [billed.pk])

    def test_a_new_rate_is_for_the_next_hours_only(self):
        self.log(self.loader, "10", 2)
        equipment.save_equipment(self.owner, {"name": "لودر 950", "kind": "hired", "hour_rate": "500", "supplier": self.supplier}, self.loader)
        self.assertEqual(EquipmentLog.objects.get().rate, D("450.00"))
        self.assertEqual(self.log(self.loader, "10", 1).rate, D("500.00"))


class ScreenTests(EquipmentSetup):
    def test_the_engineer_logs_but_cannot_bill_and_sees_no_money(self):
        self.client.force_login(self.engineer)
        tab = self.client.get(self.url("equipment"))
        self.assertContains(tab, "data-equipment-log-form")
        self.assertContains(tab, 'data-tab="equipment"')
        posted = self.client.post(self.url("equipment"), {"action": "log", "equipment": str(self.loader.pk), "work_date": day(1).isoformat(), "hours": "10",
                                                          "operator": "سيد"})
        self.assertEqual(posted.status_code, 302, re.findall(r"data-project-error[^>]*>([^<]*)", posted.content.decode()))
        log = EquipmentLog.objects.get()
        self.assertEqual((log.hours, log.rate, log.operator, log.created_by, log.purchase_line), (D("10.00"), D("450.00"), "سيد", self.engineer, None))
        page = self.client.get(self.url("equipment")).content.decode()
        for money in ("4,500.00", "450.00", "data-hire-bill-form", "data-equipment-hire", "data-equipment-memo", "data-memo="):
            self.assertNotIn(money, page)
        self.assertIn("data-hours-only", page)
        self.assertIn('data-equipment-log="%d"' % log.pk, page)

        for data in ({"action": "bill", "supplier": str(self.supplier.pk)}, {"action": "withdraw", "invoice": "1"}):
            self.assertEqual(self.client.post(self.url("equipment"), data).status_code, 403, data["action"])
        self.assertEqual(self.invoices(), 0)
        self.assertEqual(ProjectPurchase.objects.count(), 0)
        log.refresh_from_db()
        self.assertIsNone(log.purchase_line)
        # The register is read-only for the engineer: names and hours, no rate, no form.
        register = self.client.get(reverse("projects:equipment_register"))
        self.assertContains(register, "data-equipment=")
        self.assertNotContains(register, "data-equipment-form")
        self.assertNotContains(register, "450.00")
        self.assertEqual(self.client.post(reverse("projects:equipment_register"), {"name": "x", "kind": "owned", "hour_rate": "1"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("projects:equipment_edit", args=[self.loader.pk]), {"name": "x", "kind": "hired", "hour_rate": "1"}).status_code, 403)
        self.assertEqual(Equipment.objects.count(), 2)

    def test_the_office_bills_from_the_page_and_the_draft_is_linked(self):
        self.log_loader_30_hours()
        self.client.force_login(self.owner)
        page = self.client.get(self.url("equipment")).content.decode()
        self.assertIn("data-hire-group", page)
        self.assertIn("13,500.00", page)
        self.assertIn("اعمل فاتورة الإيجار", page)
        done = self.client.post(self.url("equipment"), {"action": "bill", "supplier": str(self.supplier.pk)}, follow=True)
        self.assertContains(done, "EQ-P-0001-01")
        invoice = PurchaseInvoice.objects.get()
        self.assertEqual((invoice.status, invoice.total_amount), ("draft", D("13500.00")))
        self.assertNotContains(done, "data-hire-bill-form")
        self.assertContains(done, 'data-hire-bill="EQ-P-0001-01"')
        # The same click again is refused, with the reason on the page and nothing written.
        again = self.client.post(self.url("equipment"), {"action": "bill", "supplier": str(self.supplier.pk)})
        self.assertContains(again, "مفيش ساعات إيجار لسه ما اتفوترتش")
        self.assertEqual(self.invoices(), 1)
        self.assertEqual(self.client.post(self.url("equipment"), {"action": "bill", "supplier": "99999"}).status_code, 404)
        withdrawn = self.client.post(self.url("equipment"), {"action": "withdraw", "invoice": str(invoice.pk)}, follow=True)
        self.assertEqual(self.invoices(), 0)
        self.assertContains(withdrawn, "data-hire-bill-form")
        english = self.client.get(self.url("equipment") + "?lang=en").content.decode()
        self.assertIn("Make the hire bill", english)
        self.assertIn('dir="ltr"', english)

    def test_the_register_adds_and_edits_a_machine(self):
        self.client.force_login(self.owner)
        register_url = reverse("projects:equipment_register")
        page = self.client.get(register_url).content.decode()
        self.assertIn("data-equipment-form", page)
        self.assertIn("450.00", page)
        added = self.client.post(register_url, {"name": "مولد كهرباء", "kind": "owned", "hour_rate": "120", "active": "1"})
        self.assertEqual(added.status_code, 302)
        machine = Equipment.objects.get(name="مولد كهرباء")
        self.assertEqual((machine.kind, machine.hour_rate, machine.active, machine.supplier), (EquipmentKind.OWNED, D("120.00"), True, None))
        edit_url = reverse("projects:equipment_edit", args=[machine.pk])
        self.assertContains(self.client.get(edit_url), "data-equipment-form")
        self.client.post(edit_url, {"name": "مولد كهرباء", "kind": "owned", "hour_rate": "150", "active": "0"})
        machine.refresh_from_db()
        self.assertEqual((machine.hour_rate, machine.active), (D("150.00"), False))
        self.assertContains(self.client.post(register_url, {"name": "لودر", "kind": "hired", "hour_rate": "10"}), "اختار المورد")
        self.assertContains(self.client.get(register_url + "?lang=en"), "Equipment register")

    def test_another_entitys_machine_and_log_answer_404_and_change_nothing(self):
        from entities.models import Entity, EntityMembership

        log = self.log(self.loader, "10", 1)
        branch = Entity.objects.create(code="E-EQ", name_ar="فرع المعدات")
        make_location(location_code="EQ-LOC", entity=branch, is_receiving_location=True)
        make_cashbox(cashbox_code="EQ-BOX", entity=branch)
        manager = person(RoleCode.MANAGER, "c6_branch_manager")
        EntityMembership.objects.create(user=manager, entity=branch, is_default=True)
        self.client.force_login(manager)
        edit_url = reverse("projects:equipment_edit", args=[self.loader.pk])
        self.assertEqual(self.client.get(edit_url).status_code, 404)
        self.assertEqual(self.client.post(edit_url, {"name": "x", "kind": "hired", "hour_rate": "1", "supplier": str(self.supplier.pk)}).status_code, 404)
        tab = self.client.get(self.url("equipment"))
        self.assertNotContains(tab, 'data-equipment-log="%d"' % log.pk)
        self.assertNotContains(tab, "data-hire-group")
        self.assertEqual(self.client.post(self.url("equipment"), {"action": "log", "equipment": str(self.loader.pk), "hours": "5",
                                                                  "work_date": day(0).isoformat()}).status_code, 404)
        self.assertEqual(self.client.post(self.url("equipment"), {"action": "delete_log", "log": str(log.pk)}).status_code, 404)
        self.assertEqual(self.client.post(self.url("equipment"), {"action": "bill", "supplier": str(self.supplier.pk)}).status_code, 200)  # nothing of its own to bill
        self.assertNotContains(self.client.get(reverse("projects:equipment_register")), "data-equipment=")
        self.assertEqual(self.invoices(), 0)
        self.assertEqual(EquipmentLog.objects.count(), 1)
        self.loader.refresh_from_db()
        self.assertEqual(self.loader.hour_rate, D("450.00"))
        # And the machine of this entity is billed to its own books: an owner working in the branch sees none of the main entity's.
        from entities.current import working_in

        with working_in(branch):
            self.assertEqual(equipment.hire_groups(self.project), [])
            with self.assertRaisesMessage(ValidationError, "كيان تاني"):
                equipment.log_hours(self.project, self.owner, equipment=self.loader, hours="1")

    def test_a_branch_machine_is_billed_into_the_branch_location(self):
        from entities.current import working_in
        from entities.models import Entity

        branch = Entity.objects.create(code="E-EQ2", name_ar="فرع الحفر")
        site_store = make_location(location_code="EQ2-LOC", entity=branch, is_receiving_location=True)
        make_cashbox(cashbox_code="EQ2-BOX", entity=branch)
        with working_in(branch):
            machine = equipment.save_equipment(self.owner, {"name": "لودر الفرع", "kind": "hired", "hour_rate": "400", "supplier": self.supplier})
            self.log(machine, "10", 1)
            (invoice,) = equipment.bill_hire(self.project, self.owner, self.supplier)
        self.assertEqual((machine.entity, invoice.receiving_location, invoice.total_amount), (branch, site_store, D("4000.00")))
        post_purchase_invoice(invoice.pk, self.owner)
        ledger_ok(self)

    def test_the_tab_and_register_open_for_the_people_who_should_and_not_for_others(self):
        from hesba_testing.factories import grant, make_permission, make_role, make_user, make_user_profile

        self.client.force_login(self.owner)
        overview = self.client.get(self.url("detail")).content.decode()
        self.assertIn('data-tab="equipment"', overview)
        stranger = make_user(username="c6_stranger")
        make_user_profile(user=stranger, role=make_role(code="R-c6-none"))
        self.client.force_login(stranger)
        self.assertEqual(self.client.get(self.url("equipment")).status_code, 403)
        self.assertEqual(self.client.get(reverse("projects:equipment_register")).status_code, 403)
        role = make_role(code="R-c6-buyer")
        for code in ("projects.view_projects", "purchases.view_purchase_invoices", "purchases.create_purchase_invoice"):
            from permissions.models import Permission

            grant(role, Permission.objects.filter(code=code).first() or make_permission(code=code))
        buyer = make_user(username="c6_buyer")
        make_user_profile(user=buyer, role=role)
        self.log_loader_30_hours()
        self.client.force_login(buyer)
        page = self.client.get(self.url("equipment")).content.decode()
        self.assertIn("data-hire-bill-form", page)  # the buyer bills, but cannot log hours:
        self.assertNotIn("data-equipment-log-form", page)
        self.assertEqual(self.client.post(self.url("equipment"), {"action": "log", "equipment": str(self.loader.pk), "hours": "1"}).status_code, 403)
        self.client.post(self.url("equipment"), {"action": "bill", "supplier": str(self.supplier.pk)})
        self.assertEqual(PurchaseInvoice.objects.get().created_by, buyer)


class ScannerTests(EquipmentSetup):
    TEMPLATES = ("equipment.html", "equipment_register.html", "equipment_edit.html", "_equipment_fields.html")

    def test_the_stylesheet_uses_tokens_only_and_every_token_is_defined(self):
        css = Path(settings.BASE_DIR, "static/hesba/css/projects_equipment.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(css, r"rgba?\(")
        self.assertNotRegex(css, r"(?<![-\w])(left|right)\s*:")
        self.assertIn("@media (min-width:720px)", css)
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", Path(settings.BASE_DIR, "static/hesba/css/tokens.css").read_text(encoding="utf-8")))
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertTrue(used)
        self.assertEqual(used - defined, set())

    def test_no_template_pre_fills_a_data_label_and_the_pages_load_their_stylesheet(self):
        folder = Path(settings.BASE_DIR, "templates/projects")
        for name in self.TEMPLATES:
            source = (folder / name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertNotRegex(source, PRE_LABELLED)
                self.assertNotIn("data-label", source)
                if not name.startswith("_"):
                    self.assertIn("projects_equipment.css' %}?v=c6-1", source)

    def test_the_words_never_use_the_banned_terms(self):
        from .views_equipment import WORDS

        texts = [Path(settings.BASE_DIR, "templates/projects", name).read_text(encoding="utf-8") for name in self.TEMPLATES]
        texts.append(Path(settings.BASE_DIR, "projects/equipment.py").read_text(encoding="utf-8"))
        texts += [str(WORDS["ar"]), str(WORDS["en"]), str(equipment.MESSAGES["ar"]), str(equipment.MESSAGES["en"])]
        for word in BANNED:
            for text in texts:
                self.assertNotIn(word, text)

    def test_every_page_renders_in_both_languages_with_the_page_direction(self):
        self.log(self.loader, "10", 1)
        self.log(self.excavator, "10", 1)
        self.client.force_login(self.owner)
        for name, args in (("equipment", [self.project.pk]), ("equipment_register", []), ("equipment_edit", [self.loader.pk])):
            for lang, direction in (("ar", 'dir="rtl"'), ("en", 'dir="ltr"')):
                with self.subTest(page=name, lang=lang):
                    response = self.client.get(reverse(f"projects:{name}", args=args) + f"?lang={lang}")
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, direction)
                    self.assertContains(response, "projects_equipment.css")
        # Arabic words, not their English keys: the choice labels follow the language.
        self.assertContains(self.client.get(reverse("projects:equipment_register")), "إيجار")
        self.assertContains(self.client.get(reverse("projects:equipment_register") + "?lang=en"), "Hired")

    def test_migration_0008_follows_0007_and_adds_two_tables_only(self):
        from django.db.migrations.loader import MigrationLoader

        loader = MigrationLoader(None, ignore_no_migrations=True)
        migration = loader.disk_migrations[("projects", "0008_equipment")]
        self.assertIn(("projects", "0007_subcontract_lines"), migration.dependencies)
        self.assertEqual([type(operation).__name__ for operation in migration.operations], ["CreateModel", "CreateModel"])
        self.assertEqual([operation.name for operation in migration.operations], ["Equipment", "EquipmentLog"])
