"""AN2 (F33, Ahmed: "يظهر الاسم والكود صغير جنبه"): screens name a record; its code sits small beside it."""

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from hesba_testing.factories import add_sales_line, make_customer, make_draft_sales_invoice, make_item, make_location, make_seeded_role, make_supplier, make_user, make_user_profile
from master_data.models import Item
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.templatetags.hesba_format import choice_text, named


class NamesTests(SimpleTestCase):
    def test_a_record_reads_as_its_name(self):
        tee = Item(item_code="TS-M-BK", item_name="تيشيرت", size="M", color="أسود")
        self.assertEqual(str(tee), "تيشيرت · M · أسود")
        self.assertEqual(tee.search_label, "تيشيرت · M · أسود · TS-M-BK")  # the code is still searchable
        self.assertEqual(str(Item(item_code="NO-NAME", item_name="")), "NO-NAME")

    def test_the_code_sits_small_beside_the_name(self):
        tee = Item(item_code="TS-M-BK", item_name="تيشيرت", size="M")
        self.assertEqual(named(tee), 'تيشيرت · M <small class="hs-code"><bdi>TS-M-BK</bdi></small>')
        self.assertEqual(choice_text(tee), "تيشيرت · M · TS-M-BK")
        self.assertEqual(named(None), "")
        self.assertEqual(named(Item(item_code="<b>", item_name="<i>")), '&lt;i&gt; <small class="hs-code"><bdi>&lt;b&gt;</bdi></small>')


class ScreensTests(TestCase):
    def setUp(self):
        prepared_client()
        owner = make_user(username="an2_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)

    def test_parties_and_places_read_as_their_names(self):
        self.assertEqual(str(make_customer(customer_code="C-9", name="كريم")), "كريم")
        self.assertEqual(str(make_supplier(supplier_code="S-9", name="مصنع النور")), "مصنع النور")
        self.assertEqual(str(make_location(location_code="WH-9", name_ar="المخزن")), "المخزن")

    def test_an_invoice_line_names_the_item_first(self):
        location = make_location()
        item = make_item(item_code="IT-77", item_name="زيت")
        invoice = make_draft_sales_invoice(customer=make_customer(), location=location, invoice_number="SI-AN2")
        add_sales_line(invoice, item, 1, "10.00")
        html = self.client.get(reverse("sales:detail", args=(invoice.pk,)), {"lang": "ar"}).content.decode()
        self.assertIn('زيت <small class="hs-code"><bdi>IT-77</bdi></small>', html)
        self.assertNotIn("IT-77 - زيت", html)
