"""ACT-CRAWL: every activity, every sub-activity, every role: no link leads to an error or a closed door.

The launch gate (docs/ACTIVITY_SCORECARD.md) needs this in the code, not in a
one-off script. For each sub-activity the business is set up with the
activity's own modules, and each role signs in: every section its menu shows
(sidebar, phone tab bar and the dashboard's quick actions) must open, never
a server error, a 403 or a 404. The owner also follows the links on those
pages one level down. A second pass fills one sample business per activity
(the demo's "add sample data") so detail pages with real documents are
opened too.
"""

import re

from django.core.management import call_command
from django.test import TestCase, override_settings

from hesba_testing.factories import make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from settings_core import setup_catalog as catalog

from .tests_dashboard import prepared_client

ROLES = (RoleCode.OWNER, RoleCode.MANAGER, RoleCode.ACCOUNTANT, RoleCode.CASHIER, RoleCode.STOCK_KEEPER)
#: Links that are actions, downloads, prints or leaving the app, not screens to open.
#: Only actions and files are skipped; a screen whose path merely mentions one (the backups settings page) is crawled.
SKIP = re.compile(r"(logout|/demo/|export|download|/drive/|\.csv|format=csv|/print|pdf|manifest|service-worker|sw\.js|healthz|/ops/|"
                  r"/admin/|/share/|wa\.me|mailto:|tel:|javascript:|^#|/static/|/media/|/imports/templates/)")
HREF = re.compile(r'<a\b[^>]*\bhref="([^"]+)"')
PER_PAGE = 25


def person(role, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role))
    return user


def menu_links(html):
    """The sections this user's menu offers: sidebar, phone tab bar and dashboard quick actions."""

    found = []
    for block in (r'<aside class="hs-sidebar".*?</aside>', r'<nav class="hs-tabbar".*?</nav>', r'class="dash-actions".*?</(?:section|div)>'):
        match = re.search(block, html, re.S)
        if match:
            found += HREF.findall(match.group(0))
    return _clean(found)


SHELL = re.compile(r'<aside class="hs-sidebar".*?</aside>|<nav class="hs-tabbar".*?</nav>|<header\b.*?</header>', re.S)


def page_links(html):
    """The links on the page itself (the menu around it is walked once, from the dashboard)."""

    main = re.search(r'<main\b.*?</main>', html, re.S)
    return _clean(HREF.findall(main.group(0) if main else SHELL.sub("", html)))[:PER_PAGE]


def _clean(hrefs):
    seen, out = set(), []
    for href in hrefs:
        href = href.replace("&amp;", "&")
        if not href.startswith("/") or SKIP.search(href):
            continue
        path = href.split("#")[0]
        if path not in seen:
            seen.add(path)
            out.append(path)
    return out


class CrawlMixin:
    def open(self, path, lang="ar"):
        joiner = "&" if "?" in path else "?"
        return self.client.get(f"{path}{joiner}lang={lang}" if "lang=" not in path else path)

    def crawl(self, label, deep):
        problems, self.opened = [], 0
        for role in ROLES:
            user = person(role, f"crawl_{role}_{abs(hash(label)) % 10 ** 6}")
            self.client.force_login(user)
            lang = "en" if role == RoleCode.MANAGER else "ar"  # one role walks the English screens
            dashboard = self.open("/dashboard/", lang)
            if dashboard.status_code != 200:
                problems.append(f"{label} {role}: dashboard {dashboard.status_code}")
                continue
            menu = menu_links(dashboard.content.decode())
            if len(menu) < 3:  # the crawler itself must still find the menu: an empty walk proves nothing
                problems.append(f"{label} {role}: only {len(menu)} menu links found")
            for path in menu:
                response = self.open(path, lang)
                self.opened += 1
                if response.status_code >= 400:  # a section the menu shows must open for this role
                    problems.append(f"{label} {role}: menu {path} → {response.status_code}")
                    continue
                if deep and role == RoleCode.OWNER and response.status_code == 200:
                    for link in page_links(response.content.decode()):
                        inner = self.open(link, lang)
                        self.opened += 1
                        if inner.status_code >= 500 or inner.status_code == 404:
                            problems.append(f"{label} {role}: {path} → {link} → {inner.status_code}")
        return problems


def _make_case(activity, subs):
    def test(self):
        self.client.raise_request_exception = False
        problems = []
        for n, sub in enumerate(subs):
            with self.subTest(sub=sub):
                from settings_core.models import ClientProfile

                ClientProfile.objects.all().delete()
                prepared_client(activity, sub, ",".join(catalog.default_modules(activity)))
                problems += self.crawl(f"{activity}/{sub}", deep=(n == 0))
        self.assertEqual(problems, [])

    return test


class EveryActivityEveryRoleTests(CrawlMixin, TestCase):
    """The empty business, right after the setup wizard, for all 51 sub-activities × 5 roles."""


for _activity in catalog.ACTIVITY_LABELS:
    _subs = list(catalog.SUB_ACTIVITY_LABELS.get(_activity, {})) or [None]
    setattr(EveryActivityEveryRoleTests, f"test_{_activity}", _make_case(_activity, _subs))


@override_settings(DEMO_MODE=True, DEMO_PASSWORD="Demo-pass-1", DEBUG=True)
class EverySampleBusinessTests(CrawlMixin, TestCase):
    """One sample business per activity, with its documents, walked by every role (the owner one level deeper)."""

    def fill(self, activity, sub):
        from settings_core.management.commands.prepare_demo import Command as Prepare
        from settings_core.models import ClientProfile
        from settings_core.setup_services import complete_setup

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_setup(ClientProfile.get_active(), activity, sub, ",".join(catalog.default_modules(activity)))
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        prepare = Prepare()
        prepare._history()
        call_command("seed_demo_business", username="owner", force=True, verbosity=0)
        prepare._extras()


def _make_sample_case(activity, sub):
    def test(self):
        self.client.raise_request_exception = False
        self.fill(activity, sub)
        self.assertEqual(self.crawl(f"{activity}/{sub} (sample data)", deep=True), [])

    return test


for _activity in catalog.ACTIVITY_LABELS:
    _first = next(iter(catalog.SUB_ACTIVITY_LABELS.get(_activity, {})), None)
    setattr(EverySampleBusinessTests, f"test_{_activity}", _make_sample_case(_activity, _first))


class CrawlerSelfTests(TestCase):
    """The crawler skips actions and files only, never a screen (Codex on #188)."""

    def test_screens_are_crawled_and_actions_skipped(self):
        for screen in ("/settings/backups/", "/settings/", "/education/", "/reports/"):
            self.assertIsNone(SKIP.search(screen), screen)
        for action in ("/logout/", "/sales/1/print/", "/reports/x/?format=csv", "/ops/nightly/", "/settings/backups/drive/callback/", "https://wa.me/2010"):
            self.assertIsNotNone(SKIP.search(action), action)
