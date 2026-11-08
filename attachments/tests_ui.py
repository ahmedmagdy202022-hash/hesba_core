"""S1 UI: the "Photos & signature" card on the work-order page, who sees it, and where its forms point."""

import base64
import re
from unittest import mock

from django.core.exceptions import PermissionDenied
from django.test import SimpleTestCase
from django.urls import reverse

from hesba_testing.factories import grant, make_role, make_user, make_user_profile
from permissions.models import Permission
from workorders import services as work
from workorders.models import Status

from . import services, ui
from .models import Kind, Stage
from .tests import JPEG, PDF, PNG, AttachmentSetup, upload


class CardSetup(AttachmentSetup):
    def page(self, order=None, lang="ar", user=None):
        self.client.force_login(user or self.owner)
        return self.client.get(reverse("workorders:detail", args=[(order or self.wo).pk]) + f"?lang={lang}")

    def viewer(self):
        """Sees every order (the view permission) but may neither change one nor delete an attachment."""

        user = make_user(username="at_viewer")
        role = make_role("ROLE-AT-VIEWER")
        make_user_profile(user=user, role=role)
        permission, _ = Permission.objects.get_or_create(code=work.VIEW, defaults={"name_ar": "عرض", "module": "sales"})
        grant(role, permission)
        return user


class CardRenderingTests(CardSetup):
    def test_the_card_renders_for_the_counter_with_both_stage_groups(self):
        page = self.page()
        self.assertContains(page, 'id="wo-attachments"')
        self.assertContains(page, "الصور والتوقيع")
        self.assertContains(page, 'data-stage="receive"')
        self.assertContains(page, 'data-stage="deliver"')
        self.assertContains(page, "عند الاستلام")
        self.assertContains(page, "عند التسليم")

    def test_english_page_uses_english_words_and_urls(self):
        page = self.page(lang="en")
        self.assertContains(page, "Photos &amp; signature")
        self.assertContains(page, "Save signature")
        self.assertContains(page, "At delivery")
        self.assertContains(page, f'action="{reverse("attachments:work_order_upload", args=[self.wo.pk])}?lang=en"')
        self.assertContains(page, f'action="{reverse("attachments:work_order_signature", args=[self.wo.pk])}?lang=en"')

    def test_the_forms_point_at_the_attachment_endpoints_with_csrf(self):
        page = self.page()
        html = page.content.decode()
        uploads = re.findall(r'<form class="att-upload"[^>]*>', html)
        self.assertEqual(len(uploads), 2)  # receive and deliver
        for form in uploads:
            self.assertIn(f'action="{reverse("attachments:work_order_upload", args=[self.wo.pk])}?lang=ar"', form)
            self.assertIn('enctype="multipart/form-data"', form)
            self.assertIn('method="post"', form)
        signature = re.search(r"<form method=\"post\" action=\"([^\"]+)\" data-att-signature>(.*?)</form>", html, re.S)
        self.assertIsNotNone(signature)
        self.assertEqual(signature.group(1), f'{reverse("attachments:work_order_signature", args=[self.wo.pk])}?lang=ar')
        self.assertIn("csrfmiddlewaretoken", signature.group(2))
        self.assertIn('<canvas', signature.group(2))
        self.assertIn('data-att-clear', signature.group(2))
        self.assertIn('data-att-save', signature.group(2))
        self.assertGreaterEqual(html.count("csrfmiddlewaretoken"), 3)

    def test_the_photo_inputs_open_the_camera_and_the_gallery(self):
        html = self.page().content.decode()
        self.assertIn('accept="image/*" capture="environment"', html)
        self.assertIn('accept="image/*,application/pdf" multiple', html)

    def test_the_script_and_style_are_referenced_with_a_cache_buster(self):
        page = self.page()
        self.assertContains(page, "hesba/js/attachments.js?v=s1-ui-1")
        self.assertContains(page, "hesba/css/attachments.css?v=s1-ui-1")

    def test_the_default_signature_stage_follows_the_order(self):
        html = self.page().content.decode()
        self.assertRegex(html, r'name="stage" value="receive" checked')
        ready = self.to_ready(self.order())
        html = self.page(ready).content.decode()
        self.assertRegex(html, r'name="stage" value="deliver" checked')

    def test_stored_files_are_listed_by_stage_with_links(self):
        photo = services.add_attachment(self.wo, JPEG, self.owner, kind=Kind.PHOTO, stage=Stage.RECEIVE)
        after = services.add_attachment(self.wo, PNG, self.owner, kind=Kind.PHOTO, stage=Stage.DELIVER)
        pdf = services.add_attachment(self.wo, upload(PDF, "form.pdf", "application/pdf"), self.owner, kind=Kind.DOCUMENT, stage=Stage.RECEIVE)
        sign = services.add_signature_png(self.wo, "data:image/png;base64," + base64.b64encode(PNG).decode(), self.owner, stage=Stage.RECEIVE)
        html = self.page().content.decode()
        receive = html.split('data-stage="receive"')[1].split('data-stage="deliver"')[0]
        deliver = html.split('data-stage="deliver"')[1]
        self.assertIn(reverse("attachments:file", args=[photo.pk]), receive)
        self.assertIn(reverse("attachments:file", args=[pdf.pk]), receive)
        self.assertIn("form.pdf", receive)
        self.assertIn(f'data-attachment="{sign.pk}" data-kind="signature"', receive)
        self.assertIn(reverse("attachments:file", args=[after.pk]), deliver)
        self.assertNotIn(reverse("attachments:file", args=[photo.pk]), deliver)
        self.assertIn(f'data-url="{reverse("attachments:delete", args=[photo.pk])}?lang=ar"', receive)  # the counter may delete

    def test_the_page_never_embeds_attachment_bytes(self):
        services.add_attachment(self.wo, JPEG, self.owner, kind=Kind.PHOTO, stage=Stage.RECEIVE)
        self.assertNotContains(self.page(), "data:image")


class CardVisibilityTests(CardSetup):
    def test_no_card_and_no_script_when_the_service_says_this_user_cannot_see_attachments(self):
        with mock.patch("attachments.ui.card", return_value=None):
            page = self.page()
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'id="wo-attachments"')
        self.assertNotContains(page, "attachments.js")
        self.assertNotContains(page, "attachments.css")
        self.assertNotContains(page, reverse("attachments:work_order_upload", args=[self.wo.pk]))

    def test_the_card_helper_returns_nothing_for_a_user_without_access(self):
        stranger = make_user(username="at_stranger")
        make_user_profile(user=stranger, role=make_role("ROLE-AT-NONE"))
        self.assertIsNone(ui.card(self.wo, stranger, "ar"))
        with self.assertRaises(PermissionDenied):
            services.attachments_for(self.wo, stranger)

    def test_a_user_without_work_order_access_gets_no_page_at_all(self):
        stranger = make_user(username="at_stranger2")
        make_user_profile(user=stranger, role=make_role("ROLE-AT-NONE2"))
        page = self.page(user=stranger)
        self.assertEqual(page.status_code, 403)
        self.assertNotContains(page, "wo-attachments", status_code=403)

    def test_a_technician_sees_their_own_order_card_and_can_add_but_not_delete(self):
        tech = self.technician()
        photo = services.add_attachment(self.wo, JPEG, self.owner, kind=Kind.PHOTO, stage=Stage.RECEIVE)
        page = self.page(user=tech)
        self.assertContains(page, 'id="wo-attachments"')
        self.assertContains(page, "data-att-upload")
        self.assertContains(page, "data-att-signature")
        self.assertContains(page, reverse("attachments:file", args=[photo.pk]))
        self.assertNotContains(page, "data-att-delete")  # the service lets only the counter delete

    def test_a_viewer_sees_the_files_but_gets_no_upload_or_signature_controls(self):
        services.add_attachment(self.wo, JPEG, self.owner, kind=Kind.PHOTO, stage=Stage.RECEIVE)
        page = self.page(user=self.viewer())
        self.assertContains(page, 'id="wo-attachments"')
        self.assertContains(page, "data-att-blocked")
        self.assertContains(page, services.MESSAGES["ar"]["read_only"])
        self.assertNotContains(page, "data-att-upload")
        self.assertNotContains(page, "data-att-signature")
        self.assertNotContains(page, "data-att-delete")

    def test_a_delivered_order_is_read_only_and_its_signature_cannot_be_deleted(self):
        photo = services.add_attachment(self.wo, JPEG, self.owner, kind=Kind.PHOTO, stage=Stage.RECEIVE)
        sign = services.add_signature_png(self.wo, "data:image/png;base64," + base64.b64encode(PNG).decode(), self.owner, stage=Stage.RECEIVE)
        work.deliver(self.to_ready(self.wo), self.owner)  # nothing on it: delivered without an invoice
        self.wo.refresh_from_db()
        self.assertEqual(self.wo.status, Status.DELIVERED)
        page = self.page()
        self.assertContains(page, 'id="wo-attachments"')
        self.assertContains(page, services.MESSAGES["ar"]["closed"])
        self.assertNotContains(page, "data-att-upload")
        self.assertNotContains(page, "data-att-signature")
        self.assertContains(page, f'data-url="{reverse("attachments:delete", args=[photo.pk])}?lang=ar"')  # a photo can still go, as the service allows
        self.assertNotContains(page, f'data-url="{reverse("attachments:delete", args=[sign.pk])}')  # the signature of a delivered order stays


class CardContextTests(CardSetup):
    def test_card_context_matches_the_service_rules(self):
        context = ui.card(self.wo, self.owner, "en")
        self.assertTrue(context["can_add"])
        self.assertEqual(context["blocked"], "")
        self.assertEqual([g["stage"] for g in context["groups"]], ["receive", "deliver"])
        self.assertEqual(context["words"]["title"], "Photos & signature")
        self.assertTrue(context["upload_url"].endswith("/upload/?lang=en"))
        self.assertTrue(context["signature_url"].endswith("/signature/?lang=en"))

    def test_other_stage_files_are_listed_only_when_they_exist(self):
        services.add_attachment(self.wo, JPEG, self.owner, kind=Kind.PHOTO, stage=Stage.OTHER)
        self.assertEqual([g["stage"] for g in ui.card(self.wo, self.owner, "ar")["groups"]], ["receive", "deliver", "other"])


class AttachmentScriptTests(SimpleTestCase):
    """The browser script is read as text: Django never runs it, so what it must do is pinned here."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from pathlib import Path

        from django.conf import settings

        cls.js = Path(settings.BASE_DIR, "static/hesba/js/attachments.js").read_text(encoding="utf-8")
        cls.css = Path(settings.BASE_DIR, "static/hesba/css/attachments.css").read_text(encoding="utf-8")

    def test_signature_pad_uses_pointer_events_and_posts_a_png_data_url_with_csrf(self):
        for needle in ("pointerdown", "pointermove", "pointerup", "toDataURL('image/png')", "data_url", "X-CSRFToken", "setPointerCapture"):
            self.assertIn(needle, self.js)

    def test_no_library_and_no_colour_literal(self):
        self.assertNotRegex(self.js, r"\b(jQuery|\$\()")
        self.assertNotRegex(self.css, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(self.css, r"rgba?\(")
        self.assertNotRegex(self.js, r"#[0-9a-fA-F]{6}\b")

    def test_every_token_used_by_the_stylesheet_is_defined(self):
        from pathlib import Path

        from django.conf import settings

        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", Path(settings.BASE_DIR, "static/hesba/css/tokens.css").read_text(encoding="utf-8")))
        used = set(re.findall(r"var\((--hs-[\w-]+)", self.css))
        self.assertTrue(used)
        self.assertEqual(sorted(used - defined), [])
