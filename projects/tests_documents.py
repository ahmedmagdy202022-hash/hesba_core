"""Contracting C2a (HG-095): a project's documents: add, group by kind, open, delete; who may; the limits; nothing financial moves.

The files are ordinary attachments owned by the project (no second upload path); the six kinds ride on the attachment's
``stage`` (``projects.documents.KIND_STAGE``). "Today" is always ``timezone.localdate()``."""

from decimal import Decimal as D
from unittest import mock

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils import timezone

from attachments import services as attachments
from attachments.models import Attachment, Kind, Stage
from attachments.tests import HTML, JPEG, PDF, PNG, SVG, upload
from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from inventory.models import StockMovement
from ledger.models import JournalEntry, JournalLine
from permissions.models import RoleCode
from permissions.role_services import create_role
from sales.models import CustomerLedgerEntry, CustomerPayment, SalesInvoice

from hesba_testing.factories import make_user, make_user_profile

from . import access, contract, documents, services
from .models import BoqLine, Certificate, Project
from .tests_contract import ContractSetup, person


def footprint(project=None):
    """Everything money, stock or the contract is written to: it must not move when a document or a report is added, changed or deleted."""

    from projects.models import CertificateLine

    return {"sales_invoices": SalesInvoice.objects.count(), "payments": CustomerPayment.objects.count(), "customer_ledger": CustomerLedgerEntry.objects.count(),
            "stock_movements": StockMovement.objects.count(), "cashbox_movements": CashboxMovement.objects.count(), "journal_entries": JournalEntry.objects.count(),
            "journal_lines": JournalLine.objects.count(), "certificates": Certificate.objects.count(), "certificate_lines": CertificateLine.objects.count(),
            "boq": [(line.line_number, line.quantity, line.rate) for line in BoqLine.objects.order_by("project_id", "line_number")],
            "contract_values": [(p.pk, p.contract_value) for p in Project.objects.order_by("pk")]}


def view_only(owner, username="c2_viewer", codes=("sales.view_sales_invoices",)):
    """Sees the project screens, may not write anything on them."""

    role = create_role(name_ar=f"قارئ {username}", codes=list(codes), actor=owner)
    user = make_user(username=username)
    make_user_profile(user=user, role=role)
    return user


class C2Setup(ContractSetup):
    def setUp(self):
        super().setUp()
        self.engineer = person(RoleCode.SITE_ENGINEER, "c2_engineer")
        self.keeper = person(RoleCode.STOCK_KEEPER, "c2_keeper")  # holds neither projects.view_projects nor sales.view_sales_invoices
        self.viewer = view_only(self.owner)
        self.other = services.save_project({"name": "مشروع تاني", "customer": self.customer}, self.owner)

    def url(self, name, *args, project=None, lang=None):
        link = reverse(f"projects:{name}", args=[(project or self.project).pk, *args])
        return f"{link}?lang={lang}" if lang else link

    def add_document(self, user, raw=PDF, filename="drawing.pdf", mime="application/pdf", stage=Stage.SCAN, title="", project=None, kind=None, lang="ar"):
        self.client.force_login(user)
        data = {"file": upload(raw, filename, mime), "kind": kind or (Kind.DOCUMENT if mime == "application/pdf" else Kind.PHOTO), "stage": stage, "lang": lang}
        if title:
            data["title"] = title
        return self.client.post(self.url("document_upload", project=project, lang=lang), data)

    def attachment_of(self, project):
        return Attachment.objects.filter(content_type=ContentType.objects.get_for_model(Project), object_id=project.pk)


class KindsAndRulesTests(C2Setup):
    def test_the_six_kinds_map_onto_six_distinct_existing_stages(self):
        self.assertEqual(set(documents.KINDS), {"contract", "drawing", "letter", "permit", "photo", "other"})
        stages = list(documents.KIND_STAGE.values())
        self.assertEqual(len(set(stages)), 6)
        self.assertTrue(set(stages) <= set(Stage))  # an existing stage: no attachments migration
        self.assertEqual(set(attachments.OWNERS["projects.project"]["stages"]), set(stages))
        self.assertEqual({documents.STAGE_KIND[stage.value] for stage in stages}, set(documents.KINDS))

    def test_who_sees_and_who_adds_is_the_project_rule(self):
        rights = {self.owner: (True, True, True), self.engineer: (True, True, False), self.viewer: (True, False, False), self.keeper: (False, False, False)}
        for user, (see, add, delete) in rights.items():
            with self.subTest(user=user.username):
                self.assertEqual(access.can_view(user), see)
                self.assertEqual(access.can_write(user), add)
                self.assertEqual(access.can_delete_documents(user), delete)

    def test_the_limits_are_higher_than_a_work_orders_but_far_below_the_global_share(self):
        count, size = attachments.owner_limits(self.project)
        self.assertEqual((count, size), (40, 40 * 1024 * 1024))
        self.assertGreater(count, 20)
        self.assertLess(size, 100 * 1024 * 1024)  # the whole attachments share at the default database limit (500 MB x 0.2)
        self.assertEqual(attachments.owner_limits(Project.objects.get(pk=self.project.pk)), (count, size))


class AddingTests(C2Setup):
    def test_an_engineer_adds_a_drawing_with_a_title_and_it_is_stored_on_the_project(self):
        response = self.add_document(self.engineer, title="رسم الدور الأول", stage=documents.KIND_STAGE["drawing"])
        self.assertEqual(response.status_code, 201)
        row = self.attachment_of(self.project).get()
        self.assertEqual((row.kind, row.stage, row.uploaded_by, row.content_type_name, row.original_filename), (Kind.DOCUMENT, Stage.SCAN, self.engineer, "application/pdf", "رسم الدور الأول.pdf"))
        self.assertEqual(bytes(row.data), PDF)
        self.assertIsNone(row.entity)
        audit = AuditLog.objects.get(action="add_attachment")
        self.assertEqual((audit.after_data["owner"], audit.after_data["owner_id"], audit.actor), ("projects.project", self.project.pk, self.engineer))

    def test_without_a_title_the_file_keeps_its_own_name_and_its_real_extension(self):
        self.add_document(self.engineer, filename="scan-0042.png", raw=PNG, mime="image/png", stage=Stage.PHOTO)
        self.assertEqual(self.attachment_of(self.project).get().original_filename, "scan-0042.png")
        self.add_document(self.engineer, filename="notes.txt", raw=PDF, title="خطاب الاستشاري", stage=documents.KIND_STAGE["letter"])
        self.assertEqual(self.attachment_of(self.project).latest("pk").original_filename, "خطاب الاستشاري.pdf")  # the extension is the detected type's, never the client's

    def test_the_office_adds_every_kind_and_the_tab_groups_them_newest_first_with_who_and_when(self):
        for n, kind in enumerate(documents.KINDS):
            self.add_document(self.owner, stage=documents.KIND_STAGE[kind], title=f"مستند {kind}", raw=JPEG if kind == "photo" else PDF,
                              mime="image/jpeg" if kind == "photo" else "application/pdf")
        self.add_document(self.engineer, stage=documents.KIND_STAGE["drawing"], title="رسم تاني")
        self.client.force_login(self.engineer)
        page = self.client.get(self.url("documents", lang="ar"))
        body = page.content.decode()
        for kind in documents.KINDS:
            self.assertContains(page, f'data-doc-group="{kind}"')
        drawings = body.split('data-doc-group="drawing"')[1].split("</section>")[0]
        self.assertLess(drawings.index("رسم تاني"), drawings.index("مستند drawing"))  # newest first within the group
        self.assertContains(page, "c2_engineer")  # who added it
        self.assertEqual(body.count("data-attachment="), 7)

    def test_an_image_from_the_camera_is_kept_as_a_photo_and_a_pdf_as_a_document(self):
        self.add_document(self.engineer, raw=JPEG, filename="IMG_1.jpg", mime="image/jpeg", stage=Stage.PHOTO)
        self.add_document(self.engineer, raw=PDF, filename="permit.pdf", mime="application/pdf", stage=documents.KIND_STAGE["permit"])
        self.assertEqual(sorted(self.attachment_of(self.project).values_list("kind", "stage")), sorted([(Kind.PHOTO, Stage.PHOTO), (Kind.DOCUMENT, Stage.DELIVER)]))

    def test_a_document_goes_to_the_project_it_was_posted_to_and_to_no_other(self):
        self.add_document(self.owner, project=self.other)
        self.assertEqual((self.attachment_of(self.other).count(), self.attachment_of(self.project).count()), (1, 0))
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(self.url("documents")), "data-attachment=")

    def test_a_project_that_does_not_exist_is_a_404_not_a_500(self):
        self.client.force_login(self.owner)
        response = self.client.post(reverse("projects:document_upload", args=[999999]), {"file": upload(PDF, "a.pdf", "application/pdf"), "stage": Stage.SCAN})
        self.assertEqual(response.status_code, 404)

    def test_documents_can_be_added_to_a_finished_project_as_built_drawings(self):
        Project.objects.filter(pk=self.project.pk).update(status="done")
        self.assertEqual(self.add_document(self.owner).status_code, 201)


class OpeningAndDeletingTests(C2Setup):
    def setUp(self):
        super().setUp()
        self.add_document(self.engineer, title="عقد التوريد", stage=documents.KIND_STAGE["contract"])
        self.add_document(self.engineer, raw=JPEG, filename="a.jpg", mime="image/jpeg", stage=Stage.PHOTO, title="صورة الموقع")
        self.pdf = self.attachment_of(self.project).get(content_type_name="application/pdf")
        self.jpg = self.attachment_of(self.project).get(content_type_name="image/jpeg")

    def test_a_pdf_downloads_and_an_image_opens_inline_with_the_safe_headers(self):
        self.client.force_login(self.viewer)
        pdf = self.client.get(self.url("file", self.pdf.pk))
        self.assertEqual((pdf.status_code, pdf["Content-Type"], pdf.content), (200, "application/pdf", PDF))
        self.assertIn("attachment", pdf["Content-Disposition"])
        self.assertEqual(pdf["X-Content-Type-Options"], "nosniff")
        self.assertIn("sandbox", pdf["Content-Security-Policy"])
        image = self.client.get(self.url("file", self.jpg.pk))
        self.assertEqual((image.status_code, image["Content-Type"], image.content), (200, "image/jpeg", JPEG))
        self.assertIn("inline", image["Content-Disposition"])
        again = self.client.get(self.url("file", self.pdf.pk), HTTP_IF_NONE_MATCH=pdf["ETag"])
        self.assertEqual(again.status_code, 304)

    def test_the_office_deletes_a_document_and_it_is_audited_and_gone(self):
        self.client.force_login(self.owner)
        response = self.client.post(self.url("file_delete", self.pdf.pk))
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))
        self.assertFalse(Attachment.objects.filter(pk=self.pdf.pk).exists())
        audit = AuditLog.objects.get(action="delete_attachment")
        self.assertEqual((audit.before_data["owner"], audit.before_data["stage"], audit.actor), ("projects.project", Stage.RECEIVE, self.owner))
        self.assertEqual(self.client.get(self.url("file", self.pdf.pk)).status_code, 404)

    def test_an_engineer_adds_but_cannot_delete_and_gets_the_reason_in_the_page_language(self):
        self.client.force_login(self.engineer)
        refused = self.client.post(self.url("file_delete", self.pdf.pk, lang="ar"))
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"], attachments.MESSAGES["ar"]["project_delete_denied"])
        self.assertEqual(self.client.post(self.url("file_delete", self.pdf.pk, lang="en")).json()["error"], attachments.MESSAGES["en"]["project_delete_denied"])
        self.assertTrue(Attachment.objects.filter(pk=self.pdf.pk).exists())
        page = self.client.get(self.url("documents"))
        self.assertNotContains(page, "data-att-delete")
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.url("documents")), "data-att-delete")

    def test_delete_is_post_only(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url("file_delete", self.pdf.pk)).status_code, 405)


class AccessTests(C2Setup):
    def setUp(self):
        super().setUp()
        self.add_document(self.engineer, title="عقد", stage=documents.KIND_STAGE["contract"])
        self.doc = self.attachment_of(self.project).get()

    def test_a_user_without_the_project_right_gets_403_on_the_tab_the_upload_the_file_and_the_delete(self):
        self.client.force_login(self.keeper)
        self.assertFalse(access.can_view(self.keeper))
        self.assertEqual(self.client.get(self.url("documents")).status_code, 403)
        self.assertEqual(self.client.post(self.url("document_upload"), {"file": upload(PDF, "a.pdf", "application/pdf"), "stage": Stage.SCAN}).status_code, 403)
        self.assertEqual(self.client.get(self.url("file", self.doc.pk)).status_code, 403)
        self.assertEqual(self.client.post(self.url("file_delete", self.doc.pk)).status_code, 403)
        # the generic attachments endpoints answer the same way (the registry is the one rule)
        self.assertEqual(self.client.get(reverse("attachments:file", args=[self.doc.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("attachments:delete", args=[self.doc.pk])).status_code, 403)
        self.assertEqual(self.attachment_of(self.project).count(), 1)

    def test_a_reader_sees_and_opens_but_cannot_add_and_is_told_why(self):
        self.client.force_login(self.viewer)
        page = self.client.get(self.url("documents"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-att-blocked")
        self.assertNotContains(page, "data-doc-upload")
        self.assertEqual(self.client.get(self.url("file", self.doc.pk)).status_code, 200)
        refused = self.client.post(self.url("document_upload"), {"file": upload(PDF, "a.pdf", "application/pdf"), "stage": Stage.SCAN, "kind": "document"})
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(refused.json()["error"], attachments.MESSAGES["ar"]["project_read_only"])
        self.assertEqual(self.attachment_of(self.project).count(), 1)

    def test_the_projects_view_right_alone_is_enough_to_read_and_the_sales_view_right_alone_too(self):
        for n, code in enumerate(("projects.view_projects", "sales.view_sales_invoices")):
            user = view_only(self.owner, username=f"c2_only_{n}", codes=(code,))
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url("documents")).status_code, 200, code)
        # a write right without a view right is not enough: seeing comes first
        blind = view_only(self.owner, username="c2_blind", codes=("projects.record_progress",))
        self.client.force_login(blind)
        self.assertEqual(self.client.get(self.url("documents")).status_code, 403)
        self.assertEqual(self.client.post(self.url("document_upload"), {"file": upload(PDF, "a.pdf", "application/pdf"), "stage": Stage.SCAN}).status_code, 403)

    def test_another_projects_file_id_in_a_crafted_url_is_refused(self):
        self.add_document(self.owner, project=self.other, title="سري")
        theirs = self.attachment_of(self.other).get()
        self.client.force_login(self.owner)
        for name, method in (("file", self.client.get), ("file_delete", self.client.post)):
            with self.subTest(name=name):
                self.assertEqual(method(self.url(name, theirs.pk)).status_code, 404)  # asked through THIS project
        self.assertTrue(Attachment.objects.filter(pk=theirs.pk).exists())
        self.assertEqual(self.client.get(self.url("file", theirs.pk, project=self.other)).status_code, 200)  # through its own project it opens

    def test_a_file_that_is_not_a_project_document_is_refused_through_a_project_url(self):
        customer_ct = ContentType.objects.get_for_model(type(self.customer))
        stray = Attachment.objects.create(content_type=customer_ct, object_id=self.customer.pk, kind=Kind.DOCUMENT, stage=Stage.OTHER, original_filename="x.pdf",
                                          content_type_name="application/pdf", size=len(PDF), sha256="0" * 64, data=PDF, uploaded_by=self.owner)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url("file", stray.pk)).status_code, 404)
        self.assertEqual(self.client.get(reverse("attachments:file", args=[stray.pk])).status_code, 404)  # no owner type registered for it
        self.assertEqual(self.client.get(self.url("file", 987654)).status_code, 404)


class EntityTests(C2Setup):
    """A project has no entity of its own and its page is not narrowed to an entity today; its documents follow the page, not the stamp."""

    def setUp(self):
        super().setUp()
        from entities import services as entity_services
        from entities.models import Entity, EntityMembership

        self.main = Entity.objects.get(is_main=True)
        self.branch = entity_services.save_entity({"code": "BR9", "name_ar": "فرع التجمع", "activity_slug": "contracting", "sub_activity_slug": "general"}, self.owner)
        self.main_manager = person(RoleCode.MANAGER, "c2_main_mgr")
        EntityMembership.objects.create(user=self.main_manager, entity=self.main, is_default=True)
        self.branch_manager = person(RoleCode.MANAGER, "c2_branch_mgr")
        EntityMembership.objects.create(user=self.branch_manager, entity=self.branch, is_default=True)

    def test_a_document_added_in_one_entity_is_there_in_the_other_and_for_the_whole_group(self):
        for uploader in (self.main_manager, self.branch_manager):
            self.assertEqual(self.add_document(uploader, title=f"من {uploader.username}").status_code, 201)
        rows = list(self.attachment_of(self.project))
        self.assertEqual(len(rows), 2)
        self.assertEqual({row.entity_id for row in rows}, {None})  # not stamped: the project has no entity of its own
        for viewer in (self.main_manager, self.branch_manager, self.owner):
            self.client.force_login(viewer)
            page = self.client.get(self.url("documents"))
            self.assertEqual(page.content.decode().count("data-attachment="), 2, viewer.username)
            for row in rows:
                self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 200, (viewer.username, row.pk))
                self.assertEqual(self.client.get(reverse("attachments:file", args=[row.pk])).status_code, 200)
            self.assertEqual(len(attachments.attachments_for_many("projects.project", [self.project.pk], viewer)), 2)

    def test_the_trap_in_the_plan_a_drawing_uploaded_before_the_first_certificate_still_shows_after_it(self):
        self.add_document(self.branch_manager, title="رسم قبل المستخلص")
        contract.create_certificate(self.project, self.owner, certificate_date=timezone.localdate(), description="مستخلص 1", quantities={self.concrete.pk: D("5")})
        self.assertEqual(contract.project_entity(self.project), self.main.pk)  # the project's money is now in the main entity
        self.client.force_login(self.branch_manager)
        self.assertContains(self.client.get(self.url("documents")), "رسم قبل المستخلص")
        self.assertEqual(self.client.get(self.url("detail")).status_code, 200)  # as the project page itself, which is not narrowed

    def test_only_the_two_project_owner_types_are_group_wide_every_other_owner_keeps_the_entity_scope(self):
        flagged = sorted(label for label, rules in attachments.OWNERS.items() if rules.get("group_wide"))
        self.assertEqual(flagged, ["projects.project", "projects.sitereport"])
        self.assertTrue({"workorders.workorder", "purchases.purchaseinvoice", "medical.clinicalvisit", "medical.patientfile"} <= set(attachments.OWNERS))


class LimitsTests(C2Setup):
    def test_a_file_that_is_too_large_is_refused_with_the_limit_in_the_message(self):
        big = b"%PDF-1.4\n" + b"0" * (5 * 1024 * 1024)
        response = self.add_document(self.engineer, raw=big, filename="huge.pdf")
        self.assertIn(response.status_code, (400, 413))
        self.assertIn("5 ميجا", response.json()["error"])
        english = self.add_document(self.engineer, raw=big, filename="huge.pdf", lang="en")
        self.assertIn("5 MB", english.json()["error"])
        self.assertEqual(self.attachment_of(self.project).count(), 0)

    def test_a_wrong_type_is_refused_by_its_real_bytes_whatever_the_name_says(self):
        for raw, name, mime in ((HTML, "plan.html", "text/html"), (SVG, "plan.svg", "image/svg+xml"), (b"MZ\x90\x00 not a pdf", "plan.pdf", "application/pdf"),
                                (HTML, "plan.pdf", "application/pdf")):
            with self.subTest(name=name):
                response = self.add_document(self.engineer, raw=raw, filename=name, mime=mime)
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.json()["error"])
        self.assertEqual(self.attachment_of(self.project).count(), 0)

    def test_a_pdf_with_a_script_and_an_empty_file_are_refused(self):
        self.assertEqual(self.add_document(self.engineer, raw=b"%PDF-1.4\n/JavaScript (x)\n").status_code, 400)
        self.assertEqual(self.add_document(self.engineer, raw=b"").status_code, 400)

    def test_a_signature_or_an_unknown_document_type_is_refused(self):
        refused = self.add_document(self.engineer, stage=Stage.LAB)  # a clinic category, not a project kind
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(refused.json()["error"], attachments.MESSAGES["ar"]["project_stage"])
        self.assertEqual(self.add_document(self.engineer, kind=Kind.SIGNATURE).status_code, 400)
        self.assertEqual(self.attachment_of(self.project).count(), 0)

    def test_the_cap_on_the_number_of_files_refuses_the_next_one_with_a_clear_message(self):
        with mock.patch.dict(attachments.OWNERS["projects.project"], {"max_count": 3}):
            for n in range(3):
                self.assertEqual(self.add_document(self.engineer, title=f"ملف {n}").status_code, 201)
            refused = self.add_document(self.engineer, title="ملف زيادة")
            self.assertEqual(refused.status_code, 400)
            self.assertEqual(refused.json()["error"], attachments.MESSAGES["ar"]["max_count"].format(count=3))
            self.assertEqual(self.add_document(self.engineer, title="x", lang="en").json()["error"], attachments.MESSAGES["en"]["max_count"].format(count=3))
        self.assertEqual(self.attachment_of(self.project).count(), 3)

    def test_the_cap_on_the_total_size_refuses_the_file_that_would_pass_it(self):
        with mock.patch.dict(attachments.OWNERS["projects.project"], {"max_owner_bytes": len(PDF) * 2 + 10}):
            self.assertEqual(self.add_document(self.engineer, title="1").status_code, 201)
            self.assertEqual(self.add_document(self.engineer, title="2").status_code, 201)
            refused = self.add_document(self.engineer, title="3")
            self.assertEqual(refused.status_code, 400)
            self.assertEqual(refused.json()["error"], attachments.MESSAGES["ar"]["max_owner_bytes"].format(limit=attachments._human(len(PDF) * 2 + 10, "ar")))

    def test_the_global_share_of_the_database_still_applies_on_top_of_the_project_cap(self):
        with self.settings(DATABASE_SIZE_LIMIT_MB=1, ATTACHMENTS_GLOBAL_SHARE=0.000001):
            refused = self.add_document(self.engineer, title="ممتلئ")
            self.assertEqual(refused.status_code, 400)
            self.assertEqual(refused.json()["error"], attachments.MESSAGES["ar"]["global_full"])

    def test_the_tab_shows_the_room_left_and_warns_when_it_is_low_or_gone(self):
        self.client.force_login(self.engineer)
        page = self.client.get(self.url("documents"))
        self.assertContains(page, "data-doc-space")
        self.assertContains(page, "من 40 ميجا")
        self.assertNotContains(page, "data-doc-space-note")
        with mock.patch.dict(attachments.OWNERS["projects.project"], {"max_owner_bytes": 4 * 1024 * 1024}):  # under the warning line
            self.assertContains(self.client.get(self.url("documents")), "data-doc-space-note")
        with mock.patch.dict(attachments.OWNERS["projects.project"], {"max_count": 1}):
            self.add_document(self.engineer, title="الوحيد")
            page = self.client.get(self.url("documents", lang="en"))
            self.assertContains(page, "No room left")

    def test_room_reports_the_lower_of_the_projects_room_and_the_databases(self):
        self.add_document(self.engineer, title="a")
        room = attachments.room(self.project)
        self.assertEqual((room["files"], room["bytes"], room["max_files"]), (1, len(PDF), 40))
        self.assertEqual(room["bytes_left"], min(room["max_bytes"] - len(PDF), room["global_bytes"]))
        with self.settings(DATABASE_SIZE_LIMIT_MB=0):  # the guard is off: only the project's own room
            self.assertIsNone(attachments.room(self.project)["global_bytes"])


class NothingFinancialMovesTests(C2Setup):
    def test_adding_opening_and_deleting_documents_changes_no_money_stock_or_quantity(self):
        post = contract.create_certificate(self.project, self.owner, certificate_date=timezone.localdate(), description="مستخلص 1",
                                           quantities={self.concrete.pk: D("10"), self.masonry.pk: D("20")})
        before = footprint()
        position = contract.position(self.project)
        for user, kwargs in ((self.engineer, {"title": "رسم"}), (self.owner, {"title": "عقد", "stage": documents.KIND_STAGE["contract"]}),
                             (self.owner, {"title": "صورة", "raw": JPEG, "filename": "a.jpg", "mime": "image/jpeg", "stage": Stage.PHOTO})):
            self.assertEqual(self.add_document(user, **kwargs).status_code, 201)
        self.client.force_login(self.owner)
        for row in self.attachment_of(self.project):
            self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 200)
        self.client.get(self.url("documents"))
        victim = self.attachment_of(self.project).first()
        self.assertEqual(self.client.post(self.url("file_delete", victim.pk)).status_code, 200)
        self.assertEqual(footprint(), before)
        self.assertEqual(contract.position(self.project), position)
        self.assertEqual(Certificate.objects.get().invoice_id, post.invoice_id)

    def test_the_documents_modules_never_call_posting_stock_cashbox_or_ledger_code(self):
        from pathlib import Path

        from django.conf import settings

        for name in ("documents.py", "views_documents.py", "access.py"):
            source = Path(settings.BASE_DIR, "projects", name).read_text(encoding="utf-8")
            with self.subTest(module=name):
                for forbidden in ("post_sales_invoice", "post_purchase_invoice", "StockMovement", "CashboxMovement", "JournalEntry", "LedgerEntry", "average_cost", "adjust_stock"):
                    self.assertNotIn(forbidden, source)
