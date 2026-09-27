"""VARIANT-001: a model's sizes and colours are ordinary items, created and priced together."""

from decimal import Decimal as D

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from barcode.symbology import is_ean13
from hesba_testing.factories import make_cashbox, make_location, make_seeded_role, make_user, make_user_profile, stock_in
from inventory.services import recalculate_item_average_cost
from master_data.models import Item
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import SalesInvoice
from settings_core.capabilities import capability_enabled, set_capability_enabled
from settings_core.models import ClientProfile

from .models import Variant, VariantGroup
from .services import create_group, extend_group, matrix, parse_values, set_group_prices


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


class VariantSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(sub_activity="fashion")  # sizes & colours suggested -> on
        self.owner = person(RoleCode.OWNER, "variant_owner")

    def tshirt(self, **extra):
        return create_group(code="ts 01", name="Cotton T-shirt", sizes=["S", "M", "L"], colors=["أسود", "White"], sale_price=D("250.00"), purchase_price=D("120.00"), user=self.owner, **extra)


class ServiceTests(VariantSetup):
    def test_parse_values_accepts_arabic_commas_and_drops_repeats(self):
        self.assertEqual(parse_values("S, M ،L\nxl, s, XL"), ["S", "M", "L", "xl"])
        self.assertEqual(parse_values(""), [])

    def test_every_combination_becomes_an_item_with_code_barcode_and_price(self):
        self.assertTrue(capability_enabled("variants"))
        group, created = self.tshirt()
        self.assertEqual((group.code, len(created)), ("TS01", 6))
        codes = sorted(item.item_code for item in created)
        self.assertEqual(codes, ["TS01-L-WHITE", "TS01-L-أسود", "TS01-M-WHITE", "TS01-M-أسود", "TS01-S-WHITE", "TS01-S-أسود"])
        item = Item.objects.get(item_code="TS01-M-أسود")
        self.assertEqual((item.item_name, item.size, item.color, item.default_sale_price, item.default_purchase_price), ("Cotton T-shirt", "M", "أسود", D("250.00"), D("120.00")))
        self.assertTrue(is_ean13(item.barcode))
        self.assertEqual(len({i.barcode for i in created}), 6)
        self.assertEqual(item.variant.group, group)
        self.assertIn("M - أسود", item.search_label)
        self.assertTrue(AuditLog.objects.filter(module="variants", action="create_variant_group", object_id=str(group.pk)).exists())

    def test_codes_never_collide_and_bad_input_is_refused(self):
        Item.objects.create(item_code="TS01-S-WHITE", item_name="Old item")
        group, created = self.tshirt(barcodes=False)
        self.assertIn("TS01-S-WHITE-2", {item.item_code for item in created})
        self.assertTrue(all(item.barcode == "" for item in created))
        with self.assertRaises(ValidationError):
            self.tshirt()  # same model code
        with self.assertRaises(ValidationError):
            create_group(code="X", name="X", sizes=[], colors=[], sale_price=D("1"), purchase_price=D("1"), user=self.owner)
        with self.assertRaises(ValidationError):
            create_group(code="BIG", name="Big", sizes=[str(n) for n in range(30)], colors=[str(n) for n in range(20)], sale_price=D("1"), purchase_price=D("1"), user=self.owner)
        self.assertFalse(VariantGroup.objects.filter(code__in=["X", "BIG"]).exists())

    def test_extend_adds_only_the_missing_combinations(self):
        group, _ = self.tshirt()
        created = extend_group(group, sizes=["XL", "m"], colors=["Navy"], user=self.owner)
        # XL x 3 colours + S/M/L x Navy = 6 new; "m" is already M.
        self.assertEqual(len(created), 6)
        group.refresh_from_db()
        self.assertEqual((group.sizes, group.colors), (["S", "M", "L", "XL"], ["أسود", "White", "Navy"]))
        self.assertEqual(Variant.objects.filter(group=group).count(), 12)
        self.assertEqual(extend_group(group, sizes=["S"], user=self.owner), [])

    def test_one_price_for_the_model_and_the_stock_grid(self):
        group, created = self.tshirt()
        self.assertEqual(set_group_prices(group, sale_price=D("275.00"), user=self.owner), 6)
        self.assertEqual(set(Item.objects.filter(variant__group=group).values_list("default_sale_price", "default_purchase_price")), {(D("275.00"), D("120.00"))})
        location = make_location()
        stock_in(Item.objects.get(item_code="TS01-M-أسود"), location, 5, "120.00")
        stock_in(Item.objects.get(item_code="TS01-L-WHITE"), location, 2, "120.00")
        grid = matrix(group)
        self.assertEqual(grid["colors"], ["أسود", "White"])
        self.assertEqual([[cell["stock"] for cell in row["cells"]] for row in grid["rows"]], [[D("0"), D("0")], [D("5"), D("0")], [D("0"), D("2")]])
        self.assertEqual((grid["color_totals"], grid["total"]), ([D("5"), D("2")], D("7")))


class ScreenTests(VariantSetup):
    def test_owner_creates_a_model_extends_and_prices_it_from_the_screens(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("master_data:items")), reverse("variants:index"))
        response = self.client.post(reverse("variants:index"), {"code": "DRESS", "name": "Summer dress", "sizes": "S, M", "colors": "أحمر، أزرق", "sale_price": "400", "purchase_price": "220", "unit": "قطعة", "barcodes": "1"})
        group = VariantGroup.objects.get(code="DRESS")
        self.assertRedirects(response, f"{reverse('variants:group', args=[group.pk])}?lang=ar", fetch_redirect_response=False)
        page = self.client.get(reverse("variants:group", args=[group.pk]))
        self.assertContains(page, 'data-variant-cell="DRESS-M-أزرق"')
        self.client.post(reverse("variants:group", args=[group.pk]), {"action": "extend", "sizes": "L", "barcodes": "1"})
        self.assertEqual(Variant.objects.filter(group=group).count(), 6)
        self.client.post(reverse("variants:group", args=[group.pk]), {"action": "prices", "sale_price": "380"})
        self.assertEqual(set(Item.objects.filter(variant__group=group).values_list("default_sale_price", flat=True)), {D("380.00")})
        bad = self.client.post(reverse("variants:group", args=[group.pk]), {"action": "prices", "sale_price": "-1"}, follow=True)
        self.assertContains(bad, "السعر لازم رقم مش سالب")
        self.assertContains(self.client.get(reverse("variants:index"), {"lang": "en"}), 'data-variant-group="DRESS"')

    def test_a_variant_sells_at_the_till_by_its_barcode_like_any_item(self):
        group, created = self.tshirt()
        location = make_location(location_code="SHOP", is_default=True)
        cashbox = make_cashbox(cashbox_code="TILL", is_default=True)
        item = Item.objects.get(item_code="TS01-S-WHITE")
        stock_in(item, location, 3, "120.00", movement_date=timezone.localdate())
        recalculate_item_average_cost(item)
        self.client.force_login(person(RoleCode.CASHIER, "variant_cashier"))
        self.assertContains(self.client.get(reverse("sales:pos")), item.barcode)
        self.client.post(reverse("sales:pos"), {"line_count": "1", "customer": "", "location": str(location.pk), "cashbox": str(cashbox.pk), "discount": "0", "tendered": "250", "print": "0",
                                                "item_0": str(item.pk), "qty_0": "1", "price_0": "250.00"})
        invoice = SalesInvoice.objects.get()
        self.assertEqual((invoice.status, invoice.lines.get().item), ("posted", item))
        self.assertEqual(matrix(group)["total"], D("2"))

    def test_permissions_and_capability_gate(self):
        self.client.force_login(person(RoleCode.CASHIER, "variant_viewer"))
        self.assertEqual(self.client.post(reverse("variants:index"), {"code": "X", "name": "X", "sizes": "S"}).status_code, 403)
        self.assertFalse(VariantGroup.objects.exists())
        set_capability_enabled(ClientProfile.get_active(), "variants", False)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("variants:index")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("master_data:items")), reverse("variants:index"))
