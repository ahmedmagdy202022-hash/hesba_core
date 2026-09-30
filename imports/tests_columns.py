"""IMPORT-002: a file from another program: recognise its columns, match, preview, import."""

from decimal import Decimal as D

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from master_data.models import Customer, Item
from permissions.models import RoleCode

from . import columns
from .models import ImportBatch
from .tests_screen import HOME, csv_file, person, xlsx_file


class RecognitionTests(SimpleTestCase):
    def test_common_arabic_headers_are_recognised(self):
        headers = ["كود الصنف", "اسم الصنف", "الباركود", "سعر البيع", "سعر الشراء", "حد الطلب", "المجموعة", "ملاحظات غريبة"]
        self.assertEqual(columns.suggest("items", headers), {
            "كود الصنف": "item_code", "اسم الصنف": "item_name", "الباركود": "barcode", "سعر البيع": "default_sale_price",
            "سعر الشراء": "default_purchase_price", "حد الطلب": "min_stock", "المجموعة": "category_code", "ملاحظات غريبة": "",
        })

    def test_spelling_variants_and_english_exports_match(self):
        self.assertEqual(columns.suggest("customers", ["رقم العميل", "إسم  العميل", "الموبايل", "الرصيد"]),
                         {"رقم العميل": "customer_code", "إسم  العميل": "name", "الموبايل": "phone", "الرصيد": "opening_balance"})
        self.assertEqual(columns.suggest("items", ["SKU", "Product Name", "Price", "Cost Price"]),
                         {"SKU": "item_code", "Product Name": "item_name", "Price": "default_sale_price", "Cost Price": "default_purchase_price"})

    def test_a_field_is_taken_once_and_exact_names_win(self):
        mapping = columns.suggest("customers", ["الكود", "customer_code", "الاسم", "العميل"])
        self.assertEqual(mapping["customer_code"], "customer_code")  # the exact key beats the alias
        self.assertEqual(mapping["الكود"], "")
        self.assertEqual((mapping["الاسم"], mapping["العميل"]), ("name", ""))

    def test_values_are_cleaned(self):
        self.assertEqual(columns.clean_value("default_sale_price", "١٬٢٥٠٫٥٠"), "1250.50")
        self.assertEqual(columns.clean_value("opening_balance", "1,250.75"), "1250.75")
        self.assertEqual(columns.clean_value("opening_balance", "150-"), "-150")
        self.assertEqual(columns.clean_value("default_sale_price", "25,50"), "25.50")  # a decimal comma
        self.assertEqual(columns.clean_value("default_sale_price", "12,500,000"), "12500000")
        self.assertEqual(columns.clean_value("default_sale_price", "1.250,50"), "1250.50")
        self.assertEqual(columns.clean_value("default_sale_price", "1,250"), "1250")
        self.assertEqual(columns.clean_value("active", "نعم"), "true")
        self.assertEqual(columns.clean_value("active", "لا"), "false")
        self.assertEqual(columns.clean_value("phone", "٠١٠٠١٢٣٤٥٦٧"), "01001234567")

    def test_the_template_is_still_the_fast_path(self):
        self.assertTrue(columns.is_template("items", ["item_code", "item_name", "default_sale_price"]))
        self.assertFalse(columns.is_template("items", ["item_code", "اسم الصنف"]))
        self.assertFalse(columns.is_template("items", ["item_code"]))  # the name is required


class ColumnScreenTests(TestCase):
    def setUp(self):
        self.owner = person(RoleCode.OWNER, "cols_owner")
        self.client.force_login(self.owner)

    def upload(self, target_type, file):
        response = self.client.post(HOME, {"target_type": target_type, "file": file})
        return response, ImportBatch.objects.order_by("-pk").first()

    def test_an_old_program_excel_goes_through_matching_then_imports(self):
        file = xlsx_file([["م", "كود الصنف", "اسم الصنف", "سعر البيع", "الرصيد الحالي"],
                          [1, "P-1", "زيت عباد", "١٢٥", 40], [2, "P-2", "سكر", "1,250.50", 10]])
        response, batch = self.upload("items", file)
        url = reverse("imports:columns", args=[batch.pk])
        self.assertRedirects(response, f"{url}?lang=ar", fetch_redirect_response=False)
        self.assertEqual(batch.total_rows, 2)
        self.assertFalse(Item.objects.exists())
        self.assertRedirects(self.client.get(reverse("imports:detail", args=[batch.pk])), f"{url}?lang=ar", fetch_redirect_response=False)
        page = self.client.get(url)
        self.assertContains(page, 'data-column="كود الصنف"')
        self.assertEqual(page.context["rows"][1]["chosen"], "item_code")
        self.assertEqual(page.context["rows"][0]["chosen"], "")  # the running-number column is ignored
        self.assertIn(["P-2", "سكر", "1250.50"], page.context["preview_rows"])
        self.assertContains(page, "data-stock-hint")  # the quantity column belongs to opening stock
        confirmed = self.client.post(url, {"action": "confirm", "col_0": "", "col_1": "item_code", "col_2": "item_name", "col_3": "default_sale_price", "col_4": ""})
        self.assertRedirects(confirmed, f"/imports/{batch.pk}/?lang=ar", fetch_redirect_response=False)
        batch.refresh_from_db()
        self.assertEqual((batch.valid_rows, batch.invalid_rows), (2, 0))
        self.client.post(reverse("imports:detail", args=[batch.pk]))
        self.assertEqual(Item.objects.get(item_code="P-2").default_sale_price, D("1250.50"))
        self.assertEqual(Item.objects.get(item_code="P-1").default_sale_price, D("125.00"))

    def test_required_fields_block_the_confirm_until_matched_or_numbered(self):
        _, batch = self.upload("customers", csv_file("اسم العميل,الموبايل,الرصيد\nعلي,٠١٠٠١١١٢٢٢٢,300\nمنى,,\n"))
        url = reverse("imports:columns", args=[batch.pk])
        page = self.client.get(url)
        self.assertContains(page, "data-mapping-missing")
        self.assertContains(page, "كود العميل")
        refused = self.client.post(url, {"action": "confirm", "col_0": "name", "col_1": "phone", "col_2": "opening_balance"}, follow=True)
        self.assertContains(refused, "حقول مطلوبة")
        self.assertFalse(Customer.objects.exists())
        self.client.post(url, {"action": "confirm", "col_0": "name", "col_1": "phone", "col_2": "opening_balance", "auto_code": "on"})
        self.client.post(reverse("imports:detail", args=[batch.pk]))
        ali = Customer.objects.get(name="علي")
        self.assertEqual((ali.customer_code, ali.phone, ali.opening_balance), ("C-0001", "01001112222", D("300.00")))
        self.assertEqual(Customer.objects.get(name="منى").customer_code, "C-0002")

    def test_the_same_field_twice_is_refused(self):
        _, batch = self.upload("customers", csv_file("كود,اسم,اسم تاني\n1,أ,ب\n"))
        page = self.client.post(reverse("imports:columns", args=[batch.pk]), {"action": "confirm", "col_0": "customer_code", "col_1": "name", "col_2": "name"}, follow=True)
        self.assertContains(page, "بعمودين")

    def test_errors_after_matching_show_on_the_review_as_before(self):
        _, batch = self.upload("items", csv_file("كود الصنف,اسم الصنف,سعر البيع\nX1,قلم,عشرة\n"))
        self.client.post(reverse("imports:columns", args=[batch.pk]), {"action": "confirm", "col_0": "item_code", "col_1": "item_name", "col_2": "default_sale_price"})
        batch.refresh_from_db()
        self.assertEqual(batch.invalid_rows, 1)
        self.assertNotContains(self.client.get(reverse("imports:detail", args=[batch.pk])), "سجّل كل الصفوف")

    def test_only_the_owner_matches(self):
        _, batch = self.upload("items", csv_file("كود الصنف,اسم الصنف\nQ1,قلم\n"))
        self.client.force_login(person(RoleCode.CASHIER, "cols_cashier"))
        self.assertEqual(self.client.post(reverse("imports:columns", args=[batch.pk]), {"action": "confirm", "col_0": "item_code", "col_1": "item_name"}).status_code, 403)
