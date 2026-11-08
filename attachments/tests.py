"""S1 (HG-072): photos, signature and files on a work order. Services, endpoints, scoping, audit, and no money moved."""

import base64
import hashlib
import struct
import zlib
from decimal import Decimal as D

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from audit.models import AuditLog
from cashboxes.models import CashboxMovement
from entities import services as entity_services
from entities.models import Entity, EntityMembership
from hesba_testing.factories import make_role, make_user, make_user_profile
from inventory.models import StockMovement
from ledger.models import JournalEntry
from permissions.models import RoleCode
from purchases.models import SupplierLedgerEntry
from sales.models import CustomerLedgerEntry, SalesInvoice
from workorders import services as work
from workorders.models import Status, WorkOrder as WorkOrderModel
from workorders.tests import WorkshopSetup, person

from . import services
from .models import Attachment, Kind, Stage


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def make_png(width=2, height=2):
    rows = b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(rows)) + _chunk(b"IEND", b"")


JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\x00" * 64 + b"\xff\xd9"
PNG = make_png()
WEBP = b"RIFF" + struct.pack("<I", 20) + b"WEBPVP8 " + b"\x00" * 16
PDF = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
HTML = b"<html><body><script>alert(1)</script></body></html>"
SVG = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'


def data_url(raw, mime="image/png"):
    return f"data:{mime};base64," + base64.b64encode(raw).decode()


def upload(raw, name, mime):
    return SimpleUploadedFile(name, raw, content_type=mime)


class AttachmentSetup(WorkshopSetup):
    def setUp(self):
        super().setUp()
        self.wo = self.order()

    def counts(self):
        return tuple(model.objects.count() for model in (SalesInvoice, CashboxMovement, StockMovement, JournalEntry, CustomerLedgerEntry, SupplierLedgerEntry))

    def technician(self, username="at_tech", employee=None):
        user = make_user(username=username)
        make_user_profile(user=user, role=make_role(f"ROLE-{username}"))
        employee = employee or self.tech
        employee.user = user
        employee.save()
        return user

    def url(self, name, *args):
        return reverse(f"attachments:{name}", args=args)


class StoringTests(AttachmentSetup):
    def test_valid_files_are_kept_with_their_real_sha256_size_and_type(self):
        for raw, name, mime, kind in ((JPEG, "front.jpg", "image/jpeg", Kind.PHOTO), (PNG, "back.png", "image/png", Kind.PHOTO),
                                      (WEBP, "side.webp", "image/webp", Kind.PHOTO), (PDF, "form.pdf", "application/pdf", Kind.DOCUMENT)):
            row = services.add_attachment(self.wo, upload(raw, name, mime), self.owner, kind=kind, stage=Stage.RECEIVE)
            row = Attachment.objects.get(pk=row.pk)
            self.assertEqual((row.sha256, row.size, row.content_type_name, row.kind, row.stage, row.original_filename),
                             (hashlib.sha256(raw).hexdigest(), len(raw), mime, kind, "receive", name))
            self.assertEqual(bytes(row.data), raw)
            self.assertEqual((row.owner, row.uploaded_by, row.entity), (self.wo, self.owner, self.wo.entity))
        self.assertEqual(len(services.attachments_for(self.wo, self.owner)), 4)

    def test_raw_bytes_are_accepted_and_the_clients_mime_is_not_trusted(self):
        row = services.add_attachment(self.wo, PNG, self.owner, kind=Kind.PHOTO)
        self.assertEqual(row.content_type_name, "image/png")
        wrong = services.add_attachment(self.wo, upload(JPEG, "x.png", "image/png"), self.owner, kind=Kind.PHOTO)  # really a JPEG
        self.assertEqual(wrong.content_type_name, "image/jpeg")

    def test_a_filename_is_stripped_to_its_last_part(self):
        row = services.add_attachment(self.wo, upload(JPEG, "..\\..\\etc/passwd\x00.jpg", "image/jpeg"), self.owner)
        self.assertNotIn("/", row.original_filename)
        self.assertNotIn("\\", row.original_filename)
        self.assertNotIn("\x00", row.original_filename)

    def test_oversize_is_refused_in_both_languages(self):
        with override_settings(ATTACHMENTS_MAX_BYTES=1024):
            big = JPEG + b"\x00" * 2000
            with self.assertRaises(ValidationError) as ar:
                services.add_attachment(self.wo, upload(big, "big.jpg", "image/jpeg"), self.owner)
            with self.assertRaises(ValidationError) as en:
                services.add_attachment(self.wo, big, self.owner, lang="en")
        self.assertIn("حجم الملف أكبر من المسموح", ar.exception.messages[0])
        self.assertIn("larger than allowed", en.exception.messages[0])
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(settings.ATTACHMENTS_MAX_BYTES, 5 * 1024 * 1024)

    def test_a_png_that_is_really_html_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            services.add_attachment(self.wo, upload(HTML, "photo.png", "image/png"), self.owner)
        self.assertIn("SVG", ctx.exception.messages[0])
        with self.assertRaises(ValidationError):
            services.add_attachment(self.wo, upload(b"just some text", "photo.png", "image/png"), self.owner)
        with self.assertRaises(ValidationError):  # a PDF's name on an image's bytes, and a photo must be an image
            services.add_attachment(self.wo, upload(PDF, "a.pdf", "application/pdf"), self.owner, kind=Kind.PHOTO)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_svg_is_refused_whatever_it_is_called_and_whatever_the_settings_say(self):
        for name, mime in (("logo.svg", "image/svg+xml"), ("logo.png", "image/png"), ("logo.jpg", "")):
            with self.assertRaises(ValidationError):
                services.add_attachment(self.wo, upload(SVG, name, mime), self.owner, kind=Kind.DOCUMENT)
        with self.assertRaises(ValidationError):  # real image bytes, but declared as svg/html
            services.add_attachment(self.wo, upload(PNG, "x.svg", "image/svg+xml"), self.owner)
        with self.assertRaises(ValidationError):
            services.add_attachment(self.wo, upload(PNG, "x.png", "text/html"), self.owner)
        with override_settings(ATTACHMENTS_ALLOWED_TYPES=["image/svg+xml", "text/html", "image/png"]):  # the list can only narrow
            self.assertEqual(services.allowed_types(), ["image/png"])
            with self.assertRaises(ValidationError):
                services.add_attachment(self.wo, upload(SVG, "logo.png", "image/png"), self.owner, kind=Kind.DOCUMENT)
            with self.assertRaises(ValidationError):  # narrowed away
                services.add_attachment(self.wo, upload(JPEG, "a.jpg", "image/jpeg"), self.owner)
        self.assertEqual(Attachment.objects.count(), 0)

    def test_empty_unknown_kind_and_unknown_stage_are_refused(self):
        with self.assertRaises(ValidationError):
            services.add_attachment(self.wo, b"", self.owner)
        with self.assertRaises(ValidationError):
            services.add_attachment(self.wo, None, self.owner)
        with self.assertRaises(ValidationError):
            services.add_attachment(self.wo, JPEG, self.owner, kind="video")
        with self.assertRaises(ValidationError):
            services.add_attachment(self.wo, JPEG, self.owner, stage="later")
        with self.assertRaises(ValidationError):  # a signature is a real PNG only
            services.add_attachment(self.wo, JPEG, self.owner, kind=Kind.SIGNATURE)


class SignatureTests(AttachmentSetup):
    def test_a_canvas_data_url_is_decoded_and_kept_as_a_signature(self):
        row = services.add_signature_png(self.wo, data_url(PNG), self.owner, stage=Stage.DELIVER)
        row = Attachment.objects.get(pk=row.pk)
        self.assertEqual((row.kind, row.stage, row.content_type_name, row.size, row.sha256), (Kind.SIGNATURE, "deliver", "image/png", len(PNG), hashlib.sha256(PNG).hexdigest()))
        self.assertEqual(bytes(row.data), PNG)
        self.assertEqual(row.original_filename, f"signature-{row.sha256[:8]}.png")

    def test_anything_that_is_not_a_real_png_is_refused(self):
        bad = (data_url(JPEG), data_url(HTML), data_url(PNG[:-12]), data_url(PNG, "image/jpeg"), data_url(SVG, "image/svg+xml"),
               "data:image/png;base64,@@not base64@@", "data:image/png;base64,", "not a data url", PNG.decode("latin-1"))
        for value in bad:
            with self.assertRaises(ValidationError, msg=value[:40]):
                services.add_signature_png(self.wo, value, self.owner)
        with self.assertRaises(ValidationError) as ctx:
            services.add_signature_png(self.wo, data_url(JPEG), self.owner, lang="en")
        self.assertEqual(ctx.exception.messages[0], "A signature must be a real PNG image.")
        with self.assertRaises(ValidationError) as ctx:
            services.add_signature_png(self.wo, data_url(JPEG), self.owner)
        self.assertEqual(ctx.exception.messages[0], "التوقيع لازم يكون صورة PNG حقيقية.")
        self.assertEqual(Attachment.objects.count(), 0)

    def test_the_signature_size_is_capped_before_decoding(self):
        big = make_png(120, 120)
        with override_settings(ATTACHMENTS_SIGNATURE_MAX_BYTES=len(big) - 1):
            with self.assertRaises(ValidationError) as ctx:
                services.add_signature_png(self.wo, data_url(big), self.owner, lang="en")
            self.assertIn("larger than allowed", ctx.exception.messages[0])
            with self.assertRaises(ValidationError):  # enormous: refused from the encoded length, never decoded
                services.add_signature_png(self.wo, "data:image/png;base64," + "A" * 10_000_000, self.owner)
        self.assertEqual(Attachment.objects.count(), 0)


class PermissionTests(AttachmentSetup):
    def test_a_technician_reaches_only_their_own_orders(self):
        from staff.services import save_employee

        tech_user = self.technician()
        mine = self.wo
        other = self.order(technician=save_employee({"name": "فني تاني"}, self.owner))
        row = services.add_attachment(mine, JPEG, tech_user, stage=Stage.RECEIVE)
        self.assertEqual(list(services.attachments_for(mine, tech_user)), [row])
        theirs = services.add_attachment(other, PNG, self.owner)
        for call in (lambda: services.add_attachment(other, JPEG, tech_user), lambda: services.attachments_for(other, tech_user),
                     lambda: services.add_signature_png(other, data_url(PNG), tech_user)):
            with self.assertRaises(PermissionDenied):
                call()
        with self.assertRaises(services.OwnerNotFound):
            services.get_attachment(theirs.pk, tech_user)
        self.client.force_login(tech_user)
        self.assertEqual(self.client.get(self.url("work_order_list", mine.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("work_order_list", other.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("file", theirs.pk)).status_code, 404)
        denied = self.client.post(self.url("work_order_upload", other.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg")})
        self.assertEqual(denied.status_code, 404)
        self.assertEqual(Attachment.objects.filter(object_id=other.pk).count(), 1)  # only the owner's own

    def test_someone_with_no_work_order_right_is_refused(self):
        stock_keeper = person(RoleCode.STOCK_KEEPER, "at_stock")
        with self.assertRaises(PermissionDenied):
            services.add_attachment(self.wo, JPEG, stock_keeper)
        row = services.add_attachment(self.wo, JPEG, self.owner)
        self.client.force_login(stock_keeper)
        self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 403)
        self.assertEqual(self.client.post(self.url("work_order_upload", self.wo.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg")}).status_code, 403)
        self.assertEqual(self.client.get(self.url("work_order_list", self.wo.pk) + "?lang=en").json()["error"], services.MESSAGES["en"]["denied"])

    def test_another_entitys_work_order_is_not_found_and_a_positive_control_works(self):
        main = Entity.objects.get(is_main=True)
        branch = entity_services.save_entity({"code": "BR9", "name_ar": "فرع تاني", "activity_slug": "services", "sub_activity_slug": "maintenance"}, self.owner)
        self.assertTrue(entity_services.is_multi_entity())
        main_order = self.wo
        branch_order = self.order()
        type(branch_order).objects.filter(pk=branch_order.pk).update(entity=branch)
        branch_order.refresh_from_db()
        manager = person(RoleCode.MANAGER, "at_main_mgr")
        EntityMembership.objects.create(user=manager, entity=main, is_default=True)
        mine = services.add_attachment(main_order, JPEG, self.owner)
        theirs = services.add_attachment(branch_order, PNG, self.owner)
        self.assertEqual(theirs.entity, branch)
        self.client.force_login(manager)
        self.assertTrue(work.can_view(manager))
        # positive control: the same user, same endpoints, their own entity's order
        self.assertEqual(self.client.get(self.url("work_order_list", main_order.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("file", mine.pk)).status_code, 200)
        self.assertEqual(self.client.post(self.url("work_order_upload", main_order.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg")}).status_code, 201)
        # the branch's order and file answer as if they did not exist
        self.assertEqual(self.client.get(self.url("work_order_list", branch_order.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("file", theirs.pk)).status_code, 404)
        self.assertEqual(self.client.post(self.url("work_order_upload", branch_order.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg")}).status_code, 404)
        self.assertEqual(self.client.post(self.url("work_order_signature", branch_order.pk), {"data_url": data_url(PNG)}).status_code, 404)
        with self.assertRaises(PermissionDenied):
            from entities.current import working_in

            with working_in(main):
                services.add_attachment(branch_order, JPEG, manager)
        # the owner (whole group) and a branch member see the branch's
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url("file", theirs.pk)).status_code, 200)
        branch_manager = person(RoleCode.MANAGER, "at_branch_mgr")
        EntityMembership.objects.create(user=branch_manager, entity=branch, is_default=True)
        self.client.force_login(branch_manager)
        self.assertEqual(self.client.get(self.url("file", theirs.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("file", mine.pk)).status_code, 404)

    def test_an_anonymous_request_is_sent_to_login(self):
        row = services.add_attachment(self.wo, JPEG, self.owner)
        for method, url in ((self.client.get, self.url("file", row.pk)), (self.client.get, self.url("work_order_list", self.wo.pk)),
                            (self.client.post, self.url("work_order_upload", self.wo.pk)), (self.client.post, self.url("work_order_signature", self.wo.pk))):
            response = method(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith(settings.LOGIN_URL))

    def test_work_orders_exist_only_for_a_workshop(self):
        row = services.add_attachment(self.wo, JPEG, self.owner)
        from settings_core.models import ClientProfile

        ClientProfile.objects.update(activity_slug="commercial", sub_activity_slug="retail")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 404)


class EndpointTests(AttachmentSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_upload_list_and_serve_a_photo_with_the_right_headers(self):
        response = self.client.post(self.url("work_order_upload", self.wo.pk), {"file": upload(JPEG, "جهاز.jpg", "image/jpeg"), "stage": "receive"})
        self.assertEqual(response.status_code, 201)
        body = response.json()["attachment"]
        self.assertEqual((body["kind"], body["stage"], body["content_type"], body["size"], body["sha256"]),
                         ("photo", "receive", "image/jpeg", len(JPEG), hashlib.sha256(JPEG).hexdigest()))
        self.assertNotIn("data", body)
        served = self.client.get(body["url"])
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served.content, JPEG)
        self.assertEqual(served["Content-Type"], "image/jpeg")
        self.assertTrue(served["Content-Disposition"].startswith("inline;"))
        self.assertIn("filename*=", served["Content-Disposition"])  # the Arabic name, RFC 5987
        self.assertEqual(served["X-Content-Type-Options"], "nosniff")
        self.assertIn("private", served["Cache-Control"])
        self.assertIn("no-cache", served["Cache-Control"])
        self.assertNotIn("public", served["Cache-Control"])
        self.assertIn("sandbox", served["Content-Security-Policy"])
        self.assertEqual(self.client.get(body["url"], HTTP_IF_NONE_MATCH=f'"{body["sha256"]}"').status_code, 304)
        listing = self.client.get(self.url("work_order_list", self.wo.pk)).json()
        self.assertEqual([row["id"] for row in listing["attachments"]], [body["id"]])

    def test_a_pdf_downloads_and_a_signature_is_inline(self):
        pdf = self.client.post(self.url("work_order_upload", self.wo.pk), {"file": upload(PDF, "agreement.pdf", "application/pdf"), "kind": "document", "stage": "deliver"})
        served = self.client.get(pdf.json()["attachment"]["url"])
        self.assertTrue(served["Content-Disposition"].startswith("attachment;"))
        self.assertEqual((served["Content-Type"], served["X-Content-Type-Options"]), ("application/pdf", "nosniff"))
        sign = self.client.post(self.url("work_order_signature", self.wo.pk), {"data_url": data_url(PNG), "stage": "deliver"})
        self.assertEqual(sign.status_code, 201)
        self.assertEqual(sign.json()["attachment"]["kind"], "signature")
        shown = self.client.get(sign.json()["attachment"]["url"])
        self.assertTrue(shown["Content-Disposition"].startswith("inline;"))
        self.assertEqual(shown.content, PNG)

    def test_a_signature_can_also_come_as_a_json_body(self):
        import json

        response = self.client.post(self.url("work_order_signature", self.wo.pk), data=json.dumps({"data_url": data_url(PNG), "stage": "receive"}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 201)
        bad = self.client.post(self.url("work_order_signature", self.wo.pk), data="[1]", content_type="application/json")
        self.assertEqual(bad.status_code, 400)

    def test_bad_files_answer_400_in_arabic_and_in_english(self):
        url = self.url("work_order_upload", self.wo.pk)
        html = {"file": upload(HTML, "photo.png", "image/png")}
        ar = self.client.post(url, html)
        self.assertEqual((ar.status_code, ar.json()["ok"], ar.json()["error"]), (400, False, "ملفات SVG وHTML مش مسموحة."))
        en = self.client.post(url + "?lang=en", {"file": upload(HTML, "photo.png", "image/png")})
        self.assertEqual((en.status_code, en.json()["error"]), (400, "SVG and HTML files are not allowed."))
        self.assertEqual(self.client.post(url, {"stage": "receive"}).json()["error"], "اختار ملف الأول.")
        self.assertEqual(self.client.post(url + "?lang=en", {}).json()["error"], "Choose a file first.")
        self.assertEqual(self.client.post(url, {"file": upload(JPEG, "a.jpg", "image/jpeg"), "kind": "signature"}).status_code, 400)  # its own endpoint
        self.assertEqual(self.client.post(url, {"file": upload(JPEG, "a.jpg", "image/jpeg"), "stage": "never"}).status_code, 400)
        with override_settings(ATTACHMENTS_MAX_BYTES=1024):
            big = self.client.post(url + "?lang=en", {"file": upload(JPEG + b"\x00" * 2000, "big.jpg", "image/jpeg")})
        self.assertEqual(big.status_code, 413)
        self.assertIn("larger than allowed (1 KB)", big.json()["error"])
        signature = self.client.post(self.url("work_order_signature", self.wo.pk), {"data_url": data_url(JPEG)})
        self.assertEqual((signature.status_code, signature.json()["error"]), (400, "التوقيع لازم يكون صورة PNG حقيقية."))
        self.assertEqual(self.client.post(self.url("work_order_signature", self.wo.pk) + "?lang=en", {}).json()["error"], "Draw the signature first.")
        self.assertEqual(Attachment.objects.count(), 0)

    def test_only_the_right_methods_and_a_missing_order_or_file(self):
        self.assertEqual(self.client.get(self.url("work_order_upload", self.wo.pk)).status_code, 405)
        self.assertEqual(self.client.post(self.url("file", 1)).status_code, 405)
        self.assertEqual(self.client.get(self.url("work_order_list", 99999)).status_code, 404)
        missing = self.client.get(self.url("file", 99999) + "?lang=en")
        self.assertEqual((missing.status_code, missing.json()["error"]), (404, services.MESSAGES["en"]["not_found"]))


class AuditAndMoneyTests(AttachmentSetup):
    def test_every_upload_writes_an_audit_entry(self):
        row = services.add_attachment(self.wo, upload(JPEG, "a.jpg", "image/jpeg"), self.owner, stage=Stage.RECEIVE)
        sign = services.add_signature_png(self.wo, data_url(PNG), self.owner, stage=Stage.DELIVER)
        entries = AuditLog.objects.filter(module="attachments", action="add_attachment")
        self.assertEqual(entries.count(), 2)
        first = entries.get(object_id=str(row.pk))
        self.assertEqual((first.actor, first.event_type, first.object_type), (self.owner, "create", "attachments.Attachment"))
        self.assertEqual(first.after_data, {"owner": "workorders.workorder", "owner_id": self.wo.pk, "kind": "photo", "stage": "receive", "filename": "a.jpg",
                                            "content_type": "image/jpeg", "size": len(JPEG), "sha256": hashlib.sha256(JPEG).hexdigest()})
        self.assertEqual(entries.get(object_id=str(sign.pk)).after_data["kind"], "signature")

    def test_a_refused_upload_writes_no_audit_entry_and_no_row(self):
        before = AuditLog.objects.filter(module="attachments").count()
        with self.assertRaises(ValidationError):
            services.add_attachment(self.wo, HTML, self.owner)
        self.assertEqual((AuditLog.objects.filter(module="attachments").count(), Attachment.objects.count()), (before, 0))

    def test_attachments_move_no_money_stock_or_ledger(self):
        before = self.counts()
        invoice_total = work.totals(self.wo)
        services.add_attachment(self.wo, upload(JPEG, "a.jpg", "image/jpeg"), self.owner, stage=Stage.RECEIVE)
        services.add_attachment(self.wo, upload(PDF, "a.pdf", "application/pdf"), self.owner, kind=Kind.DOCUMENT)
        services.add_signature_png(self.wo, data_url(PNG), self.owner)
        self.client.force_login(self.owner)
        self.client.post(self.url("work_order_upload", self.wo.pk), {"file": upload(PNG, "b.png", "image/png"), "stage": "deliver"})
        self.client.post(self.url("work_order_signature", self.wo.pk), {"data_url": data_url(PNG)})
        self.assertEqual(Attachment.objects.count(), 5)
        self.assertEqual(self.counts(), before)
        self.assertEqual(work.totals(self.wo), invoice_total)
        from inventory.services import get_item_stock_quantity

        self.assertEqual(get_item_stock_quantity(self.part), D("5"))
        self.wo.refresh_from_db()
        self.assertEqual((self.wo.status, self.wo.invoice), ("received", None))


def view_only_user(owner, username="at_viewer"):
    from permissions.role_services import create_role

    role = create_role(name_ar="مراجع مبيعات", codes=["sales.view_sales_invoices"], actor=owner)
    user = make_user(username=username)
    make_user_profile(user=user, role=role)
    return user


class WriteRuleTests(AttachmentSetup):
    """Seeing an order is not the right to change it: the same rule the work-order screens use (counter, or the owning technician)."""

    def test_a_view_only_user_can_see_but_not_upload_or_sign(self):
        viewer = view_only_user(self.owner)
        self.assertTrue(work.can_view(viewer) and not work.can_manage(viewer))
        row = services.add_attachment(self.wo, JPEG, self.owner)
        for call in (lambda: services.add_attachment(self.wo, JPEG, viewer), lambda: services.add_signature_png(self.wo, data_url(PNG), viewer)):
            with self.assertRaises(PermissionDenied) as ctx:
                call()
            self.assertEqual(str(ctx.exception), services.MESSAGES["ar"]["read_only"])
        self.assertEqual(list(services.attachments_for(self.wo, viewer)), [row])  # reading still works
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 200)
        self.assertEqual(self.client.get(self.url("work_order_list", self.wo.pk)).status_code, 200)
        up = self.client.post(self.url("work_order_upload", self.wo.pk) + "?lang=en", {"file": upload(JPEG, "a.jpg", "image/jpeg")})
        self.assertEqual((up.status_code, up.json()["error"]), (403, services.MESSAGES["en"]["read_only"]))
        sign = self.client.post(self.url("work_order_signature", self.wo.pk), {"data_url": data_url(PNG)})
        self.assertEqual((sign.status_code, sign.json()["error"]), (403, services.MESSAGES["ar"]["read_only"]))
        self.assertEqual(Attachment.objects.count(), 1)
        # positive control: same order, same endpoints, a user who may change it
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url("work_order_upload", self.wo.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg")}).status_code, 201)
        self.assertEqual(self.client.post(self.url("work_order_signature", self.wo.pk), {"data_url": data_url(PNG)}).status_code, 201)

    def test_the_owning_technician_may_attach_to_their_open_order(self):
        tech_user = self.technician()
        self.assertEqual(services.add_attachment(self.wo, JPEG, tech_user).uploaded_by, tech_user)

    def test_a_delivered_or_cancelled_order_takes_no_more_attachments(self):
        ready = self.to_ready(self.order())
        photo = services.add_attachment(ready, JPEG, self.owner, stage=Stage.DELIVER)  # the hand-over photo is taken while it is ready
        services.add_signature_png(ready, data_url(PNG), self.owner, stage=Stage.DELIVER)
        work.deliver(ready, self.owner)
        cancelled = self.order()
        work.move(cancelled, Status.CANCELLED, self.owner)
        for order in (ready, cancelled):
            for call in (lambda: services.add_attachment(order, PNG, self.owner, stage=Stage.DELIVER, lang="en"),
                         lambda: services.add_signature_png(order, data_url(PNG), self.owner, stage=Stage.DELIVER, lang="en")):
                with self.assertRaises(ValidationError) as ctx:
                    call()
                self.assertEqual(ctx.exception.messages[0], services.MESSAGES["en"]["closed"])
        self.client.force_login(self.owner)
        closed = self.client.post(self.url("work_order_upload", ready.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg"), "stage": "deliver"})
        self.assertEqual((closed.status_code, closed.json()["error"]), (400, services.MESSAGES["ar"]["closed"]))
        self.assertEqual(self.client.get(self.url("file", photo.pk)).status_code, 200)  # reading stays open
        self.assertEqual(Attachment.objects.filter(object_id=ready.pk).count(), 2)

    def test_a_stale_copy_of_the_order_cannot_sneak_past_the_closed_check(self):
        stale = self.to_ready(self.order())
        work.deliver(WorkOrderModel.objects.get(pk=stale.pk), self.owner)
        self.assertEqual(stale.status, Status.READY)  # the caller's copy still says ready
        with self.assertRaises(ValidationError):
            services.add_attachment(stale, JPEG, self.owner)


class CapTests(AttachmentSetup):
    def test_the_count_per_record_is_capped(self):
        other = self.order()
        with override_settings(ATTACHMENTS_MAX_PER_OWNER=2):
            services.add_attachment(self.wo, JPEG, self.owner)
            services.add_attachment(self.wo, PNG, self.owner)
            with self.assertRaises(ValidationError) as en:
                services.add_attachment(self.wo, WEBP, self.owner, lang="en")
            with self.assertRaises(ValidationError) as ar:
                services.add_signature_png(self.wo, data_url(PNG), self.owner)
            services.add_attachment(other, JPEG, self.owner)  # another record has its own room
        self.assertEqual(en.exception.messages[0], "This record has reached the most attachments allowed (2).")
        self.assertIn("للحد الأقصى من المرفقات", ar.exception.messages[0])
        self.assertEqual(Attachment.objects.filter(object_id=self.wo.pk).count(), 2)
        self.assertEqual(settings.ATTACHMENTS_MAX_PER_OWNER, 20)

    def test_the_bytes_per_record_are_capped(self):
        with override_settings(ATTACHMENTS_MAX_BYTES_PER_OWNER=len(JPEG) * 2 + 10):
            services.add_attachment(self.wo, JPEG, self.owner)
            services.add_attachment(self.wo, JPEG + b"\x01", self.owner)
            with self.assertRaises(ValidationError) as en:
                services.add_attachment(self.wo, JPEG + b"\x02", self.owner, lang="en")
            with self.assertRaises(ValidationError) as ar:
                services.add_attachment(self.wo, JPEG + b"\x03", self.owner)
        self.assertIn("would pass the size limit", en.exception.messages[0])
        self.assertIn("هيعدّي الحد", ar.exception.messages[0])
        self.assertEqual(Attachment.objects.count(), 2)
        self.assertEqual(settings.ATTACHMENTS_MAX_BYTES_PER_OWNER, 25 * 1024 * 1024)

    def test_all_attachments_together_may_take_only_a_share_of_the_database_plan(self):
        self.assertEqual((settings.DATABASE_SIZE_LIMIT_MB, settings.ATTACHMENTS_GLOBAL_SHARE), (500, 0.2))
        room = len(JPEG) * 1.5 / (1024 * 1024)  # MB of room for one and a half files
        with override_settings(DATABASE_SIZE_LIMIT_MB=1, ATTACHMENTS_GLOBAL_SHARE=room):
            services.add_attachment(self.wo, JPEG, self.owner)
            for lang in ("en", "ar"):
                with self.assertRaises(ValidationError) as ctx:
                    services.add_attachment(self.order(), JPEG + b"\x01", self.owner, lang=lang)
                self.assertEqual(ctx.exception.messages[0], services.MESSAGES[lang]["global_full"])
        with override_settings(DATABASE_SIZE_LIMIT_MB=1, ATTACHMENTS_GLOBAL_SHARE=0):  # 0 switches the guard off
            services.add_attachment(self.wo, PNG, self.owner)
        self.assertEqual(Attachment.objects.count(), 2)


class DeleteTests(AttachmentSetup):
    def test_a_manager_deletes_with_an_audit_entry(self):
        row = services.add_attachment(self.wo, upload(JPEG, "a.jpg", "image/jpeg"), self.owner)
        self.client.force_login(self.owner)
        response = self.client.post(self.url("delete", row.pk))
        self.assertEqual((response.status_code, response.json()), (200, {"ok": True}))
        self.assertFalse(Attachment.objects.filter(pk=row.pk).exists())
        entry = AuditLog.objects.get(module="attachments", action="delete_attachment")
        self.assertEqual((entry.actor, entry.event_type, entry.object_id), (self.owner, "delete", str(row.pk)))
        self.assertEqual((entry.before_data["sha256"], entry.before_data["owner_id"], entry.before_data["filename"]), (row.sha256, self.wo.pk, "a.jpg"))
        self.assertEqual(self.client.post(self.url("delete", row.pk)).status_code, 404)  # already gone

    def test_only_the_counter_may_delete(self):
        row = services.add_attachment(self.wo, JPEG, self.owner)
        tech_user = self.technician()
        viewer = view_only_user(self.owner)
        for user in (tech_user, viewer):  # the technician may add to their own order but not remove
            with self.assertRaises(PermissionDenied):
                services.delete_attachment(row.pk, user)
            self.client.force_login(user)
            denied = self.client.post(self.url("delete", row.pk) + "?lang=en")
            self.assertEqual((denied.status_code, denied.json()["error"]), (403, services.MESSAGES["en"]["delete_denied"]))
        self.assertTrue(Attachment.objects.filter(pk=row.pk).exists())
        self.assertEqual(AuditLog.objects.filter(action="delete_attachment").count(), 0)
        self.client.force_login(self.owner)  # positive control
        self.assertEqual(self.client.post(self.url("delete", row.pk)).status_code, 200)

    def test_delete_needs_post_and_login_and_a_visible_owner(self):
        row = services.add_attachment(self.wo, JPEG, self.owner)
        self.assertEqual(self.client.post(self.url("delete", row.pk)).status_code, 302)  # anonymous
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url("delete", row.pk)).status_code, 405)
        stock_keeper = person(RoleCode.STOCK_KEEPER, "at_stock_del")
        self.client.force_login(stock_keeper)
        self.assertEqual(self.client.post(self.url("delete", row.pk)).status_code, 403)
        self.assertTrue(Attachment.objects.filter(pk=row.pk).exists())

    def test_the_signature_of_a_delivered_order_is_never_deleted(self):
        order = self.to_ready(self.order())
        sign = services.add_signature_png(order, data_url(PNG), self.owner, stage=Stage.DELIVER)
        photo = services.add_attachment(order, JPEG, self.owner, stage=Stage.DELIVER)
        spare = services.add_signature_png(self.wo, data_url(PNG), self.owner)
        services.delete_attachment(spare.pk, self.owner)  # on an open order a signature taken wrongly can go
        work.deliver(order, self.owner)
        with self.assertRaises(ValidationError) as ctx:
            services.delete_attachment(sign.pk, self.owner, lang="en")
        self.assertEqual(ctx.exception.messages[0], services.MESSAGES["en"]["signature_kept"])
        self.client.force_login(self.owner)
        refused = self.client.post(self.url("delete", sign.pk))
        self.assertEqual((refused.status_code, refused.json()["error"]), (400, services.MESSAGES["ar"]["signature_kept"]))
        self.assertTrue(Attachment.objects.filter(pk=sign.pk).exists())
        services.delete_attachment(photo.pk, self.owner)  # a photo on a delivered order may go, with its audit entry
        self.assertEqual(AuditLog.objects.filter(action="delete_attachment").count(), 2)


class HardeningTests(AttachmentSetup):
    def test_the_stored_name_carries_the_detected_types_extension(self):
        pdf = services.add_attachment(self.wo, upload(PDF, "report.hta", "application/x-hta"), self.owner, kind=Kind.DOCUMENT)
        self.assertEqual(pdf.original_filename, "report.pdf")
        png = services.add_attachment(self.wo, upload(PNG, "evil.exe", "application/octet-stream"), self.owner)
        self.assertEqual(png.original_filename, "evil.png")
        noext = services.add_attachment(self.wo, upload(JPEG, "photo", "image/jpeg"), self.owner)
        self.assertEqual(noext.original_filename, "photo.jpg")
        self.client.force_login(self.owner)
        served = self.client.get(self.url("file", pdf.pk))
        self.assertIn("report.pdf", served["Content-Disposition"])
        self.assertNotIn(".hta", served["Content-Disposition"])

    def test_a_pdf_with_script_or_an_embedded_file_is_refused(self):
        for token in (b"/JavaScript", b"/OpenAction", b"/Launch", b"/EmbeddedFile"):
            bad = PDF.replace(b"<< /Root 1 0 R >>", b"<< /Root 1 0 R " + token + b" 2 0 R >>")
            self.assertNotEqual(bad, PDF)
            with self.assertRaises(ValidationError, msg=token) as ctx:
                services.add_attachment(self.wo, upload(bad, "a.pdf", "application/pdf"), self.owner, kind=Kind.DOCUMENT, lang="en")
            self.assertEqual(ctx.exception.messages[0], services.MESSAGES["en"]["pdf_active"])
        with self.assertRaises(ValidationError) as ar:
            services.add_attachment(self.wo, PDF + b"/OpenAction", self.owner, kind=Kind.DOCUMENT)
        self.assertIn("PDF", ar.exception.messages[0])
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(services.add_attachment(self.wo, PDF, self.owner, kind=Kind.DOCUMENT).content_type_name, "application/pdf")  # a plain one passes

    def test_a_scan_whose_compressed_data_happens_to_hold_js_is_accepted(self):
        scan = PDF.replace(b"<< /Root 1 0 R >>", b"<< /Root 1 0 R >>\nstream\n\x8a/JS\x03\x91\nendstream")
        self.assertIn(b"/JS", scan)
        self.assertEqual(services.add_attachment(self.wo, scan, self.owner, kind=Kind.DOCUMENT).content_type_name, "application/pdf")

    def test_an_oversize_content_length_is_refused_before_the_body_is_read(self):
        self.client.force_login(self.owner)
        url = self.url("work_order_upload", self.wo.pk)
        huge = self.client.post(url + "?lang=en", {"file": upload(JPEG, "a.jpg", "image/jpeg")}, CONTENT_LENGTH=str(settings.ATTACHMENTS_MAX_BYTES + 10 * 1024 * 1024))
        self.assertEqual((huge.status_code, huge.json()["error"]), (413, "The file is larger than allowed (5 MB)."))
        self.assertEqual(self.client.post(url, {"file": upload(JPEG, "a.jpg", "image/jpeg")}, CONTENT_LENGTH=str(10**9)).status_code, 413)
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertEqual(self.client.post(url, {"file": upload(JPEG, "a.jpg", "image/jpeg")}).status_code, 201)  # control: a normal one

    def test_an_upload_that_passes_the_cap_while_streaming_is_stopped(self):
        from django.core.files.uploadhandler import StopUpload

        from .handlers import CapUploadHandler

        self.client.force_login(self.owner)
        with override_settings(ATTACHMENTS_MAX_BYTES=1024):
            big = self.client.post(self.url("work_order_upload", self.wo.pk), {"file": upload(JPEG + b"\x00" * 5000, "big.jpg", "image/jpeg")})  # Content-Length is honest and small
            ok = self.client.post(self.url("work_order_upload", self.wo.pk), {"file": upload(JPEG, "a.jpg", "image/jpeg")})
            handler = CapUploadHandler()
            handler.new_file("file", "a.jpg", "image/jpeg", 9000)
            with self.assertRaises(StopUpload):
                handler.receive_data_chunk(b"x" * 2000, 0)
            self.assertTrue(handler.exceeded)
        self.assertEqual((big.status_code, ok.status_code), (413, 201))
        self.assertEqual(Attachment.objects.count(), 1)

    def test_csrf_is_still_enforced_on_upload(self):
        from django.test import Client

        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        url = self.url("work_order_upload", self.wo.pk)
        self.assertEqual(strict.post(url, {"file": upload(JPEG, "a.jpg", "image/jpeg")}).status_code, 403)
        token = "a" * 32
        strict.cookies[settings.CSRF_COOKIE_NAME] = token
        ok = strict.post(url, {"file": upload(JPEG, "a.jpg", "image/jpeg"), "csrfmiddlewaretoken": token})
        self.assertEqual(ok.status_code, 201)
        self.assertEqual(Attachment.objects.count(), 1)
