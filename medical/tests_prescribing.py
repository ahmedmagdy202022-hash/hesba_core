"""M2 (HG-086): a doctor writes the prescription from the reference drug list.

The lines are clinical text saved with the visit by ``services.save_visit``: a chosen drug's names, ingredient,
strength and form are copied when it is first prescribed and never rewritten by a later import; a hand-written line
keeps only its name. The visit form searches the list through ``medical:drug_search`` (the pharmacy picker's search,
without prices, gated like every clinical screen). The allergy advice never stops a save. No stock, invoice, cashbox or
ledger row is ever made. All drug names here are invented, except the demo list's own rows.
"""

import re
import tempfile
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from unittest import mock

from django.apps import apps
from django.contrib.messages import get_messages
from django.contrib.staticfiles import finders
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from hesba_testing.factories import make_customer
from permissions.models import RoleCode
from pharmacy import picker
from pharmacy.models import DrugReference
from pharmacy.tests_reference_ui import ref
from reports.tests_dashboard import prepared_client

from . import prescribing, services
from .models import ClinicalVisit, VisitMedicine
from .tests import CLINIC, person
from .tests_access import NON_CLINICAL, Clinic

SEARCH = reverse("medical:drug_search")
TODAY = timezone.localdate()


def line(reference=None, name="", dose="", frequency="", duration="", route="", notes="", id=""):
    return {"id": str(id), "reference": str(reference.pk) if reference is not None else "", "name": name, "dose": dose, "frequency": frequency,
            "duration": duration, "route": route, "notes": notes}


def post_lines(lines):
    """The visit form's rx_<field> lists for ``lines`` (dicts as ``line`` makes them)."""

    return {f"rx_{field}": [raw.get(field, "") for raw in lines] for field in prescribing.FIELDS}


class Drugs:
    def make_drugs(self):
        self.zuba = ref("A1", "Zubacillin 500mg caps", "زوباسيلين 500 مجم", "Amoxicillin", "500 mg", "capsule", "16 caps", "62.50", TODAY, rx=True)
        self.quil = ref("P1", "Quilodol 500mg tab", "كويلودول 500", "Paracetamol", "500 mg", "tablet", "20 tabs", "35.00", TODAY)
        self.combo = ref("C1", "Augzub 1g tab", "أوجزوب 1 جم", "Amoxicillin + Clavulanic acid", "1 g", "tablet", "14 tabs", "120.00", TODAY)
        self.syrup = ref("S1", "Quilodol syrup", "كويلودول شراب", "Paracetamol", "120 mg/5 ml", "syrup", "100 ml", "20.00", TODAY)


class Setup(Drugs, TestCase):
    def setUp(self):
        from staff.services import save_employee

        prepared_client("medical", "clinic", CLINIC)
        self.owner = person(RoleCode.OWNER, "rx_owner")
        self.doctor = person(RoleCode.DOCTOR, "rx_doctor")
        self.employee = save_employee({"name": "د. سارة", "title": "طبيبة"}, self.owner)
        self.employee.user = self.doctor
        self.employee.save()
        self.patient = make_customer(customer_code="P-1", name="منى علي")
        self.make_drugs()

    def save(self, lines, user=None, visit=None, lang="ar", **fields):
        data = {"diagnosis": "التهاب حلق", "visit_date": TODAY.isoformat(), **fields, "medicines": lines}
        return services.save_visit(self.patient, data, user or self.doctor, visit, lang)

    def allergic(self, text):
        services.save_profile(services.file_for(self.patient), {"allergies": text}, self.owner)


# ---- the table ----

class ModelTests(Setup):
    def test_fields_and_order(self):
        names = {field.name for field in VisitMedicine._meta.get_fields()}
        self.assertEqual(names, {"id", "visit", "position", "reference", "name_ar", "name_en", "active_ingredient", "strength", "form", "dose",
                                 "frequency", "duration", "route", "notes"})
        self.assertEqual(VisitMedicine._meta.get_field("visit").remote_field.on_delete.__name__, "CASCADE")
        self.assertEqual(VisitMedicine._meta.get_field("reference").remote_field.on_delete.__name__, "SET_NULL")
        visit = self.save([line(self.zuba, dose="كبسولة"), line(name="فيتامين د", dose="نقطتين")])
        second, first = VisitMedicine.objects.order_by("-position")
        VisitMedicine.objects.filter(pk=first.pk).update(position=9)  # ordering follows position, not the id
        self.assertEqual(list(visit.medicines.all()), [second, VisitMedicine.objects.get(pk=first.pk)])
        self.assertEqual(VisitMedicine.objects.get(pk=first.pk).name("en"), "Zubacillin 500mg caps")

    def test_deleting_the_visit_deletes_its_lines_but_never_the_reference_row(self):
        visit = self.save([line(self.zuba, dose="كبسولة")])
        visit.delete()
        self.assertFalse(VisitMedicine.objects.exists())
        self.assertTrue(DrugReference.objects.filter(pk=self.zuba.pk).exists())


class MigrationTests(TransactionTestCase):
    """0011 only adds the table: a database with visits migrates forward and back without touching them."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_forward_on_existing_visits_is_a_no_op(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("medical", "0010_hg084_clinician_access")])
        old = executor.loader.project_state([node for node in executor.loader.graph.leaf_nodes() if node[0] != "medical"]
                                            + [("medical", "0010_hg084_clinician_access")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_rx")
        patient = old.get_model("master_data", "Customer").objects.create(customer_code="P-1", name="منى")
        old.get_model("medical", "ClinicalVisit").objects.create(patient=patient, visit_date=date(2026, 11, 1), diagnosis="برد",
                                                                 treatment="راحة", created_by=user)
        self.assertNotIn("medical_visitmedicine", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("medical", "0011_visit_medicine")])
        self.assertIn("medical_visitmedicine", connection.introspection.table_names())
        self.assertEqual(list(ClinicalVisit.objects.values_list("diagnosis", "treatment")), [("برد", "راحة")])
        self.assertEqual(VisitMedicine.objects.count(), 0)


# ---- writing the lines ----

class SaveVisitTests(Setup):
    def test_lines_from_the_list_and_by_hand_saved_with_the_visit(self):
        visit = self.save([line(self.zuba, dose="كبسولة", frequency="3 مرات يوميًا", duration="5 أيام", notes="بعد الأكل"),
                           line(name="Vitaminex D 2800", dose="نقطتين", frequency="مرة يوميًا"), line(name="غسول فم", duration="أسبوع"),
                           line(self.quil, dose="قرص", frequency="عند اللزوم", route="بالفم")])
        rows = list(visit.medicines.values_list("position", "reference", "name_ar", "name_en", "active_ingredient", "strength", "form", "dose",
                                                "frequency", "duration", "route", "notes"))
        self.assertEqual(rows, [
            (1, self.zuba.pk, "زوباسيلين 500 مجم", "Zubacillin 500mg caps", "Amoxicillin", "500 mg", "capsule", "كبسولة", "3 مرات يوميًا", "5 أيام", "", "بعد الأكل"),
            (2, None, "", "Vitaminex D 2800", "", "", "", "نقطتين", "مرة يوميًا", "", "", ""),  # by hand: only the typed name
            (3, None, "غسول فم", "", "", "", "", "", "", "أسبوع", "", ""),
            (4, self.quil.pk, "كويلودول 500", "Quilodol 500mg tab", "Paracetamol", "500 mg", "tablet", "قرص", "عند اللزوم", "", "بالفم", ""),
        ])
        audit = AuditLog.objects.filter(action="create_clinical_visit", object_id=str(visit.pk)).get()
        self.assertEqual(audit.after_data["medicines"], 4)

    def test_update_remove_reorder_and_add_in_one_save(self):
        visit = self.save([line(self.zuba, dose="كبسولة"), line(self.quil, dose="قرص"), line(name="غسول فم")])
        zuba, quil, wash = visit.medicines.all()
        self.save([line(self.quil, dose="قرصين", id=quil.pk), line(name="غسول فم بالملح", id=wash.pk), line(self.syrup, dose="5 مل")], visit=visit)
        rows = list(visit.medicines.values_list("pk", "position", "name_ar", "dose"))
        self.assertEqual(rows[:2], [(quil.pk, 1, "كويلودول 500", "قرصين"), (wash.pk, 2, "غسول فم بالملح", "")])  # same rows, new order
        self.assertEqual(rows[2][1:], (3, "كويلودول شراب", "5 مل"))
        self.assertFalse(VisitMedicine.objects.filter(pk=zuba.pk).exists())  # left out: removed
        self.save([], visit=visit)  # every line taken off
        self.assertFalse(visit.medicines.exists())

    def test_without_the_medicines_key_the_lines_stay_as_they_are(self):
        visit = self.save([line(self.zuba, dose="كبسولة")])
        services.save_visit(self.patient, {"diagnosis": "التهاب حلق شديد"}, self.doctor, visit)
        self.assertEqual(visit.medicines.count(), 1)
        visit.medicines.all().delete()
        with self.assertRaises(ValidationError):  # and a visit with nothing at all is still refused
            services.save_visit(self.patient, {"diagnosis": ""}, self.doctor, visit)

    def test_a_visit_with_only_medicines_is_enough(self):
        visit = self.save([line(self.quil, dose="قرص")], diagnosis="")
        self.assertEqual((visit.diagnosis, visit.medicines.count()), ("", 1))
        with self.assertRaises(ValidationError) as refused:
            self.save([line()], diagnosis="")  # a blank line does not count
        self.assertIn("ضيف دوا", refused.exception.messages[0])

    def test_blank_rows_are_skipped(self):
        visit = self.save([line(), line(self.zuba, dose="كبسولة"), line(name="   ")])
        self.assertEqual(list(visit.medicines.values_list("position", "reference")), [(1, self.zuba.pk)])

    def test_refusals_in_both_languages_and_nothing_is_saved(self):
        for lang, words in (("ar", "الدوا رقم 2: اكتب اسمه أو اختاره من القايمة."), ("en", "Medicine 2: type its name or choose it from the list.")):
            with self.subTest(lang=lang), self.assertRaises(ValidationError) as refused:
                self.save([line(self.zuba), line(dose="قرص", frequency="مرتين")], lang=lang, diagnosis="ZZNOTSAVED")
            self.assertEqual(refused.exception.messages, [words])
        with self.assertRaises(ValidationError) as refused:
            self.save([line(name=f"دوا {n}") for n in range(21)], lang="en")
        self.assertEqual(refused.exception.messages, ["A prescription takes up to 20 medicines."])
        for bad in ("medicines", None, [["x"]], {"name": "x"}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError) as refused:
                self.save(bad, diagnosis="ZZNOTSAVED")
            self.assertEqual(refused.exception.messages, ["الأدوية مش مظبوطة؛ جرّب تاني."])
        self.assertFalse(ClinicalVisit.objects.filter(diagnosis="ZZNOTSAVED").exists())
        self.assertFalse(VisitMedicine.objects.exists())

    def test_over_long_and_odd_text_is_cut_and_cleaned(self):
        visit = self.save([line(name="x" * 400, dose="قرص\x00" + "y" * 100, frequency="z" * 200, notes="n" * 600)])
        medicine = visit.medicines.get()
        self.assertEqual((len(medicine.name_en), len(medicine.dose), len(medicine.frequency), len(medicine.notes)), (255, 60, 80, 255))
        self.assertNotIn("\x00", medicine.dose)

    def test_the_visit_and_its_lines_land_together_or_not_at_all(self):
        visit = self.save([line(self.zuba, dose="كبسولة")])
        real = VisitMedicine.save
        calls = []

        def failing(instance, *args, **kwargs):
            calls.append(instance)
            if len(calls) == 2:
                raise RuntimeError("disk full")
            return real(instance, *args, **kwargs)

        with mock.patch.object(VisitMedicine, "save", failing), self.assertRaises(RuntimeError):
            self.save([line(self.quil, dose="قرص"), line(self.syrup, dose="5 مل")], diagnosis="ZZROLLEDBACK")
        self.assertFalse(ClinicalVisit.objects.filter(diagnosis="ZZROLLEDBACK").exists())
        calls.clear()
        with mock.patch.object(VisitMedicine, "save", failing), self.assertRaises(RuntimeError):
            self.save([line(self.quil, dose="قرص"), line(self.syrup, dose="5 مل")], visit=visit, diagnosis="ZZROLLEDBACK")
        visit.refresh_from_db()
        self.assertEqual((visit.diagnosis, list(visit.medicines.values_list("reference", flat=True))), ("التهاب حلق", [self.zuba.pk]))
        self.assertFalse(AuditLog.objects.filter(action="update_clinical_visit").exists())

    def test_a_line_id_from_another_visit_makes_a_new_line(self):
        other = self.save([line(self.zuba, dose="كبسولة")])
        theirs = other.medicines.get()
        mine = self.save([line(self.quil, dose="قرص", id=theirs.pk)])
        self.assertNotEqual(mine.medicines.get().pk, theirs.pk)
        theirs.refresh_from_db()
        self.assertEqual((theirs.visit_id, theirs.reference_id, theirs.dose), (other.pk, self.zuba.pk, "كبسولة"))
        twice = self.save([line(self.quil, dose="قرص"), line(self.quil, dose="قرصين")])  # the same drug twice is the doctor's call
        self.assertEqual(twice.medicines.count(), 2)

    def test_a_retired_or_unknown_reference_becomes_a_line_by_hand(self):
        self.quil.is_active = False
        self.quil.save()
        visit = self.save([line(self.quil, name="Quilodol 500mg tab", dose="قرص"), {**line(name="Zubacillin"), "reference": "99999999"},
                           {**line(name="x"), "reference": "1; drop"}])
        self.assertEqual(list(visit.medicines.values_list("reference", "name_en", "active_ingredient")),
                         [(None, "Quilodol 500mg tab", ""), (None, "Zubacillin", ""), (None, "x", "")])

    def test_lines_follow_the_visit_rules(self):
        visit = self.save([line(self.zuba, dose="كبسولة")], user=self.owner)
        with self.assertRaises(ValidationError):  # another doctor's visit (HG-084: write_records stays practice-wide)
            self.save([], visit=visit)
        self.assertEqual(visit.medicines.count(), 1)
        manager = person(RoleCode.MANAGER, "rx_manager")
        self.save([line(self.quil, dose="قرص")], user=manager, visit=visit)
        self.assertEqual(list(visit.medicines.values_list("reference", flat=True)), [self.quil.pk])


class SnapshotTests(Setup):
    """A prescription keeps the drug as it was when it was written."""

    def setUp(self):
        super().setUp()
        self.visit = self.save([line(self.zuba, dose="كبسولة", frequency="3 مرات يوميًا", duration="5 أيام")])
        self.medicine = self.visit.medicines.get()
        self.before = self.snapshot()

    def snapshot(self):
        return VisitMedicine.objects.filter(pk=self.medicine.pk).values("name_ar", "name_en", "active_ingredient", "strength", "form").get()

    def resave_through_the_form(self, **change):
        self.client.force_login(self.doctor)
        raw = line(self.zuba if self.medicine.reference_id else None, name=self.medicine.name("ar"), dose="كبسولة", frequency="3 مرات يوميًا",
                   duration="7 أيام", id=self.medicine.pk)
        raw.update(change)
        response = self.client.post(reverse("medical:visit_edit", args=[self.visit.pk]),
                                    {"diagnosis": "التهاب حلق", "visit_date": TODAY.isoformat(), **post_lines([raw])})
        self.assertEqual(response.status_code, 302)
        return self.visit.medicines.get()

    def printed(self):
        self.client.force_login(self.doctor)
        return self.client.get(reverse("medical:prescription", args=[self.visit.pk])).content.decode()

    def test_a_renamed_row_and_a_re_import_never_rewrite_it(self):
        from pharmacy.reference import import_file

        DrugReference.objects.filter(pk=self.zuba.pk).update(name_en="Zubacillin NEW", strength="1 g", active_ingredient="Amoxicillin trihydrate")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "list.csv"
            path.write_text("source_ref,name_en,name_ar,active_ingredient,strength,form\nA1,Zubacillin Forte,زوباسيلين فورت,Amoxicillin,875 mg,tablet\n",
                            encoding="utf-8")
            import_file(path, source="test-list", source_version="v2", layout="hesba")
        self.zuba.refresh_from_db()
        self.assertEqual(self.zuba.name_en, "Zubacillin Forte")  # the list changed...
        saved = self.resave_through_the_form()
        self.assertEqual(self.snapshot(), self.before)  # ...the prescription did not, even saved again
        self.assertEqual((saved.reference_id, saved.duration), (self.zuba.pk, "7 أيام"))
        self.assertIn("زوباسيلين 500 مجم", self.printed())
        self.assertNotIn("فورت", self.printed())

    def test_a_deleted_row_leaves_the_text_and_an_empty_reference(self):
        self.zuba.delete()
        self.medicine.refresh_from_db()
        self.assertIsNone(self.medicine.reference_id)
        self.assertEqual(self.snapshot(), self.before)
        saved = self.resave_through_the_form()  # posts reference="" for the line: still the same copy, not a line by hand
        self.assertEqual((self.snapshot(), saved.duration), (self.before, "7 أيام"))
        self.assertTrue(prescribing.display(saved, "ar")["from_list"])
        self.assertIn("Amoxicillin", self.printed())

    def test_a_row_deleted_while_the_form_is_open_keeps_the_copy(self):
        # The form was opened while the row existed, so it still posts the row's id; the stored line already lost its
        # reference (SET_NULL). Saving must not turn the line into a hand-written one and blank what was prescribed.
        stale = str(self.zuba.pk)
        self.zuba.delete()
        saved = self.resave_through_the_form(reference=stale)
        self.assertIsNone(saved.reference_id)
        self.assertEqual((self.snapshot(), saved.duration), (self.before, "7 أيام"))
        self.assertTrue(prescribing.display(saved, "ar")["from_list"])
        self.assertIn("Amoxicillin", self.printed())

    def test_choosing_another_drug_on_the_line_copies_again(self):
        saved = self.resave_through_the_form(reference=str(self.quil.pk))
        self.assertEqual((saved.pk, saved.reference_id, saved.name_en, saved.form), (self.medicine.pk, self.quil.pk, "Quilodol 500mg tab", "tablet"))

    def test_a_hand_written_line_can_be_renamed_and_is_never_blanked(self):
        visit = self.save([line(name="غسول فم", dose="مضمضة")])
        hand = visit.medicines.get()
        self.save([line(name="غسول فم بالملح", dose="مضمضة", id=hand.pk)], visit=visit)
        hand.refresh_from_db()
        self.assertEqual((hand.name_ar, hand.name_en), ("غسول فم بالملح", ""))
        self.save([line(name="Mouthwash", id=hand.pk)], visit=visit)
        hand.refresh_from_db()
        self.assertEqual((hand.name_ar, hand.name_en), ("", "Mouthwash"))
        self.save([line(name="", dose="مضمضة", id=hand.pk)], visit=visit)  # a cleared name keeps the old one
        hand.refresh_from_db()
        self.assertEqual(hand.name_en, "Mouthwash")


# ---- the search endpoint ----

class SearchEndpointTests(Drugs, Clinic):
    def setUp(self):
        super().setUp()
        self.make_drugs()

    def search(self, who, q, **extra):
        self.client.force_login(self.users[who])
        return self.client.get(SEARCH, {"q": q, **extra})

    def test_who_may_search(self):
        for who in ("owner", "manager", "doctor"):
            with self.subTest(who=who):
                self.assertEqual(self.search(who, "zuba").status_code, 200)
        for who in NON_CLINICAL:  # a cashier, a linked receptionist or stock keeper, a login with no role, a switched-off doctor
            with self.subTest(who=who):
                self.assertEqual(self.search(who, "zuba").status_code, 403)
        self.client.logout()
        anonymous = self.client.get(SEARCH, {"q": "zuba"})
        self.assertEqual(anonymous.status_code, 302)
        self.assertTrue(anonymous["Location"].startswith(reverse("login")))
        self.client.force_login(self.users["doctor"])
        self.assertEqual(self.client.post(SEARCH, {"q": "zuba"}).status_code, 405)
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        self.assertEqual(self.search("owner", "zuba").status_code, 404)

    def test_the_same_rows_as_the_pharmacy_search_without_prices(self):
        for q in ("zuba", "زوباسيلين", "amoxicillin", "quil", "كويلودول", "zz", "z", "%%"):
            with self.subTest(q=q):
                data = self.search("doctor", q).json()
                self.assertEqual([row["id"] for row in data["results"]], [row.pk for row in picker.search_reference(q)[1]])
        row = self.search("doctor", "zubacillin").json()["results"][0]
        self.assertEqual(set(row), {"id", "name_ar", "name_en", "ingredient", "strength", "form", "pack", "manufacturer", "barcode",
                                    "prescription_only", "is_drug", "source", "allergy"})
        self.assertEqual((row["form"], row["allergy"]), ({"code": "capsule", "label": "كبسول"}, []))
        self.assertNotIn("62.50", self.search("doctor", "zubacillin").content.decode())
        self.assertEqual(self.search("doctor", "zubacillin", lang="en").json()["results"][0]["form"]["label"], "Capsules")
        short = self.search("doctor", "z").json()
        self.assertEqual((short["too_short"], short["results"], short["list_empty"]), (True, [], False))

    def test_no_drugs_capability_or_manage_items_needed(self):
        from permissions.services import user_has_permission
        from settings_core import capabilities as caps

        self.assertFalse(user_has_permission(self.users["doctor"], "master_data.manage_items"))
        self.assertFalse(caps.capability_enabled("drugs"))
        self.assertEqual(self.search("doctor", "zuba").json()["count"], 1)

    def test_never_kept_in_a_cache(self):
        response = self.search("doctor", "zuba")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("private", response["Cache-Control"])

    def test_an_empty_list_is_said_so(self):
        DrugReference.objects.all().delete()
        data = self.search("doctor", "zuba").json()
        self.assertEqual((data["count"], data["list_empty"]), (0, True))
        self.client.force_login(self.users["doctor"])
        page = self.client.get(reverse("medical:visit_new", args=[self.patient.pk]))
        self.assertContains(page, "data-rx-nolist")
        self.assertNotContains(page, "data-rx-add")
        self.assertContains(page, "data-rx-hand")

    def test_allergy_flags_for_the_patient_only(self):
        self.assertEqual(self.search("doctor", "amoxicillin").json()["results"][0]["allergy"], [])  # ZZALLERGY matches nothing
        services.save_profile(services.file_for(self.patient), {"allergies": "أموكسيسيلين, amoxicillin"}, self.owner)
        flagged = self.search("doctor", "amoxicillin", patient=self.patient.pk).json()["results"]
        self.assertEqual({row["id"]: row["allergy"] for row in flagged}, {self.zuba.pk: ["Amoxicillin"], self.combo.pk: ["Amoxicillin"]})
        for odd in ("", "abc", "999999", "-1"):
            with self.subTest(patient=odd):
                response = self.search("doctor", "amoxicillin", patient=odd)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(all(row["allergy"] == [] for row in response.json()["results"]))


# ---- the allergy advice ----

class AllergyRuleTests(TestCase):
    def terms(self, allergies, ingredient="", *names):
        return prescribing.allergy_terms(prescribing.allergy_keys(allergies), ingredient, names)

    def test_matching(self):
        self.assertEqual(self.terms("Amoxicillin", "Amoxicillin"), ["Amoxicillin"])
        self.assertEqual(self.terms("AMOXICILLIN.", "amoxicillin"), ["amoxicillin"])  # case and punctuation
        self.assertEqual(self.terms("حساسية من clavulanic acid", "Amoxicillin + Clavulanic acid"), ["Clavulanic acid"])  # one part of a combination
        self.assertEqual(self.terms("بنسلين، و aspirin", "Acetylsalicylic acid", "Aspirin Protect 100mg"), ["Aspirin"])  # the trade name
        self.assertEqual(self.terms("بنسلين، وأسبرين", "Acetylsalicylic acid", "Aspirin Protect 100mg", "أسبرين بروتكت 100 مجم"), ["أسبرين"])  # «و» before it
        self.assertEqual(self.terms("أموكسيل", "", "اموكسيل 500 مجم"), ["اموكسيل"])  # hamza folded
        self.assertEqual(self.terms("حساسية من الأموكسيل", "", "أموكسيل 500 مجم"), ["أموكسيل"])  # «ال» before the word
        self.assertEqual(self.terms("بنادول", "", "بَنادول إكسترا"), ["بَنادول"])  # harakat
        self.assertEqual(self.terms("vitamin c", "Vitamin C"), ["Vitamin C"])
        self.assertEqual(self.terms("ZUBACILLIN", "Amoxicillin", "Zubacillin 500mg caps", "زوباسيلين 500 مجم"), ["Zubacillin"])

    def test_no_guessing_and_no_false_positive(self):
        for allergies, ingredient, names in (
                ("بنسلين", "Amoxicillin", ("Amoxil 500mg",)),  # a drug class is not inferred
                ("penicillin", "Amoxicillin", ()),
                ("Aspirin", "Acetylsalicylic acid", ("Jusprin 81mg",)),  # nor a synonym
                ("حساسية من الغبار والبيض", "Paracetamol", ("Panadol 500mg",)),
                ("amox", "Amoxicillin", ("Amoxil",)),  # a part of a word is not the word
                ("لا يوجد", "Paracetamol", ("بنادول",)),
                ("", "Amoxicillin", ("Amoxil",)),
                ("500 mg", "", ("Panadol 500mg",))):
            with self.subTest(allergies=allergies, ingredient=ingredient):
                self.assertEqual(self.terms(allergies, ingredient, *names), [])


class AllergyOnTheFormTests(Setup):
    def setUp(self):
        super().setUp()
        self.allergic("حساسية من الأموكسيسيلين و Amoxicillin")
        self.client.force_login(self.doctor)

    def test_warned_on_the_line_on_save_and_on_the_saved_visit_but_never_blocked(self):
        url = reverse("medical:visit_new", args=[self.patient.pk])
        response = self.client.post(url, {"diagnosis": "التهاب حلق", "visit_date": TODAY.isoformat(),
                                          **post_lines([line(self.zuba, dose="كبسولة"), line(self.quil, dose="قرص")])}, follow=True)
        visit = ClinicalVisit.objects.get(diagnosis="التهاب حلق")
        self.assertEqual(visit.medicines.count(), 2)  # saved anyway
        warnings = [str(m) for m in get_messages(response.wsgi_request)] + re.findall(r"تنبيه حساسية في الروشتة[^<]*", response.content.decode())
        self.assertTrue(any("زوباسيلين 500 مجم («Amoxicillin»)" in text for text in warnings), warnings)
        self.assertFalse(any("كويلودول" in text for text in warnings))
        self.assertContains(response, "data-visit-medicine-allergy", count=1)  # on the saved visit, beside that line only
        self.assertContains(response, "⚠ تنبيه حساسية: «Amoxicillin» مكتوب في الحساسية المسجّلة.")
        form = self.client.get(reverse("medical:visit_edit", args=[visit.pk]))
        self.assertContains(form, '<p class="rx-alert" role="alert" data-rx-alert>', count=1)  # the reopened form shows it on the line
        english = self.client.get(reverse("medical:file", args=[self.patient.pk]) + "?lang=en")
        self.assertContains(english, "Allergy alert: «Amoxicillin» is in the recorded allergies.")

    def test_a_refused_save_keeps_the_lines_and_their_warning(self):
        response = self.client.post(reverse("medical:visit_new", args=[self.patient.pk]),
                                    {"diagnosis": "x", "pulse": "999", **post_lines([line(self.zuba, dose="كبسولة", frequency="مرتين")])})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-visit-error")
        self.assertContains(response, 'value="كبسولة"')
        self.assertContains(response, "زوباسيلين 500 مجم")
        self.assertContains(response, '<p class="rx-alert" role="alert" data-rx-alert>', count=1)
        self.assertFalse(VisitMedicine.objects.exists())

    def test_a_vet_visit_checks_the_animal_not_the_owner(self):
        from . import vet

        from settings_core.models import ClientProfile

        ClientProfile.objects.update(sub_activity_slug="vet")
        cat = vet.save_animal(self.patient, {"name": "مشمش", "species": "cat", "allergies": "Paracetamol"}, self.owner)
        dog = vet.save_animal(self.patient, {"name": "روكي", "species": "dog"}, self.owner)
        stranger = make_customer(customer_code="P-9", name="غريب")
        foreign = vet.save_animal(stranger, {"name": "بسبس", "species": "cat", "allergies": "Paracetamol"}, self.owner)
        for animal, flagged in ((cat, True), (dog, False), (foreign, False)):  # the owner's own Amoxicillin allergy never counts on a vet install
            with self.subTest(animal=animal.name):
                rows = self.client.get(SEARCH, {"q": "quil", "patient": self.patient.pk, "animal": animal.pk}).json()["results"]
                self.assertEqual(rows[0]["allergy"], ["Paracetamol"] if flagged else [])
                self.assertEqual(self.client.get(SEARCH, {"q": "zuba", "patient": self.patient.pk, "animal": animal.pk}).json()["results"][0]["allergy"], [])
        response = self.client.post(reverse("medical:visit_new", args=[self.patient.pk]),
                                    {"animal": cat.pk, "diagnosis": "حمى", **post_lines([line(self.syrup, dose="1 مل")])}, follow=True)
        visit = ClinicalVisit.objects.get(animal=cat)
        self.assertEqual(visit.medicines.count(), 1)
        self.assertContains(response, "data-visit-medicine-allergy")
        card = self.client.get(reverse("medical:animal", args=[cat.pk]))
        self.assertContains(card, "data-visit-medicine-allergy")
        self.assertContains(self.client.get(reverse("medical:prescription", args=[visit.pk])), "كويلودول شراب")


# ---- the visit form and the print ----

class FormTests(Setup):
    def test_the_form_offers_the_section_and_reuses_the_sheet_look(self):
        self.client.force_login(self.doctor)
        html = self.client.get(reverse("medical:visit_new", args=[self.patient.pk])).content.decode()
        self.assertIn(f'data-url="{SEARCH}"', html)
        self.assertIn(f'data-patient="{self.patient.pk}"', html)
        self.assertIn("hesba/css/drug_picker.css?v=r1-3", html)  # the R1 sheet's stylesheet, unchanged
        self.assertIn("hesba/js/rx_picker.js?v=m1-1", html)
        self.assertIn("hesba/css/rx_lines.css?v=m2-1", html)
        self.assertNotIn("drug_picker.js", html)  # the pharmacy's item form script is not loaded here
        self.assertRegex(html, r'<button class="rx-add" type="button" data-rx-add hidden')  # without JavaScript the buttons stay hidden
        self.assertRegex(html, r'<input id="rx-picker-q" type="search" form="rx-picker-detached"')  # never posted with the visit
        self.assertIn("<template data-rx-template>", html)
        self.assertIn('id="rx-words"', html)
        self.assertNotRegex(html, r'<button(?![^>]*type="button")[^>]*data-rx')  # no button in the section submits the visit
        self.assertIn(prescribing.WORDS["ar"]["instructions"], html)

    def test_saving_through_the_form_and_reopening_it(self):
        self.client.force_login(self.doctor)
        response = self.client.post(reverse("medical:visit_new", args=[self.patient.pk]),
                                    {"diagnosis": "التهاب حلق", "treatment": "راحة", "visit_date": TODAY.isoformat(), "medicines": "forged",
                                     **post_lines([line(self.zuba, dose="كبسولة", frequency="3 مرات يوميًا", duration="5 أيام"),
                                                   line(name="غسول فم", dose="مضمضة", frequency="مرتين يوميًا")])})
        self.assertEqual(response.status_code, 302)
        visit = ClinicalVisit.objects.get()
        self.assertEqual(list(visit.medicines.values_list("position", "reference", "name_ar")),
                         [(1, self.zuba.pk, "زوباسيلين 500 مجم"), (2, None, "غسول فم")])
        page = self.client.get(reverse("medical:visit_edit", args=[visit.pk])).content.decode()
        self.assertIn('<input type="hidden" name="rx_name" value="زوباسيلين 500 مجم" data-rx-name>', page)  # from the list: not retyped
        self.assertIn('name="rx_name" value="غسول فم" placeholder=', page)  # by hand: the name stays editable
        self.assertIn("كبسولة · 3 مرات يوميًا · 5 أيام", page)
        card = self.client.get(reverse("medical:file", args=[self.patient.pk])).content.decode()
        self.assertIn("data-visit-medicines", card)
        self.assertIn("<b><bdi>زوباسيلين 500 مجم</bdi></b> · كبسول — <bdi>كبسولة · 3 مرات يوميًا · 5 أيام</bdi>", card)

    def test_a_reception_login_never_reaches_the_lines(self):
        visit = self.save([line(self.zuba, dose="ZZDOSE")])
        cashier = person(RoleCode.CASHIER, "rx_cashier")
        self.client.force_login(cashier)
        for url in (reverse("medical:visit_edit", args=[visit.pk]), reverse("medical:prescription", args=[visit.pk])):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(reverse("medical:visit_edit", args=[visit.pk]), {"diagnosis": "x", **post_lines([])}).status_code, 403)
        self.assertEqual(visit.medicines.get().dose, "ZZDOSE")


class PrintTests(Setup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.doctor)

    def printed(self, visit, lang="ar"):
        return self.client.get(reverse("medical:prescription", args=[visit.pk]) + f"?lang={lang}").content.decode()

    def test_the_lines_in_order_then_the_instructions(self):
        visit = self.save([line(self.zuba, dose="كبسولة", frequency="3 مرات يوميًا", duration="5 أيام", notes="بعد الأكل"),
                           line(name="غسول فم", dose="مضمضة", frequency="مرتين يوميًا", route="مضمضة بالفم"),
                           line(self.syrup, dose="5 مل", frequency="كل 8 ساعات")], treatment="راحة وسوائل")
        html = self.printed(visit)
        self.assertEqual(re.findall(r'data-rx-medicine="(\d+)"', html), ["1", "2", "3"])
        self.assertLess(html.index("زوباسيلين 500 مجم"), html.index("غسول فم"))
        self.assertLess(html.index("غسول فم"), html.index("كويلودول شراب"))
        self.assertIn('<b><bdi>زوباسيلين 500 مجم</bdi></b> · كبسول</div>', html)  # the name already says 500: not printed twice
        self.assertIn('<b><bdi>كويلودول شراب</bdi></b> <bdi dir="ltr">120 mg/5 ml</bdi> · شراب</div>', html)
        self.assertIn('<div class="med-ing"><bdi dir="ltr">Amoxicillin</bdi></div>', html)
        self.assertIn('<div class="med-how"><bdi>كبسولة · 3 مرات يوميًا · 5 أيام</bdi></div>', html)
        self.assertIn('<div class="med-notes"><bdi>بعد الأكل</bdi></div>', html)
        self.assertIn("<bdi>مضمضة · مرتين يوميًا · مضمضة بالفم</bdi>", html)
        self.assertIn('<h2 class="instr-title" data-rx-instructions>تعليمات</h2><div class="instr">راحة وسوائل</div>', html)
        self.assertNotIn('<div class="body">', html)
        self.assertNotIn("62.50", html)  # never a price
        english = self.printed(visit, "en")
        self.assertIn('<b><bdi>Zubacillin 500mg caps</bdi></b> · Capsules</div>', english)
        self.assertIn('<b><bdi>Quilodol syrup</bdi></b> <bdi dir="ltr">120 mg/5 ml</bdi> · Syrup</div>', english)
        self.assertIn(">Instructions</h2>", english)
        self.assertIn("<b><bdi>غسول فم</bdi></b>", english)  # a line typed in Arabic prints as typed

    def test_lines_without_instructions(self):
        html = self.printed(self.save([line(self.quil, dose="قرص")], diagnosis="صداع"))
        self.assertIn("data-rx-medicines", html)
        self.assertNotIn("data-rx-instructions", html)

    def test_a_visit_with_no_lines_prints_as_before(self):
        visit = services.save_visit(self.patient, {"diagnosis": "برد", "treatment": "كونكور 5 مجم\nمرة يوميًا"}, self.doctor)
        for lang in ("ar", "en"):
            html = self.printed(visit, lang)
            self.assertIn('<div class="big" aria-hidden="true">℞</div>\n<div class="body">كونكور 5 مجم\nمرة يوميًا</div>\n<footer>', html)
            self.assertIn("</style></head>", html)  # the extra stylesheet is only for a prescription with lines
            for absent in ("data-rx-medicines", "data-rx-instructions", ".rx .meds"):
                self.assertNotIn(absent, html)


# ---- nothing else moves ----

class NoSideEffectsTests(Setup):
    #: The rows a visit with medicines may write; every other table in the default database must stay exactly as it was.
    WRITTEN = {"medical.clinicalvisit", "medical.visitmedicine", "medical.patientfile", "audit.auditlog", "sessions.session"}

    def everything(self):
        from django.db import router

        return {model._meta.label_lower: list(model._default_manager.order_by("pk").values_list())
                for model in apps.get_models() if model._meta.managed and not model._meta.proxy and not model._meta.swapped
                and router.db_for_read(model) == "default" and model._meta.label_lower not in self.WRITTEN}

    def test_a_prescription_makes_no_stock_invoice_cashbox_or_ledger_row(self):
        self.client.force_login(self.doctor)  # the login itself writes last_login: before the picture
        before = self.everything()
        self.assertIn("inventory.stockmovement", before)
        response = self.client.post(reverse("medical:visit_new", args=[self.patient.pk]),
                                    {"diagnosis": "التهاب حلق", **post_lines([line(self.zuba, dose="كبسولة"), line(name="غسول فم")])})
        self.assertEqual(response.status_code, 302)
        visit = ClinicalVisit.objects.get()
        self.save([line(self.quil, dose="قرص")], visit=visit)
        self.client.get(reverse("medical:prescription", args=[visit.pk]))
        after = self.everything()
        self.assertEqual([label for label in before if before[label] != after[label]], [])  # stock, invoices, cashboxes, ledgers, the list
        self.assertEqual(VisitMedicine.objects.count(), 1)


# ---- the demo clinic ----

class DemoClinicTests(TestCase):
    def build(self):
        """The clinic's demo template as ``prepare_demo_templates`` builds it (``config.demo_templates._populate`` without
        VACUUM). Returns the sample visits' fields as they were before the list step, and the importer's summary."""

        from django.core.management import call_command

        from config.demo_templates import complete_demo_setup, demo_reference_list, seed_sample_business

        call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password="Demo-pass-1", verbosity=0)
        complete_demo_setup("medical", "clinic")
        call_command("seed_demo_users", password="Demo-pass-1", force=True, verbosity=0)
        self.assertTrue(seed_sample_business())
        fields = ("patient_id", "doctor_id", "visit_date", "follow_up_date", "complaint", "examination", "diagnosis", "blood_pressure", "pulse",
                  "temperature", "weight", "height", "created_by_id")
        before = {row["pk"]: row for row in ClinicalVisit.objects.values("pk", *fields)}
        return before, demo_reference_list("medical", "clinic")

    def test_the_demo_clinic_has_the_list_and_real_prescriptions(self):
        from pharmacy.demo import DEMO_SOURCE

        from .demo import prescribe_demo_visits

        before, summary = self.build()
        self.assertEqual(summary.source, DEMO_SOURCE)
        self.assertGreaterEqual(DrugReference.objects.filter(source=DEMO_SOURCE).count(), 90)
        prescribed = ClinicalVisit.objects.filter(medicines__isnull=False).distinct()
        self.assertEqual(prescribed.count(), 2)
        throat = prescribed.get(diagnosis="التهاب حلق")
        self.assertEqual(list(throat.medicines.values_list("reference__source_ref", "name_ar", "dose", "frequency", "duration")),
                         [("DEMO-026", "زيثروماكس 500 مجم", "قرص", "مرة يوميًا قبل الأكل", "3 أيام"),
                          ("DEMO-001", "بنادول 500 مجم", "قرص", "كل 8 ساعات", "عند اللزوم")])
        self.assertEqual((throat.complaint, throat.temperature, throat.treatment), ("كحة وسخونية", D("38.2"), "راحة وسوائل دافئة بكثرة"))
        pressure = prescribed.get(diagnosis="ارتفاع ضغط الدم")
        self.assertEqual((pressure.blood_pressure, pressure.pulse, pressure.medicines.get().reference.source_ref), ("150/95", 84, "DEMO-061"))
        after = {row["pk"]: row for row in ClinicalVisit.objects.values("pk", *next(iter(before.values())).keys() - {"pk"})}
        self.assertEqual(after, before)  # every other field of every visit is as the sample made it
        self.assertEqual(prescribing.saved_warning(throat, "ar"), "")  # the penicillin-allergic patient got a macrolide
        self.assertEqual(prescribe_demo_visits(), 0)  # idempotent
        self.assertEqual(VisitMedicine.objects.count(), 3)

    def test_the_demo_owner_reads_the_prescription(self):
        from django.contrib.auth import get_user_model

        self.build()
        visit = ClinicalVisit.objects.filter(medicines__isnull=False).distinct().get(diagnosis="التهاب حلق")
        self.client.force_login(get_user_model().objects.get(username="owner"))
        html = self.client.get(reverse("medical:prescription", args=[visit.pk])).content.decode()
        self.assertIn("زيثروماكس 500 مجم", html)
        self.assertIn("data-rx-instructions", html)
        self.assertIn("بنسلين", html)  # the file's allergy line is still printed once


class DemoTopUpTests(TestCase):
    def test_a_clinic_template_built_before_the_list_gets_it_on_its_next_top_up_once(self):
        from config import demo_templates as templates
        from pharmacy.demo import DEMO_SOURCE

        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(templates, "base_template", return_value=Path(folder) / "demo.sqlite3"), \
                mock.patch.object(templates, "copy_database", side_effect=lambda source, work: Path(work).write_bytes(Path(source).read_bytes())), \
                mock.patch.object(templates, "_with_db", side_effect=lambda path, work: work()), \
                mock.patch.object(templates, "top_up_current_db"):
            clinic = templates.populated_template("medical", "clinic")
            clinic.write_bytes(b"built before the list")
            self.assertEqual(templates.top_up_template("medical", "clinic"), clinic)
            loaded = DrugReference.objects.filter(source=DEMO_SOURCE).count()
            self.assertGreaterEqual(loaded, 90)
            self.assertEqual(templates.top_up_template("medical", "clinic"), clinic)
            self.assertEqual(DrugReference.objects.count(), loaded)
            self.assertIsNone(templates.top_up_demo_reference_list("medical", "clinic"))


# ---- the script and the stylesheet ----

class StaticTests(TestCase):
    def read(self, name):
        with open(finders.find(name), encoding="utf-8") as handle:
            return handle.read()

    def test_the_sheet_behaves_as_the_pharmacy_sheet(self):
        script = self.read("hesba/js/rx_picker.js")
        for pinned in ("if (query !== lastQuery && controller) { controller.abort(); controller = null; lastQuery = null; }",  # abort on typing
                       "function current() { return folded(q.value) === query; }",  # an answer for older text is dropped
                       "if (!force && query === lastQuery && !hiddenForTyping) { return; }",  # rows hidden while typing come back
                       "if (event.key === 'Escape') { event.preventDefault(); close(); }",
                       "n <= 10 ? 'count' : 'count_many'",
                       "if (data.list_empty) { show('nolist'); return; }",
                       "if (event.key !== 'Enter' || event.target.tagName !== 'INPUT') { return; }"):  # Enter in a line never saves the visit
            with self.subTest(pinned=pinned[:40]):
                self.assertIn(pinned, script)
        self.assertNotIn("innerHTML", script)  # list text is set as text, never as markup
        self.assertNotIn("public_price", script)

    def test_tokens_only(self):
        css = self.read("hesba/css/rx_lines.css")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", self.read("hesba/css/tokens.css")))
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", css), [])
        self.assertNotRegex(css, r"rgba?\(")
        used = set(re.findall(r"var\((--hs-[\w-]+)", css))
        self.assertEqual(sorted(used - defined), [])
        self.assertIn("min-height:var(--hs-control-height)", css)  # 44px targets for the quick picks
        print_css = re.search(r"<style>(\.rx \.meds.*?)</style>", open(Path(__file__).resolve().parent.parent / "templates/medical/prescription.html",
                                                                             encoding="utf-8").read()).group(1)
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", print_css), [])
