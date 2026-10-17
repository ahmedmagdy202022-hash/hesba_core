"""AN5 (Ahmed's notes on the item form): one item name with many sizes and colours, each its own code and barcode."""

from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from barcode.symbology import is_ean13
from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from master_data.models import Item
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.capabilities import set_capability_enabled
from variants.models import VariantGroup


class ItemFormVariantsTests(TestCase):
    def setUp(self):
        self.profile = prepared_client(sub_activity="fashion")  # sizes & colours on
        owner = make_user(username="an5_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        self.url = reverse("master_data:create", args=("items",))

    def post(self, **data):
        return self.client.post(self.url, {"lang": "ar", "item_name": "تيشيرت قطن", "unit": "قطعة", "default_sale_price": "250", "default_purchase_price": "120",
                                           "min_stock": "2", "is_stock_tracked": "on", "active": "on", **data})

    def test_the_new_item_form_offers_standard_sizes_and_colours(self):
        html = self.client.get(self.url, {"lang": "ar"}).content.decode()
        self.assertIn("data-variant-pick", html)
        self.assertIn('name="has_variants"', html)
        self.assertIn('value="XL"', html)
        self.assertIn('value="أسود"', html)
        self.assertIn('name="sizes_extra"', html)

    def test_ticked_and_typed_values_become_one_item_each_with_its_own_code_and_barcode(self):
        response = self.post(has_variants="on", sizes_std=["M", "L"], sizes_extra="42", colors_std=["أسود"], colors_extra="زيتي، أسود")
        group = VariantGroup.objects.get()
        self.assertRedirects(response, f"{reverse('variants:group', args=[group.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertTrue(group.code.startswith("MD-"))
        self.assertEqual(group.sizes, ["M", "L", "42"])
        self.assertEqual(group.colors, ["أسود", "زيتي"])  # a colour typed twice is made once
        items = Item.objects.filter(variant__group=group)
        self.assertEqual(items.count(), 6)
        self.assertEqual(len({item.item_code for item in items}), 6)
        self.assertTrue(all(is_ean13(item.barcode) for item in items))
        self.assertEqual(len({item.barcode for item in items}), 6)
        self.assertEqual({(item.item_name, item.default_sale_price, item.min_stock) for item in items}, {("تيشيرت قطن", Decimal("250.00"), Decimal("2.000"))})
        self.assertFalse(Item.objects.filter(variant__isnull=True, item_name="تيشيرت قطن").exists())  # no extra "parent" item

    def test_a_code_typed_by_hand_names_the_model(self):
        self.post(item_code="TEE", has_variants="on", sizes_std=["S"], colors_std=["أبيض"])
        self.assertEqual(VariantGroup.objects.get().code, "TEE")
        self.assertTrue(Item.objects.filter(item_code__startswith="TEE-S-").exists())

    def test_ticking_it_without_a_size_or_colour_is_refused(self):
        response = self.post(has_variants="on")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "اختار مقاس أو لون واحد على الأقل.")
        self.assertFalse(Item.objects.filter(item_name="تيشيرت قطن").exists())

    def test_left_unticked_it_is_one_ordinary_item(self):
        self.post(sizes_std=["M"])
        self.assertEqual(Item.objects.filter(item_name="تيشيرت قطن").count(), 1)
        self.assertFalse(VariantGroup.objects.exists())

    def test_it_is_not_offered_without_sizes_and_colours_or_when_editing(self):
        item = Item.objects.create(item_code="IT-1", item_name="قديم")
        self.assertNotIn("data-variant-pick", self.client.get(reverse("master_data:edit", args=("items", item.pk)), {"lang": "ar"}).content.decode())
        set_capability_enabled(self.profile, "variants", False)
        self.assertNotIn("data-variant-pick", self.client.get(self.url, {"lang": "ar"}).content.decode())


class ModelPageTests(TestCase):
    def setUp(self):
        prepared_client(sub_activity="fashion")
        owner = make_user(username="an5_page_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        from variants.services import create_group

        self.group, _ = create_group(code="TEE", name="تيشيرت", sizes=["M"], colors=["أسود"], sale_price=Decimal("250"), purchase_price=Decimal("100"), user=owner)

    def test_the_model_page_offers_only_what_it_does_not_have_yet(self):
        html = self.client.get(reverse("variants:group", args=[self.group.pk]), {"lang": "ar"}).content.decode()
        self.assertIn('class="vr-grid"', html)
        self.assertIn('data-variant-cell="TEE-M-أسود"', html)
        self.assertIn('name="sizes_std" value="XL"', html)
        self.assertNotIn('name="sizes_std" value="M"', html)
        self.assertNotIn('name="colors_std" value="أسود"', html)
        self.assertIn("data-variant-labels", html)

    def test_ticked_sizes_and_typed_colours_extend_the_model(self):
        self.client.post(reverse("variants:group", args=[self.group.pk]), {"lang": "ar", "action": "extend", "sizes_std": ["L"], "colors_extra": "زيتي", "barcodes": "1"})
        self.group.refresh_from_db()
        self.assertEqual((self.group.sizes, self.group.colors), (["M", "L"], ["أسود", "زيتي"]))
        self.assertEqual(Item.objects.filter(variant__group=self.group).count(), 4)


class SingleItemSizeTests(TestCase):
    """AN7 (F32): a single item's size and colour offer the standard lists; "لارج" is kept as "L"."""

    def test_the_spoken_size_is_the_standard_size(self):
        from variants.standard import standard_color, standard_size

        self.assertEqual([standard_size(v) for v in ("لارج", " l ", "اكس  لارج", "ميديم", "42", "مقاس خاص", "")],
                         ["L", "L", "XL", "M", "42", "مقاس خاص", ""])
        self.assertEqual(standard_color("اسود"), "اسود")  # spelled differently: kept as typed
        self.assertEqual(standard_color("black"), "Black")

    def test_the_item_form_suggests_and_keeps_the_standard(self):
        from master_data.forms import ItemForm

        prepared_client(sub_activity="fashion")  # sizes & colours on
        form = ItemForm(data={"item_name": "قميص", "item_code": "SH-1", "unit": "قطعة", "size": "لارج", "color": "أسود",
                              "default_sale_price": "100", "min_stock": ""}, lang="ar")
        html = str(form["size"])
        self.assertIn('list="hs-std-sizes"', html)
        self.assertIn('<option value="XL">', html)
        self.assertIn('list="hs-std-colors"', str(form["color"]))
        form.is_valid()
        self.assertEqual(form.cleaned_data.get("size"), "L")
