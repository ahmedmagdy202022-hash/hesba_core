"""SETUP-INTERVIEW (ST6): the wizard asks each activity about its work in its own words.

Ahmed, 20 Oct: «اتتبع مسار كل زرار من البداية خالص ورتّب اللي بيظهر بعده حسب كل نشاط … مش شرط كل
الأنشطة يظهرلها نفس الكلام بنفس الصياغة».
"""

from django.test import TestCase
from django.urls import reverse

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode

from . import activity_fit
from . import capabilities as caps
from . import setup_catalog as catalog
from . import setup_interview as interview
from .models import ClientProfile
from .setup_services import enabled_modules, with_needs


def ask(activity, sub_activity):
    return {row["key"]: row for row in interview.questions(activity, sub_activity, "ar")}


class InterviewTests(TestCase):
    def test_accepting_every_suggestion_sets_each_business_up_as_before(self):
        for activity, subs in catalog.SUB_ACTIVITY_LABELS.items():
            for sub in subs:
                with self.subTest(activity=activity, sub=sub):
                    modules, features = interview.resolve(activity, sub, interview.suggested_answers(activity, sub))
                    near, near_sub = catalog.closest(activity, sub)
                    old_features = {slug for slug in caps.CAPABILITY_SLUGS if caps.is_available(slug) and caps.fits(near, slug, near_sub)
                                    and caps.preset_state(near, near_sub, slug) == caps.SUGGESTED}
                    self.assertEqual(set(features), old_features)
                    needed = {module for slug in features for module in caps.needs(slug)}
                    self.assertEqual(set(with_needs(modules)), set(with_needs(list(activity_fit.default_modules(activity, sub)) + list(needed))))

    def test_each_trade_is_asked_in_its_own_words(self):
        self.assertIn("أدوية", ask("medical", "clinic")["stock"]["ask"])
        self.assertIn("طبق", ask("restaurants", "restaurant")["stock"]["ask"])
        self.assertIn("رصيد كل صنف", ask("commercial", "retail")["stock"]["ask"])
        self.assertIn("مقاولين باطن", ask("contracting", "general")["suppliers"]["ask"])
        self.assertIn("أولياء الأمور", ask("education", "tutoring_center")["instal"]["ask"])

    def test_only_what_is_left_to_decide_is_asked(self):
        self.assertNotIn("expiry", ask("commercial", "fashion"))       # clothes do not expire
        self.assertNotIn("credit", ask("services", "maintenance"))     # its customers are always on
        self.assertNotIn("making", ask("manufacturing", "food"))       # a factory always makes things
        self.assertNotIn("suppliers", ask("manufacturing", "food"))    # its purchases always bring suppliers
        self.assertIn("making", ask("restaurants", "bakery"))          # a bakery may bake by recipe

    def test_a_follow_up_hangs_on_its_answer(self):
        grocery = list(ask("commercial", "grocery"))
        self.assertEqual(ask("commercial", "grocery")["expiry"]["parent"], "stock")
        self.assertEqual(grocery.index("expiry"), grocery.index("stock") + 1)
        _, features = interview.resolve("commercial", "grocery", ["expiry"])  # expiry without stock counts for nothing
        self.assertNotIn("batches_expiry", features)
        modules, features = interview.resolve("commercial", "grocery", ["stock", "expiry"])
        self.assertIn("inventory", modules)
        self.assertIn("batches_expiry", features)

    def test_what_each_answer_gives_uses_the_activitys_names(self):
        self.assertIn("الأطباء والطاقم", ask("medical", "clinic")["team"]["gives"])
        self.assertIn("العملاء والتجار", ask("commercial", "wholesale")["credit"]["gives"])


class QuestionsPageTests(TestCase):
    def setUp(self):
        ClientProfile.objects.create(client_code="DEMO", legal_name="Demo", display_name="Demo")
        owner = make_user(username="interview_owner")
        make_user_profile(user=owner, role=make_seeded_role(RoleCode.OWNER))
        self.client.force_login(owner)

    def test_the_sub_activity_step_leads_to_the_questions(self):
        self.assertContains(self.client.get("/setup/activity/commercial/?lang=ar"), "'/setup/questions/'")

    def test_the_page_asks_in_the_frame_with_the_suggestions_ticked(self):
        page = self.client.get(reverse("setup_questions"), {"lang": "ar", "activity": "commercial", "sub_activity": "grocery"})
        body = page.content.decode()
        self.assertIn('class="setup-hero"', body)
        self.assertIn('data-q="expiry" data-parent="stock" data-default="yes"', body)
        self.assertIn('name="q_expiry" value="yes" checked', body)
        self.assertIn("هيظهرلك", body)
        self.assertIn("بضاعتك ليها تاريخ صلاحية؟", body)
        english = self.client.get(reverse("setup_questions"), {"lang": "en", "activity": "commercial", "sub_activity": "grocery"})
        self.assertContains(english, "Do your goods have expiry dates?")

    def test_the_answers_set_the_business_up(self):
        answers = {"lang": "ar", "activity": "commercial", "sub_activity": "grocery", "q_till": "yes", "q_shift": "no",
                   "q_stock": "yes", "q_expiry": "yes", "q_suppliers": "yes", "q_credit": "no", "q_vat": "no", "q_einv": "yes"}
        page = self.client.post(reverse("setup_questions"), answers)
        self.assertRedirects(page, reverse("setup_complete") + "?lang=ar", fetch_redirect_response=False)
        self.assertTrue(ClientProfile.get_active().setup_is_complete)
        modules = enabled_modules()
        self.assertIn("inventory", modules)
        self.assertIn("suppliers", modules)
        self.assertNotIn("customers", modules)
        self.assertTrue(caps.capability_enabled("batches_expiry"))
        self.assertTrue(caps.capability_enabled("pos"))
        self.assertFalse(caps.capability_enabled("shift_required"))
        self.assertFalse(caps.capability_enabled("e_invoice"))  # its parent (VAT) is no

    def test_only_screens_that_are_always_on_are_promised_as_always_there(self):
        # Codex on #258: teacher pay sits under the team module and attendance under bookings, both answered per school.
        page = self.client.get(reverse("setup_questions"), {"lang": "en", "activity": "education", "sub_activity": "tutoring_center"})
        always, rows = page.context["always"], {q["key"]: q for q in page.context["questions"]}
        self.assertIn("Students & groups", always)
        self.assertNotIn("Teacher pay", always)
        self.assertNotIn("Attendance", always)
        self.assertIn("Teacher pay", rows["team"]["labels"])
        self.assertIn("Attendance", rows["booking"]["labels"])

    def test_a_made_up_activity_goes_back_to_the_start(self):
        page = self.client.get(reverse("setup_questions"), {"activity": "spaceships", "sub_activity": "x"})
        self.assertRedirects(page, reverse("setup_activity") + "?lang=ar", fetch_redirect_response=False)
