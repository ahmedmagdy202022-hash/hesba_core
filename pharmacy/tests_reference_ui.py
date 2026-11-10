"""R1 step 2: «اختار من القايمة» on the new-item form and «بدائل مش عندك» on the drug page.

The reference list only fills the form: an item and its drug data exist once the person saves the ordinary item form,
through ``services.save_drug``; ``DrugReference`` itself is never written here. All names are invented."""

import re
from datetime import timedelta
from decimal import Decimal as D

from django.contrib.staticfiles import finders
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import make_item
from master_data.models import Item
from permissions.models import RoleCode
from settings_core import capabilities as caps

from . import picker, services
from .models import DrugInfo, DrugReference
from .reference import fold
from .tests import PharmacySetup, person

TODAY = timezone.localdate()
SEARCH = reverse("pharmacy:reference_search")
NEW_ITEM = reverse("master_data:item_create")


def ref(source_ref, name_en="", name_ar="", ingredient="", strength="", form="", pack="", price=None, price_date=None, barcode="",
        rx=None, active=True, is_drug=True, manufacturer=""):
    return DrugReference.objects.create(
        source="test-list", source_ref=source_ref, source_version="v1", name_en=name_en, name_ar=name_ar, name_key=fold(name_en or name_ar),
        active_ingredient=ingredient, ingredient_key=services.ingredient_key(ingredient) if ingredient else "", strength=strength,
        strength_key=services.strength_key(strength) if strength else "", form=form, pack=pack, manufacturer=manufacturer,
        public_price=D(price) if price is not None else None, price_date=price_date, barcode=barcode, prescription_only=rx, is_drug=is_drug,
        is_active=active)


def snapshot():
    return list(DrugReference.objects.order_by("pk").values())


class ReferenceSetup(PharmacySetup):
    def setUp(self):
        super().setUp()
        self.fresh = TODAY - timedelta(days=30)
        self.amox = ref("A1", "Zubacillin 500mg caps 20's", "زوباسيلين 500 مجم كبسول", "Amoxicillin", "500 mg", "capsule", "20 caps", "62.50",
                        self.fresh, "2000000000015", rx=True, manufacturer="Nowhere Labs")
        self.amox_susp = ref("A2", "Zubacillin 250mg/5ml susp", "زوباسيلين شراب", "Amoxicillin", "250 mg/5 ml", "suspension", "100 ml", "48.00", self.fresh)
        self.ibu = ref("I1", "Quibufen 400mg tab", "إيبوكوين 400", "Ibuprofen", "400 mg", "tablet", "20 tabs", "29.75", None, "2000000000060")
        self.old = ref("O1", "Quilodol 500mg tab", "كويلودول", "Paracetamol", "500 mg", "tablet", "20 tabs", "35.00", TODAY - timedelta(days=200))
        self.gone = ref("G1", "Quilogone 10mg tab", "", "Loratadine", "10 mg", "tablet", price="10.00", active=False)

    def search(self, q, user=None, **extra):
        self.client.force_login(user or self.owner)
        return self.client.get(SEARCH, {"q": q, **extra})


class SearchEndpointTests(ReferenceSetup):
    def test_gate_anonymous_capability_permission(self):
        anonymous = self.client.get(SEARCH, {"q": "zuba"})
        self.assertEqual(anonymous.status_code, 302)  # every Hesba route sends a signed-out visitor to the login page
        self.assertTrue(anonymous["Location"].startswith(reverse("login")))
        cashier = person(RoleCode.CASHIER, "ph_ref_cashier")  # looks drugs up, may not create items
        refused = self.search("zuba", cashier)
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["ok"], False)
        self.assertNotIn("results", refused.json())
        self.assertEqual(self.search("zuba").status_code, 200)
        caps.set_capability_enabled(self.profile, "drugs", False)
        self.assertEqual(self.search("zuba").status_code, 403)

    def test_json_shape(self):
        data = self.search("zubacillin 500").json()
        self.assertEqual((data["ok"], data["count"], data["limit"], data["too_short"]), (True, 1, 20, False))
        row = data["results"][0]
        self.assertEqual(set(row), {"id", "name_ar", "name_en", "ingredient", "strength", "form", "pack", "manufacturer", "public_price", "price_date",
                                    "price_month", "stale", "barcode", "prescription_only", "is_drug", "source"})
        self.assertEqual(row["id"], self.amox.pk)
        self.assertEqual(row["form"], {"code": "capsule", "label": "كبسول"})
        self.assertEqual((row["public_price"], row["price_date"], row["stale"]), ("62.50", self.fresh.isoformat(), False))
        self.assertEqual((row["prescription_only"], row["is_drug"], row["barcode"], row["pack"]), (True, True, "2000000000015", "20 caps"))
        self.assertIn("test-list", row["source"])
        english = self.search("zubacillin 500", lang="en").json()["results"][0]
        self.assertEqual(english["form"]["label"], "Capsules")

    def test_minimum_two_letters_after_folding(self):
        for q in ("", "z", "  z  ", "ـ", "ً"):
            with self.subTest(q=q):
                data = self.search(q).json()
                self.assertEqual((data["too_short"], data["results"]), (True, []))
        self.assertEqual(self.search("zu").json()["count"], 2)

    def test_at_most_twenty_rows(self):
        for number in range(25):
            ref(f"C{number}", f"Capfab {number:02d}", ingredient="Cetirizine", price="19.00")
        data = self.search("capfab").json()
        self.assertEqual(data["count"], 20)
        self.assertEqual(len(data["results"]), 20)

    def test_barcode_hits_then_name_prefixes_then_the_rest(self):
        inner = ref("Z1", "Aqzubac cream", ingredient="Fusidic acid", price="20.00")  # "zubac" inside the name, sorts first by name
        names = [row["id"] for row in self.search("zubac").json()["results"]]
        self.assertEqual(names, [self.amox_susp.pk, self.amox.pk, inner.pk])
        coded = ref("Z2", "Zzz other", barcode="0712345678901")
        self.assertEqual(self.search("0712345678901").json()["results"][0]["id"], coded.pk)

    def test_arabic_names_ingredient_and_scanned_upc(self):
        self.assertEqual([row["id"] for row in self.search("ايبوكوين").json()["results"]], [self.ibu.pk])  # إ typed as ا
        self.assertEqual({row["id"] for row in self.search("amoxi").json()["results"]}, {self.amox.pk, self.amox_susp.pk})
        upc = ref("U1", "Upcfab", barcode="0012345678905")  # stored as EAN-13, as the importer stores a UPC-A
        self.assertEqual([row["id"] for row in self.search("012345678905").json()["results"]], [upc.pk])
        self.assertEqual([row["id"] for row in self.search("٠١٢٣٤٥٦٧٨٩٠٥").json()["results"]], [upc.pk])

    def test_inactive_rows_are_not_offered(self):
        self.assertEqual(self.search("quilogone").json()["count"], 0)

    def test_stale_flag(self):
        self.assertTrue(self.search("quilodol").json()["results"][0]["stale"])
        self.assertTrue(self.search("quibufen").json()["results"][0]["stale"])  # no date: stale
        self.assertFalse(self.search("zubacillin 500").json()["results"][0]["stale"])
        self.assertFalse(picker.is_stale(picker.six_months_before(TODAY)))
        self.assertTrue(picker.is_stale(picker.six_months_before(TODAY) - timedelta(days=1)))
        self.assertEqual(picker.six_months_before(timezone.datetime(2026, 8, 31).date()), timezone.datetime(2026, 2, 28).date())

    def test_odd_input_never_breaks(self):
        for q in ("zu\x00ba", "%%", "__", "z" * 5000, "'; drop table x; --", "١٢", "+/", "🙂🙂"):
            with self.subTest(q=q[:20]):
                response = self.search(q)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["ok"])
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(SEARCH, {"q": "zuba"}).status_code, 405)

    def test_one_query_whatever_the_number_of_rows(self):
        with self.assertNumQueries(1):
            picker.search_reference("zubac")
        for number in range(20):
            ref(f"N{number}", f"Numfab {number:02d}", ingredient="Omeprazole", price="54.00")
        self.client.force_login(self.owner)
        counts = []
        for q in ("quibufen", "numfab"):
            with CaptureQueriesContext(connection) as queries:
                self.assertEqual(self.client.get(SEARCH, {"q": q}).status_code, 200)
            counts.append(len(queries))
        self.assertEqual(counts[0], counts[1])


class PrefillTests(ReferenceSetup):
    def test_ref_fills_the_new_item_form_but_not_the_prescription_box(self):
        self.client.force_login(self.owner)
        html = self.client.get(NEW_ITEM, {"ref": self.amox.pk}).content.decode()
        self.assertRegex(html, r'name="item_name"[^>]*value="زوباسيلين 500 مجم كبسول"')
        self.assertRegex(html, r'name="barcode"[^>]*value="2000000000015"')
        self.assertRegex(html, r'name="default_sale_price"[^>]*value="62.50"')
        self.assertRegex(html, r'name="drug-active_ingredient"[^>]*value="Amoxicillin"')
        self.assertRegex(html, r'name="drug-strength"[^>]*value="500 mg"')
        self.assertRegex(html, r'<option value="capsule" selected>')
        self.assertRegex(html, r'name="drug-manufacturer"[^>]*value="Nowhere Labs"')
        self.assertRegex(html, rf'name="drug-reference"[^>]*value="{self.amox.pk}"')
        self.assertNotRegex(html, r'name="drug-prescription_only"[^>]*checked')  # prescription_only=True on the list, still manual
        self.assertIn("اتملّا من القايمة المرجعية", html)
        self.assertIn('id="drug-prefill"', html)
        english = self.client.get(NEW_ITEM, {"ref": self.amox.pk, "lang": "en"}).content.decode()
        self.assertRegex(english, r'name="item_name"[^>]*value="Zubacillin 500mg caps 20&#x27;s"')
        self.assertIn("Filled from the reference list", english)

    def test_ref_is_refused_without_the_permission(self):
        cashier = person(RoleCode.CASHIER, "ph_ref_cashier2")
        self.client.force_login(cashier)
        self.assertEqual(self.client.get(NEW_ITEM, {"ref": self.amox.pk}).status_code, 403)

    def test_a_bad_or_retired_ref_opens_the_empty_form(self):
        self.client.force_login(self.owner)
        for raw in ("abc", "-1", "99999999", "9" * 40, str(self.gone.pk), "1.5", ""):
            with self.subTest(ref=raw):
                response = self.client.get(NEW_ITEM, {"ref": raw})
                self.assertEqual(response.status_code, 200)
                self.assertNotRegex(response.content.decode(), r'name="item_name"[^>]*value=')
                self.assertContains(response, "data-drug-part")

    def test_without_the_capability_the_form_is_as_before(self):
        caps.set_capability_enabled(self.profile, "drugs", False)
        self.client.force_login(self.owner)
        html = self.client.get(NEW_ITEM, {"ref": self.amox.pk}).content.decode()
        self.assertNotIn("data-drug-part", html)
        self.assertNotIn("data-drug-picker", html)
        self.assertNotRegex(html, r'name="item_name"[^>]*value=')

    def test_editing_an_item_shows_no_picker(self):
        self.client.force_login(self.owner)
        html = self.client.get(reverse("master_data:edit", args=["items", self.panadol.pk])).content.decode()
        self.assertNotIn("data-drug-part", html)
        self.assertNotIn("data-drug-picker", html)

    def test_without_javascript_the_buttons_stay_hidden(self):
        self.client.force_login(self.owner)
        html = self.client.get(NEW_ITEM).content.decode()
        self.assertRegex(html, r'<button class="hs-drug-open" type="button" data-drug-picker-open data-drug-open-main hidden')
        self.assertRegex(html, r'<small class="hs-help hs-drug-hint" data-drug-hint hidden>')
        self.assertRegex(html, r'class="hs-drug-banner" role="status" data-drug-banner hidden')
        self.assertIn('data-url="/pharmacy/reference/search/"', html)
        self.assertIn("drug_picker.js", html)


class SaveTests(ReferenceSetup):
    def post(self, **extra):
        self.client.force_login(self.owner)
        data = {"lang": "ar", "item_code": "", "item_name": "زوباسيلين معدّل", "unit": "علبة", "barcode": "2000000000015",
                "default_sale_price": "65.00", "min_stock": "", "is_stock_tracked": "on", "active": "on",
                "drug-reference": str(self.amox.pk), "drug-active_ingredient": "Amoxicillin", "drug-strength": "500 mg",
                "drug-form": "capsule", "drug-manufacturer": "Nowhere Labs"}
        data.update(extra)
        return self.client.post(NEW_ITEM + f"?ref={self.amox.pk}", data)

    def test_saving_a_picked_drug_makes_one_item_and_its_drug_data_and_leaves_the_list_alone(self):
        before_items, before_list = Item.objects.count(), snapshot()
        response = self.post(**{"drug-strength": "1 g"})  # the person changed the strength and the price
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Item.objects.count(), before_items + 1)
        item = Item.objects.get(item_name="زوباسيلين معدّل")
        self.assertEqual((item.default_sale_price, item.barcode, item.unit), (D("65.00"), "2000000000015", "علبة"))
        drug = DrugInfo.objects.get(item=item)
        self.assertEqual((drug.active_ingredient, drug.ingredient_key, drug.strength, drug.strength_key, drug.form, drug.manufacturer),
                         ("Amoxicillin", "amoxicillin", "1 g", "1g", "capsule", "Nowhere Labs"))
        self.assertFalse(drug.prescription_only)
        self.assertEqual(drug.updated_by, self.owner)
        self.assertTrue(AuditLog.objects.filter(module="pharmacy", action="create_drug", object_id=str(item.pk)).exists())
        self.assertEqual(snapshot(), before_list)
        self.assertIn(item, [row["item"] for row in services.alternatives(self.generic)])

    def test_the_prescription_box_is_the_persons_choice(self):
        self.post(**{"drug-prescription_only": "on"})
        self.assertTrue(DrugInfo.objects.get(item__item_name="زوباسيلين معدّل").prescription_only)

    def test_drug_data_without_an_ingredient_is_refused_before_anything_is_saved(self):
        before = Item.objects.count()
        response = self.post(**{"drug-active_ingredient": ""})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, services.MESSAGES["ar"]["ingredient"])
        self.assertEqual(Item.objects.count(), before)
        self.assertRegex(response.content.decode(), r'name="item_name"[^>]*value="زوباسيلين معدّل"')

    def test_an_item_without_drug_data_saves_as_before(self):
        response = self.post(**{"drug-reference": "", "drug-active_ingredient": "", "drug-strength": "", "drug-form": "", "drug-manufacturer": ""})
        self.assertEqual(response.status_code, 302)
        item = Item.objects.get(item_name="زوباسيلين معدّل")
        self.assertFalse(DrugInfo.objects.filter(item=item).exists())

    def test_a_refused_item_keeps_nothing(self):
        before, drugs = Item.objects.count(), DrugInfo.objects.count()
        response = self.post(item_name="")
        self.assertEqual(response.status_code, 200)
        self.assertEqual((Item.objects.count(), DrugInfo.objects.count()), (before, drugs))


class DrugPageTests(ReferenceSetup):
    """The drug page of «أموكسيسيلين 500 مجم» (Amoxicillin 500 mg, see PharmacySetup)."""

    def page(self, user=None, lang="ar"):
        self.client.force_login(user or self.owner)
        return self.client.get(reverse("pharmacy:drug", args=[self.generic.pk]), {"lang": lang}).content.decode()

    def test_lists_unstocked_alternatives_same_strength_first(self):
        html = self.page()
        self.assertIn("data-unstocked", html)
        refs = re.findall(r'data-ref="(\d+)" data-match="(\w+)"', html)
        self.assertEqual(refs, [(str(self.amox.pk), "same"), (str(self.amox_susp.pk), "other")])
        self.assertIn("بدائل مش عندك", html)
        self.assertIn("نفس التركيز", html)
        self.assertIn("تركيز تاني", html)
        self.assertIn(f'href="{NEW_ITEM}?lang=ar&amp;ref={self.amox.pk}"', html)
        self.assertIn("62.50", html)
        self.assertIn(self.fresh.strftime("%m/%Y"), html)
        self.assertNotIn(f'data-ref="{self.ibu.pk}"', html)  # another ingredient
        english = self.page(lang="en")
        self.assertIn("Alternatives not in your stock", english)
        self.assertIn("Add as item", english)

    def test_hides_what_the_pharmacy_already_has(self):
        make_item(item_code="AMX-BC", item_name="Some other name", barcode="2000000000015")  # same barcode as self.amox
        ref("A3", "", "أموكسيل 500 مجم", "Amoxicillin", "500 mg", "capsule")  # Arabic name of the stocked brand
        ref("A4", "Amoxil 500 MG", "", "Amoxicillin", "500 mg", "capsule", barcode="")
        make_item(item_code="AMX-EN", item_name="amoxil 500 mg")  # the same name, another case
        rows = picker.unstocked_alternatives(Item.objects.select_related("drug").get(pk=self.generic.pk))
        self.assertEqual([row.pk for row in rows], [self.amox_susp.pk])

    def test_a_person_who_cannot_create_items_sees_the_list_without_the_link(self):
        cashier = person(RoleCode.CASHIER, "ph_ref_cashier3")
        html = self.page(cashier)
        self.assertIn("data-unstocked", html)
        self.assertNotIn("data-add-ref", html)

    def test_hidden_without_a_match(self):
        DrugReference.objects.filter(ingredient_key="amoxicillin").update(is_active=False)
        self.assertNotIn("data-unstocked", self.page())
        self.client.force_login(self.owner)
        no_data = make_item(item_code="NO-DRUG", item_name="Plain thing")
        self.assertNotIn("data-unstocked", self.client.get(reverse("pharmacy:drug", args=[no_data.pk])).content.decode())

    def test_at_most_eight_and_one_query(self):
        for number in range(12):
            ref(f"M{number}", f"Moxfab {number:02d}", ingredient="Amoxicillin", strength="500 mg", form="capsule")
        item = Item.objects.select_related("drug").get(pk=self.generic.pk)
        with self.assertNumQueries(1):
            rows = picker.unstocked_alternatives(item)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(row.same for row in rows))

    def test_the_list_is_never_written_by_the_page(self):
        before = snapshot()
        self.page()
        self.assertEqual(snapshot(), before)


class StylesheetTests(SimpleTestCase):
    def test_tokens_only(self):
        with open(finders.find("hesba/css/drug_picker.css"), encoding="utf-8") as handle:
            css = handle.read()
        with open(finders.find("hesba/css/tokens.css"), encoding="utf-8") as handle:
            defined = set(re.findall(r"(--hs-[\w-]+)\s*:", handle.read()))
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", css), [])
        self.assertNotRegex(css, r"rgba?\(")
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertTrue(used)
        self.assertEqual(sorted(used - defined), [])
        self.assertIn("var(--hs-focus-ring)", css)
