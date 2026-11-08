"""S10 UI: the week calendar screen (page, permissions, read-only flag, assets, link from the agenda)."""

import re
from datetime import timedelta

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase
from django.urls import reverse

from permissions.models import RoleCode
from permissions.services import user_has_permission

from .tests import TODAY, AppointmentSetup, person
from .views import BOOK, VIEW
from .views_week import WEEK_STARTS_ON, week_start_of


class WeekPageTests(AppointmentSetup):
    def test_the_page_renders_for_a_permitted_user_with_the_move_controls(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("appointments:week"))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "appointments/week.html")
        self.assertTrue(response.context["can_move"])
        self.assertContains(response, "data-week-calendar")
        self.assertContains(response, 'data-can-move="1"')
        self.assertContains(response, f'data-week-start="{week_start_of(TODAY).isoformat()}"')
        self.assertContains(response, f'data-day="{TODAY.isoformat()}"')
        self.assertContains(response, reverse("appointments:week_json"))
        self.assertContains(response, reverse("appointments:move", args=[0]))
        self.assertContains(response, "csrfmiddlewaretoken")  # the script posts the move with it
        self.assertContains(response, "اسحب الميعاد")  # Arabic help text, the default language

    def test_english_and_the_chosen_day(self):
        self.client.force_login(self.owner)
        response = self.client.get(reverse("appointments:week"), {"lang": "en", "day": "2026-11-08"})
        self.assertContains(response, "Drag an appointment")
        self.assertContains(response, 'data-day="2026-11-08"')
        self.assertContains(response, 'data-week-start="2026-11-07"')  # Saturday: the week starts on Saturday
        self.assertContains(response, 'dir="ltr"')
        self.assertContains(self.client.get(reverse("appointments:week"), {"day": "garbage"}), f'data-day="{TODAY.isoformat()}"')

    def test_the_active_employees_are_offered_as_move_targets(self):
        self.client.force_login(self.owner)
        staff = self.client.get(reverse("appointments:week")).context["staff"]
        self.assertEqual([row["name"] for row in staff], ["أحمد الفني"])
        self.assertContains(self.client.get(reverse("appointments:week")), 'id="cal-staff"')

    def test_the_week_starts_on_saturday(self):
        for offset in range(14):
            day = TODAY + timedelta(days=offset)
            start = week_start_of(day)
            self.assertEqual(start.weekday(), WEEK_STARTS_ON)
            self.assertTrue(0 <= (day - start).days <= 6)

    def test_a_user_without_the_view_permission_cannot_open_it(self):
        keeper = person(RoleCode.STOCK_KEEPER, "week_keeper_ui")
        self.assertFalse(user_has_permission(keeper, VIEW))
        self.client.force_login(keeper)
        self.assertIn(self.client.get(reverse("appointments:week")).status_code, (302, 403))
        self.client.logout()
        response = self.client.get(reverse("appointments:week"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response["Location"])

    def test_a_viewer_without_the_move_permission_gets_the_calendar_read_only(self):
        accountant = person(RoleCode.ACCOUNTANT, "week_accountant_ui")
        self.assertTrue(user_has_permission(accountant, VIEW) and not user_has_permission(accountant, BOOK))  # the very check move/ makes
        self.client.force_login(accountant)
        response = self.client.get(reverse("appointments:week"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["can_move"])
        self.assertContains(response, 'data-can-move="0"')
        self.assertContains(response, "العرض للقراءة بس")
        self.assertNotContains(response, "اسحب الميعاد")
        self.assertNotContains(response, "data-week-new")  # no "new appointment" button without the booking permission

    def test_the_module_gate_applies(self):
        from settings_core.setup_services import set_module_enabled

        self.client.force_login(self.owner)
        set_module_enabled(self.profile, "appointments_visits", False, self.owner)
        self.assertNotEqual(self.client.get(reverse("appointments:week")).status_code, 200)

    def test_assets_are_referenced_and_exist(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("appointments:week")).content.decode()
        for path in ("hesba/css/calendar.css", "hesba/js/week_calendar.js"):
            with self.subTest(path=path):
                self.assertIn(path, page)
                self.assertTrue(finders.find(path), f"{path} is not collectable")

    def test_the_agenda_links_to_the_week_for_the_same_day(self):
        self.client.force_login(self.owner)
        agenda = self.client.get(reverse("appointments:agenda"), {"day": "2026-11-08", "lang": "en"})
        self.assertContains(agenda, "data-agenda-week-link")
        self.assertContains(agenda, f'{reverse("appointments:week")}?lang=en&amp;day=2026-11-08'.replace("&amp;", "&"))
        self.assertContains(self.client.get(reverse("appointments:agenda")), "الأسبوع")

    def test_the_week_page_links_back_to_the_agenda(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("appointments:week"), {"day": "2026-11-08"})
        self.assertContains(page, "data-week-agenda-link")
        self.assertContains(page, f'{reverse("appointments:agenda")}?lang=ar&day=2026-11-08')


class CalendarAssetsTests(SimpleTestCase):
    def read(self, path):
        with open(finders.find(path), encoding="utf-8") as handle:
            return handle.read()

    def test_the_stylesheet_uses_tokens_only(self):
        css = self.read("hesba/css/calendar.css")
        literals = [c for c in re.findall(r"#[0-9a-fA-F]{3,8}\b", css) if c.lower() not in {"#fff", "#ffffff"}]
        self.assertEqual(literals, [])
        self.assertNotRegex(css, r"rgba?\(")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", self.read("hesba/css/tokens.css")))
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertTrue(used)
        self.assertEqual(sorted(used - defined), [])
        self.assertNotRegex(css, r"font-weight:\s*[89]00")

    def test_the_script_posts_the_move_with_the_csrf_token_and_handles_refusals(self):
        js = self.read("hesba/js/week_calendar.js")
        for needed in ("X-CSRFToken", "pointerdown", "pointermove", "pointerup", "setPointerCapture", "status === 403", "result.message", "week_of", "data-can-move"):
            with self.subTest(needed=needed):
                self.assertIn(needed, js)
        self.assertNotIn("innerHTML", js)

    def test_the_hours_are_recomputed_after_a_move_and_the_links_follow_the_day(self):
        js = self.read("hesba/js/week_calendar.js")
        self.assertIn("function computeBounds()", js)
        self.assertRegex(js, r"applyMoved\(a, result\.body\);\s*computeBounds\(\);")  # a move to 07:00 must widen the grid before the re-render
        self.assertEqual(js.count("computeBounds();"), 2)  # load() and afterMove()
        start = js[js.index("function dragStart("):js.index("function dragActivate()")]
        self.assertNotIn("setPointerCapture", start)  # capturing at pointerdown would send a plain click on the grip elsewhere
        header = js[js.index("function renderHeader()"):js.index("function renderStrip()")]
        for needed in ("el.agendaLink", "el.newLink", "searchParams.set('day', state.day)"):
            with self.subTest(needed=needed):
                self.assertIn(needed, header)  # names come from the database: text nodes only
