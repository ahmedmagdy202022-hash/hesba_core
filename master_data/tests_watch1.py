"""Watch round 1 fixes: SH-01 (no negative price on an item) and SH-02 (no letters in a phone)."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from master_data.models import Customer, Item, Supplier
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client


def owner_client(test):
    prepared_client()
    user = make_user(username="watch1_owner")
    make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
    test.client.force_login(user)


ITEM_AR = "السعر مينفعش يبقى بالسالب. اكتب صفر لو الصنف ببلاش."
ITEM_EN = "The price cannot be negative. Type 0 for a free item."
PHONE_AR = "اكتب رقم تليفون بأرقام بس (ينفع + في الأول ومسافات وشرطات وأقواس)."
PHONE_EN = "Type a phone number in digits only (a leading +, spaces, dashes and brackets are fine)."


class NegativeItemPriceTests(TestCase):
    def setUp(self):
        owner_client(self)
        self.url = reverse("master_data:item_create")

    def post(self, sale, purchase="1", lang=None, url=None):
        data = {"item_name": "صنف تجربة", "unit": "قطعة", "default_sale_price": sale, "default_purchase_price": purchase,
                "min_stock": "0", "is_stock_tracked": "on", "active": "on"}
        return self.client.post((url or self.url) + (f"?lang={lang}" if lang else ""), data)

    def test_a_negative_sale_price_is_refused_in_arabic(self):
        answer = self.post("-5")
        self.assertEqual(answer.status_code, 200)
        self.assertContains(answer, ITEM_AR)
        self.assertFalse(Item.objects.filter(item_name="صنف تجربة").exists())

    def test_a_negative_sale_price_is_refused_in_english(self):
        answer = self.post("-5", lang="en")
        self.assertContains(answer, ITEM_EN)
        self.assertFalse(Item.objects.filter(item_name="صنف تجربة").exists())

    def test_a_negative_purchase_price_is_refused(self):
        answer = self.post("10", purchase="-0.01")
        self.assertContains(answer, ITEM_AR)
        self.assertFalse(Item.objects.filter(item_name="صنف تجربة").exists())

    def test_zero_prices_and_empty_prices_still_save(self):
        self.assertEqual(self.post("0", purchase="0").status_code, 302)
        item = Item.objects.get(item_name="صنف تجربة")
        self.assertEqual((item.default_sale_price, item.default_purchase_price), (Decimal("0.00"), Decimal("0.00")))
        item.delete()
        self.assertEqual(self.post("", purchase="").status_code, 302)
        self.assertEqual(Item.objects.get(item_name="صنف تجربة").default_sale_price, Decimal("0.00"))

    def test_editing_an_item_to_a_negative_price_is_refused_and_the_old_price_stays(self):
        item = Item.objects.create(item_code="IT-W1", item_name="صنف قديم", default_sale_price=Decimal("20.00"))
        url = reverse("master_data:edit", kwargs={"entity": "items", "pk": item.pk})
        data = {"item_code": "IT-W1", "item_name": "صنف قديم", "unit": "قطعة", "default_sale_price": "-9", "default_purchase_price": "1",
                "min_stock": "0", "is_stock_tracked": "on", "active": "on"}
        answer = self.client.post(url, data)
        self.assertContains(answer, ITEM_AR)
        item.refresh_from_db()
        self.assertEqual(item.default_sale_price, Decimal("20.00"))
        data["default_sale_price"] = "0"
        self.assertEqual(self.client.post(url, data).status_code, 302)

    def test_the_quick_add_from_an_invoice_refuses_it_too(self):
        answer = self.client.post(reverse("master_data:item_quick_create"), {"item_name": "سريع", "default_sale_price": "-3"})
        self.assertEqual(answer.status_code, 400)
        self.assertFalse(Item.objects.filter(item_name="سريع").exists())


class PhoneLettersTests(TestCase):
    def setUp(self):
        owner_client(self)

    def post_customer(self, phone, name="عميل تجربة", lang=None, whatsapp=""):
        url = reverse("master_data:customer_create") + (f"?lang={lang}" if lang else "")
        return self.client.post(url, {"name": name, "phone": phone, "whatsapp": whatsapp, "opening_balance": "", "credit_limit": "", "active": "on"})

    def test_letters_in_a_customer_phone_are_refused_in_both_languages(self):
        self.assertContains(self.post_customer("abc123"), PHONE_AR)
        self.assertContains(self.post_customer("xyz", lang="en"), PHONE_EN)
        self.assertFalse(Customer.objects.filter(name="عميل تجربة").exists())

    def test_letters_in_the_whatsapp_number_are_refused_too(self):
        self.assertContains(self.post_customer("01012345678", whatsapp="call me"), PHONE_AR)
        self.assertFalse(Customer.objects.filter(name="عميل تجربة").exists())

    def test_ordinary_ways_of_writing_a_number_still_save(self):
        for index, phone in enumerate(["01012345678", "+20 100 123-4567", "(02) 2345 6789", "٠١٠١٢٣٤٥٦٧٨", ""]):
            self.assertEqual(self.post_customer(phone, name=f"عميل {index}").status_code, 302, phone)
            self.assertEqual(Customer.objects.get(name=f"عميل {index}").phone, phone)

    def test_a_phone_of_only_signs_is_refused(self):
        self.assertContains(self.post_customer("+-"), PHONE_AR)

    def test_a_supplier_phone_is_checked_the_same_way(self):
        url = reverse("master_data:supplier_create")
        answer = self.client.post(url, {"name": "مورد تجربة", "phone": "abc123", "opening_balance": "", "active": "on"})
        self.assertContains(answer, PHONE_AR)
        self.assertFalse(Supplier.objects.filter(name="مورد تجربة").exists())
        self.assertEqual(self.client.post(url, {"name": "مورد تجربة", "phone": "0100-123", "opening_balance": "", "active": "on"}).status_code, 302)

    def test_an_old_row_with_an_odd_phone_still_saves_while_the_phone_is_untouched(self):
        old = Customer.objects.create(customer_code="C-W1", name="عميل قديم", phone="abc123")
        url = reverse("master_data:edit", kwargs={"entity": "customers", "pk": old.pk})
        data = {"customer_code": "C-W1", "name": "عميل قديم معدّل", "phone": "abc123", "whatsapp": "", "opening_balance": "", "credit_limit": "", "active": "on"}
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        old.refresh_from_db()
        self.assertEqual((old.name, old.phone), ("عميل قديم معدّل", "abc123"))
        data["phone"] = "abc1234"  # touching the phone checks it
        self.assertContains(self.client.post(url, data), PHONE_AR)
