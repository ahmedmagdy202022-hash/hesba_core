"""M6 (HG-085): a lab patient opens their results of one day from a link, behind a second factor.

A public page with health data: these tests pin that nothing is shown before the factor, that every failure looks
the same, that the factor is throttled and never reveals what is on file, and that only lab staff who may write
records can make a link.
"""

import re
import time
from datetime import date, timedelta
from unittest.mock import patch
from urllib.parse import unquote

from django.core import signing
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import grant, make_customer, make_role, make_user, make_user_profile
from permissions.models import Permission, RoleCode
from reports.tests_dashboard import prepared_client

from . import lab, public, services
from .models import LabResult, PatientFile
from .tests import CLINIC, person

BASE = "https://lab.example.test"
PRIVATE = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "same-origin"}


def token_of(url):
    return unquote(url.split("/results/", 1)[1].split("/", 1)[0])


def normalized(response, *tokens):
    """The body with what differs between two links by construction (the token, the CSRF value) taken out."""

    body = response.content.decode()
    for token in tokens:
        body = body.replace(token, "TOKEN")
    return re.sub(r'name="csrfmiddlewaretoken" value="[^"]+"', 'name="csrfmiddlewaretoken" value="X"', body)


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
        return client.post(self._url(token, lang), {"answer": answer, "lang": lang})

    def assertPrivate(self, response):
        for header, value in PRIVATE.items():
            self.assertEqual(response[header], value, header)

    def assertDead(self, response):
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "data-lab-link-dead", status_code=404)
        self.assertNotContains(response, "data-lab-ask", status_code=404)
        self.assertPrivate(response)

    def assertNothingOfThePatient(self, response, patient):
        status = response.status_code
        for secret in (patient.name, "هيموجلوبين", "سكر صائم", "فصيلة الدم", "17.25", "88.4", "A+", self.day.isoformat(),
                       self.other_day.isoformat(), "987.654", "P-0", "بنسلين", "داخلية"):
            self.assertNotContains(response, secret, status_code=status)

    # ---- before the factor ----

    def test_ranges_and_values_are_isolated_left_to_right_on_both_sheets(self):
        """«12 – 16» and «A+» must read left to right inside the RTL sheet: the whole range (dash and spaces included) in one
        LTR isolate, each value in its own isolate. The browser check (HG-085) measured the glyph order in every layout."""

        client = self._anon()
        token = self._token(self.both)
        self._pass(client, token, "1985")
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
        self.assertContains(response, "سنة ميلادك")
        self.assertNotContains(response, "data-lab-sheet")
        self.assertNothingOfThePatient(response, self.both)
        self.assertPrivate(response)
        self.assertFalse(AuditLog.objects.filter(action="open_lab_result_link").exists())

    def test_the_page_embeds_nothing_from_another_site(self):
        client = self._anon()
        token = self._token(self.both)
        for response in (client.get(self._url(token)), self._pass(client, token, "0000"), client.get(self._url("x:y:z"))):
            embedded = re.findall(r'<(?:script|link|img|iframe|source|video|audio)\b[^>]*(?:src|href)="(?:https?:)?//', response.content.decode())
            self.assertEqual(embedded, [])
        self._pass(client, token, "1985")
        sheet = client.get(self._url(token)).content.decode()
        self.assertEqual(re.findall(r'<(?:script|link|img|iframe|source|video|audio)\b[^>]*(?:src|href)="(?:https?:)?//', sheet), [])

    # ---- the factor ----

    def test_the_right_birth_year_opens_exactly_the_sheet_of_that_day(self):
        client = self._anon()
        token = self._token(self.both)
        response = self._pass(client, token, "1985")
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
        self.assertContains(ask, 'data-lab-factor="year"')
        self.assertContains(ask, "سنة ميلادك")
        self.assertNotContains(ask, "آخر 4 أرقام")
        self.assertEqual(self._pass(self._anon(), both, "4567").status_code, 400)
        self.assertEqual(self._pass(self._anon(), both, "1985").status_code, 303)
        self.assertEqual(self._pass(self._anon(), self._token(self.dob_only), "1990").status_code, 303)
        self.assertEqual(public.factor(self.both), ("year", "1985"))
        self.assertEqual(public.factor(self.phone_only), ("phone", "3334"))
        self.assertIsNone(public.factor(self.nothing))

    def test_wrong_answers_never_pass_and_look_the_same_within_each_kind_of_factor(self):
        bodies = []
        for patient in (self.both, self.dob_only, self.phone_only, self.phone_only2):
            client = self._anon()
            token = self._token(patient)
            for answer in ("0000", "2000", "1234"):
                response = self._pass(client, token, answer)
                self.assertEqual(response.status_code, 400)
                self.assertContains(response, "data-lab-message", status_code=400)
                self.assertNotIn(public._cookie_name(token), response.cookies)
                self.assertNothingOfThePatient(response, patient)
                self.assertPrivate(response)
            self.assertContains(client.get(self._url(token)), 'data-lab-link="ask"')
            bodies.append(normalized(self._pass(client, token, "0000"), token))
        self.assertEqual(bodies[0], bodies[1])  # birth year: a file with a phone too answers exactly as one without
        self.assertEqual(bodies[2], bodies[3])  # phone's last 4 digits
        # a birth year never opens a phone-only file, nor last-4 a birth-date file
        self.assertEqual(self._pass(self._anon(), self._token(self.phone_only), "1985").status_code, 400)
        self.assertEqual(self._pass(self._anon(), self._token(self.dob_only), "4567").status_code, 400)
        self.assertEqual(self._pass(self._anon(), self._token(self.both), "4567").status_code, 400)

    def test_a_badly_typed_answer_is_not_counted(self):
        client = self._anon()
        token = self._token(self.both)
        for answer in ("", "12", "12345", "abcd"):
            response = self._pass(client, token, answer)
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, "4 أرقام", status_code=400)
        self.assertEqual(self._pass(client, token, "1985").status_code, 303)

    def test_wrong_answers_are_throttled_per_link_and_a_locked_link_refuses_even_the_right_one(self):
        client = self._anon()
        token = self._token(self.both)
        for _ in range(public.TOKEN_SHORT[1]):
            self.assertEqual(self._pass(client, token, "0000").status_code, 400)
        locked = self._pass(client, token, "1985")
        self.assertEqual(locked.status_code, 429)
        self.assertContains(locked, "محاولات كتير", status_code=429)
        self.assertNotIn(public._cookie_name(token), locked.cookies)
        self.assertPrivate(locked)
        self.assertEqual(self._pass(self._anon(REMOTE_ADDR="10.9.9.9"), token, "1985").status_code, 429)  # per link, whatever the address
        # another patient's link from the same address still works
        self.assertEqual(self._pass(client, self._token(self.phone_only), "3334").status_code, 303)
        # the window passes: the right answer works again
        with patch("medical.public.timezone.now", return_value=timezone.now() + timedelta(seconds=public.TOKEN_SHORT[2] + 1)):
            self.assertEqual(self._pass(client, token, "1985").status_code, 303)

    def _spend(self, token, wrong, start):
        """``wrong`` wrong answers, spread over 15-minute windows so the short bucket never refuses them."""

        done = 0
        window = 0
        while done < wrong:
            with patch("medical.public.timezone.now", return_value=start + timedelta(seconds=window * public.TOKEN_SHORT[2])):
                for _ in range(min(public.TOKEN_SHORT[1], wrong - done)):
                    self.assertEqual(self._pass(self._anon(), token, "0000").status_code, 400)
                    done += 1
            window += 1
        return start + timedelta(seconds=window * public.TOKEN_SHORT[2])

    def test_a_link_has_a_lifetime_of_wrong_answers_then_it_is_dead(self):
        self.assertEqual(public.TOKEN_LIFE[1], 8)
        token = self._token(self.both)
        passed = self._anon()
        self.assertEqual(self._pass(passed, token, "1985").status_code, 303)  # a right answer is not charged
        later = self._spend(token, public.TOKEN_LIFE[1], timezone.now())
        with patch("medical.public.timezone.now", return_value=later):
            self.assertDead(self._pass(self._anon(), token, "1985"))  # even the right answer, from anywhere
            self.assertDead(self._pass(self._anon(REMOTE_ADDR="10.7.7.7"), token, "1985"))
            self.assertDead(self._anon().get(self._url(token)))  # no question left to ask
            self.assertContains(passed.get(self._url(token)), 'data-lab-link="sheet"')  # a browser that passed keeps its 30 minutes
            self.assertDead(self._pass(passed, token, "1985"))  # but makes no new answer
            # «ابعت تاني»: a new token with fresh counters, even within the same second
            again = self._token(self.both)
            self.assertNotEqual(again, token)
            self.assertEqual(self._pass(self._anon(), again, "1985").status_code, 303)
        self.assertEqual(AuditLog.objects.filter(action="open_lab_result_link").count(), 1)

    def test_answers_take_their_slot_before_they_are_compared(self):
        """N answers at once cannot compare more than the limit: the slot is taken (atomically) before the compare."""

        import threading

        from django.test import RequestFactory

        token = self._token(self.both)
        request = RequestFactory().post("/", REMOTE_ADDR="10.3.3.3")
        # simulate N answers that all reach the counting step before any of them compares
        outcomes = [public.take(token, request) for _ in range(12)]
        self.assertEqual(outcomes.count("ok"), public.TOKEN_SHORT[1])
        self.assertEqual(outcomes.count("busy"), 12 - public.TOKEN_SHORT[1])
        self.assertEqual(cache.get(public._key(public.TOKEN_LIFE, public._digest(token))), public.TOKEN_SHORT[1])  # refused ones gave theirs back
        # and with real threads, against the lifetime cap
        cache.clear()
        fresh = self._token(self.both)
        results, barrier = [], threading.Barrier(20)

        def answer():
            barrier.wait()
            results.append(public.take(fresh, request))

        with patch.object(public, "TOKEN_SHORT", ("token15", 1000, 900)):
            threads = [threading.Thread(target=answer) for _ in range(20)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        self.assertEqual(results.count("ok"), public.TOKEN_LIFE[1])
        self.assertEqual(results.count("dead"), 20 - public.TOKEN_LIFE[1])
        self.assertTrue(public.used_up(fresh))
        # the view itself compares only after its slot is taken
        cache.clear()
        seen = []
        real = public._matches

        def spy(patient, file, answer):
            seen.append(cache.get(public._key(public.TOKEN_LIFE, public._digest(fresh))))
            return real(patient, file, answer)

        with patch.object(public, "_matches", side_effect=spy):
            self.assertEqual(self._pass(self._anon(), fresh, "0000").status_code, 400)
        self.assertEqual(seen, [1])
        # a right answer gives its slot back
        cache.clear()
        self.assertEqual(public.take(fresh, request), "ok")
        public.give_back(fresh, request)
        self.assertEqual(cache.get(public._key(public.TOKEN_LIFE, public._digest(fresh))), 0)

    def test_wrong_answers_are_throttled_per_address(self):
        with patch.object(public, "ADDRESS_HOUR", ("ip", 3, 3600)):
            client = self._anon(REMOTE_ADDR="10.0.0.7")
            for patient in (self.both, self.dob_only, self.phone_only):
                self.assertEqual(self._pass(client, self._token(patient), "0000").status_code, 400)
            fresh = self._token(self.both)
            self.assertEqual(self._pass(client, fresh, "1985").status_code, 429)
            self.assertEqual(self._pass(self._anon(REMOTE_ADDR="10.0.0.8"), fresh, "1985").status_code, 303)

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
                    response = client.get(self._url(bad)) if method == "get" else self._pass(client, bad, "1985")
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
        self._pass(client, token, "1985")
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
                self._pass(client, token, "1985")
                self.assertContains(client.get(self._url(token)), 'data-lab-link="sheet"')
                change()
                self.both.refresh_from_db()
                response = client.get(self._url(token))
                if kills:
                    self.assertDead(response)
                    self.assertDead(self._pass(self._anon(), token, "1985"))
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
        self._pass(client, first, "1985")
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
        self._pass(staff, token, "1985")
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
        self.assertEqual(strict.post(self._url(token), {"answer": "1985"}).status_code, 403)
        page = strict.get(self._url(token))
        csrf = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)
        self.assertEqual(strict.post(self._url(token), {"answer": "1985", "csrfmiddlewaretoken": csrf}).status_code, 303)

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
            self.assertIn("سنة ميلادك" if lang == "ar" else "birth year", text)
            self.assertNotIn("آخر 4" if lang == "ar" else "last 4", text)  # the phone is no factor for a file with a birth date
            phone = unquote(self._link(self.phone_only, lang=lang)["whatsapp"])
            self.assertIn("آخر 4 أرقام" if lang == "ar" else "last 4 digits", phone)
            self.assertNotIn("سنة ميلادك" if lang == "ar" else "birth year", phone)
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
        self.assertContains(page, "مفيهوش سنة ميلاد ولا رقم موبايل")
        self.assertNotContains(page, "data-lab-share=")
        response = self.client.post(url, {"action": "share", "day": self.day.isoformat()})
        self.assertContains(response, "مفيهوش سنة ميلاد ولا رقم موبايل")
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
        wrong = self._pass(client, token, "0000", "en")
        self.assertContains(wrong, "does not match", status_code=400)
        self.assertEqual(self._pass(client, token, "1985", "en")["Location"], self._url(token, "en"))
        sheet = client.get(self._url(token, "en"))
        self.assertContains(sheet, "Laboratory report")
        self.assertContains(sheet, "High ↑")
        self.assertContains(client.get(self._url(token, "ar")), "تقرير نتيجة تحاليل")  # the pass survives the language switch
        dead = self._anon().get(self._url("x", "en"))
        self.assertContains(dead, "This link no longer works", status_code=404)

    def test_audit_of_a_view_has_no_actor_no_address_and_no_device(self):
        client = self._anon(REMOTE_ADDR="10.2.3.4", HTTP_USER_AGENT="PhoneBrowser/1.0")
        token = self._token(self.both)
        self._pass(client, token, "0000")
        self.assertFalse(AuditLog.objects.filter(action="open_lab_result_link").exists())  # failed tries are throttled, not audited
        self._pass(client, token, "1985")
        client.get(self._url(token))
        log = AuditLog.objects.get(action="open_lab_result_link")
        self.assertEqual((log.actor, log.ip_address, log.user_agent, log.object_id), (None, None, "", str(self.both.pk)))
        self.assertEqual(log.after_data, {"patient": self.both.pk, "day": self.day.isoformat()})


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
