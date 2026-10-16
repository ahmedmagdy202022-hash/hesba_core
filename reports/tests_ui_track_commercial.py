"""UI-TRACK commercial (clothes shop): every button of every screen reviewed; these are the fixes.

Each test names the finding it covers (F01…F31 on the track page).
"""

from decimal import Decimal

from django.contrib.messages import get_messages
from django.template import Context, Template
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cashboxes.models import Cashbox
from hesba_testing.factories import (
    add_purchase_line,
    make_cashbox,
    make_customer,
    make_item,
    make_location,
    make_seeded_role,
    make_supplier,
    make_user,
    make_user_profile,
    posted_invoice_ready,
    purchase_ready,
    stock_in,
)
from master_data.models import Category, Location
from permissions.models import RoleCode
from purchases.models import PurchaseInvoice, PurchaseReturn, SupplierPayment
from purchases.services import post_purchase_invoice
from reports.tests_dashboard import prepared_client
from sales.models import SalesReturn
from sales.services import post_sales_invoice


def person(role, username, name=""):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role), display_name=name)
    return user


def today():
    return timezone.localdate().isoformat()


class ShopScreensTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "ut_owner", "المالك")
        self.client.force_login(self.owner)

    def page(self, name, *args, **params):
        return self.client.get(reverse(name, args=args), {"lang": "ar", **params}).content.decode()

    # F01
    def test_a_new_item_is_counted_in_pieces_not_in_unit(self):
        self.assertIn('value="قطعة"', self.page("master_data:create", "items"))
        old = make_item(item_code="IT-OLD", item_name="قديم", unit="unit")
        location = make_location()
        stock_in(old, location, 3)
        rendered = Template("{% load hesba_format %}{{ u|unit_label:'ar' }}|{{ u|unit_label:'en' }}|{{ v|unit_label:'ar' }}").render(Context({"u": "unit", "v": "جوز"}))
        self.assertEqual(rendered, "قطعة|piece|جوز")
        both = Template("{% load hesba_format %}{{ a|unit_label:'en' }}|{{ b|unit_label:'ar' }}").render(Context({"a": "قطعة", "b": "piece"}))
        self.assertEqual(both, "piece|قطعة")  # whichever language typed the default, each screen reads its own word
        self.client.post(reverse("master_data:create", args=["items"]), {"lang": "ar", "item_code": "", "item_name": "شراب", "unit": "قطعة",
                                                                         "default_sale_price": "10", "default_purchase_price": "5", "min_stock": "0", "is_stock_tracked": "on", "active": "on"})
        from master_data.models import Item

        self.assertEqual(Item.objects.get(item_name="شراب").unit, "unit")
        self.assertNotIn(">unit<", self.page("inventory:stocktake"))

    # F02, F23
    def test_the_sale_shows_no_developer_note_and_one_message_after_save_and_post(self):
        invoice, item, location, cashbox = posted_invoice_ready()
        post_sales_invoice(invoice.pk, self.owner)
        self.assertNotIn("HG-003", self.page("sales:detail", invoice.pk))
        supplier = make_supplier()
        response = self.client.post(reverse("purchases:create") + "?lang=ar", {
            "lang": "ar", "then": "post", "invoice_number": "", "invoice_date": today(), "supplier": supplier.pk,
            "receiving_location": location.pk, "discount_amount": "", "tax_amount": "", "paid_now": "",
            "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-0-item": item.pk, "lines-0-quantity": "2", "lines-0-unit_purchase_price": "10"})
        notes = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(notes, ["تم ترحيل فاتورة الشراء."])
        bought = PurchaseInvoice.objects.get(supplier=supplier)
        self.assertTrue(bought.invoice_number.startswith("PI-"))  # F12: no number typed → the next PI- number
        self.assertEqual((bought.status, bought.discount_amount, bought.paid_now), ("posted", Decimal("0"), Decimal("0")))  # F14: blanks are 0

    # F04
    def test_period_names_show_one_language(self):
        rendered = Template("{% load hesba_format %}{{ n|one_language:'ar' }}|{{ n|one_language:'en' }}").render(Context({"n": "أكتوبر 2026 / October 2026"}))
        self.assertEqual(rendered, "أكتوبر 2026|October 2026")

    # F08, F13
    def test_a_supplier_payment_numbers_itself_and_starts_on_the_default_cashbox(self):
        supplier = make_supplier(opening_balance=Decimal("500"))
        make_cashbox(cashbox_code="CB-2", name_ar="خزنة تانية")
        main = make_cashbox(cashbox_code="CB-MAIN", is_default=True)
        Cashbox.objects.filter(pk=main.pk).update(opening_balance=Decimal("1000"))
        page = self.page("purchases:payment_create")
        self.assertIn(f'<option value="{main.pk}" selected>', page)
        self.assertIn('data-auto-number="SP-"', page)
        self.client.post(reverse("purchases:payment_create"), {"lang": "ar", "payment_number": "", "payment_date": today(), "supplier": supplier.pk, "cashbox": main.pk, "amount": "100"})
        self.assertTrue(SupplierPayment.objects.get(supplier=supplier).payment_number.startswith("SP-"))

    # F09, F15
    def test_a_purchase_return_lists_the_invoice_lines_and_takes_only_what_is_typed(self):
        invoice, item, location, cashbox = purchase_ready(quantity=4)
        other = make_item(item_code="IT-2", item_name="بنطلون")
        add_purchase_line(invoice, other, quantity=3, unit_purchase_price="10.00")
        from hesba_testing.factories import recalculate_purchase_totals

        recalculate_purchase_totals(invoice)
        post_purchase_invoice(invoice.pk, self.owner)
        page = self.page("purchases:return_create", invoice.pk)
        self.assertIn("data-return-lines", page)
        self.assertEqual(page.count("data-return-line="), 2)
        self.assertIn("بنطلون", page)
        lines = list(invoice.lines.order_by("line_number"))
        response = self.client.post(reverse("purchases:return_create", args=[invoice.pk]), {
            "lang": "ar", "return_number": "", "return_date": today(), "reason": "عيب",
            "lines-TOTAL_FORMS": "2", "lines-INITIAL_FORMS": "2",
            "lines-0-source_line": lines[0].pk, "lines-0-quantity": "",
            "lines-1-source_line": lines[1].pk, "lines-1-quantity": "1"})
        made = PurchaseReturn.objects.get(source_invoice=invoice)
        self.assertRedirects(response, f"/purchases/returns/{made.pk}/?lang=ar", fetch_redirect_response=False)
        self.assertTrue(made.return_number.startswith("PR-"))
        self.assertEqual([(line.source_line_id, line.quantity) for line in made.lines.all()], [(lines[1].pk, Decimal("1.000"))])
        again = self.page("purchases:return_create", invoice.pk)
        self.assertIn("<strong>2</strong>", again)  # 3 bought, 1 back: 2 left on that line

    def test_a_failed_save_and_post_says_saved_and_why_in_one_message(self):
        # Codex #233: one message, not "saved" and the reason separately.
        from unittest import mock

        from django.core.exceptions import ValidationError

        from purchases import views

        location, item, supplier = make_location(), make_item(), make_supplier()
        with mock.patch.object(views, "post_purchase_invoice", side_effect=ValidationError("Purchase invoice must have at least one line before posting.")):
            response = self.client.post(reverse("purchases:create") + "?lang=ar", {
                "lang": "ar", "then": "post", "invoice_number": "", "invoice_date": today(), "supplier": supplier.pk,
                "receiving_location": location.pk, "discount_amount": "0", "tax_amount": "0", "paid_now": "0",
                "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "0", "lines-0-item": item.pk, "lines-0-quantity": "1", "lines-0-unit_purchase_price": "5"})
        notes = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("تم حفظ مسودة فاتورة الشراء.; "))
        self.assertEqual(PurchaseInvoice.objects.get(supplier=supplier).status, "draft")

    def test_a_sales_return_with_nothing_typed_says_so(self):
        invoice, item, location, cashbox = posted_invoice_ready()
        post_sales_invoice(invoice.pk, self.owner)
        line = invoice.lines.get()
        response = self.client.post(reverse("sales:return_create", args=[invoice.pk]), {
            "lang": "ar", "return_number": "", "return_date": today(), "reason": "x",
            "lines-TOTAL_FORMS": "1", "lines-INITIAL_FORMS": "1", "lines-0-source_line": line.pk, "lines-0-quantity": ""})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(SalesReturn.objects.exists())

    # F10, F11
    def test_stock_operations_and_categories_number_themselves(self):
        item = make_item()
        main = make_location(location_code="L1", is_default=True)
        make_location(location_code="L2", name_ar="فرع")
        stock_in(item, main, 5, "10.00")
        page = self.page("inventory:transfer")
        self.assertIn('data-auto-number="TR-"', page)
        self.assertIn(f'<option value="{main.pk}" selected>', page)
        self.assertIn('data-auto-number="ADJ-"', self.page("inventory:adjustment"))
        self.assertIn('data-auto-number="CAT-"', self.page("master_data:create", "categories"))
        self.client.post(reverse("master_data:create", args=["categories"]), {"lang": "ar", "category_code": "", "name_ar": "أحذية", "active": "on"})
        self.assertTrue(Category.objects.get(name_ar="أحذية").category_code.startswith("CAT-"))

    # F16
    def test_hidden_always_hides(self):
        from pathlib import Path

        from django.conf import settings

        css = (Path(settings.BASE_DIR) / "static/hesba/css/form_controls.css").read_text()
        self.assertIn("[hidden]{display:none!important}", css)

    # F17
    def test_cancelling_a_row_is_folded_away(self):
        page = self.page("expenses:list")
        self.assertNotIn('<button class="op-danger" type="submit">إلغاء</button>', page)

    # F19
    def test_the_inventory_report_has_a_total(self):
        item = make_item(average_cost=Decimal("10.00"))
        stock_in(item, make_location(), 3, "10.00")
        self.assertIn("data-inventory-total", self.page("reports:inventory"))

    # F20
    def test_the_reports_title_is_just_reports(self):
        self.assertNotIn("قراءة فقط</h1>", self.page("report_hub"))

    # F21
    def test_the_owner_sees_the_cashiers_open_shift(self):
        from shifts.services import open_shift

        cashier = person(RoleCode.CASHIER, "ut_cashier", "الكاشير")
        open_shift(cashier, make_cashbox(), Decimal("200"))
        page = self.page("shifts:mine")
        self.assertIn("data-shifts-open-now", page)
        self.assertIn("الكاشير", page)

    # F22
    def test_the_daily_summary_says_which_day(self):
        self.assertIn("امبارح", self.page("reports:daily"))

    # F24, F25
    def test_a_shop_role_form_offers_no_medical_or_tables_section_and_keeps_what_a_copy_grants(self):
        page = self.page("settings_core:role_new")
        self.assertNotIn("الملفات الطبية", page)
        self.assertNotIn("الطاولات والطلبات", page)
        self.assertNotIn("* *", page)
        owner_role = make_seeded_role(RoleCode.OWNER)
        copy = self.page("settings_core:role_new", **{"from": owner_role.pk})
        from permissions.models import Permission

        medical = Permission.objects.filter(module="medical", rolepermission__role=owner_role, rolepermission__allow=True).first()
        if medical is not None:  # carried, unseen, so saving the copy does not drop it
            self.assertIn(f'type="hidden" name="perm" value="{medical.code}"', copy)

    # F06
    def test_the_daily_email_notice_is_not_technical(self):
        page = self.page("settings_core:daily_email")
        self.assertNotIn("EMAIL_HOST", page)
        self.assertNotIn("DAILY_EMAIL_SETUP", page)

    # F29
    def test_a_journal_entry_without_memo_says_what_it_was(self):
        from ledger.models import JournalEntry

        entry = JournalEntry(entry_date=timezone.localdate(), source_type="supplier_payment", reference="SP-00001")
        rendered = Template("{% load hesba_labels %}{% entry_memo e %}").render(Context({"e": entry, "lang": "ar"}))
        self.assertEqual(rendered, "سداد لمورد")


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1", DEBUG=True)
class ShopDemoDataTests(TestCase):
    """F26, F27, F28: the sample clothes shop looks like one."""

    def test_the_sample_shop_has_shop_people_a_real_model_and_cash_that_never_starts_red(self):
        from django.contrib.auth import get_user_model
        from django.core.management import call_command

        from settings_core import setup_catalog as catalog
        from settings_core.management.commands.prepare_demo import Command as Prepare
        from settings_core.models import ClientProfile
        from settings_core.setup_services import complete_setup
        from variants.models import VariantGroup

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_setup(ClientProfile.get_active(), "commercial", "fashion", ",".join(catalog.default_modules("commercial")))
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        prepare = Prepare()
        prepare._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        prepare._extras()
        users = set(get_user_model().objects.filter(is_active=True).values_list("username", flat=True))
        self.assertNotIn("waiter", users)
        self.assertNotIn("engineer", users)
        self.assertIn("cashier", users)
        group = VariantGroup.objects.get()
        self.assertNotIn("تجريبي", group.name)
        first_day = timezone.localdate() - timezone.timedelta(days=29)
        for cashbox in Cashbox.objects.exclude(opening_balance=0):
            self.assertLess(timezone.localdate(cashbox.created_at), first_day)
        from master_data.models import Item

        # Codex #233: the default unit is stored language-neutral ("unit") and read in the screen's language.
        self.assertEqual(set(Item.objects.filter(item_code__startswith=group.code).values_list("unit", flat=True)), {"unit"})
