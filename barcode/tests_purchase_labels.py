"""FB2-LABELS: from a posted purchase invoice straight to its items' price labels, one per unit received."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import add_purchase_line, make_item, make_seeded_role, make_user, make_user_profile, purchase_ready
from permissions.models import RoleCode
from purchases.services import post_purchase_invoice
from reports.tests_dashboard import prepared_client


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


class PurchaseLabelsTests(TestCase):
    def setUp(self):
        prepared_client()
        self.owner = person(RoleCode.OWNER, "pl_owner")
        self.client.force_login(self.owner)
        self.invoice, self.item, _, _ = purchase_ready(quantity=4)
        add_purchase_line(self.invoice, self.item, quantity="2.5", unit_purchase_price="25.00")  # same item again: 6.5 in all
        self.other = make_item(item_code="ITEM-002", item_name="كوباية")
        add_purchase_line(self.invoice, self.other, quantity=3, unit_purchase_price="10.00")
        self.labels = f"{reverse('barcode:labels')}?purchase={self.invoice.pk}"

    def test_a_posted_invoice_offers_its_labels_a_draft_does_not(self):
        detail = reverse("purchases:detail", kwargs={"pk": self.invoice.pk})
        self.assertNotIn("data-labels-link", self.client.get(detail).content.decode())  # draft: quantities may still change
        post_purchase_invoice(self.invoice.pk, self.owner)
        page = self.client.get(detail).content.decode()
        self.assertIn("data-labels-link", page)
        self.assertIn(f"purchase={self.invoice.pk}", page)

    def test_the_labels_page_lists_only_its_items_one_label_per_unit(self):
        post_purchase_invoice(self.invoice.pk, self.owner)
        page = self.client.get(self.labels).content.decode()
        self.assertIn("data-from-purchase", page)
        self.assertIn(f'name="copies_{self.item.pk}" value="7"', page)  # 6.5 units → 7 labels
        self.assertIn(f'name="copies_{self.other.pk}" value="3"', page)
        unrelated = make_item(item_code="ITEM-003", item_name="صنف تاني")
        self.assertNotIn(f'name="copies_{unrelated.pk}"', self.client.get(self.labels).content.decode())
        # The plain labels page is unchanged: every item, no count filled in.
        plain = self.client.get(reverse("barcode:labels")).content.decode()
        self.assertIn(f'name="copies_{unrelated.pk}" value="0"', plain)
        self.assertNotIn("data-from-purchase", plain)

    def test_someone_who_cannot_see_purchases_gets_the_plain_page(self):
        from permissions.models import Permission, Role, RolePermission

        post_purchase_invoice(self.invoice.pk, self.owner)
        printer = Role.objects.create(code="label_printer", name_ar="طباعة ملصقات", is_system_role=False, active=True)
        RolePermission.objects.create(role=printer, permission=Permission.objects.get(code="barcode.print_labels"), allow=True)
        user = make_user(username="pl_printer")
        make_user_profile(user=user, role=printer)
        self.client.force_login(user)
        page = self.client.get(self.labels)
        self.assertEqual(page.status_code, 200)  # labels, yes…
        self.assertNotIn("data-from-purchase", page.content.decode())  # …but not what was bought, nor how much
        self.assertNotIn('value="7"', page.content.decode())
