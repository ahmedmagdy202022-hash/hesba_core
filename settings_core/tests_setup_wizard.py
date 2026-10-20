"""SETUP-WIZARD (ST5): the wizard's modules and features steps say what each option needs, as the settings screens do.

Ahmed, 20 Oct: after "ابدأ تجربة جديدة" the wizard still showed the old screens; it is where the options are first chosen.
"""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode


class WizardLinksTests(TestCase):
    def setUp(self):
        owner = make_user(username="wizard_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)

    def test_the_modules_step_groups_the_modules_and_names_their_links(self):
        page = self.client.get(reverse("setup_modules"), {"lang": "ar", "activity": "commercial", "sub_activity": "retail"})
        body = page.content.decode()
        for group in ("required", "suggested", "optional"):
            self.assertIn(f'data-module-group="{group}"', body)
        self.assertRegex(body, r'data-module="purchases" data-preset-state="\w+" data-needs="suppliers" data-needed-by=""')
        self.assertRegex(body, r'data-module="suppliers" data-preset-state="\w+" data-needs="" data-needed-by="purchases"')
        self.assertIn("محتاج الموردون", body)
        self.assertIn('href="#hs-i-purchases"', body)  # its icon, from the shared sprite
        self.assertIn("closeOverNeeds();", body)

    def test_the_features_step_groups_the_features_and_says_what_they_need(self):
        page = self.client.get(reverse("setup_review"), {"lang": "ar", "activity": "commercial", "sub_activity": "retail",
                                                        "modules": "customers,items_services,sales_operations,cashboxes,reports"})
        body = page.content.decode()
        self.assertEqual([group["key"] for group in page.context["capability_groups"]], ["selling", "goods", "money"])
        self.assertIn('data-capability="batches_expiry" data-needs="inventory"', body)
        self.assertIn('data-need-module="inventory" data-need-name="المخزون"', body)
        self.assertIn('data-capability="shift_required" data-needs="" data-needs-cap="pos"', body)
        self.assertIn("هتلاقيها في", body)

    def test_a_fashion_shop_is_not_offered_expiry_dates_here_either(self):
        page = self.client.get(reverse("setup_review"), {"lang": "ar", "activity": "commercial", "sub_activity": "fashion", "modules": ""})
        self.assertNotIn('data-capability="batches_expiry"', page.content.decode())
