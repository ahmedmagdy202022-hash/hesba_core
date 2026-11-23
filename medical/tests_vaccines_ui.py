"""M7 (HG-090): the vaccination screens — the card on the animal's page, the due list, the vaccines set-up and the record form —
in Arabic and English, who may open and post what, another entity's records, the template scanners, and the migration on a
database that already holds animals and visits.
"""

import re
from datetime import date, timedelta
from decimal import Decimal as D
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from permissions.models import RoleCode
from sales.models import SalesInvoice
from sales.services import cancel_posted_sales_invoice, post_sales_invoice
from settings_core.templatetags.hesba_format import day_text

from . import vaccines, vet
from .models import ClinicalVisit, Vaccination, Vaccine
from .tests_inpatient import footprint, person
from .tests_vaccines import Clinic

ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
#: A cell with its label already written (the table-cards script writes it at load; a template never does).
PRE_LABELLED = re.compile(r"<t[dh]\b[^>]*\bdata-label\b")


def strip_inputs(html):
    """The page without its form controls: a date input carries an ISO value by design; the text around it must not."""

    return re.sub(r"<(input|select|option|textarea)\b[^>]*>", "", html)


class Screens(Clinic):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def animal_url(self, lang="ar", animal=None):
        return f"{reverse('medical:animal', args=[(animal or self.rocky).pk])}?lang={lang}"

    def post(self, name, args, data=None, user=None, lang="ar"):
        if user is not None:
            self.client.force_login(user)
        return self.client.post(f"{reverse(name, args=args)}?lang={lang}", {"lang": lang, **(data or {})})


class CardTests(Screens):
    def test_the_card_lists_what_was_given_when_next_due_and_the_batch(self):
        dose = self.dose(days_ago=40, batch="LOT-55", given_by=self.karim, note="ZZNOTE خمول")
        page = self.client.get(self.animal_url())
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertIn('id="vaccinations"', body)
        self.assertContains(page, "سعار")
        self.assertContains(page, day_text(dose.given_on, "ar"))
        self.assertContains(page, day_text(dose.next_due, "ar"))
        self.assertContains(page, "LOT-55")
        self.assertContains(page, "د. كريم")
        self.assertContains(page, "ZZNOTE")  # a reader of patient records sees the clinical note
        self.assertContains(page, "سجّل تطعيم")
        self.assertContains(page, f'href="{reverse("medical:vaccinate", args=[self.rocky.pk])}?lang=ar"')

    def test_dates_are_written_in_words_not_iso(self):
        self.dose(days_ago=40)
        text = strip_inputs(self.client.get(self.animal_url()).content.decode())
        card = text[text.index('id="vaccinations"'):]
        self.assertNotRegex(card.split("</section>")[0], ISO)

    def test_the_english_card(self):
        dose = self.dose(days_ago=40)
        page = self.client.get(self.animal_url("en"))
        self.assertContains(page, "Vaccinations")
        self.assertContains(page, "Record a vaccination")
        self.assertContains(page, day_text(dose.given_on, "en"))
        self.assertContains(page, "Next dose:")

    def test_an_overdue_dose_says_so_and_a_closed_one_says_it_was_closed(self):
        old = self.dose(days_ago=380)
        page = self.client.get(self.animal_url())
        self.assertContains(page, "data-overdue")
        self.assertContains(page, "متأخر")
        self.dose(days_ago=0)
        page = self.client.get(self.animal_url())
        self.assertNotContains(page, "data-overdue")
        self.assertContains(page, "اتقفلت بجرعة أحدث")
        self.assertEqual(vaccines.superseded(old), True)

    def test_an_animal_with_no_doses_says_so_and_a_new_install_points_to_the_set_up(self):
        self.assertContains(self.client.get(self.animal_url()), "data-no-doses")
        Vaccine.objects.all().delete()
        page = self.client.get(self.animal_url())
        self.assertContains(page, "data-no-vaccines")
        self.assertContains(page, reverse("medical:vaccine_setup"))
        self.assertNotContains(page, "data-vaccinate")

    def test_the_bill_button_only_where_the_vaccine_has_an_item_and_the_dose_is_not_on_an_invoice(self):
        self.dose(vaccine=self.puppy, days_ago=3)
        self.assertNotContains(self.client.get(self.animal_url()), "data-bill")
        free = self.dose(days_ago=2)
        page = self.client.get(self.animal_url())
        self.assertContains(page, "data-bill")
        self.assertContains(page, reverse("medical:vaccination_action", args=[free.pk, "bill"]))
        vaccines.bill(free, self.owner)
        page = self.client.get(self.animal_url())
        self.assertNotContains(page, "data-bill")
        self.assertContains(page, "data-dose-invoice")
        self.assertContains(page, "مسودة")

    def test_the_card_is_in_the_page_of_a_vet_only(self):
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(sub_activity_slug="clinic")
        self.assertEqual(self.client.get(self.animal_url()).status_code, 404)  # the animal file itself is a vet's

    def test_the_stylesheet_is_loaded_with_its_version(self):
        self.assertContains(self.client.get(self.animal_url()), "vaccines.css?v=m7-1")


class RecordFormTests(Screens):
    URL = property(lambda self: reverse("medical:vaccinate", args=[self.rocky.pk]))

    def test_the_form_offers_the_animals_vaccines_with_their_interval_in_both_languages(self):
        page = self.client.get(f"{self.URL}?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "سعار — كل سنة")
        self.assertContains(page, "أول جرعة جراوي — مرة واحدة")
        self.assertContains(page, "طارد ديدان — كل 90 يوم")
        self.assertNotContains(page, "ثلاثي القطط")  # a cat's vaccine is not offered for a dog
        english = self.client.get(f"{self.URL}?lang=en")
        self.assertContains(english, "Rabies — every year")
        self.assertContains(english, "Record a vaccination")
        self.assertEqual(english.context["dir"], "ltr")

    def test_the_form_is_phone_ready(self):
        page = self.client.get(f"{self.URL}?lang=ar").content.decode()
        self.assertIn('type="date"', page)
        self.assertIn("max=", page)
        self.assertIn("data-save", page)
        self.assertNotRegex(page, PRE_LABELLED)

    def test_posting_records_the_dose_and_returns_to_the_card(self):
        response = self.post("medical:vaccinate", [self.rocky.pk], {"vaccine": self.rabies.pk, "given_on": self.today.isoformat(), "batch": "LOT-1",
                                                                    "given_by": self.karim.pk, "note": "ZZ"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("#vaccinations", response["Location"])
        dose = Vaccination.objects.get()
        self.assertEqual((dose.animal, dose.vaccine, dose.batch, dose.given_by, dose.note, dose.created_by), (self.rocky, self.rabies, "LOT-1", self.karim, "ZZ", self.owner))
        self.assertEqual(dose.next_due, vaccines.due_after(self.today, 365))
        self.assertContains(self.client.get(response["Location"]), "اتسجّل تطعيم سعار لـ روكي")

    def test_a_typed_next_date_and_the_no_next_box(self):
        typed = (self.today + timedelta(days=45)).isoformat()
        self.post("medical:vaccinate", [self.rocky.pk], {"vaccine": self.rabies.pk, "next_due": typed})
        self.assertEqual(Vaccination.objects.get().next_due.isoformat(), typed)
        self.post("medical:vaccinate", [self.mishmish.pk], {"vaccine": self.fvrcp.pk, "no_next": "1"})
        self.assertIsNone(Vaccination.objects.get(animal=self.mishmish).next_due)

    def test_a_mistake_shows_the_error_and_keeps_what_was_typed(self):
        response = self.post("medical:vaccinate", [self.rocky.pk], {"vaccine": self.rabies.pk, "given_on": (self.today + timedelta(days=2)).isoformat(), "batch": "LOT-KEEP"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "لسه مجاش")
        self.assertContains(response, "LOT-KEEP")
        self.assertEqual(Vaccination.objects.count(), 0)
        none = self.post("medical:vaccinate", [self.rocky.pk], {"given_on": self.today.isoformat()})
        self.assertContains(none, "اختار التطعيم")
        english = self.post("medical:vaccinate", [self.rocky.pk], {"vaccine": self.fvrcp.pk}, lang="en")
        self.assertEqual(english.status_code, 200)
        self.assertContains(english, "This vaccine is for Cat; this animal is Dog")

    def test_the_visit_choice_belongs_to_this_animal_and_a_preset_is_selected(self):
        visit = ClinicalVisit.objects.create(patient=self.mona, animal=self.rocky, visit_date=self.today, complaint="x", created_by=self.owner)
        other = ClinicalVisit.objects.create(patient=self.mona, animal=self.mishmish, visit_date=self.today, complaint="y", created_by=self.owner)
        page = self.client.get(f"{self.URL}?lang=ar&visit={visit.pk}")
        self.assertContains(page, f'<option value="{visit.pk}" selected>')
        self.assertNotContains(page, f'value="{other.pk}"')
        self.assertEqual(self.post("medical:vaccinate", [self.rocky.pk], {"vaccine": self.rabies.pk, "visit": other.pk}).status_code, 404)  # a crafted id
        self.post("medical:vaccinate", [self.rocky.pk], {"vaccine": self.rabies.pk, "visit": visit.pk})
        self.assertEqual(Vaccination.objects.get().visit, visit)

    def test_nothing_to_choose_says_so(self):
        Vaccine.objects.all().delete()
        page = self.client.get(f"{self.URL}?lang=ar")
        self.assertContains(page, "data-no-vaccines")
        self.assertNotContains(page, "data-vaccination-form")

    def test_recording_through_the_form_moves_nothing(self):
        before = footprint()
        self.post("medical:vaccinate", [self.rocky.pk], {"vaccine": self.rabies.pk})
        self.assertEqual(footprint(), before)
        self.assertEqual(SalesInvoice.objects.count(), 0)


class ActionTests(Screens):
    def test_bill_makes_a_draft_once_and_the_page_says_so(self):
        dose = self.dose()
        response = self.post("medical:vaccination_action", [dose.pk, "bill"])
        self.assertEqual(response.status_code, 302)
        dose.refresh_from_db()
        self.assertEqual((dose.invoice.status, dose.invoice.total_amount), ("draft", D("250.00")))
        page = self.client.get(response["Location"])
        self.assertContains(page, f"فاتورة التطعيم {dose.invoice.invoice_number} مسودة")
        self.assertContains(page, f'href="{reverse("sales:detail", args=[dose.invoice.pk])}?lang=ar"')
        self.post("medical:vaccination_action", [dose.pk, "bill"])  # again: refused, still one invoice
        self.assertEqual(SalesInvoice.objects.count(), 1)

    def test_bill_again_after_the_invoice_was_cancelled(self):
        dose = self.dose()
        self.post("medical:vaccination_action", [dose.pk, "bill"])
        dose.refresh_from_db()
        post_sales_invoice(dose.invoice.pk, self.owner)
        cancel_posted_sales_invoice(dose.invoice.pk, self.owner, "خطأ")
        self.post("medical:vaccination_action", [dose.pk, "bill"])
        self.assertEqual(SalesInvoice.objects.count(), 2)
        dose.refresh_from_db()
        self.assertEqual(dose.invoice.status, "draft")

    def test_remove_a_mistaken_dose_but_not_one_on_a_live_invoice(self):
        mistake, billed = self.dose(days_ago=3), self.dose(days_ago=1, vaccine=self.deworm)
        self.assertEqual(self.post("medical:vaccination_action", [mistake.pk, "remove"]).status_code, 302)
        self.assertFalse(Vaccination.objects.filter(pk=mistake.pk).exists())
        vaccines.bill(billed, self.owner)
        response = self.post("medical:vaccination_action", [billed.pk, "remove"])
        self.assertContains(self.client.get(response["Location"]), "على فاتورة")
        self.assertTrue(Vaccination.objects.filter(pk=billed.pk).exists())

    def test_the_actions_take_post_only_and_know_two_words(self):
        dose = self.dose()
        self.assertEqual(self.client.get(reverse("medical:vaccination_action", args=[dose.pk, "bill"])).status_code, 405)
        self.assertEqual(self.client.get(reverse("medical:vaccination_action", args=[dose.pk, "remove"])).status_code, 405)
        self.assertEqual(self.post("medical:vaccination_action", [dose.pk, "post"]).status_code, 404)
        self.assertEqual(self.post("medical:vaccination_action", [999999, "bill"]).status_code, 404)
        self.assertEqual(SalesInvoice.objects.count(), 0)


class DueListScreenTests(Screens):
    URL = property(lambda self: reverse("medical:vaccinations"))

    def given(self, vaccine, animal, due_in):
        return vaccines.record(animal, {"vaccine": vaccine, "given_on": self.today - timedelta(days=30),
                                        "next_due": (self.today + timedelta(days=due_in)).isoformat()}, self.owner)

    def test_the_list_with_its_chips_dates_status_and_contact_buttons(self):
        late = self.given(self.rabies, self.rocky, -4)
        soon = self.given(self.fvrcp, self.mishmish, 2)
        page = self.client.get(f"{self.URL}?lang=ar")
        self.assertEqual(page.status_code, 200)
        body = page.content.decode()
        self.assertEqual([m for m in re.findall(r'data-vx-row="(\d+)"', body)], [str(late.pk), str(soon.pk)])
        self.assertContains(page, day_text(late.next_due, "ar"))
        self.assertContains(page, "متأخر 4 يوم")
        self.assertContains(page, "بعد 2 يوم")
        self.assertContains(page, 'href="tel:01001234567"')
        self.assertContains(page, "https://wa.me/201001234567?text=")
        self.assertContains(page, "منى علي")
        self.assertRegex(body, r'data-vx-count="overdue">1<')
        self.assertRegex(body, r'data-vx-count="week">1<')
        self.assertNotRegex(strip_inputs(body.split("<table")[1]), ISO)
        self.assertNotRegex(body, PRE_LABELLED)

    def test_the_whatsapp_text_names_the_animal_the_vaccine_the_date_and_the_clinic_only(self):
        from urllib.parse import unquote

        dose = self.given(self.rabies, self.rocky, 2)
        dose.note = "ZZNOTE"
        dose.save()
        body = self.client.get(f"{self.URL}?lang=ar").content.decode()
        link = unquote(re.search(r'href="(https://wa\.me/[^"]+)"', body).group(1).replace("&amp;", "&"))
        for expected in ("منى علي", "روكي", "سعار", day_text(dose.next_due, "ar"), "Demo Store"):
            self.assertIn(expected, link)
        self.assertNotIn("ZZNOTE", link)

    def test_the_filters(self):
        self.given(self.rabies, self.rocky, -4)
        self.given(self.fvrcp, self.mishmish, 2)
        self.given(self.deworm, self.rocky, 20)
        def rows(query):
            return len(re.findall(r"data-vx-row=", self.client.get(f"{self.URL}?lang=ar&{query}").content.decode()))
        self.assertEqual((rows("f=all"), rows("f=overdue"), rows("f=week"), rows("f=month"), rows("f=bogus")), (3, 1, 1, 2, 3))
        self.assertEqual((rows("q=روكي"), rows("q=مشمش"), rows("q=0100123"), rows("q=Rabies")), (2, 1, 3, 1))
        self.assertContains(self.client.get(f"{self.URL}?lang=ar&f=overdue"), 'data-vx-filter="overdue"')
        self.assertContains(self.client.get(f"{self.URL}?lang=ar&q=مفيش"), "data-empty-filtered")

    def test_the_empty_states(self):
        self.assertContains(self.client.get(f"{self.URL}?lang=ar"), "data-empty-none")
        Vaccine.objects.all().delete()
        page = self.client.get(f"{self.URL}?lang=ar")
        self.assertContains(page, "data-empty-first")
        self.assertContains(page, reverse("medical:vaccine_setup"))

    def test_english(self):
        dose = self.given(self.rabies, self.rocky, -1)
        page = self.client.get(f"{self.URL}?lang=en")
        self.assertContains(page, "Vaccinations")
        self.assertContains(page, "1 day late")
        self.assertContains(page, day_text(dose.next_due, "en"))
        self.assertContains(page, "Overdue")
        self.assertContains(page, ">Call<")
        self.assertEqual(page.context["dir"], "ltr")

    def test_the_tabs_are_there_for_the_vet(self):
        page = self.client.get(f"{self.URL}?lang=ar")
        self.assertContains(page, "data-tab-vaccinations")
        self.assertContains(page, "data-tab-vaccine-setup")
        self.assertContains(self.client.get(reverse("medical:patients") + "?lang=ar"), "data-tab-vaccinations")
        self.client.force_login(self.doctor)
        doctor = self.client.get(reverse("medical:patients") + "?lang=ar")
        self.assertContains(doctor, "data-tab-vaccinations")
        self.assertNotContains(doctor, "data-tab-vaccine-setup")  # set-up is the owner's and the manager's

    def test_on_a_phone_the_section_tabs_are_one_row_that_scrolls(self):
        """Six stacked full-width buttons pushed the list under the first screen. The tabs carry ``med-tabs`` and the shared
        sheet makes them one sideways-scrolling row at phone width, each still a 44 px target."""

        from django.contrib.staticfiles import finders

        page = self.client.get(f"{self.URL}?lang=ar")
        self.assertContains(page, 'class="op-form-actions med-tabs"')
        self.assertContains(page, "operations.css?v=w4-4")
        with open(finders.find("hesba/css/operations.css"), encoding="utf-8") as handle:
            css = handle.read()
        phone = css[css.index("@media (max-width:700px){\n  .op-form-actions.edu-tabs"):]
        self.assertIn(".op-form-actions.med-tabs{display:flex;flex-wrap:nowrap;overflow-x:auto", phone.split("}\n}")[0].replace(".op-form-actions.edu-tabs,", ""))
        self.assertIn(".med-tabs .op-primary,.med-tabs .op-secondary{flex:0 0 auto;width:auto;min-height:44px", phone)


class SetupScreenTests(Screens):
    URL = property(lambda self: reverse("medical:vaccine_setup"))

    def test_the_page_lists_the_vaccines_with_species_interval_and_item(self):
        page = self.client.get(f"{self.URL}?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "سعار")
        self.assertContains(page, "كلب · كل سنة · تطعيم سعار")
        self.assertContains(page, "كل الأنواع · كل 90 يوم")
        self.assertContains(page, "data-new-vaccine")
        self.assertContains(self.client.get(f"{self.URL}?lang=en"), "Dog · every year")

    def test_adding_editing_and_switching_off(self):
        response = self.post("medical:vaccine_setup", [], {"name_ar": "سعار الخيل", "species": "horse", "interval_days": "730", "item": self.service_item.pk})
        self.assertEqual(response.status_code, 302)
        added = Vaccine.objects.get(name_ar="سعار الخيل")
        self.assertEqual((added.species, added.interval_days, added.item, added.active), ("horse", 730, self.service_item, True))
        self.post("medical:vaccine_setup", [], {"vaccine": added.pk, "name_ar": "سعار الخيل", "species": "horse", "interval_days": "365", "active": "0"})
        added.refresh_from_db()
        self.assertEqual((added.interval_days, added.item, added.active), (365, None, False))
        self.assertContains(self.client.get(f"{self.URL}?lang=ar"), "موقوف")

    def test_a_mistake_shows_the_error_and_keeps_the_typed_values(self):
        response = self.post("medical:vaccine_setup", [], {"name_ar": "جديد", "interval_days": "-5"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "المدة لحد الجرعة الجاية")
        self.assertContains(response, 'value="جديد"')
        self.assertFalse(Vaccine.objects.filter(name_ar="جديد").exists())

    def test_a_manager_sets_up_and_a_doctor_does_not(self):
        self.assertEqual(self.client.get(f"{self.URL}?lang=ar").status_code, 200)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(f"{self.URL}?lang=ar").status_code, 200)
        self.assertEqual(self.post("medical:vaccine_setup", [], {"name_ar": "من المدير"}).status_code, 302)
        self.client.force_login(self.doctor)
        self.assertEqual(self.client.get(f"{self.URL}?lang=ar").status_code, 403)
        self.assertEqual(self.post("medical:vaccine_setup", [], {"name_ar": "من الدكتور"}).status_code, 403)
        self.assertFalse(Vaccine.objects.filter(name_ar="من الدكتور").exists())


class AccessTests(Screens):
    """Every screen and every POST, for every kind of person."""

    def targets(self):
        dose = self.dose()
        return [("get", reverse("medical:vaccinations"), None), ("get", reverse("medical:vaccine_setup"), None),
                ("get", reverse("medical:vaccinate", args=[self.rocky.pk]), None), ("post", reverse("medical:vaccinate", args=[self.rocky.pk]), {"vaccine": self.rabies.pk}),
                ("post", reverse("medical:vaccine_setup"), {"name_ar": "x"}), ("post", reverse("medical:vaccination_action", args=[dose.pk, "bill"]), {}),
                ("post", reverse("medical:vaccination_action", args=[dose.pk, "remove"]), {})]

    def test_reception_the_stock_keeper_and_a_login_with_no_role_are_refused_everywhere_and_nothing_changes(self):
        targets = self.targets()
        before = (Vaccination.objects.count(), Vaccine.objects.count(), SalesInvoice.objects.count())
        for user in (self.desk, self.keeper, person(RoleCode.ACCOUNTANT, "vx_acc3")):
            self.client.force_login(user)
            for method, url, data in targets:
                with self.subTest(user=user.username, url=url, method=method):
                    response = getattr(self.client, method)(f"{url}?lang=ar", {"lang": "ar", **(data or {})})
                    self.assertEqual(response.status_code, 403)
        self.assertEqual((Vaccination.objects.count(), Vaccine.objects.count(), SalesInvoice.objects.count()), before)

    def test_a_cashier_cannot_record_a_dose_and_never_sees_an_owners_phone(self):
        self.given_one = self.dose()
        self.client.force_login(self.desk)
        response = self.client.post(f"{reverse('medical:vaccinate', args=[self.rocky.pk])}?lang=ar", {"lang": "ar", "vaccine": self.rabies.pk})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Vaccination.objects.count(), 1)
        for url in (reverse("medical:vaccinations"), reverse("medical:animal", args=[self.rocky.pk])):
            page = self.client.get(f"{url}?lang=ar")
            self.assertEqual(page.status_code, 403)
            self.assertNotContains(page, "01001234567", status_code=403)

    def test_signed_out_goes_to_login(self):
        self.client.logout()
        for method, url, data in self.targets():
            with self.subTest(url=url):
                response = getattr(self.client, method)(url, data or {})
                self.assertEqual(response.status_code, 302)
                self.assertIn("login", response["Location"])

    def test_the_clinical_roles_may_record_and_bill_and_remove_their_own(self):
        for user in (self.owner, self.manager, self.doctor):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.post(f"{reverse('medical:vaccinate', args=[self.rocky.pk])}?lang=ar",
                                            {"lang": "ar", "vaccine": self.deworm.pk, "given_on": (self.today - timedelta(days=Vaccination.objects.count())).isoformat()})
                self.assertEqual(response.status_code, 302)
        self.assertEqual(Vaccination.objects.count(), 3)

    def test_billing_needs_the_invoice_permission_even_for_a_doctor(self):
        from permissions.services import user_has_permission

        dose = self.dose()
        self.assertFalse(user_has_permission(self.doctor, "sales.create_sales_invoice"))  # the seeded doctor role holds no sales permission
        self.assertTrue(user_has_permission(self.manager, "sales.create_sales_invoice"))
        self.client.force_login(self.doctor)
        response = self.client.post(f"{reverse('medical:vaccination_action', args=[dose.pk, 'bill'])}?lang=ar", {"lang": "ar"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(SalesInvoice.objects.count(), 0)
        page = self.client.get(f"{reverse('medical:animal', args=[self.rocky.pk])}?lang=ar")
        self.assertNotContains(page, "data-bill")  # the button is not offered to someone who may not use it
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(f"{reverse('medical:vaccination_action', args=[dose.pk, 'bill'])}?lang=ar", {"lang": "ar"}).status_code, 302)
        self.assertEqual(SalesInvoice.objects.count(), 1)

    def test_a_doctor_removes_only_their_own_dose(self):
        theirs = self.dose(user=self.owner)
        self.client.force_login(self.doctor)
        response = self.client.post(f"{reverse('medical:vaccination_action', args=[theirs.pk, 'remove'])}?lang=ar", {"lang": "ar"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Vaccination.objects.filter(pk=theirs.pk).exists())
        page = self.client.get(f"{reverse('medical:animal', args=[self.rocky.pk])}?lang=ar")
        self.assertNotContains(page, "data-remove")  # the button is not even offered

    def test_a_non_vet_business_has_none_of_it(self):
        from settings_core.models import ClientProfile

        targets = self.targets()
        ClientProfile.objects.update(sub_activity_slug="clinic")
        for method, url, data in targets:
            with self.subTest(url=url, method=method):
                self.assertEqual(getattr(self.client, method)(f"{url}?lang=ar", {"lang": "ar", **(data or {})}).status_code, 404)
        page = self.client.get(reverse("medical:patients") + "?lang=ar")
        self.assertNotContains(page, "data-tab-vaccinations")
        self.assertNotContains(page, "data-tab-vaccine-setup")

    def test_a_non_medical_business_has_none_of_it(self):
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        for url in (reverse("medical:vaccinations"), reverse("medical:vaccine_setup")):
            self.assertEqual(self.client.get(f"{url}?lang=ar").status_code, 404)


class EntityScreenTests(Screens):
    def setUp(self):
        from entities.current import working_in
        from entities.models import Entity, EntityMembership
        from entities.services import save_entity

        super().setUp()
        self.main = Entity.objects.get(is_main=True)
        self.branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي"}, self.owner)
        with working_in(self.branch):
            self.local = vaccines.save_vaccine({"name_ar": "سعار الفرع", "species": "dog", "interval_days": "365", "item": self.service_item}, self.owner)
            self.local_dose = vaccines.record(self.rocky, {"vaccine": self.local, "given_on": self.today - timedelta(days=30),
                                                           "next_due": (self.today + timedelta(days=3)).isoformat()}, self.owner)
        EntityMembership.objects.create(user=self.manager, entity=self.main, is_default=True)  # the manager works in the main entity only
        self.main_dose = self.dose(vaccine=self.deworm, days_ago=1)

    def test_another_entitys_dose_and_vaccine_answer_404_before_anything_runs(self):
        self.client.force_login(self.manager)
        for action in ("bill", "remove"):
            self.assertEqual(self.post("medical:vaccination_action", [self.local_dose.pk, action]).status_code, 404)
        self.assertEqual(self.post("medical:vaccinate", [self.rocky.pk], {"vaccine": self.local.pk}).status_code, 404)  # a crafted vaccine id
        self.assertEqual(self.post("medical:vaccine_setup", [], {"vaccine": self.local.pk, "name_ar": "اخترقت"}).status_code, 404)
        self.local.refresh_from_db()
        self.assertEqual(self.local.name_ar, "سعار الفرع")
        self.assertTrue(Vaccination.objects.filter(pk=self.local_dose.pk).exists())
        self.assertEqual(SalesInvoice.objects.count(), 0)
        self.assertEqual(self.post("medical:vaccination_action", [self.main_dose.pk, "remove"]).status_code, 302)  # its own entity's: allowed

    def test_the_due_list_and_the_set_up_keep_to_the_entity(self):
        self.client.force_login(self.manager)
        page = self.client.get(reverse("medical:vaccinations") + "?lang=ar")
        self.assertNotContains(page, f'data-vx-row="{self.local_dose.pk}"')
        self.assertNotContains(self.client.get(reverse("medical:vaccine_setup") + "?lang=ar"), "سعار الفرع")
        self.assertNotContains(self.client.get(reverse("medical:vaccinate", args=[self.rocky.pk]) + "?lang=ar"), "سعار الفرع")
        self.client.force_login(self.owner)
        session = self.client.session
        from entities.current import SESSION_KEY

        session[SESSION_KEY] = self.branch.pk
        session.save()
        page = self.client.get(reverse("medical:vaccinations") + "?lang=ar")
        self.assertContains(page, f'data-vx-row="{self.local_dose.pk}"')
        self.assertNotContains(page, f'data-vx-row="{self.main_dose.pk}"')

    def test_the_audit_trail_names_who_did_what(self):
        self.post("medical:vaccination_action", [self.main_dose.pk, "bill"])
        actions = list(AuditLog.objects.filter(module="medical", action__in=("save_vaccine", "record_vaccination", "bill_vaccination")).order_by("pk").values_list("action", flat=True))
        self.assertEqual(actions.count("bill_vaccination"), 1)
        self.assertIn("record_vaccination", actions)
        self.assertIn("save_vaccine", actions)


# ---- the scanners -------------------------------------------------------------------------


class ScannerTests(TestCase):
    TEMPLATES = ("_vaccination_card.html", "_vaccine_fields.html", "vaccination_form.html", "vaccinations.html", "vaccine_setup.html")

    def test_no_template_pre_fills_a_data_label(self):
        for name in self.TEMPLATES:
            source = Path(settings.BASE_DIR, "templates/medical", name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertNotRegex(source, PRE_LABELLED)
                self.assertNotIn("data-label", source)  # not even in a comment: the table-cards script writes them

    def test_the_list_template_uses_the_shared_row_roles_and_a_versioned_stylesheet(self):
        source = Path(settings.BASE_DIR, "templates/medical/vaccinations.html").read_text(encoding="utf-8")
        self.assertIn("data-list-rows", source)
        for role in ("title", "sub", "meta", "action", "empty"):
            self.assertIn(f'data-row="{role}"', source)
        self.assertIn("vaccines.css' %}?v=m7-1", source)

    def test_the_stylesheet_uses_tokens_only_and_every_token_is_defined(self):
        css = Path(settings.BASE_DIR, "static/hesba/css/vaccines.css").read_text(encoding="utf-8")
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(css, r"rgba?\(")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", Path(settings.BASE_DIR, "static/hesba/css/tokens.css").read_text(encoding="utf-8")))
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertTrue(used)
        self.assertEqual(sorted(used - defined), [])
        self.assertNotRegex(css, r"font-weight:\s*[89]00")

    def test_no_screen_writes_a_money_posting_stock_or_ledger_call(self):
        for name in ("vaccines.py", "views_vaccines.py"):
            source = Path(settings.BASE_DIR, "medical", name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                for forbidden in ("post_sales_invoice", "StockMovement", "CashboxMovement", "LedgerEntry", "JournalEntry", "average_cost", "post_"):
                    self.assertNotIn(forbidden, source)


# ---- the migration on a database that already holds data ---------------------------------------


class MigrationTests(TransactionTestCase):
    """0014 only adds two tables, after 0013; animals and visits that exist are untouched."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_depends_on_0013_and_adds_two_tables_without_touching_data(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("medical", "0014_vaccinations")]
        self.assertIn(("medical", "0013_inpatient"), node.dependencies)
        self.assertEqual([type(op).__name__ for op in node.operations], ["CreateModel", "CreateModel"])
        executor.migrate([("medical", "0013_inpatient")])
        old = executor.loader.project_state([n for n in executor.loader.graph.leaf_nodes() if n[0] != "medical"] + [("medical", "0013_inpatient")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_vx")
        owner = old.get_model("master_data", "Customer").objects.create(customer_code="P-1", name="منى")
        animal = old.get_model("medical", "Animal").objects.create(owner=owner, name="روكي", species="dog", created_by=user)
        old.get_model("medical", "ClinicalVisit").objects.create(patient=owner, animal=animal, visit_date=date(2026, 11, 1), diagnosis="برد", treatment="راحة", created_by=user)
        self.assertNotIn("medical_vaccine", connection.introspection.table_names())
        self.assertNotIn("medical_vaccination", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("medical", "0014_vaccinations")])
        names = connection.introspection.table_names()
        self.assertIn("medical_vaccine", names)
        self.assertIn("medical_vaccination", names)
        self.assertEqual(list(ClinicalVisit.objects.values_list("diagnosis", "treatment")), [("برد", "راحة")])
        self.assertEqual(vet.Animal.objects.get().name, "روكي")
        self.assertEqual((Vaccine.objects.count(), Vaccination.objects.count()), (0, 0))  # nothing is seeded by the migration


class MakeMigrationsCleanTests(TestCase):
    databases = {"default", "feedback"}  # makemigrations reads the history of every database

    def test_the_two_tables_are_the_only_change_to_the_schema(self):
        from django.core.management import call_command

        call_command("makemigrations", "--check", "--dry-run", verbosity=0)


class DemoSeedTests(TestCase):
    """A vet demo built the way ``prepare_demo_templates`` builds one: two vaccines, four doses, one overdue and one due this week.
    (No vet demo template exists yet; this pins what its data will hold.)"""

    def test_the_vet_demo_has_vaccines_and_doses_with_one_overdue_and_one_due_this_week(self):
        from settings_core.tests_demo_clinic import DemoClinicBookingTests

        DemoClinicBookingTests("fill").fill("medical", "vet")
        owner = get_user_model().objects.get(username="owner")
        self.assertEqual(sorted(Vaccine.objects.values_list("name_ar", flat=True)), ["ثلاثي القطط", "سعار"])
        self.assertEqual(Vaccination.objects.count(), 4)
        result = vaccines.due_list(owner)
        late = [row for row in result["rows"] if row["overdue"]]
        week = [row for row in result["rows"] if 0 <= row["days"] <= vaccines.WEEK_DAYS]
        self.assertEqual([(row["animal"].name, row["vaccine"].name_ar) for row in late], [("ريكس", "سعار")])
        self.assertEqual([(row["animal"].name, row["vaccine"].name_ar) for row in week], [("بيلا", "ثلاثي القطط")])
        self.assertEqual(SalesInvoice.objects.filter(vaccinations__isnull=False).count(), 0)  # nothing was billed
        self.assertTrue(Vaccination.objects.filter(visit__isnull=False).exists())
