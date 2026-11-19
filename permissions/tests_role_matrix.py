"""The seeded role matrix, pinned code by code.

Written before Manufacturing F8 (HG-140) touched the permission seed, so that adding one permission and one role can be
shown to give no existing role a code and take none away. A change to any seeded role's grants fails here and has to be
made on purpose, in this file, in the same change.

HG-140 then added one row (``production_worker``) and one code (``manufacturing.record_stage``). The owner and the manager
hold that code too (so that they can hand it out, as with HG-048's and HG-084's codes); no other row changed.
"""

from django.test import TestCase

from .models import Permission, Role, RolePermission

#: role code: every permission code it is granted (allow=True), sorted
MATRIX = {
    "owner": """
        accounting.manage_accounts accounting.view_ledger approvals.approve audit.view_audit_log barcode.print_labels cashboxes.manage_cashboxes
        cashboxes.move_cash cashboxes.record_expenses cashboxes.view_cashboxes cashboxes.view_expenses cashboxes.view_finance closing.reopen_period
        closing.run_closing giftcards.breakage giftcards.sell imports.run_import inventory.adjust_stock inventory.manage_movements inventory.transfer_stock
        inventory.view_cost inventory.view_group_stock inventory.view_stock manufacturing.record_stage master_data.adjust_opening_balances
        master_data.manage_items master_data.manage_locations master_data.manage_parties master_data.view_master_data master_data.view_suppliers
        medical.treat_patients medical.view_records medical.write_records permissions.manage_roles projects.issue_to_site projects.record_progress projects.view_projects
        purchases.create_purchase_invoice purchases.pay_supplier purchases.return_purchase purchases.view_purchase_invoices reports.export_reports
        reports.view_all_sales_report reports.view_cashbox_report reports.view_customer_report reports.view_inventory_report reports.view_profit_report
        reports.view_purchase_report reports.view_sales_report reports.view_supplier_report restaurant.prepare_at_payment restaurant.take_orders
        sales.create_sales_invoice sales.receive_customer_payment sales.return_sale sales.view_sales_invoices settings.manage_settings
        settings.manage_support_access settings.view_settings
    """,
    "manager": """
        accounting.view_ledger approvals.approve barcode.print_labels cashboxes.manage_cashboxes cashboxes.view_cashboxes cashboxes.view_expenses
        giftcards.breakage giftcards.sell inventory.adjust_stock inventory.manage_movements inventory.transfer_stock inventory.view_group_stock
        inventory.view_stock manufacturing.record_stage master_data.manage_items master_data.manage_locations master_data.manage_parties
        master_data.view_master_data master_data.view_suppliers medical.treat_patients medical.view_records medical.write_records projects.issue_to_site projects.record_progress
        projects.view_projects purchases.create_purchase_invoice purchases.return_purchase purchases.view_purchase_invoices reports.view_all_sales_report
        reports.view_customer_report reports.view_inventory_report reports.view_purchase_report reports.view_sales_report restaurant.prepare_at_payment
        restaurant.take_orders sales.create_sales_invoice sales.receive_customer_payment sales.return_sale sales.view_sales_invoices settings.view_settings
    """,
    "accountant": """
        accounting.manage_accounts accounting.view_ledger cashboxes.move_cash cashboxes.record_expenses cashboxes.view_cashboxes cashboxes.view_expenses
        cashboxes.view_finance master_data.adjust_opening_balances master_data.manage_parties master_data.view_master_data master_data.view_suppliers
        purchases.pay_supplier purchases.view_purchase_invoices reports.export_reports reports.view_all_sales_report reports.view_cashbox_report
        reports.view_customer_report reports.view_purchase_report reports.view_sales_report reports.view_supplier_report sales.receive_customer_payment
        sales.view_sales_invoices
    """,
    "cashier": """
        cashboxes.view_cashboxes giftcards.sell inventory.view_group_stock master_data.view_master_data reports.view_sales_report
        restaurant.prepare_at_payment restaurant.take_orders sales.create_sales_invoice sales.receive_customer_payment sales.view_sales_invoices
    """,
    "stock_keeper": """
        barcode.print_labels inventory.adjust_stock inventory.manage_movements inventory.transfer_stock inventory.view_group_stock inventory.view_stock
        master_data.manage_items master_data.manage_locations master_data.view_master_data master_data.view_suppliers purchases.create_purchase_invoice
        purchases.view_purchase_invoices reports.view_inventory_report
    """,
    "support": """
        master_data.view_master_data master_data.view_suppliers reports.view_inventory_report settings.view_settings
    """,
    "waiter": """
        restaurant.take_orders
    """,
    "site_engineer": """
        inventory.view_group_stock inventory.view_stock projects.issue_to_site projects.record_progress projects.view_projects
    """,
    "doctor": """
        medical.treat_patients
    """,
    # HG-140 (Manufacturing F8): the only row this change adds. Above it, the owner and the manager gained the one new code.
    "production_worker": """
        inventory.view_stock manufacturing.record_stage
    """,
}


def seeded_matrix():
    rows = {}
    for role in Role.objects.filter(is_system_role=True).order_by("code"):
        rows[role.code] = sorted(role.rolepermission_set.filter(allow=True).values_list("permission__code", flat=True))
    return rows


class SeededRoleMatrixPinTests(TestCase):
    maxDiff = None

    def test_every_seeded_role_holds_exactly_these_codes(self):
        self.assertEqual(seeded_matrix(), {code: sorted(text.split()) for code, text in MATRIX.items()})

    def test_no_seeded_grant_is_a_refusal_and_no_code_is_outside_the_matrix(self):
        self.assertEqual(RolePermission.objects.filter(allow=False).count(), 0)
        granted = {code for text in MATRIX.values() for code in text.split()}
        self.assertEqual(set(Permission.objects.values_list("code", flat=True)), granted)


class ProductionWorkerRoleTests(TestCase):
    """HG-140 (Manufacturing F8): the floor permission belongs to the production worker; the owner and the manager hold it
    only so that they can hand it out (both already hold inventory.adjust_stock, which allows everything it allows)."""

    CODE = "manufacturing.record_stage"

    def test_the_production_worker_holds_exactly_two_codes(self):
        role = Role.objects.get(code="production_worker")
        self.assertTrue(role.is_system_role and role.active)
        self.assertEqual((role.name_ar, role.name_en), ("عامل إنتاج", "Production worker"))
        self.assertEqual(seeded_matrix()["production_worker"], ["inventory.view_stock", self.CODE])

    def test_only_the_owner_and_the_manager_hold_the_floor_permission_beside_the_worker(self):
        holders = sorted(RolePermission.objects.filter(permission__code=self.CODE, allow=True).values_list("role__code", flat=True))
        self.assertEqual(holders, ["manager", "owner", "production_worker"])
        for code in ("owner", "manager"):  # it lets them do nothing new: the wider permission was already theirs
            self.assertIn("inventory.adjust_stock", seeded_matrix()[code])

    def test_the_permission_sits_in_the_inventory_section_and_is_not_a_finance_or_report_one(self):
        permission = Permission.objects.get(code=self.CODE)
        self.assertEqual((permission.module, permission.is_sensitive_finance, permission.is_report_permission, permission.active),
                         ("inventory", False, False, True))
