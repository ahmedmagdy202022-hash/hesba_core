"""TRACK-ACTS W12: every activity screen the modules page offers opens in the shop that sees the link."""

from django.test import TestCase

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core import setup_catalog as catalog
from settings_core.activity_fit import features
from settings_core.models import ClientProfile
from settings_core.setup_services import complete_setup


class FeatureLinksOpenTests(TestCase):
    def test_no_feature_link_is_a_404_where_it_is_shown(self):
        prepared_client()
        owner = make_user(username="links_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)
        modules = ",".join(catalog.MODULE_SLUGS)
        for activity, subs in catalog.SUB_ACTIVITY_LABELS.items():
            for sub in subs:
                complete_setup(ClientProfile.get_active(), activity, sub, modules)
                for row in features(activity, sub):
                    if not row["url"]:
                        continue
                    with self.subTest(activity=activity, sub=sub, feature=row["slug"]):
                        self.assertNotEqual(self.client.get(row["url"], {"lang": "ar"}).status_code, 404)


class PersonNameTests(TestCase):
    def test_a_user_is_shown_by_the_name_the_shop_gave_them(self):
        from django.template import Context, Template

        owner = make_user(username="owner_w13")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER), display_name="المالك")
        text = Template("{% load hesba_format %}{{ u|person }}").render(Context({"u": owner}))
        self.assertEqual(text, "المالك")
        bare = make_user(username="plain_w13")
        self.assertEqual(Template("{% load hesba_format %}{{ u|person }}").render(Context({"u": bare})), "plain_w13")
        self.assertEqual(Template("{% load hesba_format %}{{ u|person }}").render(Context({"u": None})), "")
