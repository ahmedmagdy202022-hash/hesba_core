"""AN6 (Ahmed: "مرتجع البيع فيها مشكلة ومحتاج تعديل"): the return screen says what goes back and how much it is worth."""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile, posted_invoice_ready
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.services import post_sales_invoice


class ReturnScreenTests(TestCase):
    def setUp(self):
        prepared_client()
        owner = make_user(username="ret_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        self.invoice, self.item, _, _ = posted_invoice_ready(paid_now="60.00")
        post_sales_invoice(self.invoice.pk, owner)

    def test_each_line_carries_its_unit_value_and_the_screen_can_return_everything(self):
        html = self.client.get(reverse("sales:return_create", args=[self.invoice.pk]), {"lang": "ar"}).content.decode()
        self.assertIn('data-unit-value="30.0000"', html)
        self.assertIn('data-left="2', html)
        self.assertIn("data-return-all", html)
        self.assertIn("data-return-total", html)
        self.assertIn("المقاس مش مظبوط", html)  # a reason a click away
        self.assertIn("البضاعة بترجع المخزن", html)
        self.assertNotIn("تتحقق الخدمة", html)  # no engine talk

    def test_the_purchase_return_screen_speaks_plainly_too(self):
        # The purchase return shares the lines partial; its own wording lives in its template.
        with open("templates/purchases/return_form.html", encoding="utf-8") as template:
            text = template.read()
        self.assertIn("بضاعة فيها عيب", text)
        self.assertNotIn("تتحقق الخدمة", text)
