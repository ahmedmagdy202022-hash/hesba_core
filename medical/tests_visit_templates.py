"""M1 (HG-089): visit templates per specialty.

A template is clinical wording and a few medicine lines, kept as a starting point for the visit form. These tests pin: the
table and its migration; the built-in starting templates (written once, text only, never overwritten or brought back);
who may use, read and change which template (HG-084: an employee link alone gives nothing); the JSON the visit form's sheet
reads (guard, no patient data, no price, retired drugs become typed lines); «احفظ ده كقالب» (never a vital, a date, a
patient or an animal); the manager's create, edit, switch off, reorder, copy and delete; the vet; and the form's POST shape,
which a template never changes. The browser half (filling only empty boxes, the keyboard) is checked in a real browser.
"""

import re
import json
from datetime import date
from pathlib import Path

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
from pharmacy.models import DrugReference
from pharmacy.tests_reference_ui import ref
from settings_core.models import SystemSetting

from . import prescribing, services, visit_template_seeds, visit_templates
from .models import ClinicalVisit, VisitTemplate
from .tests import person
from .tests_access import NON_CLINICAL, Clinic

DATA = reverse("medical:visit_templates_data")
LIST = reverse("medical:visit_templates")
NEW = reverse("medical:visit_template_new")
SAVE = reverse("medical:visit_template_from_visit")
TODAY = timezone.localdate()
#: What the visit form posted before M1: a template must never add or remove one of these.
VISIT_FORM_FIELDS = {"csrfmiddlewaretoken", "lang", "visit_date", "doctor", "complaint", "examination", "blood_pressure", "pulse", "temperature",
                     "weight", "height", "diagnosis", "rx_id", "rx_reference", "rx_name", "rx_dose", "rx_frequency", "rx_duration", "rx_route",
                     "rx_notes", "treatment", "follow_up_date"}


def entry(**fields):
    return {"name": "برد", "specialty": "general", "lang": "ar", "complaint": "رشح", **fields}


class Setup(Clinic):
    """The clinic of HG-084's matrix, with a second doctor, a drug list and the template helpers."""

    def setUp(self):
        super().setUp()
        self.users["doctor2"] = person(RoleCode.DOCTOR, "mx_doctor2")
        self.zuba = ref("A1", "Zubacillin 500mg caps", "زوباسيلين 500 مجم", "Amoxicillin", "500 mg", "capsule", "16 caps", "62.50", TODAY, rx=True)
        self.quil = ref("P1", "Quilodol 500mg tab", "كويلودول 500", "Paracetamol", "500 mg", "tablet", "20 tabs", "35.00", TODAY)
        # the built-in starting templates are written on first use; every test but SeedTests starts with them already done, to see only its own rows
        SystemSetting.objects.create(key=visit_templates.SEED_SETTING, value=json.dumps([row[0] for row in visit_template_seeds.rows()]),
                                     data_type=SystemSetting.DataType.JSON)

    def make(self, owner=None, **fields):
        base = {"name": "قالب", "specialty": "general", "lang": "ar", "complaint": "شكوى", "owner": self.users[owner] if isinstance(owner, str) else owner}
        return VisitTemplate.objects.create(**{**base, **fields})

    def get(self, who, url, **params):
        self.client.force_login(self.users[who])
        return self.client.get(url, params)

    def post(self, who, url, data=None, **extra):
        self.client.force_login(self.users[who])
        return self.client.post(url, data or {}, **extra)

    def names(self, who, **params):
        return [row["name"] for row in self.get(who, DATA, **params).json()["templates"]]

    def form_post(self, **fields):
        """The manager's form as the browser sends it."""

        return {"name": "قالبي", "specialty": "general", "text_lang": "ar", "complaint": "رشح وكحة", "examination": "", "diagnosis": "", "treatment": "راحة",
                "active": "1", "lang": "ar", **fields}


# ---- the table and its migration ----

class ModelTests(Setup):
    def test_fields_and_ordering(self):
        names = {field.name for field in VisitTemplate._meta.get_fields()}
        self.assertEqual(names, {"id", "name", "lang", "specialty", "owner", "complaint", "examination", "diagnosis", "treatment", "lines", "active",
                                 "position", "seed_key", "created_by", "created_at", "updated_at"})
        # nothing of the patient, the animal, the vitals or a date, and no link to a visit
        for forbidden in ("patient", "animal", "visit", "appointment", "blood_pressure", "pulse", "weight", "temperature", "visit_date", "follow_up_date"):
            self.assertNotIn(forbidden, names)
        self.assertEqual(VisitTemplate._meta.get_field("owner").remote_field.on_delete.__name__, "PROTECT")  # a personal template never turns shared
        self.assertEqual(VisitTemplate._meta.get_field("created_by").remote_field.on_delete.__name__, "SET_NULL")
        self.assertFalse(VisitTemplate._meta.get_field("specialty").choices)  # the list lives in code, so it can grow without a migration
        second, first = self.make(name="ب", position=20), self.make(name="أ", position=10)
        self.assertEqual(list(VisitTemplate.objects.all()), [first, second])
        self.assertEqual(VisitTemplate._meta.get_field("lines").default, list)

    def test_a_visit_never_points_at_a_template(self):
        self.assertNotIn("template", {field.name for field in ClinicalVisit._meta.get_fields()})


class MigrationTests(TransactionTestCase):
    """0012 only adds the table, after 0011."""

    serialized_rollback = True

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_it_depends_on_0011_and_adds_one_table(self):
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        node = executor.loader.disk_migrations[("medical", "0012_visit_template")]
        self.assertIn(("medical", "0011_visit_medicine"), node.dependencies)
        executor.migrate([("medical", "0011_visit_medicine")])
        old = executor.loader.project_state([n for n in executor.loader.graph.leaf_nodes() if n[0] != "medical"] + [("medical", "0011_visit_medicine")]).apps
        user = old.get_model("auth", "User").objects.create(username="mig_vt")
        patient = old.get_model("master_data", "Customer").objects.create(customer_code="P-1", name="منى")
        old.get_model("medical", "ClinicalVisit").objects.create(patient=patient, visit_date=date(2026, 11, 1), diagnosis="برد", treatment="راحة", created_by=user)
        self.assertNotIn("medical_visittemplate", connection.introspection.table_names())
        executor = MigrationExecutor(connection)
        executor.loader.build_graph()
        executor.migrate([("medical", "0012_visit_template")])
        self.assertIn("medical_visittemplate", connection.introspection.table_names())
        self.assertEqual(list(ClinicalVisit.objects.values_list("diagnosis", "treatment")), [("برد", "راحة")])
        self.assertEqual(VisitTemplate.objects.count(), 0)  # nothing is seeded by the migration: the first use does it


# ---- the built-in starting templates ----

class SeedTests(Setup):
    def setUp(self):
        super().setUp()
        SystemSetting.objects.filter(key=visit_templates.SEED_SETTING).delete()

    def test_text_only_two_to_four_per_specialty_in_both_languages(self):
        by = {}
        for specialty, slug, texts in visit_template_seeds.SEEDS:
            by.setdefault(specialty, []).append(slug)
            self.assertEqual(set(texts), {"ar", "en"}, (specialty, slug))
            for lang, (name, complaint, examination, instructions) in texts.items():
                self.assertTrue(name and complaint and examination and instructions, (specialty, slug, lang))
        self.assertEqual(set(by), set(visit_templates.SPECIALTIES))
        for specialty, slugs in by.items():
            self.assertTrue(2 <= len(slugs) <= 4, (specialty, slugs))
        self.assertEqual(len({row[0] for row in visit_template_seeds.rows()}), len(visit_template_seeds.rows()))  # every key is its own
        self.assertTrue(all(len(row[0]) <= 60 for row in visit_template_seeds.rows()))

    def test_no_medicine_dose_or_diagnosis_in_the_defaults(self):
        self.assertEqual(visit_templates.seed(), len([r for r in visit_template_seeds.rows() if r[1] in visit_templates.HUMAN]))
        rows = VisitTemplate.objects.all()
        self.assertTrue(rows.exists())
        for row in rows:
            with self.subTest(row=row.seed_key):
                self.assertEqual((row.lines, row.diagnosis, row.owner_id, row.created_by_id, row.active), ([], "", None, None, True))
                text = " ".join((row.complaint, row.examination, row.treatment))
                self.assertNotRegex(text, r"\d\s*(mg|ml|g|mcg|مجم|مل|جم)\b", re.IGNORECASE)  # no dose
                self.assertNotRegex(text.lower(), r"paracetamol|amoxicillin|ibuprofen|aspirin|antibiotic|بنادول|مضاد حيوي|بروفين|أموكس")  # no drug

    def test_seeding_is_idempotent_and_never_overwrites_or_brings_back(self):
        written = visit_templates.seed()
        self.assertGreater(written, 0)
        self.assertEqual(visit_templates.seed(), 0)  # a second call writes nothing
        self.assertEqual(VisitTemplate.objects.count(), written)
        edited, deleted, off = (VisitTemplate.objects.filter(seed_key="general.cold.ar").get(), VisitTemplate.objects.get(seed_key="general.cold.en"),
                                VisitTemplate.objects.get(seed_key="eye.eye_strain.ar"))
        edited.name, edited.complaint = "برد بتاعي", "نصي أنا"
        edited.save()
        deleted.delete()
        off.active = False
        off.save()
        self.assertEqual(visit_templates.seed(), 0)
        self.assertEqual(VisitTemplate.objects.count(), written - 1)  # the deleted one is not brought back
        edited.refresh_from_db()
        self.assertEqual((edited.name, edited.complaint), ("برد بتاعي", "نصي أنا"))
        off.refresh_from_db()
        self.assertFalse(off.active)
        keys = json.loads(SystemSetting.objects.get(key=visit_templates.SEED_SETTING).value)
        self.assertIn("general.cold.en", keys)  # remembered after deleting

    def test_a_key_added_later_is_written_without_touching_the_rest(self):
        visit_templates.seed()
        row = VisitTemplate.objects.get(seed_key="ent.sinus.ar")
        row.complaint = "عدّلتها"
        row.save()
        setting = SystemSetting.objects.get(key=visit_templates.SEED_SETTING)
        setting.value = json.dumps([key for key in json.loads(setting.value) if key != "ent.sinus.en"])  # as if this release added it
        setting.save()
        VisitTemplate.objects.filter(seed_key="ent.sinus.en").delete()
        self.assertEqual(visit_templates.seed(), 1)
        self.assertTrue(VisitTemplate.objects.filter(seed_key="ent.sinus.en").exists())
        row.refresh_from_db()
        self.assertEqual(row.complaint, "عدّلتها")

    def test_a_vet_gets_vet_templates_and_a_dental_clinic_dental_ones(self):
        self.set_sub_activity("vet")
        visit_templates.seed()
        self.assertEqual(set(VisitTemplate.objects.values_list("specialty", flat=True)), {"vet"})
        VisitTemplate.objects.all().delete()
        SystemSetting.objects.filter(key=visit_templates.SEED_SETTING).delete()
        self.set_sub_activity("dental")
        visit_templates.seed()
        self.assertEqual(set(VisitTemplate.objects.values_list("specialty", flat=True)), {"dental"})

    def test_the_first_use_seeds_from_the_sheet_and_the_manager(self):
        self.assertFalse(VisitTemplate.objects.exists())
        self.get("doctor", DATA)
        self.assertTrue(VisitTemplate.objects.exists())
        VisitTemplate.objects.all().delete()
        SystemSetting.objects.filter(key=visit_templates.SEED_SETTING).delete()
        self.get("doctor", LIST)
        self.assertTrue(VisitTemplate.objects.exists())
        count = VisitTemplate.objects.count()
        self.get("doctor", LIST)
        self.assertEqual(VisitTemplate.objects.count(), count)
        self.client.logout()
        VisitTemplate.objects.all().delete()
        SystemSetting.objects.filter(key=visit_templates.SEED_SETTING).delete()
        self.assertEqual(self.get("desk", DATA).status_code, 403)
        self.assertFalse(VisitTemplate.objects.exists())  # a refused request writes nothing


# ---- who may use and change what ----

class AccessTests(Setup):
    def test_the_screens_and_the_endpoint_need_a_clinical_permission(self):
        mine = self.make(owner="doctor", name="لبتاعي")
        urls = (DATA, LIST, NEW, reverse("medical:visit_template_edit", args=[mine.pk]))
        self.assert_matrix(urls, allowed=("owner", "manager", "doctor"), denied=NON_CLINICAL)
        for who in NON_CLINICAL:
            for action in ("up", "off", "copy", "delete"):
                with self.subTest(who=who, action=action):
                    self.assertEqual(self.post(who, reverse("medical:visit_template_action", args=[mine.pk, action])).status_code, 403)
            with self.subTest(who=who, what="save"):
                self.assertEqual(self.post(who, SAVE, {"name": "x", "complaint": "y"}).status_code, 403)
        self.assertTrue(VisitTemplate.objects.filter(pk=mine.pk, active=True).exists())
        self.client.logout()
        anonymous = self.client.get(DATA)
        self.assertEqual(anonymous.status_code, 302)
        self.assertTrue(anonymous["Location"].startswith(reverse("login")))

    def test_not_a_medical_install_and_the_methods(self):
        from settings_core.models import ClientProfile

        self.client.force_login(self.users["doctor"])
        self.assertEqual(self.client.post(DATA).status_code, 405)
        self.assertEqual(self.client.get(SAVE).status_code, 405)
        mine = self.make(owner="doctor")
        self.assertEqual(self.client.get(reverse("medical:visit_template_action", args=[mine.pk, "off"])).status_code, 405)
        self.assertEqual(self.post("doctor", reverse("medical:visit_template_action", args=[mine.pk, "explode"])).status_code, 404)
        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        for url in (DATA, LIST, NEW):
            with self.subTest(url=url):
                self.assertEqual(self.get("owner", url).status_code, 404)
        self.assertEqual(self.post("owner", SAVE, {"name": "x", "complaint": "y"}).status_code, 404)

    def test_another_doctors_personal_template_is_never_offered_or_shown(self):
        theirs = self.make(owner="doctor2", name="لدكتور تاني")
        shared = self.make(owner=None, name="مشترك")
        mine = self.make(owner="doctor", name="بتاعي")
        for who in ("doctor", "owner", "manager"):
            with self.subTest(who=who):
                self.assertEqual(self.names("doctor") if who == "doctor" else self.names(who), self.names(who))
        self.assertEqual(self.names("doctor"), ["بتاعي", "مشترك"])  # their own first, then the shared one
        self.assertNotIn("لدكتور تاني", self.names("owner"))  # not even for the owner on a visit: a doctor's own is theirs
        self.assertNotIn("لدكتور تاني", self.names("manager"))
        self.assertEqual(self.names("doctor2"), ["لدكتور تاني", "مشترك"])
        page = self.get("doctor", reverse("medical:visit_template_edit", args=[theirs.pk]))
        self.assertEqual(page.status_code, 403)
        self.assertEqual(self.post("doctor", reverse("medical:visit_template_edit", args=[theirs.pk]), self.form_post(name="مسروق")).status_code, 403)
        theirs.refresh_from_db()
        self.assertEqual(theirs.name, "لدكتور تاني")
        for action in ("up", "down", "off", "delete", "copy"):
            with self.subTest(action=action):
                self.assertEqual(self.post("doctor", reverse("medical:visit_template_action", args=[theirs.pk, action])).status_code, 403)
        self.assertTrue(VisitTemplate.objects.filter(pk=theirs.pk, active=True).exists())
        listing = self.get("doctor", LIST, scope="others")
        self.assertNotContains(listing, "لدكتور تاني")  # «قوالب الدكاترة» is for the practice-wide permission
        self.assertNotContains(listing, 'data-vt-scope="others"')
        manager_view = self.get("manager", LIST, scope="others")
        self.assertContains(manager_view, "لدكتور تاني")
        self.assertContains(manager_view, 'data-vt-scope="others"')
        self.assertContains(self.get("manager", reverse("medical:visit_template_edit", args=[theirs.pk])), "لدكتور تاني")
        self.assertEqual(self.post("manager", reverse("medical:visit_template_edit", args=[theirs.pk]), self.form_post(name="صلّحها المدير", scope="mine")).status_code, 302)
        theirs.refresh_from_db()
        self.assertEqual((theirs.name, theirs.owner_id), ("صلّحها المدير", self.users["doctor2"].pk))  # still that doctor's own
        self.assertEqual((mine.owner_id, shared.owner_id), (self.users["doctor"].pk, None))

    def test_shared_templates_are_written_by_the_practice_wide_permission_only(self):
        shared = self.make(owner=None, name="مشترك")
        url = reverse("medical:visit_template_edit", args=[shared.pk])
        page = self.get("doctor", url)
        self.assertContains(page, "data-vt-readonly")  # a doctor reads it, and may copy it
        self.assertContains(page, "data-vt-copy")
        self.assertNotContains(page, 'name="scope"')
        self.assertEqual(self.post("doctor", url, self.form_post(name="غيّرت")).status_code, 403)
        for action in ("up", "off", "delete"):
            self.assertEqual(self.post("doctor", reverse("medical:visit_template_action", args=[shared.pk, action])).status_code, 403)
        shared.refresh_from_db()
        self.assertEqual((shared.name, shared.active), ("مشترك", True))
        self.assertEqual(self.post("manager", url, self.form_post(name="المدير غيّر", scope="shared")).status_code, 302)
        shared.refresh_from_db()
        self.assertEqual((shared.name, shared.owner_id), ("المدير غيّر", None))
        # a doctor asking for «مشترك» on a new template gets a personal one
        self.post("doctor", NEW, self.form_post(name="عايزه مشترك", scope="shared"))
        made = VisitTemplate.objects.get(name="عايزه مشترك")
        self.assertEqual((made.owner_id, made.created_by_id), (self.users["doctor"].pk, self.users["doctor"].pk))
        self.post("manager", NEW, self.form_post(name="مشترك جديد", scope="shared"))
        self.assertIsNone(VisitTemplate.objects.get(name="مشترك جديد").owner_id)
        self.post("manager", NEW, self.form_post(name="بتاع المدير", scope="mine"))
        self.assertEqual(VisitTemplate.objects.get(name="بتاع المدير").owner_id, self.users["manager"].pk)

    def test_the_sheet_follows_the_visit_forms_own_rule(self):
        """A doctor changes only their own visit unless they hold write_records; templates follow the same split."""

        mine, shared = self.make(owner="doctor"), self.make(owner=None, name="س")
        self.assertTrue(visit_templates.can_edit(self.users["doctor"], mine))
        self.assertFalse(visit_templates.can_edit(self.users["doctor"], shared))
        self.assertFalse(visit_templates.can_edit(self.users["doctor2"], mine))
        for who in ("owner", "manager"):
            self.assertTrue(visit_templates.can_edit(self.users[who], mine))
            self.assertTrue(visit_templates.can_edit(self.users[who], shared))
        for who in NON_CLINICAL:  # an employee link, a role without the permission, or a switched-off login grants nothing
            self.assertFalse(visit_templates.can_edit(self.users[who], mine))
            self.assertFalse(visit_templates.can_edit(self.users[who], shared))


# ---- the JSON the visit form reads ----

class EndpointTests(Setup):
    def test_what_it_returns_and_its_order(self):
        self.make(owner=None, name="مشترك أ", position=10, specialty="eye")
        self.make(owner="doctor", name="بتاعي ب", position=20, specialty="general", complaint="الشكوى", examination="الفحص", diagnosis="التشخيص", treatment="التعليمات")
        self.make(owner="doctor", name="بتاعي أ", position=10, specialty="general", active=False)  # switched off: not offered
        response = self.get("doctor", DATA)
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response["Cache-Control"])
        data = response.json()
        self.assertEqual((data["ok"], data["count"], data["default_specialty"]), (True, 2, ""))
        self.assertEqual([row["name"] for row in data["templates"]], ["بتاعي ب", "مشترك أ"])
        mine = data["templates"][0]
        self.assertEqual({key: mine[key] for key in ("complaint", "examination", "diagnosis", "treatment", "lang", "specialty", "specialty_label", "mine", "shared", "starter")},
                         {"complaint": "الشكوى", "examination": "الفحص", "diagnosis": "التشخيص", "treatment": "التعليمات", "lang": "ar", "specialty": "general",
                          "specialty_label": "باطنة وعام", "mine": True, "shared": False, "starter": False})
        self.assertEqual(data["specialties"][0], {"code": "general", "label": "باطنة وعام"})
        self.assertNotIn("vet", [s["code"] for s in data["specialties"]])
        english = self.get("doctor", DATA, lang="en").json()
        self.assertEqual(english["templates"][0]["specialty_label"], "General and internal")

    def test_no_patient_data_and_no_price(self):
        services.save_profile(services.file_for(self.patient), {"allergies": "ZZALLERGY Amoxicillin"}, self.owner)
        self.make(owner=None, name="بمضاد", lines=[{"reference": self.zuba.pk, "name": "Zubacillin", "dose": "كبسولة", "frequency": "", "duration": "", "route": "", "notes": ""},
                                                    {"reference": self.quil.pk, "name": "Quilodol", "dose": "", "frequency": "", "duration": "", "route": "", "notes": ""}])
        plain = self.get("doctor", DATA)
        body = plain.content.decode()
        for private in ("منى علي", "01001234567", "P-00001", "ZZALLERGY", "62.50", "35.00", "public_price", "price_date"):
            self.assertNotIn(private, body)
        self.assertEqual([line["row"]["allergy"] for line in plain.json()["templates"][0]["lines"]], [[], []])  # no patient asked about: no flag
        flagged = self.get("doctor", DATA, patient=self.patient.pk)
        text = flagged.content.decode()
        for private in ("منى علي", "01001234567", "P-00001", "ZZALLERGY", "62.50", "public_price"):
            self.assertNotIn(private, text)  # the allergy text, name, phone and file number are never echoed
        lines = flagged.json()["templates"][0]["lines"]
        self.assertEqual([line["row"]["allergy"] for line in lines], [["Amoxicillin"], []])  # only the drug's own matching word
        self.assertEqual(self.get("doctor", DATA, patient="abc", animal="x").status_code, 200)  # odd ids are ignored, never an error

    def test_a_line_whose_drug_left_the_list_comes_back_as_a_typed_line(self):
        lines = [{"reference": self.zuba.pk, "name": "زوباسيلين", "dose": "كبسولة", "frequency": "3 مرات", "duration": "5 أيام", "route": "", "notes": "بعد الأكل"},
                 {"reference": self.quil.pk, "name": "كويلودول", "dose": "قرص", "frequency": "", "duration": "", "route": "", "notes": ""},
                 {"reference": "", "name": "غسول فم", "dose": "", "frequency": "", "duration": "أسبوع", "route": "", "notes": ""}]
        self.make(owner=None, name="بأدوية", lines=lines)
        before = self.get("doctor", DATA).json()["templates"][0]["lines"]
        self.assertEqual([bool(line["row"]) for line in before], [True, True, False])
        self.assertEqual((before[0]["reference"], before[0]["row"]["name_en"], before[0]["notes"]), (self.zuba.pk, "Zubacillin 500mg caps", "بعد الأكل"))
        self.quil.is_active = False
        self.quil.save()
        self.zuba.delete()
        after = self.get("doctor", DATA).json()["templates"][0]["lines"]
        self.assertEqual([(line["reference"], line["row"], line["name"]) for line in after], [("", None, "زوباسيلين"), ("", None, "كويلودول"), ("", None, "غسول فم")])
        self.assertEqual(after[1]["dose"], "قرص")  # the typed directions stay

    def test_the_vet_install_offers_vet_templates_and_a_human_clinic_never_does(self):
        self.make(owner=None, name="للكلاب", specialty="vet")
        self.make(owner=None, name="باطنة", specialty="general")
        self.assertEqual(self.names("doctor"), ["باطنة"])
        self.set_sub_activity("vet")
        self.assertEqual(self.names("doctor"), ["للكلاب"])
        data = self.get("doctor", DATA).json()
        self.assertEqual((data["default_specialty"], [s["code"] for s in data["specialties"]]), ("vet", ["vet"]))
        self.set_sub_activity("dental")
        self.assertEqual(self.get("doctor", DATA).json()["default_specialty"], "dental")


# ---- applying: what the server can pin ----

class ApplyTests(Setup):
    """The browser puts a template's lines on the form; the visit is then saved by ``save_visit`` like any visit."""

    def line_post(self, payload_line):
        return {"id": "", "reference": str(payload_line["reference"] or ""), "name": payload_line["name"], "dose": payload_line["dose"],
                "frequency": payload_line["frequency"], "duration": payload_line["duration"], "route": payload_line["route"], "notes": payload_line["notes"]}

    def apply_and_save(self, who="doctor"):
        template = self.get(who, DATA).json()["templates"][0]
        data = {"diagnosis": "التهاب حلق", "visit_date": TODAY.isoformat(), "medicines": [self.line_post(line) for line in template["lines"]]}
        return services.save_visit(self.patient, data, self.users[who], None, "ar")

    def test_template_lines_go_through_the_normal_resolve_and_snapshot(self):
        self.make(owner="doctor", lines=[{"reference": self.zuba.pk, "name": "Zubacillin", "dose": "كبسولة", "frequency": "3 مرات يوميًا", "duration": "5 أيام", "route": "", "notes": ""}])
        visit = self.apply_and_save()
        line = visit.medicines.get()
        self.assertEqual((line.reference_id, line.name_en, line.active_ingredient, line.strength, line.form, line.dose),
                         (self.zuba.pk, "Zubacillin 500mg caps", "Amoxicillin", "500 mg", "capsule", "كبسولة"))
        self.zuba.name_en = "Renamed"
        self.zuba.save()
        line.refresh_from_db()
        self.assertEqual(line.name_en, "Zubacillin 500mg caps")  # a later change of the list never rewrites it (HG-086)

    def test_a_retired_reference_becomes_a_hand_written_line(self):
        self.make(owner="doctor", lines=[{"reference": self.zuba.pk, "name": "زوباسيلين", "dose": "كبسولة", "frequency": "", "duration": "", "route": "", "notes": ""}])
        self.zuba.is_active = False
        self.zuba.save()
        line = self.apply_and_save().medicines.get()
        self.assertEqual((line.reference_id, line.name_ar, line.active_ingredient, line.dose), (None, "زوباسيلين", "", "كبسولة"))

    def test_the_allergy_advice_still_runs_on_a_template_line(self):
        services.save_profile(services.file_for(self.patient), {"allergies": "Amoxicillin"}, self.owner)
        self.make(owner="doctor", lines=[{"reference": self.zuba.pk, "name": "Zubacillin", "dose": "", "frequency": "", "duration": "", "route": "", "notes": ""}])
        template = self.get("doctor", DATA, patient=self.patient.pk).json()["templates"][0]
        self.assertEqual(template["lines"][0]["row"]["allergy"], ["Amoxicillin"])  # on the form, while writing
        visit = self.apply_and_save()
        self.assertIn("Amoxicillin", prescribing.saved_warning(visit, "en"))  # and after saving: advice only, the visit was saved

    def test_a_template_can_fill_nothing_the_save_would_refuse(self):
        self.make(owner="doctor", complaint="", examination="فحص بس")
        with self.assertRaises(ValidationError):  # the form's own rule (a complaint, diagnosis, treatment or medicine) still applies
            services.save_visit(self.patient, {"examination": "فحص بس", "visit_date": TODAY.isoformat()}, self.users["doctor"])


# ---- «احفظ ده كقالب» ----

class SaveFromFormTests(Setup):
    def payload(self, **fields):
        return {"name": "قالب جديد", "specialty": "general", "text_lang": "ar", "lang": "ar", "patient": str(self.patient.pk), "animal": "",
                "complaint": "كحة", "examination": "الصدر سليم", "diagnosis": "برد", "treatment": "راحة", **fields}

    def rx(self, *lines):
        return {f"rx_{field}": [line.get(field, "") for line in lines] for field in prescribing.FIELDS}

    def test_it_copies_the_texts_and_lines_and_nothing_else(self):
        services.save_profile(services.file_for(self.patient), {}, self.owner)
        extras = {"visit_date": "2026-01-01", "follow_up_date": "2026-02-01", "blood_pressure": "190/120", "pulse": "130", "temperature": "39.5", "weight": "88", "height": "170",
                  "doctor": str(self.sara.pk), "appointment": str(self.appointment.pk)}
        data = {**self.payload(), **extras, **self.rx({"id": "55", "reference": str(self.zuba.pk), "name": "Zubacillin", "dose": "كبسولة", "frequency": "3 مرات", "duration": "5 أيام",
                                                           "notes": "بعد الأكل"}, {"name": "غسول فم", "duration": "أسبوع"})}
        response = self.post("doctor", SAVE, data)
        self.assertEqual(response.status_code, 200)
        answer = response.json()
        template = VisitTemplate.objects.get(pk=answer["id"])
        self.assertEqual((answer["ok"], answer["name"]), (True, "قالب جديد"))
        self.assertTrue(answer["url"].startswith(reverse("medical:visit_template_edit", args=[template.pk])))
        self.assertEqual((template.owner_id, template.created_by_id, template.specialty, template.lang, template.active), (self.users["doctor"].pk, self.users["doctor"].pk, "general", "ar", True))
        self.assertEqual((template.complaint, template.examination, template.diagnosis, template.treatment), ("كحة", "الصدر سليم", "برد", "راحة"))
        self.assertEqual([(line["reference"], line["name"], line["dose"], line["frequency"], line["duration"], line["notes"]) for line in template.lines],
                         [(self.zuba.pk, "زوباسيلين 500 مجم", "كبسولة", "3 مرات", "5 أيام", "بعد الأكل"), (None, "غسول فم", "", "", "أسبوع", "")])
        stored = json.dumps([template.name, template.complaint, template.examination, template.diagnosis, template.treatment, template.lines], ensure_ascii=False)
        for private in ("190/120", "130", "39.5", "88", "170", "2026-01-01", "2026-02-01", "55", "منى", "01001234567"):
            self.assertNotIn(private, stored.replace(str(self.zuba.pk), ""))
        self.assertFalse(ClinicalVisit.objects.filter(complaint="كحة").exists())  # saving a template never saves a visit
        audit = AuditLog.objects.get(action="create_visit_template", object_id=str(template.pk))
        self.assertEqual(audit.after_data, {"name": "قالب جديد", "specialty": "general", "shared": False, "lines": 2})  # names and counts, never the texts

    def test_the_patients_name_file_number_phone_and_animal_are_blanked_where_typed(self):
        from . import vet

        self.set_sub_activity("vet")
        animal = vet.save_animal(self.patient, {"name": "مشمش", "species": "cat"}, self.owner)
        file = services.file_for(self.patient)
        data = self.payload(complaint=f"{self.patient.name} جابت مشمش بتشتكي", examination=f"تليفونها 01001234567 ورقم الملف {file.file_number}",
                            treatment="سوائل", animal=str(animal.pk), specialty="vet")
        template = VisitTemplate.objects.get(pk=self.post("doctor", SAVE, data).json()["id"])
        self.assertEqual(template.complaint, "… جابت … بتشتكي")
        self.assertEqual(template.examination, "تليفونها … ورقم الملف …")
        # an animal that is not this patient's own is not looked up: its name stays as typed (nothing is exposed either way)
        stranger = vet.save_animal(make_customer(customer_code="P-9", name="سيد"), {"name": "ريكس", "species": "dog"}, self.owner)
        other = VisitTemplate.objects.get(pk=self.post("doctor", SAVE, self.payload(name="تاني", specialty="vet", complaint="ريكس", animal=str(stranger.pk))).json()["id"])
        self.assertEqual(other.complaint, "ريكس")
        self.assertEqual(visit_templates.scrub("Mona Ali came", ["mona ali", "ab"]), "… came")  # case-blind; a 2-letter needle is too short to blank

    def test_refusals_say_why_and_save_nothing(self):
        self.assertEqual(self.post("doctor", SAVE, self.payload(name="  ")).status_code, 400)
        refused = self.post("doctor", SAVE, self.payload(complaint="", examination="", diagnosis="", treatment=""))
        self.assertEqual(refused.status_code, 400)
        self.assertIn("اكتب نص واحد", refused.json()["error"])
        english = self.post("doctor", SAVE + "?lang=en", self.payload(name="", lang="en"))
        self.assertEqual(english.json()["error"], "Enter the template's name.")
        self.assertEqual(self.post("doctor", SAVE, self.payload(specialty="vet")).status_code, 400)  # a human clinic has no vet templates
        self.assertEqual(self.post("doctor", SAVE, self.payload(name="س" * 200)).status_code, 400)
        self.assertEqual(self.post("doctor", SAVE, self.payload(**self.rx(*[{"name": f"دوا {n}"} for n in range(21)]))).status_code, 400)
        self.assertFalse(VisitTemplate.objects.exists())
        ok = self.post("doctor", SAVE, self.payload(**self.rx({"name": "غسول", "dose": "مضمضة"}), complaint="", examination="", diagnosis="", treatment=""))
        self.assertEqual(ok.status_code, 200)  # medicines alone are enough, as for a visit
        self.assertEqual(self.post("doctor", SAVE, self.payload(**self.rx({"name": "غسول", "dose": "مضمضة"}), complaint="", examination="", diagnosis="", treatment="")).status_code, 400)  # same name again

    def test_a_retired_drug_is_saved_as_its_name(self):
        self.zuba.is_active = False
        self.zuba.save()
        template = VisitTemplate.objects.get(pk=self.post("doctor", SAVE, self.payload(**self.rx({"reference": str(self.zuba.pk), "name": "Zubacillin", "dose": "كبسولة"}))).json()["id"])
        self.assertEqual(template.lines[0]["reference"], None)
        self.assertEqual(template.lines[0]["name"], "Zubacillin")

    def test_the_cap_per_person(self):
        VisitTemplate.objects.bulk_create([VisitTemplate(name=f"ق{n}", owner=self.users["doctor"], complaint="x") for n in range(visit_templates.MAX_PER_SCOPE)])
        refused = self.post("doctor", SAVE, self.payload())
        self.assertEqual(refused.status_code, 400)
        self.assertIn("وصلت للحد", refused.json()["error"])
        self.assertEqual(self.post("doctor2", SAVE, self.payload()).status_code, 200)  # the cap is per person


# ---- the manager ----

class ManagerTests(Setup):
    def test_create_edit_switch_off_and_on(self):
        response = self.post("doctor", NEW, self.form_post(name="حرارة", complaint="سخونية", examination="الحلق", diagnosis="", treatment="سوائل"))
        self.assertEqual(response.status_code, 302)
        template = VisitTemplate.objects.get(name="حرارة")
        self.assertEqual((template.owner_id, template.lang, template.specialty, template.treatment, template.active), (self.users["doctor"].pk, "ar", "general", "سوائل", True))
        listing = self.get("doctor", LIST)
        self.assertContains(listing, f'data-vt-row="{template.pk}"')
        self.assertContains(listing, "حرارة")
        edit = reverse("medical:visit_template_edit", args=[template.pk])
        page = self.get("doctor", edit)
        self.assertContains(page, 'value="حرارة"')
        self.assertContains(page, 'data-rx')  # the medicine lines are the visit form's own section
        self.assertEqual(self.post("doctor", edit, self.form_post(name="حرارة عالية", complaint="سخونية شديدة", active="")).status_code, 302)
        template.refresh_from_db()
        self.assertEqual((template.name, template.complaint, template.active), ("حرارة عالية", "سخونية شديدة", False))
        self.assertNotIn("حرارة عالية", self.names("doctor"))  # switched off: not offered on a visit
        listing = self.get("doctor", LIST)
        self.assertContains(listing, "data-vt-off")
        self.assertContains(listing, "حرارة عالية")  # still in the manager, to switch on again
        self.assertEqual(self.post("doctor", reverse("medical:visit_template_action", args=[template.pk, "on"])).status_code, 302)
        self.assertIn("حرارة عالية", self.names("doctor"))
        self.post("doctor", reverse("medical:visit_template_action", args=[template.pk, "off"]))
        self.assertFalse(VisitTemplate.objects.get(pk=template.pk).active)
        actions = list(AuditLog.objects.filter(object_id=str(template.pk)).values_list("action", flat=True))
        self.assertEqual(actions.count("create_visit_template"), 1)
        self.assertGreaterEqual(actions.count("update_visit_template"), 3)

    def test_the_form_checks_and_keeps_what_was_typed(self):
        bad = self.post("doctor", NEW, self.form_post(name="", complaint="جملة مكتوبة"))
        self.assertEqual(bad.status_code, 200)
        self.assertContains(bad, "اكتب اسم القالب.")
        self.assertContains(bad, "جملة مكتوبة")  # what was typed stays in the form
        self.assertEqual(self.post("doctor", NEW, self.form_post(complaint="", treatment="")).status_code, 200)
        self.assertEqual(self.post("doctor", NEW, self.form_post(specialty="vet")).status_code, 200)
        self.assertEqual(self.post("doctor", NEW, self.form_post(text_lang="fr")).status_code, 302)  # an unknown language falls back to the page's
        self.assertEqual(VisitTemplate.objects.get().lang, "ar")
        self.assertEqual(self.post("doctor", NEW, self.form_post()).status_code, 200)  # the same name, specialty and language
        english = self.post("doctor", NEW + "?lang=en", self.form_post(name="", lang="en"))
        self.assertContains(english, "Enter the template&#x27;s name.")
        too_long = self.post("doctor", NEW, self.form_post(name="س" * 150, complaint="x"))
        self.assertContains(too_long, "أطول من 120")

    def test_medicine_lines_are_saved_through_the_visit_forms_rules(self):
        data = {**self.form_post(name="بأدوية"), "rx_id": ["", ""], "rx_reference": [str(self.zuba.pk), ""], "rx_name": ["Zubacillin", "غسول فم"], "rx_dose": ["كبسولة", "مضمضة"],
                "rx_frequency": ["3 مرات", ""], "rx_duration": ["5 أيام", "أسبوع"], "rx_route": ["", ""], "rx_notes": ["", ""]}
        self.assertEqual(self.post("doctor", NEW, data).status_code, 302)
        template = VisitTemplate.objects.get(name="بأدوية")
        self.assertEqual([(line["reference"], line["name"], line["dose"]) for line in template.lines], [(self.zuba.pk, "زوباسيلين 500 مجم", "كبسولة"), (None, "غسول فم", "مضمضة")])
        page = self.get("doctor", reverse("medical:visit_template_edit", args=[template.pk]))
        self.assertContains(page, "زوباسيلين 500 مجم")
        self.assertContains(page, 'name="rx_name"')
        bad = self.post("doctor", NEW, {**data, "name": "ناقص", "rx_name": ["", "غسول"], "rx_reference": ["", ""], "rx_dose": ["x", "y"]})
        self.assertContains(bad, "الدوا رقم 1")  # a line without a name is refused as in a visit
        self.assertFalse(VisitTemplate.objects.filter(name="ناقص").exists())
        too_many = {**data, "name": "كتير", **{f"rx_{f}": [""] * 21 for f in ("id", "reference", "dose", "frequency", "duration", "route", "notes")}, "rx_name": [f"د{n}" for n in range(21)]}
        self.assertContains(self.post("doctor", NEW, too_many), "الروشتة بتاخد لحد 20 دوا")

    def test_reorder_swaps_within_the_group_and_renumbers(self):
        a, b, c = (self.make(owner="doctor", name=n, position=p) for n, p in (("أ", 10), ("ب", 20), ("ج", 30)))
        other = self.make(owner="doctor", name="عيون", specialty="eye", position=10)
        down = reverse("medical:visit_template_action", args=[a.pk, "down"])
        self.assertEqual(self.post("doctor", down).status_code, 302)
        self.assertEqual(list(VisitTemplate.objects.filter(specialty="general").values_list("name", flat=True)), ["ب", "أ", "ج"])
        self.assertEqual(list(VisitTemplate.objects.filter(specialty="general").values_list("position", flat=True)), [10, 20, 30])
        self.post("doctor", reverse("medical:visit_template_action", args=[c.pk, "up"]))
        self.assertEqual(list(VisitTemplate.objects.filter(specialty="general").values_list("name", flat=True)), ["ب", "ج", "أ"])
        self.post("doctor", reverse("medical:visit_template_action", args=[b.pk, "up"]))  # already first: nothing changes
        self.assertEqual(list(VisitTemplate.objects.filter(specialty="general").values_list("name", flat=True)), ["ب", "ج", "أ"])
        other.refresh_from_db()
        self.assertEqual(other.position, 10)  # another specialty is another group
        self.assertEqual(self.names("doctor")[:3], ["ب", "ج", "أ"])  # the sheet follows the order
        page = self.get("doctor", LIST)
        self.assertContains(page, "data-vt-up")
        self.assertContains(page, "data-vt-down")

    def test_copy_delete_and_the_scopes(self):
        shared = self.make(owner=None, name="قالب جماعي", lines=[{"reference": None, "name": "غسول", "dose": "", "frequency": "", "duration": "", "route": "", "notes": ""}], diagnosis="تشخيص")
        self.assertContains(self.get("doctor", LIST, scope="shared"), "قالب جماعي")
        self.assertNotContains(self.get("doctor", LIST, scope="mine"), "قالب جماعي")
        self.assertEqual(self.post("doctor", reverse("medical:visit_template_action", args=[shared.pk, "copy"])).status_code, 302)
        mine = VisitTemplate.objects.get(owner=self.users["doctor"])
        self.assertEqual((mine.name, mine.diagnosis, mine.lines, mine.seed_key), ("قالب جماعي", "تشخيص", shared.lines, ""))
        self.post("doctor", reverse("medical:visit_template_action", args=[shared.pk, "copy"]))
        self.assertEqual(sorted(VisitTemplate.objects.filter(owner=self.users["doctor"]).values_list("name", flat=True)), ["قالب جماعي", "قالب جماعي (نسخة)"])
        mine.lines.append({"reference": None, "name": "x"})  # changing the copy never changes the shared one
        mine.save()
        shared.refresh_from_db()
        self.assertEqual(len(shared.lines), 1)
        delete = reverse("medical:visit_template_action", args=[mine.pk, "delete"])
        self.assertEqual(self.post("doctor", delete).status_code, 302)
        self.assertFalse(VisitTemplate.objects.filter(pk=mine.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="delete_visit_template", object_id=str(mine.pk)).exists())
        self.assertEqual(self.post("doctor", reverse("medical:visit_template_action", args=[shared.pk, "delete"])).status_code, 403)
        self.assertEqual(self.post("manager", reverse("medical:visit_template_action", args=[shared.pk, "delete"])).status_code, 302)
        self.assertFalse(VisitTemplate.objects.filter(pk=shared.pk).exists())

    def test_filters_language_and_the_english_page(self):
        self.make(owner="doctor", name="Cold", lang="en", specialty="general")
        self.make(owner="doctor", name="برد", lang="ar", specialty="general")
        self.make(owner="doctor", name="عين", lang="ar", specialty="eye")
        arabic = self.get("doctor", LIST, scope="mine")
        self.assertContains(arabic, "برد")
        self.assertNotContains(arabic, ">Cold<")  # the page's own language first
        self.assertContains(self.get("doctor", LIST, scope="mine", tl="all"), ">Cold<")
        self.assertContains(self.get("doctor", LIST, scope="mine", tl="en"), ">Cold<")
        self.assertNotContains(self.get("doctor", LIST, scope="mine", specialty="eye"), "برد")
        self.assertContains(self.get("doctor", LIST, scope="mine", q="عي"), "عين")
        self.assertNotContains(self.get("doctor", LIST, scope="mine", q="zzz"), "data-vt-row=")
        english = self.get("doctor", LIST, scope="mine", lang="en")
        self.assertContains(english, "Visit templates")
        self.assertContains(english, 'dir="ltr"')
        self.assertContains(self.get("doctor", NEW, lang="en"), "Template name")

    def test_the_tab_and_the_phone_band(self):
        for who in ("owner", "manager", "doctor"):
            self.assertContains(self.get(who, reverse("medical:patients")), "data-tab-templates")
        page = self.get("doctor", LIST).content.decode()
        self.assertContains(self.get("doctor", LIST), "data-med-tabs")
        self.assertTrue(page.index('class="op-title-row') < page.index("data-med-tabs"))  # the navy band is the page's first title row
        self.assertNotRegex(page, r"<td[^>]*data-label=")  # table_cards.js labels the cells


# ---- the visit form: the control, and the POST shape that never changes ----

class VisitFormTests(Setup):
    def setUp(self):
        super().setUp()
        self.url = reverse("medical:visit_new", args=[self.patient.pk])

    def names_in_form(self, html):
        form = re.search(r"<form[^>]*data-visit-form.*?</form>", html, re.S).group(0)
        return set(re.findall(r'<(?:input|select|textarea|button)\b[^>]*?\sname="([^"]+)"', form))

    def test_the_control_is_there_and_outside_the_posted_form(self):
        html = self.get("doctor", self.url).content.decode()
        self.assertIn("data-vt-bar", html)
        self.assertIn("data-vt-open", html)
        self.assertIn("data-vt-save-open", html)
        self.assertIn("hesba/js/visit_templates.js?v=m1-1", html)
        self.assertIn("hesba/css/visit_templates.css?v=m1-2", html)
        self.assertIn("hesba/js/rx_picker.js?v=m1-1", html)
        self.assertLess(html.index('class="op-title-row'), html.index("data-vt-bar"))  # after the page's own head, so the phone band stays on it
        self.assertLess(html.index("data-vt-bar"), html.index("data-visit-form"))
        end = html.index("</form>", html.index("data-visit-form"))
        for sheet in ('id="vt-sheet"', 'id="vt-save"'):
            self.assertGreater(html.index(sheet), end)  # the sheets are outside the visit's form
        self.assertRegex(html, r'<input id="vt-q" type="search" form="vt-detached"')  # never posted with the visit
        self.assertRegex(html, r'<button class="op-secondary vt-open" type="button"')
        self.assertRegex(html, r'<button class="op-secondary" type="button" data-vt-save-open hidden')
        self.assertNotRegex(html, r'<button(?![^>]*type="button")[^>]*data-vt-(?!save-go)')  # nothing of the control submits the visit
        self.assertIn("<section class=\"vt-bar\" data-vt-bar", html)
        self.assertRegex(html, r'data-vt-bar[^>]* hidden>')  # without JavaScript the form is exactly as before

    def test_the_post_shape_is_the_visit_forms_own(self):
        html = self.get("doctor", self.url).content.decode()
        self.assertEqual(self.names_in_form(html), VISIT_FORM_FIELDS)
        self.assertNotIn("name=", re.search(r'<section class="vt-bar".*?</section>', html, re.S).group(0))  # nothing in the control has a name
        english = self.get("doctor", self.url, lang="en").content.decode()
        self.assertEqual(self.names_in_form(english), VISIT_FORM_FIELDS)
        saved = services.save_visit(self.patient, {"diagnosis": "برد", "visit_date": TODAY.isoformat(), "doctor": self.sara}, self.users["doctor"])
        edit = self.get("doctor", reverse("medical:visit_edit", args=[saved.pk])).content.decode()
        self.assertEqual(self.names_in_form(edit), VISIT_FORM_FIELDS)
        self.assertIn("data-vt-bar", edit)  # a saved visit offers it too

    def test_a_visit_saved_with_or_without_a_template_posts_the_same_fields(self):
        """What the browser sends after applying a template is the same fields with the texts and lines filled: the
        server saves both through the one path, and the template leaves no trace on the visit."""

        template = self.make(owner="doctor", complaint="كحة من يومين", examination="الصدر: سليم", treatment="راحة", diagnosis="",
                             lines=[{"reference": self.zuba.pk, "name": "Zubacillin", "dose": "كبسولة", "frequency": "3 مرات", "duration": "5 أيام", "route": "", "notes": ""}])
        base = {"visit_date": TODAY.isoformat(), "doctor": str(self.sara.pk), "lang": "ar", "blood_pressure": "120/80", "pulse": "80", "temperature": "37", "weight": "70",
                "height": "170", "follow_up_date": ""}
        typed = {"complaint": "كتبتها بإيدي", "examination": "", "diagnosis": "التهاب", "treatment": ""}
        rx_empty = {f"rx_{f}": [] for f in prescribing.FIELDS}
        without = self.post("doctor", self.url, {**base, **typed, **rx_empty})
        applied_texts = {"complaint": typed["complaint"], "examination": template.examination, "diagnosis": typed["diagnosis"], "treatment": template.treatment}  # empty boxes filled, the typed one kept
        line = template.lines[0]
        applied = self.post("doctor", self.url, {**base, **applied_texts, "rx_id": [""], "rx_reference": [str(line["reference"])], "rx_name": [line["name"]], "rx_dose": [line["dose"]],
                                                 "rx_frequency": [line["frequency"]], "rx_duration": [line["duration"]], "rx_route": [""], "rx_notes": [""]})
        self.assertEqual((without.status_code, applied.status_code), (302, 302))
        plain, filled = ClinicalVisit.objects.filter(pk__gt=self.visit.pk).order_by("pk")
        self.assertEqual((plain.complaint, filled.complaint), ("كتبتها بإيدي", "كتبتها بإيدي"))  # the doctor's own words are never replaced
        self.assertEqual((filled.examination, filled.treatment, filled.diagnosis), ("الصدر: سليم", "راحة", "التهاب"))
        self.assertEqual((plain.medicines.count(), filled.medicines.count()), (0, 1))
        self.assertEqual(filled.medicines.get().reference_id, self.zuba.pk)  # through prescribing.resolve, a snapshot like a hand-picked line
        for visit in (plain, filled):
            self.assertEqual({field.name for field in visit._meta.get_fields()} & {"template", "visit_template"}, set())

    def test_the_vet_form_offers_it_with_the_animal_untouched(self):
        from . import vet

        self.set_sub_activity("vet")
        animal = vet.save_animal(self.patient, {"name": "مشمش", "species": "cat"}, self.owner)
        html = self.get("doctor", self.url, animal=animal.pk).content.decode()
        self.assertIn("data-vt-bar", html)
        self.assertIn('data-default-specialty="vet"', html)
        self.assertEqual(self.names_in_form(html), VISIT_FORM_FIELDS | {"animal"})
        self.assertIn("vt-save-specialty", html)
        self.assertEqual(re.findall(r'<option value="(\w+)"( selected)?>', re.search(r'id="vt-save-specialty".*?</select>', html, re.S).group(0)), [("vet", " selected")])

    def test_reception_never_sees_the_control(self):
        for who in NON_CLINICAL:
            self.assertEqual(self.get(who, self.url).status_code, 403)


# ---- the script, the stylesheet and the templates ----

class StaticTests(TestCase):
    def read(self, name):
        with open(finders.find(name), encoding="utf-8") as handle:
            return handle.read()

    def test_the_script_never_overwrites_and_never_submits(self):
        script = self.read("hesba/js/visit_templates.js")
        for pinned in ("if (field.value.trim() === '') {",  # only an empty box is filled
                       "kept.push(say('f_' + name));",  # a box with text is left alone and said so
                       "if (event.key === 'Enter') {",  # Enter in the search never saves the visit
                       "event.preventDefault();  // Enter in the name box saves the template, never the visit",
                       "if (event.key === 'Escape') { event.preventDefault(); close(); }",
                       "if (event.key === 'ArrowDown')",
                       "rx.room() > 0 && rx.add(spec)",  # lines are appended after the existing ones, up to the cap
                       "'X-CSRFToken'"):
            with self.subTest(pinned=pinned[:48]):
                self.assertIn(pinned, script)
        self.assertNotIn("innerHTML", script)  # template text is set as text, never as markup
        self.assertNotIn("localStorage", script)  # no clinical text kept in the browser
        self.assertNotRegex(script, r"\.name\s*=")  # nothing is given a name inside the visit's form
        rx = self.read("hesba/js/rx_picker.js")
        self.assertIn("window.HesbaRx = {", rx)
        self.assertIn("if (lines().length >= max) { return null; }", rx)

    def test_tokens_only(self):
        css = self.read("hesba/css/visit_templates.css")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", self.read("hesba/css/tokens.css")))
        self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b", css), [])
        self.assertNotRegex(css, r"rgba?\(")
        self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])
        self.assertIn("min-height:var(--hs-control-height)", css)  # 44px targets

    def test_templates_keep_their_comments_on_one_line_and_never_label_cells(self):
        root = Path(__file__).resolve().parent.parent / "templates" / "medical"
        for name in ("_vt_bar.html", "_vt_sheets.html", "_vt_filter_fields.html", "visit_templates.html", "visit_template_form.html"):
            text = (root / name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                for opening in re.finditer(r"\{#", text):
                    line_end = text.index("\n", opening.start()) if "\n" in text[opening.start():] else len(text)
                    self.assertIn("#}", text[opening.start():line_end])
                self.assertNotRegex(text, r"<td[^>]*data-label=")
                self.assertNotIn("{% comment", text)


# ---- nothing else moves ----

class NothingElseTests(Setup):
    def test_templates_touch_no_money_stock_or_visit(self):
        from cashboxes.models import CashboxMovement
        from inventory.models import StockMovement
        from sales.models import SalesInvoice

        watched = (CashboxMovement, StockMovement, SalesInvoice, ClinicalVisit)
        before = [model.objects.count() for model in watched]
        self.get("doctor", DATA)
        self.post("doctor", NEW, self.form_post(name="جديد"))
        self.post("doctor", SAVE, {"name": "من الكشف", "complaint": "x", "specialty": "general", "text_lang": "ar"})
        mine = VisitTemplate.objects.get(name="جديد")
        for action in ("up", "down", "off", "on", "copy", "delete"):
            self.post("doctor", reverse("medical:visit_template_action", args=[mine.pk, action]))
        self.assertEqual([model.objects.count() for model in watched], before)
