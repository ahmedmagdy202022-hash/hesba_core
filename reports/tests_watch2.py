"""Watch round 2 fixes on shared shell screens: ED-09 (the tab bar's label for a training centre's accounts)."""

import re

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from .tests_dashboard import prepared_client

CENTER = "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians"
TAB = re.compile(r'data-tab="customers"[^>]*>.*?<span class="hs-tab__label">([^<]*)</span>', re.S)


def owner_client(test, activity, sub_activity, modules=CENTER):
    prepared_client(activity, sub_activity, modules)
    user = make_user(username="watch2_owner")
    make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
    test.client.force_login(user)


def tab_label(test, lang):
    html = test.client.get(reverse("dashboard_snapshot"), {"lang": lang}).content.decode()
    return TAB.search(html).group(1)


class TabLabelTests(TestCase):
    def test_a_training_centre_tab_says_trainees_while_the_page_keeps_the_full_name(self):
        owner_client(self, "education", "training")
        self.assertEqual(tab_label(self, "ar"), "المتدربين")
        self.assertEqual(tab_label(self, "en"), "Trainees")
        self.assertContains(self.client.get(reverse("master_data:customers")), "حسابات المتدربين")
        self.assertContains(self.client.get(reverse("master_data:customers"), {"lang": "en"}), "Trainee accounts")

    def test_a_language_centre_is_shortened_the_same_way(self):
        owner_client(self, "education", "languages")
        self.assertEqual((tab_label(self, "ar"), tab_label(self, "en")), ("الدارسين", "Learners"))

    def test_other_activities_keep_their_own_full_words(self):
        owner_client(self, "education", "tutoring_center")
        self.assertEqual(tab_label(self, "ar"), "أولياء الأمور")

    def test_a_shop_still_says_customers(self):
        owner_client(self, "commercial", "retail", "customers,suppliers,items_services,sales_operations,purchases,inventory,cashboxes,reports")
        self.assertEqual((tab_label(self, "ar"), tab_label(self, "en")), ("العملاء", "Customers"))
