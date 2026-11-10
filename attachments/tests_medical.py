"""M5 (HG-083): scans, lab reports, photos and reports on a patient's file and on a visit.

Who sees and who adds (the clinic's own rule), who deletes (the records permission), the entity an upload is stamped with, the
categories and their words in both languages, the caps, the audit, the card on the two screens, and that no money, stock or
ledger row is ever touched. Work orders keep their own stages."""

import re
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from entities import services as entity_services
from entities.models import Entity, EntityMembership
from hesba_testing.factories import grant, make_customer, make_role, make_user, make_user_profile
from inventory.models import StockMovement
from ledger.models import JournalEntry
from medical import services as med
from medical.tests import CLINIC, person
from permissions.models import Permission, RoleCode
from purchases.models import SupplierLedgerEntry
from reports.tests_dashboard import prepared_client
from sales.models import CustomerLedgerEntry, SalesInvoice
from staff.services import save_employee

from . import services, ui
from .models import Attachment, Kind, Stage
from .tests import JPEG, PDF, PNG, WEBP, upload


class ClinicSetup(TestCase):
    def setUp(self):
        prepared_client("medical", "clinic", CLINIC)
        self.owner = person(RoleCode.OWNER, "m5_owner")
        self.manager = person(RoleCode.MANAGER, "m5_manager")
        self.patient = make_customer(customer_code="P-5", name="منى علي", phone="01001234567")
        self.file = med.file_for(self.patient)
        self.visit = med.save_visit(self.patient, {"complaint": "كحة", "diagnosis": "التهاب"}, self.owner)
        self.doctor = self.doctor_user("m5_doctor")
        self.nobody = person(RoleCode.CASHIER, "m5_nobody")  # no records right and no employee: not a doctor
        self.viewer = self.view_only("m5_viewer")

    def doctor_user(self, username):
        user = person(RoleCode.CASHIER, username)
        employee = save_employee({"name": f"د. {username}"}, self.owner)
        employee.user = user
        employee.save()
        return user

    def view_only(self, username):
        user = make_user(username=username)
        role = make_role(f"ROLE-{username}")
        make_user_profile(user=user, role=role)
        permission, _ = Permission.objects.get_or_create(code=med.VIEW, defaults={"name_ar": "عرض", "module": "medical"})
        grant(role, permission)
        return user

    def url(self, name, *args):
        return reverse(f"attachments:{name}", args=args)

    def post_visit(self, **fields):
        data = {"file": upload(JPEG, "xray.jpg", "image/jpeg"), "kind": "photo", "stage": "scan"}
        data.update(fields)
        return self.client.post(self.url("visit_upload", self.visit.pk), data)

    def counts(self):
        return tuple(model.objects.count() for model in (SalesInvoice, CashboxMovement, StockMovement, JournalEntry, CustomerLedgerEntry, SupplierLedgerEntry))


class AccessMatrixTests(ClinicSetup):
    def test_doctor_manager_and_owner_attach_and_read_on_a_visit_and_a_file(self):
        for user in (self.doctor, self.manager, self.owner):
            self.client.force_login(user)
            for name, pk in (("visit_upload", self.visit.pk), ("patient_file_upload", self.file.pk)):
                with self.subTest(user=user.username, route=name):
                    response = self.client.post(self.url(name, pk), {"file": upload(JPEG, "a.jpg", "image/jpeg"), "kind": "photo", "stage": "scan"})
                    self.assertEqual(response.status_code, 201, response.content)
                    row = Attachment.objects.get(pk=response.json()["attachment"]["id"])
                    self.assertEqual((row.stage, row.kind, row.uploaded_by), ("scan", "photo", user))
                    self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 200)
            self.assertEqual(self.client.get(self.url("visit_list", self.visit.pk)).status_code, 200)
            self.assertEqual(self.client.get(self.url("patient_file_list", self.file.pk)).status_code, 200)

    def test_a_user_without_records_rights_gets_403_everywhere(self):
        row = services.add_attachment(self.visit, JPEG, self.owner, stage=Stage.SCAN)
        mine = services.add_attachment(self.file, PNG, self.owner, stage=Stage.LAB)
        self.client.force_login(self.nobody)
        for url in (self.url("visit_list", self.visit.pk), self.url("patient_file_list", self.file.pk), self.url("file", row.pk), self.url("file", mine.pk)):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        self.assertEqual(self.post_visit().status_code, 403)
        self.assertEqual(self.client.post(self.url("patient_file_upload", self.file.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg"), "stage": "lab"}).status_code, 403)
        self.assertEqual(self.client.post(self.url("delete", row.pk)).status_code, 403)
        with self.assertRaises(PermissionDenied):
            services.add_attachment(self.visit, JPEG, self.nobody, stage=Stage.SCAN)
        self.assertEqual(self.client.get(reverse("medical:file", args=[self.patient.pk])).status_code, 403)
        self.assertEqual(self.client.get(self.url("visit_list", self.visit.pk) + "?lang=en").json()["error"], services.MESSAGES["en"]["denied"])
        self.assertEqual(Attachment.objects.count(), 2)

    def test_a_view_only_user_reads_but_cannot_add_and_gets_the_clinics_own_reason(self):
        row = services.add_attachment(self.visit, JPEG, self.owner, stage=Stage.SCAN)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("visit_list", self.visit.pk)).status_code, 200)
        denied = self.post_visit(lang="en")
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(denied.json()["error"], services.MESSAGES["en"]["medical_read_only"])
        self.assertEqual(self.post_visit().json()["error"], services.MESSAGES["ar"]["medical_read_only"])
        with self.assertRaises(PermissionDenied):
            services.add_attachment(self.visit, PNG, self.viewer, stage=Stage.SCAN)
        self.assertEqual(self.client.post(self.url("delete", row.pk)).status_code, 403)
        self.assertEqual(Attachment.objects.count(), 1)

    def test_only_the_records_permission_deletes_not_merely_a_doctor(self):
        for user, status in ((self.doctor, 403), (self.nobody, 403), (self.viewer, 403), (self.manager, 200), (self.owner, 200)):
            row = services.add_attachment(self.visit, JPEG, self.owner, stage=Stage.SCAN)
            self.client.force_login(user)
            response = self.client.post(self.url("delete", row.pk) + "?lang=en")
            self.assertEqual(response.status_code, status, user.username)
            self.assertEqual(Attachment.objects.filter(pk=row.pk).exists(), status == 403, user.username)
            if user == self.doctor:  # a doctor may add (can_write) but not remove: the role permission is the rule
                self.assertTrue(med.can_write(user))
                self.assertEqual(response.json()["error"], services.MESSAGES["en"]["medical_delete_denied"])
                self.assertEqual(self.post_visit().status_code, 201)

    def test_an_anonymous_request_is_sent_to_login(self):
        row = services.add_attachment(self.visit, JPEG, self.owner, stage=Stage.SCAN)
        for method, url in ((self.client.get, self.url("file", row.pk)), (self.client.get, self.url("visit_list", self.visit.pk)),
                            (self.client.post, self.url("visit_upload", self.visit.pk)), (self.client.post, self.url("patient_file_upload", self.file.pk))):
            response = method(url)
            self.assertEqual(response.status_code, 302)
            self.assertIn("login", response["Location"])

    def test_csrf_is_still_enforced_on_the_new_upload_routes(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        for name, pk in (("visit_upload", self.visit.pk), ("patient_file_upload", self.file.pk)):
            response = strict.post(self.url(name, pk), {"file": upload(JPEG, "a.jpg", "image/jpeg"), "stage": "scan"})
            self.assertEqual(response.status_code, 403, name)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_attachments_exist_only_on_a_medical_install_and_a_walk_in_has_no_file(self):
        row = services.add_attachment(self.visit, JPEG, self.owner, stage=Stage.SCAN)
        walk_in = med.file_for(make_customer(customer_code="WALK-IN", name="عميل نقدي"))
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url("patient_file_list", walk_in.pk)).status_code, 404)
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("visit_list", self.visit.pk)).status_code, 404)
        self.assertEqual(self.post_visit().status_code, 404)

    def test_an_unknown_visit_is_404(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url("visit_list", 99999)).status_code, 404)
        self.assertEqual(self.client.post(self.url("visit_upload", 99999), {"file": upload(JPEG, "a.jpg", "image/jpeg")}).status_code, 404)


class EntityTests(ClinicSetup):
    def setUp(self):
        super().setUp()
        self.main = Entity.objects.get(is_main=True)
        self.branch = entity_services.save_entity({"code": "BR5", "name_ar": "فرع المعادي", "activity_slug": "medical", "sub_activity_slug": "clinic"}, self.owner)
        self.assertTrue(entity_services.is_multi_entity())
        self.main_manager = person(RoleCode.MANAGER, "m5_main_mgr")
        EntityMembership.objects.create(user=self.main_manager, entity=self.main, is_default=True)
        self.branch_manager = person(RoleCode.MANAGER, "m5_branch_mgr")
        EntityMembership.objects.create(user=self.branch_manager, entity=self.branch, is_default=True)

    def test_an_upload_is_stamped_with_the_entity_it_was_made_in_and_others_get_404(self):
        self.client.force_login(self.main_manager)
        mine = Attachment.objects.get(pk=self.post_visit().json()["attachment"]["id"])
        self.client.force_login(self.branch_manager)
        theirs = Attachment.objects.get(pk=self.post_visit().json()["attachment"]["id"])
        self.assertEqual((mine.entity, theirs.entity), (self.main, self.branch))
        # each sees their own entity's file (positive control) and the other's answers as if it did not exist
        self.assertEqual(self.client.get(self.url("file", theirs.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("file", mine.pk)).status_code, 404)
        self.assertEqual([row["id"] for row in self.client.get(self.url("visit_list", self.visit.pk)).json()["attachments"]], [theirs.pk])
        self.client.force_login(self.main_manager)
        self.assertEqual(self.client.get(self.url("file", mine.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("file", theirs.pk)).status_code, 404)
        self.assertEqual([row["id"] for row in self.client.get(self.url("visit_list", self.visit.pk)).json()["attachments"]], [mine.pk])
        # the owner, working across the whole group, reads both
        self.client.force_login(self.owner)
        self.assertEqual((self.client.get(self.url("file", mine.pk)).status_code, self.client.get(self.url("file", theirs.pk)).status_code), (200, 200))

    def test_the_patient_file_lists_only_this_entitys_visit_attachments(self):
        self.client.force_login(self.main_manager)
        self.post_visit()
        self.client.force_login(self.branch_manager)
        self.post_visit(file=upload(PNG, "branch-lab.png", "image/png"), stage="lab")
        page = self.client.get(reverse("medical:file", args=[self.patient.pk]))
        self.assertContains(page, "branch-lab.png")
        self.assertNotContains(page, "xray.jpg")

    def test_a_group_wide_owners_upload_belongs_to_the_main_entity(self):
        self.client.force_login(self.owner)
        row = Attachment.objects.get(pk=self.post_visit().json()["attachment"]["id"])
        self.assertIsNone(row.entity)  # NULL is the main entity (HG-034)
        self.client.force_login(self.main_manager)
        self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 200)
        self.client.force_login(self.branch_manager)
        self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 404)

    def test_an_entity_that_is_not_medical_cannot_use_them(self):
        shop = entity_services.save_entity({"code": "SH5", "name_ar": "محل", "activity_slug": "commercial", "sub_activity_slug": "retail"}, self.owner)
        user = person(RoleCode.MANAGER, "m5_shop_mgr")
        EntityMembership.objects.create(user=user, entity=shop, is_default=True)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url("visit_list", self.visit.pk)).status_code, 404)


class CategoryAndCapTests(ClinicSetup):
    def test_each_clinic_category_is_kept_with_its_words_in_both_languages(self):
        words = {"ar": {"scan": "أشعة", "lab": "تحاليل", "photo": "صور", "report": "تقارير"},
                 "en": {"scan": "Scan / X-ray", "lab": "Lab report", "photo": "Photo", "report": "Report"}}
        for stage, raw, kind, name in (("scan", JPEG, "photo", "chest.jpg"), ("lab", PDF, "document", "cbc.pdf"), ("photo", WEBP, "photo", "rash.webp"), ("report", PDF, "document", "report.pdf")):
            row = services.add_attachment(self.visit, upload(raw, name, "x/y"), self.owner, kind=kind, stage=stage)
            self.assertEqual((Attachment.objects.get(pk=row.pk).stage, row.original_filename), (stage, name))
        for lang in ("ar", "en"):
            for stage, label in words[lang].items():
                self.assertEqual(ui.MEDICAL_WORDS[lang][stage], label)
            card = ui.medical_card(self.visit, self.owner, lang)
            self.assertEqual([c["label"] for c in card["categories"]], [words[lang][s] for s in ("scan", "lab", "photo", "report")])
            self.assertEqual({item["category"] for item in card["items"]}, set(words[lang].values()))

    def test_a_clinic_never_takes_receive_deliver_other_or_a_signature(self):
        for stage in ("receive", "deliver", "other", "", "nonsense"):
            with self.assertRaises(ValidationError) as caught:
                services.add_attachment(self.visit, JPEG, self.owner, stage=stage)
            self.assertIn(services.MESSAGES["ar"]["category"], caught.exception.messages)
        with self.assertRaises(ValidationError):
            services.add_attachment(self.visit, PNG, self.owner, kind=Kind.SIGNATURE, stage=Stage.SCAN)
        with self.assertRaises(ValidationError):
            services.add_signature_png(self.visit, "data:image/png;base64,AAAA", self.owner, stage=Stage.SCAN)
        self.client.force_login(self.owner)
        response = self.post_visit(stage="deliver", lang="en")
        self.assertEqual((response.status_code, response.json()["error"]), (400, services.MESSAGES["en"]["category"]))
        self.assertEqual(self.post_visit(kind="signature").status_code, 400)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_a_work_order_still_refuses_the_clinic_categories_and_keeps_its_own(self):
        stages = services.OWNERS["workorders.workorder"]["stages"]
        self.assertEqual(tuple(stages), (Stage.RECEIVE, Stage.DELIVER, Stage.OTHER))
        self.assertEqual(tuple(services.OWNERS["purchases.purchaseinvoice"]["stages"]), (Stage.RECEIVE, Stage.DELIVER, Stage.OTHER))
        self.assertNotIn("scan", [stage.value for stage in stages])
        # the check itself, as add_attachment runs it
        words = services.MESSAGES["ar"]
        for stage in ("scan", "lab", "photo", "report"):
            with self.assertRaises(ValidationError):
                services._check_kind_stage(Kind.PHOTO, stage, words, services.OWNERS["workorders.workorder"])
        for stage in ("receive", "deliver", "other"):
            services._check_kind_stage(Kind.SIGNATURE, stage, words, services.OWNERS["workorders.workorder"])

    def test_the_per_file_and_per_record_caps_and_the_type_rules_hold(self):
        self.client.force_login(self.owner)
        with override_settings(ATTACHMENTS_MAX_BYTES=len(JPEG) - 1):
            response = self.post_visit()  # stopped while it streams: 413
            self.assertEqual(response.status_code, 413)
            self.assertIn("أكبر من المسموح", response.json()["error"])
            with self.assertRaises(ValidationError):  # and by the service itself, whatever the caller
                services.add_attachment(self.visit, JPEG, self.owner, stage=Stage.SCAN)
        with override_settings(ATTACHMENTS_MAX_PER_OWNER=2):
            self.assertEqual(self.post_visit().status_code, 201)
            self.assertEqual(self.post_visit().status_code, 201)
            third = self.post_visit(lang="en")
            self.assertEqual(third.status_code, 400)
            self.assertIn("most attachments", third.json()["error"])
            # the cap is per record: the patient's file still takes its own
            self.assertEqual(self.client.post(self.url("patient_file_upload", self.file.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg"), "stage": "lab"}).status_code, 201)
        with override_settings(ATTACHMENTS_MAX_BYTES_PER_OWNER=len(PNG) + 10):
            first = self.client.post(self.url("patient_file_upload", self.file.pk), {"file": upload(PNG, "b.png", "image/png"), "stage": "photo"})
            second = self.client.post(self.url("patient_file_upload", self.file.pk), {"file": upload(PNG, "c.png", "image/png"), "stage": "photo"})
            self.assertEqual((first.status_code, second.status_code), (400, 400))  # the earlier JPEG already uses the room
        dicom = b"\x00" * 128 + b"DICM" + b"\x00" * 64
        for raw, name in ((dicom, "scan.dcm"), (b"<svg onload=alert(1)>", "x.svg"), (b"<html></html>", "x.html"), (b"GIF89a....", "x.gif")):
            self.assertEqual(self.post_visit(file=upload(raw, name, "application/octet-stream")).status_code, 400, name)
        pdf = self.post_visit(file=upload(PDF, "lab.pdf", "application/pdf"), kind="document", stage="lab")
        self.assertEqual(pdf.status_code, 201)
        photo_pdf = self.post_visit(file=upload(PDF, "lab.pdf", "application/pdf"), kind="photo", stage="lab")
        self.assertEqual(photo_pdf.status_code, 400)  # a "photo" must be an image

    def test_an_active_pdf_is_refused(self):
        self.client.force_login(self.owner)
        evil = PDF + b"/JavaScript"
        self.assertEqual(self.post_visit(file=upload(evil, "x.pdf", "application/pdf"), kind="document", stage="report").status_code, 400)

    def test_the_file_is_served_safely_images_inline_and_a_pdf_as_a_download(self):
        image = services.add_attachment(self.visit, JPEG, self.owner, stage=Stage.SCAN)
        doc = services.add_attachment(self.file, upload(PDF, "lab.pdf", "application/pdf"), self.owner, kind=Kind.DOCUMENT, stage=Stage.LAB)
        self.client.force_login(self.owner)
        shown, got = self.client.get(self.url("file", image.pk)), self.client.get(self.url("file", doc.pk))
        self.assertTrue(shown["Content-Disposition"].startswith("inline"))
        self.assertTrue(got["Content-Disposition"].startswith("attachment"))
        for response in (shown, got):
            self.assertEqual(response["X-Content-Type-Options"], "nosniff")
            self.assertEqual(response["Content-Security-Policy"], "default-src 'none'; sandbox")
            self.assertIn("private", response["Cache-Control"])


class AuditAndMoneyTests(ClinicSetup):
    def test_upload_and_delete_are_audited_with_the_category(self):
        self.client.force_login(self.owner)
        row_id = self.post_visit(stage="lab", file=upload(PNG, "cbc.png", "image/png")).json()["attachment"]["id"]
        added = AuditLog.objects.get(module="attachments", action="add_attachment", object_id=str(row_id))
        self.assertEqual((added.actor, added.after_data["owner"], added.after_data["owner_id"], added.after_data["stage"], added.after_data["filename"]),
                         (self.owner, "medical.clinicalvisit", self.visit.pk, "lab", "cbc.png"))
        self.assertEqual(self.client.post(self.url("delete", row_id)).status_code, 200)
        removed = AuditLog.objects.get(module="attachments", action="delete_attachment", object_id=str(row_id))
        self.assertEqual((removed.actor, removed.before_data["owner"], removed.before_data["stage"]), (self.owner, "medical.clinicalvisit", "lab"))
        self.assertFalse(Attachment.objects.filter(pk=row_id).exists())

    def test_no_invoice_stock_cashbox_or_ledger_row_is_created_by_uploading_or_deleting(self):
        before = self.counts()
        self.client.force_login(self.owner)
        ids = [self.post_visit().json()["attachment"]["id"],
               self.client.post(self.url("patient_file_upload", self.file.pk), {"file": upload(PDF, "r.pdf", "application/pdf"), "kind": "document", "stage": "report"}).json()["attachment"]["id"]]
        self.assertEqual(self.counts(), before)
        for pk in ids:
            self.assertEqual(self.client.post(self.url("delete", pk)).status_code, 200)
        self.assertEqual(self.counts(), before)


class ScreenTests(ClinicSetup):
    def file_page(self, user=None, lang="ar"):
        self.client.force_login(user or self.owner)
        return self.client.get(reverse("medical:file", args=[self.patient.pk]) + f"?lang={lang}")

    def visit_page(self, user=None, lang="ar"):
        self.client.force_login(user or self.owner)
        return self.client.get(reverse("medical:visit_edit", args=[self.visit.pk]) + f"?lang={lang}")

    def test_the_patient_file_shows_the_card_with_categories_and_the_dicom_note_in_both_languages(self):
        for lang, words in (("ar", ("أشعة", "تحاليل", "صور", "تقارير", "DICOM مش مدعومة")), ("en", ("Scan / X-ray", "Lab report", "Photo", "Report", "DICOM files are not supported"))):
            page = self.file_page(lang=lang)
            self.assertContains(page, 'id="med-attachments"')
            for word in words:
                self.assertContains(page, word)
            self.assertContains(page, f'action="{reverse("attachments:patient_file_upload", args=[self.file.pk])}?lang={lang}"')
            self.assertEqual(page.content.decode().count('name="stage"'), 4)  # one chip per category
            self.assertContains(page, "attachments.css?v=m5-1")
            self.assertContains(page, "attachments.js?v=m5-1")

    def test_the_visit_screen_shows_the_card_only_for_a_saved_visit(self):
        page = self.visit_page(lang="en")
        self.assertContains(page, 'id="med-attachments"')
        self.assertContains(page, f'action="{reverse("attachments:visit_upload", args=[self.visit.pk])}?lang=en"')
        self.assertContains(page, "DICOM files are not supported")
        self.assertNotContains(page, "data-att-after-save")
        # the card's forms sit after the visit form, never inside it (forms cannot nest)
        html = page.content.decode()
        self.assertLess(html.index("</form>", html.index("data-visit-form")), html.index('id="med-attachments"'))
        self.client.force_login(self.owner)
        fresh = self.client.get(reverse("medical:visit_new", args=[self.patient.pk]))
        self.assertNotContains(fresh, 'id="med-attachments"')
        self.assertContains(fresh, "data-att-after-save")

    def test_uploads_show_category_date_and_uploader_with_a_thumbnail_for_images_and_a_pdf_chip(self):
        image = services.add_attachment(self.visit, upload(JPEG, "chest.jpg", "image/jpeg"), self.owner, stage=Stage.SCAN)
        doc = services.add_attachment(self.visit, upload(PDF, "cbc.pdf", "application/pdf"), self.manager, kind=Kind.DOCUMENT, stage=Stage.LAB)
        page = self.visit_page()
        self.assertContains(page, f'data-attachment="{image.pk}"')
        self.assertContains(page, f'<img src="{reverse("attachments:file", args=[image.pk])}"')
        self.assertContains(page, "att-pdf")
        self.assertContains(page, "chest.jpg")
        self.assertContains(page, "cbc.pdf")
        self.assertContains(page, "att-cat-scan")
        self.assertContains(page, "att-cat-lab")
        self.assertContains(page, "m5_manager")
        self.assertContains(page, doc.created_at.astimezone().strftime("%Y-%m-%d"))
        self.assertContains(page, "data-att-delete")  # the owner holds the records permission

    def test_delete_buttons_follow_the_records_permission_and_a_view_only_user_sees_no_add_form(self):
        services.add_attachment(self.visit, JPEG, self.owner, stage=Stage.SCAN)
        doctor_page = self.file_page(self.doctor)
        self.assertContains(doctor_page, "data-att-upload")  # a doctor may add
        self.assertNotContains(doctor_page, "data-att-delete")  # but not delete
        viewer_page = self.file_page(self.viewer)
        self.assertNotContains(viewer_page, "data-att-upload")
        self.assertNotContains(viewer_page, "data-att-delete")
        self.assertContains(viewer_page, "data-att-blocked")
        self.assertContains(viewer_page, "att-item")  # a view-only user still reads what is attached
        self.assertEqual(self.client.get(reverse("medical:visit_edit", args=[self.visit.pk])).status_code, 403)  # the visit form is for writers
        self.assertEqual(self.file_page(self.nobody).status_code, 403)

    def test_the_patient_file_groups_the_visit_attachments_and_links_each_to_its_visit(self):
        second = med.save_visit(self.patient, {"complaint": "ألم", "diagnosis": "شد"}, self.owner)
        a = services.add_attachment(self.visit, upload(JPEG, "one.jpg", "image/jpeg"), self.owner, stage=Stage.SCAN)
        b = services.add_attachment(second, upload(PNG, "two.png", "image/png"), self.owner, stage=Stage.PHOTO)
        own = services.add_attachment(self.file, upload(PNG, "own.png", "image/png"), self.owner, stage=Stage.LAB)
        page = self.file_page()
        html = page.content.decode()
        self.assertContains(page, "data-visit-attachments")
        section = html[html.index("data-visit-attachments"):]
        self.assertLess(section.index("two.png"), section.index("one.jpg"))  # newest first
        self.assertNotIn("own.png", section)  # the patient's own documents are in the card above
        self.assertIn(f'data-attachment="{own.pk}"', html[:html.index("data-visit-attachments")])
        self.assertContains(page, reverse("medical:visit_edit", args=[second.pk]))
        self.assertLessEqual({a.pk, b.pk}, {int(x) for x in re.findall(r'data-attachment="(\d+)"', section)})

    def test_another_patients_attachments_never_show(self):
        other = make_customer(customer_code="P-6", name="سمير")
        visit = med.save_visit(other, {"diagnosis": "x"}, self.owner)
        services.add_attachment(visit, upload(JPEG, "secret.jpg", "image/jpeg"), self.owner, stage=Stage.SCAN)
        self.assertNotContains(self.file_page(), "secret.jpg")
        self.assertNotContains(self.visit_page(), "secret.jpg")

    def test_listing_a_patients_visit_attachments_does_not_grow_with_the_number_of_visits(self):
        def queries():
            with CaptureQueriesContext(connection) as captured:
                result = ui.patient_visit_files(self.patient, self.owner, "ar")
            return len(captured), len(result["items"])

        services.add_attachment(self.visit, JPEG, self.owner, stage=Stage.SCAN)
        few, few_items = queries()
        for index in range(5):
            visit = med.save_visit(self.patient, {"diagnosis": f"d{index}"}, self.owner)
            services.add_attachment(visit, PNG, self.owner, stage=Stage.LAB)
            services.add_attachment(visit, JPEG, self.owner, stage=Stage.PHOTO)
        many, many_items = queries()
        self.assertEqual((few_items, many_items), (1, 11))
        self.assertEqual(few, many)

    def test_the_patient_file_page_costs_the_same_queries_with_or_without_visit_attachments(self):
        visits = [self.visit] + [med.save_visit(self.patient, {"diagnosis": f"d{index}"}, self.owner) for index in range(4)]
        self.client.force_login(self.owner)
        url = reverse("medical:file", args=[self.patient.pk])

        def count():
            with CaptureQueriesContext(connection) as captured:
                self.assertEqual(self.client.get(url).status_code, 200)
            return len(captured)

        count()  # warm the caches
        without = count()
        for visit in visits:
            services.add_attachment(visit, JPEG, self.owner, stage=Stage.SCAN)
            services.add_attachment(visit, PNG, self.owner, stage=Stage.LAB)
        self.assertEqual(count(), without)

    def test_the_more_note_appears_past_the_page_limit(self):
        for index in range(3):
            services.add_attachment(self.visit, upload(JPEG, f"n{index}.jpg", "image/jpeg"), self.owner, stage=Stage.SCAN)
        with mock.patch.object(ui, "PATIENT_VISITS_LIMIT", 2):
            page = self.file_page()
        self.assertContains(page, "data-att-more")
        self.assertContains(page, "n2.jpg")
        self.assertNotContains(page, "n0.jpg")  # the oldest is the one left out
