"""IMPORT-001: upload, review and import from the screen, with the HG-013 guards."""

import io
from datetime import date
from decimal import Decimal as D

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from closing.models import Period, PeriodStatus
from hesba_testing.factories import make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.models import StockMovement, StockMovementType
from master_data.models import Customer, Item
from permissions.models import RoleCode
from sales.models import CustomerLedgerEntry, CustomerLedgerEntryType

from .models import ImportBatch


HOME = reverse("imports:home")


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def csv_file(text, name="data.csv", encoding="utf-8"):
    return SimpleUploadedFile(name, text.encode(encoding), content_type="text/csv")


def xlsx_file(rows, name="data.xlsx"):
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


class ImportScreenTests(TestCase):
    def setUp(self):
        self.owner = person(RoleCode.OWNER, "import_owner")
        self.client.force_login(self.owner)

    def upload(self, target_type, file, **extra):
        response = self.client.post(HOME, {"target_type": target_type, "file": file, **extra})
        batch = ImportBatch.objects.order_by("-pk").first()
        return response, batch

    def test_csv_items_are_reviewed_then_imported(self):
        response, batch = self.upload("items", csv_file("item_code,item_name,default_sale_price,barcode\nA1,قميص,240,5901234123457\nA2,حزام,120,\n"))
        self.assertRedirects(response, f"/imports/{batch.pk}/?lang=ar", fetch_redirect_response=False)
        self.assertEqual((batch.total_rows, batch.valid_rows, batch.invalid_rows), (2, 2, 0))
        self.assertFalse(Item.objects.filter(item_code="A1").exists())
        page = self.client.get(response.url)
        self.assertContains(page, "سجّل كل الصفوف")
        self.client.post(response.url)
        item = Item.objects.get(item_code="A1")
        self.assertEqual((item.item_name, item.default_sale_price, item.barcode), ("قميص", D("240.00"), "5901234123457"))
        batch.refresh_from_db()
        self.assertEqual((batch.status, batch.imported_rows), ("imported", 2))

    def test_excel_customers(self):
        _, batch = self.upload("customers", xlsx_file([["customer_code", "name", "phone", "credit_limit"], ["C1", "علي", "0100", 5000], ["C2", "منى", None, None]]))
        self.assertEqual(batch.valid_rows, 2)
        self.client.post(reverse("imports:detail", args=[batch.pk]))
        self.assertEqual(Customer.objects.get(customer_code="C1").credit_limit, D("5000.00"))
        self.assertEqual(Customer.objects.get(customer_code="C2").phone, "")

    def test_windows_arabic_csv_and_semicolons(self):
        _, batch = self.upload("customers", csv_file("customer_code;name\nW1;عميل ويندوز\n", encoding="cp1256"))
        self.assertEqual(batch.valid_rows, 1)
        self.client.post(reverse("imports:detail", args=[batch.pk]))
        self.assertEqual(Customer.objects.get(customer_code="W1").name, "عميل ويندوز")

    def test_a_bad_row_blocks_the_whole_file(self):
        _, batch = self.upload("items", csv_file("item_code,item_name\nB1,سليم\nB2,\n"))
        self.assertEqual((batch.valid_rows, batch.invalid_rows), (1, 1))
        page = self.client.get(reverse("imports:detail", args=[batch.pk]))
        self.assertNotContains(page, "سجّل كل الصفوف")
        self.assertContains(page, 'role="alert"')
        self.client.post(reverse("imports:detail", args=[batch.pk]), follow=True)
        self.assertFalse(Item.objects.filter(item_code__in=["B1", "B2"]).exists())

    def test_used_opening_balance_cannot_be_changed_by_import(self):
        customer = make_customer(customer_code="USED", opening_balance=D("100.00"))
        CustomerLedgerEntry.objects.create(customer=customer, entry_date=timezone.localdate(), entry_type=CustomerLedgerEntryType.SALES_DUE, due_increase=D("50.00"))
        _, batch = self.upload("opening_balances", csv_file("entity_type,entity_code,opening_balance\ncustomer,USED,999\n"))
        self.assertEqual(batch.invalid_rows, 1)
        self.assertIn("تسوية الرصيد الافتتاحي", batch.raw_rows.get().validation_errors[0])
        # The same customer re-imported with the same balance is fine.
        _, same = self.upload("customers", csv_file("customer_code,name,opening_balance\nUSED,Used Co,100.00\n"))
        self.assertEqual(same.valid_rows, 1)
        _, changed = self.upload("customers", csv_file("customer_code,name,opening_balance\nUSED,Used Co,300\n"))
        self.assertEqual(changed.invalid_rows, 1)
        customer.refresh_from_db()
        self.assertEqual(customer.opening_balance, D("100.00"))

    def test_opening_stock_once_and_never_in_closed_books(self):
        item = make_item(item_code="S1")
        location = make_location(location_code="L1")
        good = f"item_code,location_code,movement_date,quantity,unit_cost\nS1,L1,{timezone.localdate()},10,5\n"
        _, batch = self.upload("stock", csv_file(good))
        self.assertEqual(batch.valid_rows, 1)
        self.client.post(reverse("imports:detail", args=[batch.pk]))
        self.assertEqual(StockMovement.objects.filter(item=item, movement_type=StockMovementType.OPENING_STOCK).count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.average_cost, D("5.0000"))
        _, again = self.upload("stock", csv_file(good))
        self.assertEqual(again.invalid_rows, 1)
        # Duplicates inside one file.
        other = make_item(item_code="S2")
        _, dup = self.upload("stock", csv_file(f"item_code,location_code,quantity\nS2,L1,1\nS2,L1,2\n"), go_live_date=str(timezone.localdate()))
        self.assertEqual((dup.valid_rows, dup.invalid_rows), (1, 1))
        # Closed books.
        Period.objects.create(period_code="OLD", name="old", start_date=date(2024, 1, 1), end_date=date(2024, 1, 31), status=PeriodStatus.CLOSED, closed_at=timezone.now())
        make_item(item_code="S3")
        _, closed = self.upload("stock", csv_file("item_code,location_code,movement_date,quantity\nS3,L1,2024-01-10,4\n"))
        self.assertEqual(closed.invalid_rows, 1)
        self.assertFalse(StockMovement.objects.filter(item__item_code__in=["S2", "S3"]).exists())
        self.assertIsNotNone(other)

    def test_wrong_file_type_and_empty_file(self):
        response = self.client.post(HOME, {"target_type": "items", "file": SimpleUploadedFile("x.pdf", b"%PDF")}, follow=True)
        self.assertContains(response, "xlsx أو csv")
        response = self.client.post(HOME, {"target_type": "items", "file": csv_file("item_code,item_name\n")}, follow=True)
        self.assertContains(response, "الملف فاضي")
        self.assertFalse(ImportBatch.objects.exists())

    def test_users_are_not_imported_from_this_screen(self):
        response = self.client.post(HOME, {"target_type": "users", "file": csv_file("username\nx\n")}, follow=True)
        self.assertFalse(ImportBatch.objects.exists())
        self.assertNotContains(self.client.get(HOME), 'value="users"')
        self.assertEqual(response.status_code, 200)

    def test_templates_download(self):
        response = self.client.get(reverse("imports:template", args=["items.csv"]))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"item_code", b"".join(response.streaming_content))
        self.assertEqual(self.client.get(reverse("imports:template", args=["users.csv"])).status_code, 404)
        self.assertEqual(self.client.get("/imports/templates/..%2Fconfig%2Fsettings.py").status_code, 404)

    def test_only_the_owner_imports(self):
        self.client.force_login(person(RoleCode.MANAGER, "import_manager"))
        self.assertEqual(self.client.get(HOME).status_code, 403)
        self.assertEqual(self.client.post(HOME, {"target_type": "items", "file": csv_file("item_code,item_name\nZ,z\n")}).status_code, 403)
