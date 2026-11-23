"""M6 (HG-085): a lab patient opens their results of one day from a link, behind a second factor.

A public page with health data: these tests pin that nothing is shown before the factor, that every failure looks
the same, that the factor is throttled and never reveals what is on file, and that only lab staff who may write
records can make a link.
"""

import html
import re
import threading
import time
from datetime import date, timedelta
from unittest import skipUnless
from unittest.mock import patch
from urllib.parse import unquote

from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import grant, make_customer, make_role, make_user, make_user_profile
from permissions.models import Permission, RoleCode
from reports.tests_dashboard import prepared_client

from . import lab, public, services
from .models import LabLinkCounter, LabResult, PatientFile
from .tests import CLINIC, person

BASE = "https://lab.example.test"
BOTH_BORN = date(1985, 3, 4)  # the birth date of the patient with a phone and a date
DOB_BORN = date(1990, 7, 1)  # the birth date of the patient with a date only
WRONG_DATE = date(2001, 2, 3)  # a real date that is nobody's in these tests
# What is on the files and must never be on a page before the factor, in any shape: the dates (ISO, day first, with and without zeros, in
# Arabic-Indic digits), the birth years, the phones and their last four digits.
SECRETS_OF_THE_FILES = ("1985-03-04", "04/03/1985", "4/3/1985", "04-03-1985", "1985", "١٩٨٥", "١٩٩٠", "1990-07-01", "01/07/1990", "1/7/1990",
                        "01001234567", "1001234567", "4567", "01112223334", "3334", "01229998888", "8888", "٤٥٦٧", "٣٣٣٤")
PRIVATE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "same-origin"}


def token_of(url):
    return unquote(url.split("/results/", 1)[1].split("/", 1)[0])


def normalized(response, *tokens):
    """The body with what differs between two links by construction (the token, the CSRF value) taken out."""

    body = response.content.decode()
    for token in tokens:
        body = body.replace(token, "TOKEN")
    return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', 'name="csrfmiddlewaretoken" value="X"', body)


def _without_random_text(page):
    """The page without its signed link address (wherever it is written) and without the CSRF token's value."""

    page = re.sub(r"/results/[^/\s\"'<>?]+/", "/results/-/", page)
    return re.sub(r'(name="csrfmiddlewaretoken" value=")[^"]*', r"\1", page)


@override_settings(PUBLIC_BASE_URL=BASE, MEDICAL_LINK_DAYS=7)
class LabLinkTests(TestCase):
    def setUp(self):
        cache.clear()
        prepared_client("medical", "lab", CLINIC)
        self.owner = person(RoleCode.OWNER, "ll_owner")
        self.hb = lab.save_test({"name": "هيموجلوبين", "unit": "g/dL", "low": "12", "high": "16"}, self.owner)
        self.sugar = lab.save_test({"name": "سكر صائم", "unit": "mg/dL", "low": "70", "high": "100"}, self.owner)
        self.group = lab.save_test({"name": "فصيلة الدم"}, self.owner)
        self.day = timezone.localdate() - timedelta(days=1)
        self.other_day = timezone.localdate() - timedelta(days=20)
        # one patient per second-factor shape, with the same results (two of the phone-only kind, to compare them)
        self.both = self._patient("LL-1", "منى عبد الله", "01001234567", date(1985, 3, 4))
        self.dob_only = self._patient("LL-2", "سامي فريد", "", date(1990, 7, 1))
        self.phone_only = self._patient("LL-3", "هالة نبيل", "01112223334", None)
        self.phone_only2 = self._patient("LL-5", "عادل سمير", "01229998888", None)
        self.nothing = self._patient("LL-4", "كريم بلا بيانات", "", None)
        for patient in (self.both, self.dob_only, self.phone_only, self.phone_only2, self.nothing):
            lab.record_results(patient, self.day, {self.hb: ("17.25", ""), self.sugar: ("88.4", ""), self.group: ("", "A+")}, self.owner)
        lab.record_results(self.both, self.other_day, {self.sugar: ("987.654", "")}, self.owner)  # another day: never on this link
        PatientFile.objects.filter(customer=self.both).update(allergies="بنسلين سري", notes="ملاحظة داخلية سرية")
        self.client.force_login(self.owner)

    def _patient(self, code, name, phone, born):
        patient = make_customer(customer_code=code, name=name, phone=phone)
        services.file_for(patient)
        if born:
            PatientFile.objects.filter(customer=patient).update(date_of_birth=born)
        return patient

    def _link(self, patient, day=None, lang="ar"):
        return public.make_link(patient, day or self.day, self.owner, lang)

    def _token(self, patient, day=None):
        return token_of(self._link(patient, day)["url"])

    def _url(self, token, lang="ar"):
        return f"{reverse('lab_result_link', args=[token])}?lang={lang}"

    def _anon(self, **extra):
        return Client(**extra)

    def _pass(self, client, token, answer, lang="ar"):
        """A date is typed in the three boxes (day, month, year); any other answer is the phone's last four digits."""

        if isinstance(answer, date):
            return client.post(self._url(token, lang), {"day": str(answer.day), "month": str(answer.month), "year": str(answer.year), "lang": lang})
        return client.post(self._url(token, lang), {"answer": answer, "lang": lang})

    def _life(self, token):
        """The wrong answers counted in the database for this link (0 when no row was made yet)."""

        return LabLinkCounter.objects.filter(link_hash=public.link_hash(token)).values_list("wrong_answers", flat=True).first() or 0

    def _wrong(self, patient):
        """A well-formed answer that is wrong for this patient's kind of factor."""

        return WRONG_DATE if public.factor(patient)[0] == public.DATE else "0000"

    def assertPrivate(self, response):
        for header, value in PRIVATE.items():
            self.assertEqual(response[header], value, header)

    def assertDead(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "data-lab-link-dead", status_code=404)
        self.assertNotContains(response, "data-lab-ask", status_code=404)
        self.assertPrivate(response)

    def assertNothingOfThePatient(self, response, patient):
        """Nothing of the patient is on the page. The link's own address and the form's token are random text and are taken out
        first: a short marker such as «P-0» can turn up inside them by chance (it did, once, on the PostgreSQL job of #399).
        The birth date is never on the page in any format, and the phone never in any shape; the form's boxes carry no value."""

        text = _without_random_text(response.content.decode())
        for secret in (patient.name, "هيموجلوبين", "سكر صائم", "فصيلة الدم", "17.25", "88.4", "A+", self.day.isoformat(),
                       self.other_day.isoformat(), "987.654", "P-0", "بنسلين", "داخلية", *SECRETS_OF_THE_FILES):
            self.assertNotIn(secret, text)
        for box in re.findall(r"<input\b[^>]*>", text):  # scoped to the elements: only the two hidden ones (token, language) carry a value
            if 'name="day"' in box or 'name="month"' in box or 'name="year"' in box or 'name="answer"' in box:
                self.assertNotRegex(box, r'value="[^"]', box)

    def test_the_leak_check_skips_the_links_own_random_text_and_still_sees_a_real_leak(self):
        page = ('<a href="/results/abP-0cd:1x:zz/?lang=en">English</a><form action="/results/abP-0cd:1x:zz/?lang=ar">'
                '<input type="hidden" name="csrfmiddlewaretoken" value="qP-0q"><p>P-0007</p></form>')
        self.assertEqual(page.count("P-0"), 4)
        self.assertEqual(_without_random_text(page).count("P-0"), 1)  # only the file number that is really printed

    # ---- before the factor ----

    def test_ranges_and_values_are_isolated_left_to_right_on_both_sheets(self):
        """«12 – 16» and «A+» must read left to right inside the RTL sheet: the whole range (dash and spaces included) in one
        LTR isolate, each value in its own isolate. The browser check (HG-085) measured the glyph order in every layout."""

        client = self._anon()
        token = self._token(self.both)
        self._pass(client, token, BOTH_BORN)
        staff = self.client.get(reverse("medical:lab_sheet", args=[self.both.pk, self.day.isoformat()]))
        for lang in ("ar", "en"):
            for page in (client.get(self._url(token, lang)), self.client.get(reverse("medical:lab_sheet", args=[self.both.pk, self.day.isoformat()]) + f"?lang={lang}")):
                body = page.content.decode()
                self.assertIn('<td data-cell="{}"><span dir="ltr">12 – 16</span></td>'.format("المعدل الطبيعي" if lang == "ar" else "Normal range"), body)
                self.assertIn('<span dir="ltr">70 – 100</span>', body)
                self.assertIn("<bdi> A+</bdi>", body)
                self.assertIn("<bdi>17.25</bdi>", body)
                self.assertIn("<bdi>g/dL</bdi>", body)
        self.assertEqual(staff.status_code, 200)

    def test_a_valid_link_shows_only_the_lab_and_the_question(self):
        link = self._link(self.both)
        self.assertTrue(link["url"].startswith(f"{BASE}/results/"))
        self.assertTrue(link["url"].endswith("?lang=ar"))
        response = self._anon().get(self._url(token_of(link["url"])))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-lab-link="ask"')
        self.assertContains(response, "Demo Store")
        self.assertContains(response, "تاريخ ميلاد المريض")
        self.assertNotContains(response, "سنة ميلاد")
        self.assertNotContains(response, "data-lab-sheet")
        self.assertNothingOfThePatient(response, self.both)
        self.assertPrivate(response)
        self.assertFalse(AuditLog.objects.filter(action="open_lab_result_link").exists())

    def test_the_question_is_a_birth_date_in_three_numeric_boxes_day_month_year_in_one_row(self):
        token = self._token(self.both)
        expected = {"ar": ("تاريخ ميلاد المريض", ("اليوم", "الشهر", "السنة")), "en": ("Patient's birth date", ("Day", "Month", "Year"))}
        for lang, (legend, labels) in expected.items():
            page = self._anon().get(self._url(token, lang)).content.decode()
            row = re.search(r'<div class="ll-date" dir="ltr">(.*?)</div>\s*</fieldset>', page, re.S)
            self.assertIsNotNone(row, lang)  # one row, left to right in both languages: day, month, year
            boxes = re.findall(r"<input\b[^>]*>", row.group(1))
            self.assertEqual([re.search(r'name="(\w+)"', box).group(1) for box in boxes], ["day", "month", "year"])
            for box in boxes:
                self.assertIn('inputmode="numeric"', box)
                self.assertIn('autocomplete="off"', box)
                self.assertNotIn("type=\"date\"", box)  # no native date picker
                self.assertNotIn("type=\"number\"", box)
                self.assertIn("required", box)
                self.assertIn("class=\"ll-input ll-input--part\"", box)
            self.assertEqual([int(n) for n in re.findall(r'maxlength="(\d)"', row.group(1))], [2, 2, 4])
            self.assertEqual(re.findall(r'<label class="ll-label" for="(ll-\w+)">([^<]+)</label>', row.group(1)),
                             [("ll-day", labels[0]), ("ll-month", labels[1]), ("ll-year", labels[2])])
            self.assertIn("<legend", page)
            self.assertIn(html.escape(legend), page)
            self.assertNotIn('name="answer"', page)
            self.assertEqual(page.count('pattern="[0-9٠-٩۰-۹]*"'), 3)  # Arabic-Indic and Persian digits may be typed
        # a phone-only file keeps its single box
        phone = self._anon().get(self._url(self._token(self.phone_only))).content.decode()
        self.assertIn('name="answer"', phone)
        self.assertNotIn('name="day"', phone)

    def test_each_box_is_at_least_44px_tall(self):
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "static/hesba/css"
        tokens = (root / "tokens.css").read_text()
        height = int(re.search(r"--hs-control-height-lg:\s*(\d+)px", tokens).group(1))
        self.assertGreaterEqual(height, 44)
        css = (root / "lab_link.css").read_text()
        self.assertRegex(css, r"\.ll-input\{[^}]*min-block-size:var\(--hs-control-height-lg\)")  # the part boxes are .ll-input too
        self.assertIn(".ll-input--part", css)
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")  # tokens only, no colour literals

    def test_the_right_date_is_accepted_in_every_way_it_can_be_typed(self):
        token = self._token(self.both)
        typed = [("4", "3", "1985"), ("04", "03", "1985"), (" 4 ", "3", "1985 "), ("٤", "٣", "١٩٨٥"), ("٠٤", "٠٣", "١٩٨٥"), ("۴", "۳", "۱۹۸۵"), ("4", "٣", "1985")]
        for day, month, year in typed:
            with self.subTest((day, month, year)):
                response = self._anon().post(self._url(token), {"day": day, "month": month, "year": year})
                self.assertEqual(response.status_code, 303)
        self.assertEqual(self._life(token), 0)  # right answers are never charged

    def test_a_real_date_that_does_not_match_is_a_wrong_answer_and_is_counted(self):
        token = self._token(self.both)
        wrong = [(4, 3, 1986), (4, 4, 1985), (5, 3, 1985), (3, 4, 1985), (1, 1, 1900)]  # 3 April is not 4 March
        for day, month, year in wrong:
            with self.subTest((day, month, year)):
                response = self._anon().post(self._url(token), {"day": str(day), "month": str(month), "year": str(year)})
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, "مش مطابقة", status_code=400)
                self.assertNotContains(response, "اكتب تاريخ ميلاد حقيقي", status_code=400)
                self.assertNotIn(public._cookie_name(token), response.cookies)
                self.assertNothingOfThePatient(response, self.both)
        self.assertEqual(self._life(token), 5)  # every one was counted
        self.assertEqual(self._pass(self._anon(), token, BOTH_BORN).status_code, 429)  # and the window of five is full

    def test_a_date_that_is_not_a_real_calendar_date_is_a_format_error_and_is_not_counted(self):
        token = self._token(self.both)
        this_year = timezone.localdate().year
        bad = [("31", "2", "1985"), ("29", "2", "1985"), ("30", "2", "2000"), ("0", "3", "1985"), ("4", "0", "1985"), ("32", "1", "1985"), ("4", "13", "1985"),
               ("4", "3", "1899"), ("4", "3", "0985"), ("4", "3", str(this_year + 1)), ("4", "3", "9999"), ("4", "3", "85"), ("4", "3", "985"),
               ("4", "3", "19850"), ("", "3", "1985"), ("4", "", "1985"), ("4", "3", ""), ("", "", ""), ("اربعة", "3", "1985"), ("4", "3", "x985"),
               ("4.5", "3", "1985"), ("123", "3", "1985"), ("4", "123", "1985")]
        for day, month, year in bad:
            with self.subTest((day, month, year)):
                response = self._anon().post(self._url(token), {"day": day, "month": month, "year": year})
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, "اكتب تاريخ ميلاد حقيقي", status_code=400)
                self.assertNotContains(response, "مش مطابقة", status_code=400)
                self.assertContains(response, 'data-lab-ask', status_code=400)
                self.assertNothingOfThePatient(response, self.both)
        self.assertEqual(self._life(token), 0)
        self.assertFalse(LabLinkCounter.objects.exists())
        # missing fields altogether, and the old single box, are format errors too
        for data in ({}, {"answer": "1985"}, {"day": "4", "month": "3"}):
            self.assertEqual(self._anon().post(self._url(token), data).status_code, 400)
        self.assertFalse(LabLinkCounter.objects.exists())
        for _ in range(public.TOKEN_SHORT[1] + 3):  # any number of typos never locks the patient out
            self._anon().post(self._url(token), {"day": "31", "month": "2", "year": "1985"})
        self.assertEqual(self._pass(self._anon(), token, BOTH_BORN).status_code, 303)

    def test_the_first_and_last_year_that_is_a_date_is_accepted_as_a_format_but_not_as_a_match(self):
        token = self._token(self.both)
        this_year = timezone.localdate().year
        for year in ("1900", str(this_year)):
            response = self._anon().post(self._url(token), {"day": "1", "month": "1", "year": year})
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "مش مطابقة", status_code=400)

    def test_the_date_is_compared_in_constant_time(self):
        import hmac

        token = self._token(self.both)
        with patch("medical.public.hmac.compare_digest", wraps=hmac.compare_digest) as compared:
            self._pass(self._anon(), token, BOTH_BORN)
            self._pass(self._anon(), token, WRONG_DATE)
        dates = [call.args for call in compared.call_args_list if re.fullmatch(r"\d{4}-\d\d-\d\d", str(call.args[0]))]
        self.assertEqual(dates, [("1985-03-04", "1985-03-04"), ("1985-03-04", "2001-02-03")])  # the answer, as one ISO string, against the file's

    def test_the_page_never_shows_the_birth_date_or_the_phone_before_or_after_the_answer(self):
        token = self._token(self.both)
        client = self._anon()
        pages = [client.get(self._url(token)), client.get(self._url(token, "en"))]
        pages.append(self._pass(client, token, WRONG_DATE))
        pages.append(client.post(self._url(token), {"day": "31", "month": "2", "year": "1985"}))
        pages.append(client.post(self._url(token), {"day": "4", "month": "3", "year": "1986", "lang": "en"}))
        for _ in range(public.TOKEN_SHORT[1]):
            self._pass(client, token, WRONG_DATE)
        busy = self._pass(client, token, BOTH_BORN)  # the right answer while the window is full: the page is refused, and must not echo it
        self.assertEqual(busy.status_code, 429)
        pages.append(busy)
        for response in pages:
            self.assertNothingOfThePatient(response, self.both)
        self.assertNothingOfThePatient(self._anon().get(self._url(self._token(self.dob_only))), self.dob_only)
        self.assertNothingOfThePatient(self._pass(self._anon(), self._token(self.dob_only), WRONG_DATE), self.dob_only)
        # after the answer: the sheet shows the age of that day, never the date of birth, never the phone
        fresh = self._anon()
        token = self._token(self.both)
        self._pass(fresh, token, BOTH_BORN)
        for lang in ("ar", "en"):
            sheet = _without_random_text(fresh.get(self._url(token, lang)).content.decode())
            self.assertIn("data-lab-sheet", sheet)
            for secret in SECRETS_OF_THE_FILES:
                self.assertNotIn(secret, sheet)
            for month in ("March", "مارس", "آذار"):
                self.assertNotIn(month, sheet)

    def test_the_wrong_and_the_format_pages_are_the_same_for_every_file_with_a_date(self):
        bodies = {}
        for patient in (self.both, self.dob_only):
            client = self._anon()
            token = self._token(patient)
            bodies[patient.pk] = (normalized(self._pass(client, token, WRONG_DATE), token),
                                  normalized(client.post(self._url(token), {"day": "31", "month": "2", "year": "1985"}), token))
        self.assertEqual(bodies[self.both.pk], bodies[self.dob_only.pk])
        self.assertNotEqual(*bodies[self.both.pk])

    def test_the_words_say_birth_date_never_birth_year_and_keep_the_customer_voice(self):
        from . import views

        arabic, english = public.WORDS["ar"], public.WORDS["en"]
        every = [text for words in (arabic, english) for text in words.values()] + [views.LAB_WORDS[lang]["send_note"] for lang in ("ar", "en")]
        for text in every:
            for old in ("سنة ميلاد", "سنة الميلاد", "birth year"):
                self.assertNotIn(old, text)
            self.assertNotIn("لينك", text)
        for key in ("ask_date", "label_date", "label_day", "label_month", "label_year", "format_date", "wa_text_date"):
            self.assertIn(key, arabic)
            self.assertIn(key, english)
        for key in ("ask_year", "format_year", "wa_text_year"):
            self.assertNotIn(key, arabic)
            self.assertNotIn(key, english)
        self.assertEqual((arabic["label_date"], arabic["label_day"], arabic["label_month"], arabic["label_year"]), ("تاريخ ميلاد المريض", "اليوم", "الشهر", "السنة"))
        self.assertEqual((english["label_day"], english["label_month"], english["label_year"]), ("Day", "Month", "Year"))
        # the patient is «حضرتك», and no Arabic line starts or ends with an English word
        for key in ("ask_date", "wa_text_date", "wa_text_phone", "private"):
            self.assertIn("حضرتك", arabic[key], key)
        latin = re.compile(r"[A-Za-z]")
        mine = ("ask_date", "label_date", "label_day", "label_month", "label_year", "format_date", "wa_text_date", "no_factor", "wrong")
        for text in [arabic[key] for key in mine] + [views.LAB_WORDS["ar"]["send_note"]]:  # (the older «print» ends with «PDF», as it did before)
            for line in str(text).splitlines():
                line = line.strip()
                if line:
                    self.assertFalse(latin.match(line[0]) or latin.match(line[-1]), line)
        # the staff are told the factor and that a spent link needs a new one
        for lang in ("ar", "en"):
            note = views.LAB_WORDS[lang]["send_note"]
            self.assertIn("{days}", note)
        self.assertIn("تاريخ ميلاد", views.LAB_WORDS["ar"]["send_note"])
        self.assertIn("birth date", views.LAB_WORDS["en"]["send_note"])

    def test_the_staff_note_and_the_whatsapp_text_name_the_date(self):
        for lang, word in (("ar", "تاريخ ميلاد"), ("en", "birth date")):
            link = self._link(self.both, lang=lang)
            text = unquote(link["whatsapp"])
            self.assertIn(word, text)
            self.assertNotIn("سنة ميلاد", text)
            self.assertNotIn("birth year", text)
            self.assertEqual(link["kind"], "date")
        page = self.client.post(reverse("medical:lab", args=[self.both.pk]), {"action": "share", "day": self.day.isoformat(), "lang": "ar"})
        self.assertContains(page, "تاريخ ميلاد")
        self.assertNotContains(page, "سنة ميلاد")
        self.assertContains(page, "بيتقفل")  # a link answered wrongly too often closes: send a new one
        english = self.client.post(reverse("medical:lab", args=[self.both.pk]), {"action": "share", "day": self.day.isoformat(), "lang": "en"})
        self.assertContains(english, "birth date")
        self.assertContains(english, "closes")
        phone = unquote(self._link(self.phone_only)["whatsapp"])
        self.assertIn("آخر 4 أرقام", phone)  # the phone fallback's text is as it was
        self.assertNotIn("تاريخ ميلاد", phone)

    def test_the_page_embeds_nothing_from_another_site(self):
        client = self._anon()
        token = self._token(self.both)
        for response in (client.get(self._url(token)), self._pass(client, token, WRONG_DATE), client.get(self._url("x:y:z"))):
            embedded = re.findall(r'<(?:script|link|img|iframe|source|video|audio)\b[^>]*(?:src|href)="(?:https?:)?//', response.content.decode())
            self.assertEqual(embedded, [])
        self._pass(client, token, BOTH_BORN)
        sheet = client.get(self._url(token)).content.decode()
        self.assertEqual(re.findall(r'<(?:script|link|img|iframe|source|video|audio)\b[^>]*(?:src|href)="(?:https?:)?//', sheet), [])

    # ---- the factor ----

    def test_the_right_birth_date_opens_exactly_the_sheet_of_that_day(self):
        client = self._anon()
        token = self._token(self.both)
        response = self._pass(client, token, BOTH_BORN)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response["Location"], self._url(token))
        self.assertPrivate(response)
        cookie = response.cookies[public._cookie_name(token)]
        self.assertEqual(cookie["path"], reverse("lab_result_link", args=[token]))
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(int(cookie["max-age"]), public.PASS_SECONDS)
        self.assertNotIn("sessionid", response.cookies)  # never the Django session

        page = client.get(self._url(token))
        self.assertEqual(page.status_code, 200)
        self.assertPrivate(page)
        self.assertContains(page, 'data-lab-link="sheet"')
        shown = re.findall(r'data-sheet-result="(\d+)"', page.content.decode())
        self.assertEqual(sorted(map(int, shown)), sorted([self.hb.pk, self.sugar.pk, self.group.pk]))
        for text in ("منى عبد الله", "هيموجلوبين", "17.25", "88.4", "A+", "g/dL", "مرتفع ↑", self.day.isoformat(), "Demo Store", "P-0"):
            self.assertContains(page, text)
        self.assertContains(page, "data-out")  # the out-of-range row is marked, not by colour alone
        # nothing beyond the sheet: no other day, no history, no notes, no staff
        for hidden in ("987.654", self.other_day.isoformat(), "بنسلين", "داخلية", "ll_owner", "المرة اللي فاتت", "النتايج السابقة"):
            self.assertNotContains(page, hidden)
        # the same rows as the printed sheet the lab staff sees
        staff = self.client.get(reverse("medical:lab_sheet", args=[self.both.pk, self.day.isoformat()]))
        self.assertEqual(re.findall(r'data-sheet-result="(\d+)"', staff.content.decode()), shown)

    def test_the_last_four_digits_open_it_only_when_the_file_has_no_birth_date(self):
        client = self._anon()
        token = self._token(self.phone_only)
        ask = client.get(self._url(token))
        self.assertContains(ask, 'data-lab-factor="phone"')
        self.assertContains(ask, "آخر 4 أرقام من رقم الموبايل")
        self.assertNotContains(ask, "سنة ميلادك")  # one question, no «or»
        self.assertEqual(self._pass(client, token, "٣٣٣٤").status_code, 303)  # Arabic digits are read
        self.assertContains(client.get(self._url(token)), 'data-lab-link="sheet"')
        # a file with a birth date: only the birth year; the phone's last 4 digits are no factor at all (the link goes to that phone)
        both = self._token(self.both)
        ask = self._anon().get(self._url(both))
        self.assertContains(ask, 'data-lab-factor="date"')
        self.assertContains(ask, "تاريخ ميلاد المريض")
        self.assertNotContains(ask, "آخر 4 أرقام")
        self.assertEqual(self._pass(self._anon(), both, "4567").status_code, 400)  # the phone's digits are no factor for a file with a date
        self.assertEqual(self._pass(self._anon(), both, BOTH_BORN).status_code, 303)
        self.assertEqual(self._pass(self._anon(), self._token(self.dob_only), DOB_BORN).status_code, 303)
        self.assertEqual(public.factor(self.both), ("date", "1985-03-04"))
        self.assertEqual(public.factor(self.phone_only), ("phone", "3334"))
        self.assertIsNone(public.factor(self.nothing))

    def test_wrong_answers_never_pass_and_look_the_same_within_each_kind_of_factor(self):
        bodies = []
        for patient in (self.both, self.dob_only, self.phone_only, self.phone_only2):
            client = self._anon()
            token = self._token(patient)
            kind = public.factor(patient)[0]
            for answer in (("0000", "2000", "1234") if kind == public.PHONE else (WRONG_DATE, date(1985, 4, 3), date(1986, 3, 4))):
                response = self._pass(client, token, answer)
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, "data-lab-message", status_code=400)
                self.assertNotIn(public._cookie_name(token), response.cookies)
                self.assertNothingOfThePatient(response, patient)
                self.assertPrivate(response)
            self.assertContains(client.get(self._url(token)), 'data-lab-link="ask"')
            bodies.append(normalized(self._pass(client, token, self._wrong(patient)), token))
        self.assertEqual(bodies[0], bodies[1])  # birth year: a file with a phone too answers exactly as one without
        self.assertEqual(bodies[2], bodies[3])  # phone's last 4 digits
        # a birth year never opens a phone-only file, nor last-4 a birth-date file
        self.assertEqual(self._pass(self._anon(), self._token(self.phone_only), BOTH_BORN).status_code, 400)
        self.assertEqual(self._pass(self._anon(), self._token(self.dob_only), "4567").status_code, 400)
        self.assertEqual(self._pass(self._anon(), self._token(self.both), "4567").status_code, 400)
        # and the birth YEAR alone, which was the old factor, opens nothing now
        self.assertEqual(self._pass(self._anon(), self._token(self.both), "1985").status_code, 400)

    def test_a_badly_typed_phone_answer_is_not_counted(self):
        client = self._anon()
        token = self._token(self.phone_only)
        for answer in ("", "12", "12345", "abcd"):
            response = self._pass(client, token, answer)
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "4 أرقام", status_code=400)
        self.assertEqual(self._life(token), 0)
        self.assertEqual(self._pass(client, token, "3334").status_code, 303)

    def test_wrong_answers_are_throttled_per_link_and_a_locked_link_refuses_even_the_right_one(self):
        client = self._anon()
        token = self._token(self.both)
        for _ in range(public.TOKEN_SHORT[1]):
            self.assertEqual(self._pass(client, token, WRONG_DATE).status_code, 400)
        locked = self._pass(client, token, BOTH_BORN)
        self.assertEqual(locked.status_code, 429)
        self.assertContains(locked, "محاولات كتير", status_code=429)
        self.assertNotIn(public._cookie_name(token), locked.cookies)
        self.assertPrivate(locked)
        self.assertEqual(self._pass(self._anon(REMOTE_ADDR="10.9.9.9"), token, BOTH_BORN).status_code, 429)  # per link, whatever the address
        # another patient's link from the same address still works
        self.assertEqual(self._pass(client, self._token(self.phone_only), "3334").status_code, 303)
        # the window passes: the right answer works again
        with patch("medical.public.timezone.now", return_value=timezone.now() + timedelta(seconds=public.TOKEN_SHORT[2] + 1)):
            self.assertEqual(self._pass(client, token, BOTH_BORN).status_code, 303)

    def _spend(self, token, wrong, start):
        """``wrong`` wrong answers, spread over 15-minute windows so the short bucket never refuses them."""

        done = 0
        window = 0
        while done < wrong:
            with patch("medical.public.timezone.now", return_value=start + timedelta(seconds=window * public.TOKEN_SHORT[2])):
                for _ in range(min(public.TOKEN_SHORT[1], wrong - done)):
                    self.assertEqual(self._pass(self._anon(), token, WRONG_DATE).status_code, 400)
                    done += 1
            window += 1
        return start + timedelta(seconds=window * public.TOKEN_SHORT[2])

    def test_a_link_has_a_lifetime_of_wrong_answers_then_it_is_dead(self):
        self.assertEqual(public.TOKEN_LIFE[1], 8)
        token = self._token(self.both)
        passed = self._anon()
        self.assertEqual(self._pass(passed, token, BOTH_BORN).status_code, 303)  # a right answer is not charged
        later = self._spend(token, public.TOKEN_LIFE[1], timezone.now())
        with patch("medical.public.timezone.now", return_value=later):
            self.assertDead(self._pass(self._anon(), token, BOTH_BORN))  # even the right answer, from anywhere
            self.assertDead(self._pass(self._anon(REMOTE_ADDR="10.7.7.7"), token, BOTH_BORN))
            self.assertDead(self._anon().get(self._url(token)))  # no question left to ask
            self.assertContains(passed.get(self._url(token)), 'data-lab-link="sheet"')  # a browser that passed keeps its 30 minutes
            self.assertDead(self._pass(passed, token, BOTH_BORN))  # but makes no new answer
            # «ابعت تاني»: a new token with fresh counters, even within the same second
            again = self._token(self.both)
            self.assertNotEqual(again, token)
            self.assertEqual(self._pass(self._anon(), again, BOTH_BORN).status_code, 303)
        self.assertEqual(AuditLog.objects.filter(action="open_lab_result_link").count(), 1)

    def test_answers_take_their_slot_before_they_are_compared(self):
        """N answers at once cannot compare more than the limit: the slot is taken (atomically, in the row) before the compare."""

        from django.test import RequestFactory

        token = self._token(self.both)
        request = RequestFactory().post("/", REMOTE_ADDR="10.3.3.3")
        # simulate N answers that all reach the counting step before any of them compares
        outcomes = [public.take(token, request) for _ in range(12)]
        self.assertEqual(outcomes.count("ok"), public.TOKEN_SHORT[1])
        self.assertEqual(outcomes.count("busy"), 12 - public.TOKEN_SHORT[1])
        self.assertEqual(self._life(token), public.TOKEN_SHORT[1])  # refused ones gave theirs back
        # against the lifetime limit itself (the short window made roomy so only the row refuses)
        fresh = self._token(self.both)
        with patch.object(public, "TOKEN_SHORT", ("token15", 1000, 900)):
            outcomes = [public.take(fresh, request) for _ in range(20)]
        self.assertEqual((outcomes.count("ok"), outcomes.count("dead")), (public.TOKEN_LIFE[1], 20 - public.TOKEN_LIFE[1]))
        self.assertEqual(self._life(fresh), public.TOKEN_LIFE[1])  # the refused ones did not count past the limit
        self.assertTrue(public.used_up(fresh))
        # the view itself compares only after its slot is taken
        seen = []
        real = public._matches
        other = self._token(self.both)

        def spy(patient, file, answer):
            seen.append(self._life(other))
            return real(patient, file, answer)

        with patch.object(public, "_matches", side_effect=spy):
            self.assertEqual(self._pass(self._anon(), other, WRONG_DATE).status_code, 400)
        self.assertEqual(seen, [1])
        # a right answer gives its slot back
        third = self._token(self.both)
        self.assertEqual(public.take(third, request), "ok")
        self.assertEqual(self._life(third), 1)
        public.give_back(third, request)
        self.assertEqual(self._life(third), 0)
        public.give_back(third, request)  # never below zero
        self.assertEqual(self._life(third), 0)

    # ---- the lifetime counter lives in the database ----

    def test_the_lifetime_counter_is_a_row_that_holds_a_fingerprint_and_no_patient_data(self):
        token = self._token(self.both)
        self.assertFalse(LabLinkCounter.objects.exists())  # making a link writes no counter; the row is made by the first answer
        self.assertFalse(public.used_up(token))
        self.assertFalse(LabLinkCounter.objects.exists())  # and asking whether it is spent never makes one
        self._anon().get(self._url(token))
        self.assertFalse(LabLinkCounter.objects.exists())  # a visit is not an answer
        self._pass(self._anon(), token, WRONG_DATE)
        row = LabLinkCounter.objects.get()
        self.assertEqual(row.wrong_answers, 1)
        self.assertEqual(row.link_hash, public.link_hash(token))
        self.assertEqual(len(row.link_hash), 64)
        self.assertNotIn(token, row.link_hash)
        self.assertNotEqual(row.link_hash, public._digest(token))  # keyed: not the hash the cookie and the cache already use
        self.assertEqual(sorted(field.name for field in LabLinkCounter._meta.concrete_fields),
                         ["created_at", "id", "link_hash", "updated_at", "wrong_answers"])  # no patient, no name, no date, no phone
        self.assertIsNotNone(row.created_at)
        self.assertIsNotNone(row.updated_at)
        # a second link for the same patient and day is another token, so another row
        again = self._token(self.both)
        self._pass(self._anon(), again, WRONG_DATE)
        self.assertEqual(LabLinkCounter.objects.count(), 2)

    def test_the_limit_survives_a_cleared_cache_and_a_restart(self):
        token = self._token(self.both)
        later = self._spend(token, public.TOKEN_LIFE[1] - 1, timezone.now())
        cache.clear()  # a deploy, or another worker: the cache knows nothing
        self.assertEqual(self._life(token), public.TOKEN_LIFE[1] - 1)
        with patch("medical.public.timezone.now", return_value=later):
            self.assertEqual(self._pass(self._anon(), token, WRONG_DATE).status_code, 400)  # the eighth wrong answer
            cache.clear()
            self.assertDead(self._pass(self._anon(), token, BOTH_BORN))  # spent: even the right answer finds the dead page
            self.assertDead(self._anon().get(self._url(token)))
            cache.clear()
            self.assertTrue(public.used_up(token))
        self.assertEqual(self._life(token), public.TOKEN_LIFE[1])

    def test_a_right_answer_gives_its_slot_back_in_the_row(self):
        token = self._token(self.both)
        self._spend(token, 3, timezone.now())
        self.assertEqual(self._life(token), 3)
        self.assertEqual(self._pass(self._anon(), token, BOTH_BORN).status_code, 303)
        self.assertEqual(self._life(token), 3)  # taken, then given back: a patient who is right is never charged
        self.assertEqual(self._pass(self._anon(), token, BOTH_BORN).status_code, 303)
        self.assertEqual(self._life(token), 3)

    def test_a_refused_answer_is_not_charged_to_the_row(self):
        token = self._token(self.both)
        for _ in range(public.TOKEN_SHORT[1]):
            self._pass(self._anon(), token, WRONG_DATE)
        self.assertEqual(self._life(token), public.TOKEN_SHORT[1])
        self.assertEqual(self._pass(self._anon(), token, WRONG_DATE).status_code, 429)  # the window is full: refused without a compare
        self.assertEqual(self._life(token), public.TOKEN_SHORT[1])

    def test_a_spent_link_gives_the_same_dead_page_as_any_invalid_link_and_a_new_link_starts_at_zero(self):
        token = self._token(self.both)
        later = self._spend(token, public.TOKEN_LIFE[1], timezone.now())
        with patch("medical.public.timezone.now", return_value=later):
            spent = self._pass(self._anon(), token, BOTH_BORN)
            forged = self._pass(self._anon(), "forged:token:x", BOTH_BORN)
            self.assertDead(spent)
            self.assertEqual(normalized(spent, token), normalized(forged, "forged:token:x"))
            self.assertEqual(spent.status_code, forged.status_code)
            again = self._token(self.both)  # «ابعت تاني»: a new token, a new fingerprint, a clean count
            self.assertEqual(self._life(again), 0)
            self.assertEqual(self._pass(self._anon(), again, BOTH_BORN).status_code, 303)
            self.assertDead(self._pass(self._anon(), token, BOTH_BORN))  # and the old one stays dead

    def test_an_answer_writes_the_counter_row_and_nothing_else(self):
        from django.test.utils import CaptureQueriesContext

        def writes(call):
            with CaptureQueriesContext(connection) as captured:
                response = call()
            return response, [q["sql"].split()[0] + " " + re.search(r'(?:INTO|UPDATE|FROM)\s+"?(\w+)"?', q["sql"]).group(1)
                              for q in captured if q["sql"].split()[0] in ("INSERT", "UPDATE", "DELETE")]

        token = self._token(self.both)
        client = self._anon()
        _, written = writes(lambda: client.get(self._url(token)))
        self.assertEqual(written, [])  # the question page writes nothing
        _, written = writes(lambda: client.post(self._url(token), {"day": "31", "month": "2", "year": "1985"}))
        self.assertEqual(written, [])  # a format error writes nothing
        _, written = writes(lambda: self._pass(client, token, WRONG_DATE))
        # the first answer of a link: the guarded UPDATE finds no row, the row is made, the UPDATE runs again; nothing but the counter
        self.assertEqual(written, ["UPDATE medical_lablinkcounter", "INSERT medical_lablinkcounter", "UPDATE medical_lablinkcounter"])
        _, written = writes(lambda: self._pass(client, token, WRONG_DATE))
        self.assertEqual(written, ["UPDATE medical_lablinkcounter"])  # a later wrong answer: one UPDATE
        _, written = writes(lambda: self._pass(client, token, BOTH_BORN))
        self.assertEqual(written, ["UPDATE medical_lablinkcounter", "UPDATE medical_lablinkcounter"])  # a right one: take, then give back
        _, written = writes(lambda: client.get(self._url(token)))
        self.assertEqual(written, ["INSERT audit_auditlog"])  # the sheet's own audit row is the only write of opening it

    def test_forged_tokens_and_format_errors_make_no_row(self):
        for bad in ("garbage", "a:b:c", signing.dumps({"p": 1}, salt=public.SALT)):
            self._pass(self._anon(), bad, BOTH_BORN)
        token = self._token(self.both)
        for day, month, year in (("31", "2", "1985"), ("", "3", "1985"), ("4", "3", "85")):
            self._anon().post(self._url(token), {"day": day, "month": month, "year": year})
        self.assertFalse(LabLinkCounter.objects.exists())

    def test_the_lazy_row_is_made_once_even_when_another_request_makes_it_first(self):
        """The lost race, made deterministic (it runs on SQLite and on PostgreSQL): this request's lookup misses, the competing request
        commits its row, this request's INSERT meets the unique constraint and falls back to that row. One row, and no count is lost."""

        from django.db.models import QuerySet

        token = self._token(self.both)
        fingerprint = public.link_hash(token)
        real_get, state = QuerySet.get, {"missed": False}

        def get(queryset, *args, **kwargs):
            if queryset.model is LabLinkCounter and not state["missed"]:
                state["missed"] = True
                LabLinkCounter.objects.create(link_hash=fingerprint, wrong_answers=2)  # the competing request's row
                raise LabLinkCounter.DoesNotExist()
            return real_get(queryset, *args, **kwargs)

        with patch.object(QuerySet, "get", get):
            self.assertTrue(public._life_take(token))
        self.assertTrue(state["missed"])
        self.assertEqual(LabLinkCounter.objects.count(), 1)
        self.assertEqual(LabLinkCounter.objects.get().wrong_answers, 3)  # the other request's 2 and this one's 1

    def test_a_unique_fingerprint_is_enforced_by_the_database(self):
        from django.db import IntegrityError, transaction

        LabLinkCounter.objects.create(link_hash="a" * 64)
        with self.assertRaises(IntegrityError), transaction.atomic():
            LabLinkCounter.objects.create(link_hash="a" * 64)

    def test_wrong_answers_are_throttled_per_address(self):
        with patch.object(public, "ADDRESS_HOUR", ("ip", 3, 3600)):
            client = self._anon(REMOTE_ADDR="10.0.0.7")
            for patient in (self.both, self.dob_only, self.phone_only):
                self.assertEqual(self._pass(client, self._token(patient), self._wrong(patient)).status_code, 400)
            fresh = self._token(self.both)
            self.assertEqual(self._pass(client, fresh, BOTH_BORN).status_code, 429)
            self.assertEqual(self._pass(self._anon(REMOTE_ADDR="10.0.0.8"), fresh, BOTH_BORN).status_code, 303)

    def test_a_flood_of_requests_is_refused_calmly(self):
        token = self._token(self.both)
        client = self._anon(REMOTE_ADDR="10.1.1.1")
        with patch.object(public, "PAGE_MINUTE", ("page", 2, 60)):
            client.get(self._url(token))
            client.get(self._url(token))
            response = client.get(self._url(token))
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "data-lab-link-dead", status_code=429)
        self.assertPrivate(response)

    # ---- every failure looks the same ----

    def test_forged_other_salt_expired_wrong_patient_or_day_show_the_same_page(self):
        token = self._token(self.both)
        mark = signing.loads(token, salt=public.SALT)["f"]
        from printing.share import make_token as share_token

        failures = {
            "forged": token[:-3] + ("aaa" if not token.endswith("aaa") else "bbb"),
            "garbage": "not-a-token",
            "share salt": share_token("sales_invoice", self.both.pk),
            "work order salt": share_token("work_order", 1),
            "other patient": signing.dumps({"p": self.dob_only.pk, "d": self.day.isoformat(), "f": mark}, salt=public.SALT, compress=True),
            "other day": signing.dumps({"p": self.both.pk, "d": self.other_day.isoformat(), "f": mark}, salt=public.SALT, compress=True),
            "a day with no results": signing.dumps({"p": self.both.pk, "d": "2020-01-01", "f": mark}, salt=public.SALT, compress=True),
            "not a date": signing.dumps({"p": self.both.pk, "d": "nope", "f": mark}, salt=public.SALT, compress=True),
            "no such patient": signing.dumps({"p": 999999, "d": self.day.isoformat(), "f": mark}, salt=public.SALT, compress=True),
            "a list": signing.dumps(["x"], salt=public.SALT),
        }
        reference = None
        for label, bad in failures.items():
            with self.subTest(label):
                for method in ("get", "post"):
                    client = self._anon()
                    response = client.get(self._url(bad)) if method == "get" else self._pass(client, bad, BOTH_BORN)
                    self.assertDead(response)
                    body = normalized(response, bad)
                    reference = reference or body
                    self.assertEqual(body, reference)
        self.assertFalse(AuditLog.objects.filter(action="open_lab_result_link").exists())

    def test_a_lab_token_never_opens_a_share_page_or_the_work_order_endpoints(self):
        token = self._token(self.both)
        response = self._anon().get(reverse("share_document", args=[token]))
        self.assertContains(response, "data-share-expired", status_code=404)
        for name in ("share_work_order_approve", "share_work_order_refuse"):
            self.assertContains(self._anon().post(reverse(name, args=[token])), "data-share-expired", status_code=404)

    def test_an_expired_link_shows_the_dead_page(self):
        token = self._token(self.both)
        now = time.time()
        with patch("django.core.signing.time.time", return_value=now + 6 * 86400):
            self.assertEqual(self._anon().get(self._url(token)).status_code, 200)
        with patch("django.core.signing.time.time", return_value=now + 8 * 86400):
            self.assertDead(self._anon().get(self._url(token)))
        with override_settings(MEDICAL_LINK_DAYS=1), patch("django.core.signing.time.time", return_value=now + 2 * 86400):
            self.assertDead(self._anon().get(self._url(token)))
        # a passed browser does not outlive the link either
        client = self._anon()
        self._pass(client, token, BOTH_BORN)
        with patch("django.core.signing.time.time", return_value=now + 8 * 86400):
            self.assertDead(client.get(self._url(token)))

    def test_any_change_to_the_results_kills_old_links(self):
        cases = [
            ("re-saved with the same values: still valid", lambda: lab.record_results(self.both, self.day, {self.hb: ("17.25", "")}, self.owner), False),
            ("a value edited", lambda: lab.record_results(self.both, self.day, {self.hb: ("17.3", "")}, self.owner), True),
            ("a result added", lambda: lab.record_results(self.both, self.day, {lab.save_test({"name": "كوليسترول", "unit": "mg/dL"}, self.owner): ("150", "")},
                                                          self.owner), True),
            ("a result removed", lambda: lab.remove_result(LabResult.objects.filter(patient=self.both, taken_on=self.day).last(), self.owner, "غلط"), True),
            ("a text result edited", lambda: lab.record_results(self.both, self.day, {self.group: ("", "B+")}, self.owner), True),
            ("the phone corrected", lambda: type(self.both).objects.filter(pk=self.both.pk).update(phone="01009998887"), True),
            ("the birth date corrected", lambda: PatientFile.objects.filter(customer=self.both).update(date_of_birth=date(1986, 3, 4)), True),
        ]
        for label, change, kills in cases:
            with self.subTest(label):
                client = self._anon()
                token = self._token(self.both)
                self._pass(client, token, BOTH_BORN)
                self.assertContains(client.get(self._url(token)), 'data-lab-link="sheet"')
                change()
                self.both.refresh_from_db()
                response = client.get(self._url(token))
                if kills:
                    self.assertDead(response)
                    self.assertDead(self._pass(self._anon(), token, BOTH_BORN))
                else:
                    self.assertContains(response, 'data-lab-link="sheet"')

    def test_a_link_whose_patient_lost_both_factors_shows_nothing(self):
        token = self._token(self.phone_only)
        with patch.object(public, "fingerprint", return_value=signing.loads(token, salt=public.SALT)["f"]):  # only the factors change
            type(self.phone_only).objects.filter(pk=self.phone_only.pk).update(phone="")
            self.assertDead(self._anon().get(self._url(token)))

    # ---- the remembered pass ----

    def test_the_pass_of_one_link_never_opens_another(self):
        client = self._anon()
        first = self._token(self.both)
        second = self._token(self.both, self.other_day)  # the same patient, another day
        third = self._token(self.dob_only)
        self._pass(client, first, BOTH_BORN)
        self.assertContains(client.get(self._url(first)), 'data-lab-link="sheet"')
        for other in (second, third):
            response = client.get(self._url(other))
            self.assertContains(response, 'data-lab-link="ask"')
            self.assertNotContains(response, "987.654")
        # the first link's signed value copied under the second link's cookie name does not open it either
        value = client.cookies[public._cookie_name(first)].value
        client.cookies[public._cookie_name(second)] = value
        self.assertContains(client.get(self._url(second)), 'data-lab-link="ask"')
        # nor does a cookie properly signed under the second link's name that holds the first link's digest
        name = public._cookie_name(second)
        client.cookies[name] = signing.get_cookie_signer(salt=name + public.PASS_SALT).sign(public._digest(first))
        self.assertContains(client.get(self._url(second)), 'data-lab-link="ask"')
        client.cookies[name] = signing.get_cookie_signer(salt=name + public.PASS_SALT).sign(public._digest(second))
        self.assertContains(client.get(self._url(second)), 'data-lab-link="sheet"')  # the control: the right digest opens it
        del client.cookies[name]
        # a tampered or unsigned cookie opens nothing
        fresh = self._anon()
        fresh.cookies[public._cookie_name(first)] = public._digest(first)
        self.assertContains(fresh.get(self._url(first)), 'data-lab-link="ask"')
        # and the pass is short-lived
        with patch("django.core.signing.time.time", return_value=time.time() + public.PASS_SECONDS + 5):
            self.assertContains(client.get(self._url(first)), 'data-lab-link="ask"')

    def test_a_staff_login_neither_opens_the_page_nor_is_touched_by_it(self):
        token = self._token(self.both)
        staff = self.client  # logged in as the lab's owner
        before = (staff.session.session_key, staff.session.get("_auth_user_id"))
        response = staff.get(self._url(token))
        self.assertContains(response, 'data-lab-link="ask"')
        self.assertNothingOfThePatient(response, self.both)
        self._pass(staff, token, BOTH_BORN)
        self.assertContains(staff.get(self._url(token)), 'data-lab-link="sheet"')
        self.assertEqual((staff.session.session_key, staff.session.get("_auth_user_id")), before)
        self.assertEqual(staff.get(reverse("medical:lab", args=[self.both.pk])).status_code, 200)  # still signed in
        log = AuditLog.objects.filter(action="open_lab_result_link").latest("pk")
        self.assertIsNone(log.actor)  # not filed under the staff member who happened to be signed in

    # ---- headers, methods, CSRF ----

    def test_every_answer_carries_the_private_headers_and_csrf_is_enforced(self):
        token = self._token(self.both)
        client = self._anon()
        self.assertPrivate(client.get(self._url(token)))
        self.assertPrivate(client.put(self._url(token)))
        self.assertEqual(client.put(self._url(token)).status_code, 405)
        strict = self._anon(enforce_csrf_checks=True)
        self.assertEqual(strict.post(self._url(token), {"day": "4", "month": "3", "year": "1985"}).status_code, 403)
        page = strict.get(self._url(token))
        csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        self.assertEqual(strict.post(self._url(token), {"day": "4", "month": "3", "year": "1985", "csrfmiddlewaretoken": csrf}).status_code, 303)

    # ---- the lab's side ----

    def test_the_lab_makes_the_link_from_the_results_screen(self):
        url = reverse("medical:lab", args=[self.both.pk])
        page = self.client.get(url)
        self.assertContains(page, f'data-lab-share="{self.day.isoformat()}"')
        self.assertContains(page, f'data-lab-share="{self.other_day.isoformat()}"')
        self.assertNotContains(page, "data-lab-share-blocked")
        response = self.client.post(url, {"action": "share", "day": self.day.isoformat(), "lang": "ar"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-lab-share-box")
        self.assertContains(response, f'value="{BASE}/results/')
        self.assertContains(response, "data-copy-link")
        self.assertContains(response, "https://wa.me/201001234567?text=")
        log = AuditLog.objects.filter(action="send_lab_result_link").latest("pk")
        self.assertEqual((log.actor, log.object_id, log.after_data["patient"], log.after_data["day"]),
                         (self.owner, str(self.both.pk), self.both.pk, self.day.isoformat()))
        token = token_of(re.search(r'data-copy-link="([^"]+)"', response.content.decode()).group(1))
        self.assertEqual(self._anon().get(self._url(token)).status_code, 200)
        # the token is never kept in the audit
        for row in AuditLog.objects.filter(module="medical"):
            self.assertNotIn(token, f"{row.before_data}{row.after_data}{row.reason}")
        # a day that is not a date, or has no results
        self.assertEqual(self.client.post(url, {"action": "share", "day": "nope"}).status_code, 404)
        empty = self.client.post(url, {"action": "share", "day": "2020-01-01"})
        self.assertContains(empty, "مفيش نتايج في اليوم ده.")
        self.assertNotContains(empty, "data-lab-share-box")

    def test_the_whatsapp_text_names_the_lab_and_never_a_test_or_a_value(self):
        for lang in ("ar", "en"):
            link = self._link(self.both, lang=lang)
            text = unquote(link["whatsapp"])
            self.assertIn("Demo Store", text)
            self.assertIn(link["url"], text)
            self.assertIn("تاريخ ميلاد المريض" if lang == "ar" else "birth date", text)
            self.assertNotIn("آخر 4" if lang == "ar" else "last 4", text)  # the phone is no factor for a file with a birth date
            phone = unquote(self._link(self.phone_only, lang=lang)["whatsapp"])
            self.assertIn("آخر 4 أرقام" if lang == "ar" else "last 4 digits", phone)
            self.assertNotIn("تاريخ ميلاد" if lang == "ar" else "birth date", phone)
            for secret in ("هيموجلوبين", "سكر", "فصيلة", "17.25", "88.4", "A+", "g/dL", "مرتفع", "High", "منى", self.day.isoformat()):
                self.assertNotIn(secret, text)

    def test_the_lab_is_told_when_only_the_phone_protects_the_link(self):
        url = reverse("medical:lab", args=[self.phone_only.pk])
        page = self.client.get(url)
        self.assertContains(page, "data-lab-phone-only")
        self.assertContains(page, "مفيهوش تاريخ ميلاد")
        self.assertContains(page, "data-lab-share=")  # advice, not a block
        response = self.client.post(url, {"action": "share", "day": self.day.isoformat()})
        self.assertContains(response, "data-lab-share-box")
        self.assertContains(response, "data-lab-share-warning")
        self.assertContains(response, "مش بتحمي لو الرقم نفسه غلط")
        english = self.client.post(url, {"action": "share", "day": self.day.isoformat(), "lang": "en"})
        self.assertContains(english, "does not help if the number itself is wrong")
        for patient in (self.both, self.dob_only):  # a birth date on file: no such note
            url = reverse("medical:lab", args=[patient.pk])
            self.assertNotContains(self.client.get(url), "data-lab-phone-only")
            self.assertNotContains(self.client.post(url, {"action": "share", "day": self.day.isoformat()}), "data-lab-share-warning")

    def test_a_patient_with_neither_factor_gets_no_link(self):
        url = reverse("medical:lab", args=[self.nothing.pk])
        page = self.client.get(url)
        self.assertContains(page, "data-lab-share-blocked")
        self.assertContains(page, "مفيهوش تاريخ ميلاد ولا رقم موبايل")
        self.assertNotContains(page, "data-lab-share=")
        response = self.client.post(url, {"action": "share", "day": self.day.isoformat()})
        self.assertContains(response, "مفيهوش تاريخ ميلاد ولا رقم موبايل")
        self.assertNotContains(response, "data-lab-share-box")
        self.assertFalse(AuditLog.objects.filter(action="send_lab_result_link").exists())
        # a phone too short to be a number is no factor either
        type(self.nothing).objects.filter(pk=self.nothing.pk).update(phone="123")
        self.assertContains(self.client.get(url), "data-lab-share-blocked")

    @override_settings(PUBLIC_BASE_URL="")
    def test_without_the_public_address_no_link_is_made(self):
        response = self.client.post(reverse("medical:lab", args=[self.both.pk]), {"action": "share", "day": self.day.isoformat()})
        self.assertContains(response, "PUBLIC_BASE_URL")
        self.assertNotContains(response, "data-lab-share-box")
        self.assertFalse(AuditLog.objects.filter(action="send_lab_result_link").exists())

    def test_only_lab_staff_who_may_write_records_make_a_link(self):
        url = reverse("medical:lab", args=[self.both.pk])
        data = {"action": "share", "day": self.day.isoformat()}
        reader_role = make_role(code="LAB-READER")
        grant(reader_role, Permission.objects.get(code=services.VIEW))
        reader = make_user(username="ll_reader")
        make_user_profile(user=reader, role=reader_role)
        self.client.force_login(reader)
        page = self.client.get(url)
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "data-lab-share")
        self.assertEqual(self.client.post(url, data).status_code, 403)
        with self.assertRaises(PermissionDenied):
            public.make_link(self.both, self.day, reader)
        cashier = person(RoleCode.CASHIER, "ll_cashier")
        self.client.force_login(cashier)
        self.assertEqual(self.client.post(url, data).status_code, 403)
        manager = person(RoleCode.MANAGER, "ll_manager")
        self.client.force_login(manager)
        self.assertContains(self.client.post(url, data), "data-lab-share-box")
        self.assertEqual(AuditLog.objects.filter(action="send_lab_result_link").count(), 1)

    def test_lang_english(self):
        link = self._link(self.both, lang="en")
        self.assertTrue(link["url"].endswith("?lang=en"))
        token = token_of(link["url"])
        client = self._anon()
        ask = client.get(self._url(token, "en"))
        self.assertContains(ask, "Your lab results are ready")
        self.assertContains(ask, 'dir="ltr"')
        self.assertContains(ask, f'href="{reverse("lab_result_link", args=[token])}?lang=ar"')  # the language switch stays on this link
        wrong = self._pass(client, token, WRONG_DATE, "en")
        self.assertContains(wrong, "does not match", status_code=400)
        self.assertEqual(self._pass(client, token, BOTH_BORN, "en")["Location"], self._url(token, "en"))
        sheet = client.get(self._url(token, "en"))
        self.assertContains(sheet, "Laboratory report")
        self.assertContains(sheet, "High ↑")
        self.assertContains(client.get(self._url(token, "ar")), "تقرير نتيجة تحاليل")  # the pass survives the language switch
        dead = self._anon().get(self._url("x", "en"))
        self.assertContains(dead, "This link no longer works", status_code=404)

    def test_audit_of_a_view_has_no_actor_no_address_and_no_device(self):
        client = self._anon(REMOTE_ADDR="10.2.3.4", HTTP_USER_AGENT="PhoneBrowser/1.0")
        token = self._token(self.both)
        self._pass(client, token, WRONG_DATE)
        self.assertFalse(AuditLog.objects.filter(action="open_lab_result_link").exists())  # failed tries are throttled, not audited
        self._pass(client, token, BOTH_BORN)
        client.get(self._url(token))
        log = AuditLog.objects.get(action="open_lab_result_link")
        self.assertEqual((log.actor, log.ip_address, log.user_agent, log.object_id), (None, None, "", str(self.both.pk)))
        self.assertEqual(log.after_data, {"patient": self.both.pk, "day": self.day.isoformat()})


@skipUnless(connection.vendor == "postgresql", "real concurrent connections need PostgreSQL: SQLite's shared in-memory test database answers a concurrent reader with "
                                               "'database table is locked'. On SQLite the same properties are pinned by the sequential tests in LabLinkTests "
                                               "(20 takes admit exactly 8; the lost race is replayed deterministically).")
class LabLinkLifetimeRaceTests(TransactionTestCase):
    """N answers at once never compare more than the lifetime limit, and two first answers at once make one row and lose no count.

    A TransactionTestCase, because threads cannot see a TestCase's uncommitted rows. On PostgreSQL the threads are real concurrent connections
    (this is the proof of the lazy row's race-safety). On SQLite the class is skipped (see the reason above); there the lost race is pinned
    deterministically in ``LabLinkTests.test_the_lazy_row_is_made_once_even_when_another_request_makes_it_first`` and the limit by the sequential takes."""

    serialized_rollback = True  # the content types and permissions come back after the flush, so the migration tests after this class still start clean

    def setUp(self):
        cache.clear()
        self.request = RequestFactory().post("/", REMOTE_ADDR="10.4.4.4")

    def _at_once(self, token, workers, **patches):
        results, errors, barrier = [], [], threading.Barrier(workers, timeout=30)

        def answer():
            try:
                barrier.wait()
                results.append(public.take(token, self.request))
            except Exception as error:  # noqa: BLE001  (reported below, never swallowed)
                errors.append(repr(error))
            finally:
                connection.close()

        with patch.object(public, "TOKEN_SHORT", ("token15", 1000, 900)), patch.object(public, "ADDRESS_HOUR", ("ip", 1000, 3600)):
            threads = [threading.Thread(target=answer) for _ in range(workers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        return results

    def test_twenty_answers_at_once_compare_exactly_eight(self):
        """The guarded UPDATE alone, on a row that already exists (the education portal's own race, HG-101)."""

        token = "race-token-one"
        LabLinkCounter.objects.create(link_hash=public.link_hash(token))
        results = self._at_once(token, 20)
        self.assertEqual((results.count("ok"), results.count("dead")), (public.TOKEN_LIFE[1], 20 - public.TOKEN_LIFE[1]), results)
        self.assertEqual(LabLinkCounter.objects.get().wrong_answers, public.TOKEN_LIFE[1])
        self.assertTrue(public.used_up(token))

    def test_twenty_first_answers_at_once_make_one_row_and_compare_exactly_eight(self):
        token = "race-token-two"
        results = self._at_once(token, 20)  # no row yet: the twenty race to make it
        self.assertEqual((results.count("ok"), results.count("dead")), (public.TOKEN_LIFE[1], 20 - public.TOKEN_LIFE[1]), results)
        self.assertEqual(LabLinkCounter.objects.count(), 1)
        self.assertEqual(LabLinkCounter.objects.get().wrong_answers, public.TOKEN_LIFE[1])
        self.assertTrue(public.used_up(token))

    def test_first_answers_at_once_lose_no_count_below_the_limit(self):
        with patch.object(public, "TOKEN_LIFE", ("tokenlife", 100, None)):
            results = self._at_once("race-token-three", 20)
            self.assertEqual(results.count("ok"), 20)
            self.assertEqual(LabLinkCounter.objects.count(), 1)
            self.assertEqual(LabLinkCounter.objects.get().wrong_answers, 20)  # every one of the twenty is in the count

    def test_many_links_first_answered_at_once_each_make_their_own_row(self):
        tokens = [f"race-link-{number}" for number in range(6)]
        results, errors, barrier = [], [], threading.Barrier(12, timeout=30)

        def answer(token):
            try:
                barrier.wait()
                results.append(public.take(token, self.request))
            except Exception as error:  # noqa: BLE001
                errors.append(repr(error))
            finally:
                connection.close()

        with patch.object(public, "TOKEN_SHORT", ("token15", 1000, 900)), patch.object(public, "ADDRESS_HOUR", ("ip", 1000, 3600)):
            threads = [threading.Thread(target=answer, args=(token,)) for token in tokens for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(results.count("ok"), 12)
        self.assertEqual(sorted(LabLinkCounter.objects.values_list("wrong_answers", flat=True)), [2] * 6)


class LabLinkInstallTests(TestCase):
    """The lab screens (and so the link) are for a lab install only."""

    def _try(self, activity, sub):
        prepared_client(activity, sub, CLINIC)
        owner = person(RoleCode.OWNER, f"{sub}_owner")
        patient = make_customer(customer_code="X-1", name="مريض", phone="01001234567")
        self.client.force_login(owner)
        return owner, patient, self.client.post(reverse("medical:lab", args=[patient.pk]), {"action": "share", "day": timezone.localdate().isoformat()})

    @override_settings(PUBLIC_BASE_URL=BASE)
    def test_a_clinic_cannot_make_a_lab_link(self):
        owner, patient, response = self._try("medical", "clinic")
        self.assertEqual(response.status_code, 404)
        with self.assertRaises(PermissionDenied):
            public.make_link(patient, timezone.localdate(), owner)

    @override_settings(PUBLIC_BASE_URL=BASE)
    def test_a_shop_cannot_make_a_lab_link(self):
        owner, patient, response = self._try("commercial", "retail")
        self.assertEqual(response.status_code, 404)
