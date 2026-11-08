"""S2 (page): the designed customer page behind the signed link: states, forms, languages, nothing private."""

import re

from django.contrib.staticfiles import finders
from django.core import signing
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from hesba_testing.factories import make_customer
from printing.share import make_token

from . import services
from .models import Status, WorkOrder
from .tests import WorkshopSetup
from .tests_customer_link import SECRET_NOTE

HTML = {"HTTP_ACCEPT": "text/html,application/xhtml+xml"}
HIDDEN = (SECRET_NOTE, "1350", "average_cost", "cost", "أحمد الفني", "commission", "OTHER-DEVICE-XYZ", "عميل تاني خالص", "محمد علي", "01001234567", "wo_owner")
STAFF = ("/staff/", "/workorders/", "sidebar", "logout", "href=\"/login")


class CustomerPageTests(WorkshopSetup):
    def setUp(self):
        super().setUp()
        self.order_ = self.order(notes=SECRET_NOTE)
        services.add_line(self.order_, self.part, self.owner, quantity="1")
        services.add_line(self.order_, self.labour, self.owner)
        for status in (Status.DIAGNOSED, Status.QUOTED):
            services.move(self.order_, status, self.owner)
        self.order_.refresh_from_db()
        self.public = Client()
        self.other = self.order(customer=make_customer(customer_code="CUS-OTHER", name="عميل تاني خالص", phone="01110000000"), device="OTHER-DEVICE-XYZ")

    def token(self):
        return make_token("work_order", self.order_.pk)

    def get(self, lang="ar", client=None):
        return (client or self.public).get(reverse("share_document", args=[self.token()]), {"lang": lang}, **HTML)

    def post(self, action, lang="ar", client=None, **extra):
        data = {"lang": lang, "fingerprint": services.quote_fingerprint(self.order_), **extra}
        return (client or self.public).post(reverse(f"share_work_order_{action}", args=[self.token()]), data, **HTML)

    def assert_private(self, response, lang, varies=True):
        body = response.content.decode()
        for hidden in HIDDEN:
            with self.subTest(hidden=hidden, lang=lang):
                self.assertNotIn(hidden, body)
        for staff in STAFF:
            with self.subTest(staff=staff, lang=lang):
                self.assertNotIn(staff, body)
        self.assertEqual(response["X-Robots-Tag"], "noindex, nofollow")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        if varies:  # the order page answers JSON or HTML by Accept; the expired page is always HTML
            self.assertIn("Accept", response["Vary"])

    # -- quoted --

    def test_quoted_page_shows_status_timeline_quote_total_and_the_two_answers(self):
        response = self.get()
        self.assertEqual((response.status_code, response["Content-Type"].split(";")[0]), (200, "text/html"))
        body = response.content.decode()
        self.assertIn('lang="ar" dir="rtl"', body)
        for shown in (self.order_.number, "غسالة توشيبا", "مستني موافقة العميل", "موتور غسالة", "مصنعية إصلاح", "1,800.00", "300.00", "2,100.00", "موافق على العرض", "مش موافق"):
            with self.subTest(shown=shown):
                self.assertContains(response, shown)
        self.assertContains(response, 'data-status="quoted"')
        self.assertEqual(body.count("<li class=\"cl-step"), 3)  # received, diagnosed, quoted: only what happened
        self.assertContains(response, "Demo Store")
        self.assert_private(response, "ar")

    def test_quoted_page_in_english_is_ltr_and_has_the_switch_back_to_arabic(self):
        response = self.get("en")
        body = response.content.decode()
        self.assertIn('lang="en" dir="ltr"', body)
        for shown in ("Waiting for approval", "Repair quote", "Approve the quote", "Decline", "Total", "2,100.00"):
            with self.subTest(shown=shown):
                self.assertContains(response, shown)
        self.assertNotIn("عرض الإصلاح", body)
        self.assertRegex(body, r'href="[^"]*lang=ar"[^>]*data-lang-switch')
        self.assertRegex(self.get("ar").content.decode(), r'href="[^"]*lang=en"[^>]*data-lang-switch')
        self.assert_private(response, "en")

    def test_forms_carry_csrf_the_current_fingerprint_and_the_language(self):
        for lang in ("ar", "en"):
            body = self.get(lang).content.decode()
            fingerprint = services.quote_fingerprint(self.order_)
            for action in ("approve", "refuse"):
                with self.subTest(lang=lang, action=action):
                    url = reverse(f"share_work_order_{action}", args=[self.token()])
                    form = re.search(rf'<form[^>]*method="post"[^>]*action="{re.escape(url)}">(.*?)</form>', body, re.S)
                    self.assertIsNotNone(form)
                    self.assertIn('name="csrfmiddlewaretoken"', form.group(1))
                    self.assertIn(f'name="fingerprint" value="{fingerprint}"', form.group(1))
                    self.assertIn(f'name="lang" value="{lang}"', form.group(1))
            self.assertEqual(body.count("<form"), 2)

    def test_both_answers_pass_through_a_confirm_step_and_there_is_no_script(self):
        body = self.get().content.decode()
        for action in ("approve", "refuse"):
            self.assertIn(f'href="#confirm-{action}"', body)  # the buttons open the confirm step, they do not post
            self.assertIn(f'id="confirm-{action}"', body)
        self.assertNotIn("<script", body)
        self.assertNotRegex(body, r"\son(click|submit|load)=")

    def test_a_browser_post_goes_through_csrf_then_shows_the_approved_page(self):
        strict = Client(enforce_csrf_checks=True)
        self.assertEqual(self.post("approve", client=strict).status_code, 403)  # no token, no decision
        self.assertEqual(WorkOrder.objects.get(pk=self.order_.pk).status, Status.QUOTED)
        opened = self.get(client=strict)
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', opened.content.decode()).group(1)
        done = self.post("approve", client=strict, csrfmiddlewaretoken=token)
        self.assertEqual(done.status_code, 303)
        self.assertEqual(done["Location"], f"{reverse('share_document', args=[self.token()])}?lang=ar")
        self.assertEqual(WorkOrder.objects.get(pk=self.order_.pk).status, Status.APPROVED)
        after = strict.get(done["Location"], **HTML)
        self.assertContains(after, 'data-banner="approved"')
        self.assertContains(after, "وصلت موافقتك يوم")
        self.assertNotContains(after, "data-decision")  # nothing left to answer
        self.assertNotContains(after, "<form")

    # -- approved / refused --

    def test_approved_page_shows_the_outcome_and_no_buttons(self):
        self.post("approve")
        for lang, banner in (("ar", "وصلت موافقتك يوم"), ("en", "Your approval reached us on")):
            response = self.get(lang)
            with self.subTest(lang=lang):
                self.assertContains(response, 'data-status="approved"')
                self.assertContains(response, banner)
                self.assertContains(response, "2,100.00")  # the approved quote stays readable
                self.assertNotContains(response, "<form")
                self.assertNotContains(response, "data-decision")
                self.assertEqual(response.content.decode().count("<li class=\"cl-step"), 4)
                self.assert_private(response, lang)

    def test_refused_page_shows_the_outcome_closed_quote_and_no_lines(self):
        self.post("refuse", lang="en")
        for lang, banner, closed in (("en", "You declined the quote on", "The quote is closed"), ("ar", "اتسجّل إنك رفضت العرض يوم", "العرض اتقفل")):
            response = self.get(lang)
            with self.subTest(lang=lang):
                self.assertContains(response, 'data-status="refused"')
                self.assertContains(response, banner)
                self.assertContains(response, closed)
                self.assertContains(response, "data-quote-closed")
                self.assertNotContains(response, "data-quote-line")
                self.assertNotContains(response, "موتور غسالة")
                self.assertNotContains(response, "<form")
                self.assertContains(response, "is-branch")
                self.assert_private(response, lang)

    def test_the_quote_is_not_shown_before_it_is_quoted(self):
        diagnosed = self.order()
        services.add_line(diagnosed, self.part, self.owner, quantity="1")
        services.move(diagnosed, Status.DIAGNOSED, self.owner)
        response = self.public.get(reverse("share_document", args=[make_token("work_order", diagnosed.pk)]), **HTML)
        self.assertContains(response, 'data-status="diagnosed"')
        self.assertNotContains(response, "data-quote")
        self.assertNotContains(response, "<form")

    def test_a_quoted_order_with_no_lines_still_offers_both_answers(self):
        empty = self.order()
        for status in (Status.DIAGNOSED, Status.QUOTED):
            services.move(empty, status, self.owner)
        response = self.public.get(reverse("share_document", args=[make_token("work_order", empty.pk)]), **HTML)
        self.assertContains(response, 'data-status="quoted"')
        self.assertContains(response, "data-decision")
        self.assertContains(response, 'id="confirm-approve"')
        self.assertContains(response, 'id="confirm-refuse"')
        self.assertNotContains(response, "data-quote-line")

    # -- the answer can be stale --

    def test_a_changed_quote_re_shows_the_page_with_the_message_and_the_new_fingerprint(self):
        stale = services.quote_fingerprint(self.order_)
        services.add_line(self.order_, self.labour, self.owner)
        response = self.public.post(reverse("share_work_order_approve", args=[self.token()]), {"lang": "en", "fingerprint": stale}, **HTML)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "data-link-message", status_code=409)
        self.assertContains(response, services.quote_fingerprint(self.order_), status_code=409)
        self.assertEqual(WorkOrder.objects.get(pk=self.order_.pk).status, Status.QUOTED)
        self.assertRegex(response.content.decode(), r'href="[^"]*/share/[^"]+/\?lang=ar"[^>]*data-lang-switch')  # the switch goes to the page, not the answer

    def test_json_callers_still_get_json(self):
        self.assertEqual(self.public.get(reverse("share_document", args=[self.token()])).json()["status"], "quoted")

    # -- expired --

    def test_expired_page_is_standalone_in_both_languages_and_knows_no_order(self):
        bad = signing.dumps({"k": "work_order", "id": self.order_.pk}, salt="other-salt")
        for lang, words in (("ar", "الرابط ده مبقاش شغال"), ("en", "This link is no longer valid")):
            response = self.public.get(reverse("share_document", args=[bad]), {"lang": lang}, **HTML)
            with self.subTest(lang=lang):
                self.assertContains(response, "data-share-expired", status_code=404)
                self.assertContains(response, words, status_code=404)
                self.assertContains(response, f'dir="{"ltr" if lang == "en" else "rtl"}"', status_code=404)
                self.assertNotContains(response, self.order_.number, status_code=404)
                self.assert_private(response, lang, varies=False)

    def test_contact_buttons_appear_only_when_the_shop_has_a_phone(self):
        from settings_core.models import SystemSetting

        self.assertNotContains(self.get(), "tel:")
        SystemSetting.objects.update_or_create(key="company.phone", defaults={"value": "01012345678", "active": True})
        response = self.get("en")
        self.assertContains(response, 'href="tel:01012345678"')
        self.assertContains(response, "https://wa.me/201012345678")


class CustomerStylesheetTests(SimpleTestCase):
    def css(self):
        with open(finders.find("hesba/css/customer_link.css"), encoding="utf-8") as handle:
            return handle.read()

    def test_the_page_stylesheet_uses_only_defined_tokens_and_no_colour_literals(self):
        with open(finders.find("hesba/css/tokens.css"), encoding="utf-8") as handle:
            defined = set(re.findall(r"(--hs-[\w-]+)\s*:", handle.read()))
        css = self.css()
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertTrue(used)
        self.assertEqual(sorted(used - defined), [])
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(", css), [])
        self.assertIn("var(--hs-font-body)", css)
        self.assertIn("var(--hs-focus-ring)", css)
