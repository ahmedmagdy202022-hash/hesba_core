"""Contracting C1 (HG-103): the «أوامر التغيير» screens: the register, the form, one order, the print and the bill of
quantities under the lock. Who may open and who may act, Arabic and English, the phone, and the scanners.

The clock is the reference contract's (``tests_variation_baseline``), so no screen depends on the day the suite runs.
"""

import re
from pathlib import Path

from decimal import Decimal as D

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from attachments.models import Attachment
from attachments.tests import JPEG, PDF, SVG, upload
from permissions.models import RoleCode

from . import contract, services, variations
from .models import Project, VariationOrder, VariationStatus
from .tests_c2_ui import PRE_LABELLED, sentences
from .tests_contract import person
from .tests_site_reports import visible_text
from .tests_variation_baseline import DAY
from .tests_variations import NEW_C, VariationSetup, boq_state

LATIN_START = re.compile(r"^[A-Za-z]")
TEMPLATES = ("variations.html", "variation_form.html", "variation.html", "variation_print.html")


def element(html, marker):
    """The one tag that carries ``marker``, with what it holds: a figure is read from the element it belongs to, never from
    the whole page (another picker can carry the same number)."""

    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw_element(html, marker))).strip()


def raw_element(html, marker):
    """The same one element, with its markup: for reading an attribute (what a box holds) and nothing but that element.

    It ends at the first closing tag of the same name, so for an element that nests one of its own kind use ``block``."""

    found = re.search(r"<(\w+)[^>]*" + re.escape(marker) + r"[^>]*>(.*?)</\1>", html, flags=re.S)
    return found.group(2) if found else ""


def block(html, marker, until):
    """From the element that carries ``marker`` to the next ``until``: one row of a form whose cells nest divs of their own."""

    start = html.find(marker)
    if start < 0:
        return ""
    rest = html[start:]
    end = rest.find(until, len(marker))
    return rest[:end] if end > 0 else rest


class ScreenSetup(VariationSetup):
    def url(self, name, *args, project=None, lang=None):
        link = reverse(f"projects:{name}", args=[(project or self.project).pk, *args])
        return f"{link}?lang={lang}" if lang else link

    def form_data(self, **over):
        data = {"lang": "ar", "order_date": DAY.isoformat(), "title": "تعديلات الدور الأخير", "reason": "طلب المالك في محضر الاجتماع", "extension_days": "0",
                f"d_{self.a.pk}": "up", f"c_{self.a.pk}": "20", f"d_{self.b.pk}": "down", f"c_{self.b.pk}": "100",
                "new_code": ["C", ""], "new_description": ["عزل مائي للسطح", ""], "new_unit": ["م²", ""], "new_quantity": ["1000", ""], "new_rate": ["80", ""]}
        data.update(over)
        return data


class RegisterTests(ScreenSetup):
    def test_the_register_is_empty_with_a_clear_message_and_the_primary_action(self):
        self.client.force_login(self.owner)
        page = self.client.get(self.url("variations"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "data-empty-none")
        self.assertContains(page, "لسه مفيش أوامر تغيير على المشروع ده.")
        self.assertContains(page, "data-new-variation")
        self.assertEqual(element(page.content.decode(), 'data-summary="original"'), "1,000,000.00")

    def test_the_register_shows_each_order_with_its_value_status_and_the_totals(self):
        self.two_certificates()
        approved = self.approve(self.draft(extension_days="30"))
        rejected = variations.close(self.project, self.draft(title="بند اتلغى", changes=[(self.a.pk, "-10")], new_items=[]), self.owner, status=VariationStatus.REJECTED)
        self.draft(title="أعمال إضافية تحت الدراسة", changes=[], new_items=[dict(NEW_C, code="D", rate="100")])
        self.client.force_login(self.owner)
        html = self.client.get(self.url("variations")).content.decode()
        self.assertIn("أمر تغيير رقم 3", element(html, 'data-variation-order="3"'))
        for marker, value in (('data-summary="original"', "1,000,000.00"), ('data-summary="approved"', "+90,000.00"),
                              ('data-summary="revised"', "1,090,000.00"), ('data-summary="pending"', "+100,000.00"),
                              ('data-summary="extension"', "30 يوم")):
            with self.subTest(marker=marker):
                self.assertEqual(element(html, marker), value)
        rows = re.findall(r'<tr data-variation-order="(\d+)" data-status="(\w+)">', html)
        self.assertEqual(rows, [("3", "draft"), ("2", "rejected"), ("1", "approved")])
        self.assertEqual(approved.value, D("90000.00"))
        self.assertEqual(variations.order_value(rejected), D("-15000.00"))

    def test_the_header_shows_the_original_and_the_approved_orders_only_once_there_are_any(self):
        self.client.force_login(self.owner)
        overview = self.client.get(self.url("detail")).content.decode()
        self.assertEqual(element(overview, 'data-position="contract"'), "1,000,000.00")
        self.assertNotIn('data-position="original"', overview)
        self.approve(self.draft())
        overview = self.client.get(self.url("detail")).content.decode()
        self.assertEqual(element(overview, 'data-position="contract"'), "1,090,000.00")
        self.assertEqual(element(overview, 'data-position="original"'), "الأصلية 1,000,000.00 · أوامر التغيير +90,000.00")

    def test_every_project_page_links_to_the_tab(self):
        self.client.force_login(self.owner)
        for name in ("detail", "boq", "certificates", "variations"):
            with self.subTest(page=name):
                self.assertContains(self.client.get(self.url(name)), f'data-tab="variations"')

    def test_a_closed_project_is_read_only(self):
        self.approve(self.draft())
        Project.objects.filter(pk=self.project.pk).update(status="done")
        self.client.force_login(self.owner)
        page = self.client.get(self.url("variations"))
        self.assertContains(page, "data-project-closed")
        self.assertNotContains(page, "data-new-variation")
        self.assertEqual(self.client.get(self.url("variation_new")).status_code, 200)  # the form itself says the project is closed
        self.assertContains(self.client.get(self.url("variation_new")), "data-project-closed")


class FormTests(ScreenSetup):
    def test_the_office_writes_a_draft_from_the_form_and_lands_on_it(self):
        self.two_certificates()
        self.client.force_login(self.owner)
        page = self.client.get(self.url("variation_new"))
        self.assertContains(page, f'data-change-row="{self.a.line_number}"')
        self.assertContains(page, "data-variation-new")
        made = self.client.post(self.url("variation_new"), self.form_data())
        order = VariationOrder.objects.get()
        self.assertRedirects(made, self.url("variation", order.pk) + "?lang=ar", fetch_redirect_response=False)
        self.assertEqual([(line.code, line.quantity_change, line.amount) for line in order.lines.all()],
                         [("A", D("20.000"), D("30000.00")), ("B", D("-100.000"), D("-20000.00")), ("C", D("1000.000"), D("80000.00"))])
        landed = self.client.get(self.url("variation", order.pk))
        self.assertContains(landed, "اتحفظ أمر التغيير رقم 1")
        self.assertEqual(element(landed.content.decode(), "data-variation-value"), "+90,000.00")

    def test_the_direction_beside_the_box_makes_the_change_negative(self):
        self.client.force_login(self.owner)
        self.client.post(self.url("variation_new"), self.form_data(**{f"d_{self.a.pk}": "down", f"c_{self.a.pk}": "20", f"c_{self.b.pk}": "",
                                                                      "new_code": ["", ""], "new_description": ["", ""], "new_unit": ["", ""],
                                                                      "new_quantity": ["", ""], "new_rate": ["", ""]}))
        self.assertEqual([(line.code, line.quantity_change) for line in VariationOrder.objects.get().lines.all()], [("A", D("-20.000"))])

    def test_a_refusal_comes_back_in_arabic_with_what_was_typed(self):
        self.client.force_login(self.owner)
        page = self.client.post(self.url("variation_new"), self.form_data(title=""))
        self.assertContains(page, 'role="alert"')
        self.assertContains(page, variations.MESSAGES["ar"]["title"])
        html = page.content.decode()
        row = block(html, f'data-change-row="{self.a.line_number}"', "data-change-row=")  # the row of item A alone, not the whole page
        self.assertIn('value="20"', row)  # what was typed is still there
        self.assertIn('value="عزل مائي للسطح"', raw_element(html, "data-variation-new"))
        self.assertFalse(VariationOrder.objects.exists())

    def test_the_edit_form_opens_the_draft_as_stored_and_an_approved_one_redirects_to_its_page(self):
        order = self.draft()
        self.client.force_login(self.owner)
        html = self.client.get(self.url("variation_edit", order.pk)).content.decode()
        self.assertIn("تعديلات الدور الأخير", html)
        self.assertEqual(element(html, 'data-sr-rows="item"').count("عزل مائي للسطح"), 0)  # the typed values live in inputs, not in text
        self.assertIn('value="عزل مائي للسطح"', html)
        self.approve(order)
        self.assertRedirects(self.client.get(self.url("variation_edit", order.pk)), self.url("variation", order.pk) + "?lang=ar", fetch_redirect_response=False)

    def test_a_lump_sum_project_gets_the_amount_form_and_no_items(self):
        villa = services.save_project({"name": "تشطيب فيلا الشروق", "customer": self.customer, "contract_value": "300000"}, self.owner)
        self.client.force_login(self.owner)
        html = self.client.get(self.url("variation_new", project=villa)).content.decode()
        self.assertIn("data-variation-lump", html)
        self.assertNotIn("data-variation-changes", html)
        self.client.post(self.url("variation_new", project=villa),
                         {"lang": "ar", "order_date": DAY.isoformat(), "title": "أعمال إضافية في المطبخ", "reason": "", "extension_days": "0",
                          "amount_direction": "up", "amount": "50000"})
        order = VariationOrder.objects.get(project=villa)
        self.assertEqual((order.lump_amount, order.lines.count()), (D("50000.00"), 0))


class OrderPageTests(ScreenSetup):
    def setUp(self):
        super().setUp()
        self.two_certificates()
        self.order = self.draft()
        self.client.force_login(self.owner)

    def post(self, **data):
        return self.client.post(self.url("variation", self.order.pk), dict({"lang": "ar"}, **data))

    def upload(self, user=None, raw=PDF, name="signed.pdf", mime="application/pdf", oid=None):
        self.client.force_login(user or self.owner)
        return self.client.post(self.url("variation_files", oid or self.order.pk), {"file": upload(raw, name, mime), "kind": "document", "stage": "other"})

    def test_the_page_shows_the_lines_the_value_and_what_the_contract_becomes(self):
        html = self.client.get(self.url("variation", self.order.pk)).content.decode()
        self.assertEqual(element(html, 'data-variation-status="draft"'), "مسودة")
        self.assertTrue(element(html, 'data-variation-line="1"').startswith("تعديل كمية"))
        self.assertTrue(element(html, 'data-variation-line="3"').startswith("بند جديد"))
        self.assertEqual(re.findall(r'data-line-amount>([^<]+)<', html), ["+30,000.00", "−20,000.00", "+80,000.00"])
        self.assertEqual(re.findall(r'data-line-change>([^<]+)<', html), ["+20", "−100", "+1000"])
        for marker, value in (('data-total="before"', "1,000,000.00"), ('data-total="order"', "+90,000.00"), ('data-total="after"', "1,090,000.00")):
            with self.subTest(marker=marker):
                self.assertEqual(element(html, marker), value)
        self.assertIn("الكمية الحالية 400 · بعد التعديل 420", element(html, 'data-variation-line="1"'))

    def test_the_approval_needs_the_signed_copy_and_then_moves_the_contract(self):
        page = self.post(action="approve", approved_on=DAY.isoformat(), owner_reference="خطاب 114")
        self.assertContains(page, variations.MESSAGES["ar"]["signed_copy"])
        self.assertEqual(contract.boq_total(self.project), D("1000000.00"))
        self.assertEqual(self.upload().status_code, 201)
        self.assertEqual(Attachment.objects.filter(object_id=self.order.pk).count(), 1)
        done = self.post(action="approve", approved_on=DAY.isoformat(), owner_reference="خطاب 114")
        self.assertRedirects(done, self.url("variation", self.order.pk) + "?lang=ar", fetch_redirect_response=False)
        landed = self.client.get(self.url("variation", self.order.pk))
        self.assertContains(landed, "اتسجّل اعتماد أمر التغيير رقم 1، وقيمة العقد بقت 1,090,000.00")
        html = landed.content.decode()
        self.assertEqual(element(html, 'data-variation-status="approved"'), "معتمد")
        self.assertIn("خطاب 114", element(html, "data-variation-approval"))
        self.assertNotIn("data-approve-form", html)  # nothing left to decide
        self.assertNotIn("data-variation-edit", html)
        self.assertEqual(contract.boq_total(self.project), D("1090000.00"))

    def test_the_office_rejects_and_whoever_drafts_cancels(self):
        self.assertRedirects(self.post(action="reject", note="المالك اعتذر"), self.url("variation", self.order.pk) + "?lang=ar", fetch_redirect_response=False)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.decision_note), ("rejected", "المالك اعتذر"))
        second = self.draft(title="أمر تاني")
        self.client.force_login(self.engineer)
        self.client.post(self.url("variation", second.pk), {"lang": "ar", "action": "cancel", "note": "اتكتب بالغلط"})
        second.refresh_from_db()
        self.assertEqual((second.status, second.decided_by), ("cancelled", self.engineer))
        self.assertEqual(boq_state(self.project), [("A", D("400.000"), D("1500.00"), D("600000.00"), None), ("B", D("2000.000"), D("200.00"), D("400000.00"), None)])

    def test_the_signed_copy_is_uploaded_opened_and_deleted_through_the_project(self):
        self.assertEqual(self.upload(raw=JPEG, name="signed.jpg", mime="image/jpeg").status_code, 201)
        row = Attachment.objects.get(object_id=self.order.pk)
        page = self.client.get(self.url("variation", self.order.pk))
        self.assertContains(page, f'data-attachment="{row.pk}"')
        self.assertEqual(self.client.get(self.url("file", row.pk)).status_code, 200)
        self.assertEqual(self.client.post(self.url("file_delete", row.pk)).status_code, 200)
        self.assertEqual(Attachment.objects.filter(object_id=self.order.pk).count(), 0)

    def test_a_wrong_file_and_a_second_project_s_order_are_refused_by_the_endpoint(self):
        self.assertEqual(self.upload(raw=SVG, name="x.svg", mime="image/svg+xml").status_code, 400)
        other = services.save_project({"name": "مشروع تاني", "customer": self.customer}, self.owner)
        self.assertEqual(self.client.post(self.url("variation_files", self.order.pk, project=other),
                                          {"file": upload(PDF, "a.pdf", "application/pdf"), "kind": "document"}).status_code, 404)
        self.assertEqual(Attachment.objects.filter(object_id=self.order.pk).count(), 0)

    def test_the_print_carries_the_order_and_its_signature_lines(self):
        self.upload()
        self.post(action="approve", approved_on=DAY.isoformat(), owner_reference="خطاب 114")
        html = self.client.get(self.url("variation_print", self.order.pk)).content.decode()
        self.assertIn('data-kind="variation_order"', html)
        self.assertEqual(element(html, "data-print-heading"), "أمر تغيير رقم 1")
        self.assertEqual(element(html, "data-print-value"), "+90,000.00")
        self.assertEqual(element(html, "data-print-total"), "1,090,000.00 EGP")
        self.assertEqual(element(html, "data-print-day"), "11 نوفمبر 2026")
        for word in ("المقاول", "الاستشاري", "المالك"):
            self.assertIn(word, html)
        self.assertNotIn("pr-watermark", html)  # approved: no status across the paper
        draft = self.draft(title="أمر تاني")
        self.assertContains(self.client.get(self.url("variation_print", draft.pk)), "pr-watermark")


class AccessTests(ScreenSetup):
    def setUp(self):
        super().setUp()
        self.order = self.draft()

    def pages(self):
        return (self.url("variations"), self.url("variation", self.order.pk), self.url("variation_print", self.order.pk))

    def test_a_user_without_the_project_right_gets_403_everywhere(self):
        self.client.force_login(self.keeper)
        for url in self.pages() + (self.url("variation_new"), self.url("variation_edit", self.order.pk)):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.post(self.url("variation", self.order.pk), {"action": "approve"}).status_code, 403)
        self.assertEqual(self.client.post(self.url("variation_files", self.order.pk), {"file": upload(PDF, "a.pdf", "application/pdf")}).status_code, 403)

    def test_a_reader_sees_the_order_but_gets_no_control_and_cannot_post(self):
        self.client.force_login(self.viewer)
        for url in self.pages():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        html = self.client.get(self.url("variation", self.order.pk)).content.decode()
        for marker in ("data-variation-edit", "data-approve-form", "data-close-form", "data-att-upload"):
            self.assertNotIn(marker, html)
        self.assertIn("data-att-blocked", html)
        self.assertNotContains(self.client.get(self.url("variations")), "data-new-variation")
        self.assertEqual(self.client.get(self.url("variation_new")).status_code, 403)
        for data in ({"action": "approve"}, {"action": "reject"}, {"action": "cancel"}):
            self.assertEqual(self.client.post(self.url("variation", self.order.pk), data).status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "draft")

    def test_the_site_engineer_drafts_and_cancels_but_never_approves_or_rejects(self):
        self.client.force_login(self.engineer)
        self.assertEqual(self.client.get(self.url("variation_new")).status_code, 200)
        self.assertEqual(self.client.post(self.url("variation_new"), self.form_data(title="من مهندس الموقع")).status_code, 302)
        page = self.client.get(self.url("variation", self.order.pk))
        self.assertContains(page, "data-variation-edit")
        self.assertContains(page, "data-cancel")
        self.assertNotContains(page, "data-approve")
        self.assertNotContains(page, "data-reject")
        from .views_variations import WORDS

        self.assertContains(page, WORDS["ar"]["decide_only"])
        for action in ("approve", "reject"):
            with self.subTest(action=action):
                self.assertEqual(self.client.post(self.url("variation", self.order.pk), {"action": action}).status_code, 403)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "draft")

    def test_another_projects_order_id_is_refused_on_every_screen(self):
        other = services.save_project({"name": "مشروع تاني", "customer": self.customer}, self.owner)
        self.client.force_login(self.owner)
        for name in ("variation", "variation_edit", "variation_print"):
            with self.subTest(screen=name):
                self.assertEqual(self.client.get(self.url(name, self.order.pk, project=other)).status_code, 404)
        self.assertEqual(self.client.post(self.url("variation", self.order.pk, project=other), {"action": "cancel"}).status_code, 404)
        self.assertEqual(self.client.get(self.url("variation", 10 ** 9)).status_code, 404)

    def test_another_entity_sees_none_of_the_projects_orders(self):
        from entities.current import working_in
        from entities.models import Entity, EntityMembership

        from hesba_testing.factories import make_cashbox, make_location

        self.two_certificates()  # the project's money is now in the main entity
        branch = Entity.objects.create(code="E8", name_ar="فرع السويس")
        make_location(location_code="E8-LOC", entity=branch)
        make_cashbox(cashbox_code="E8-BOX", entity=branch)
        manager = person(RoleCode.MANAGER, "c1_branch_mgr")
        EntityMembership.objects.create(user=manager, entity=branch, is_default=True)
        self.client.force_login(manager)
        with working_in(branch):
            self.assertEqual(variations.orders_for(self.project).count(), 0)
            self.assertIsNone(variations.get_order(self.project, self.order.pk))
        for name in ("variation", "variation_edit", "variation_print"):
            with self.subTest(screen=name):
                self.assertEqual(self.client.get(self.url(name, self.order.pk)).status_code, 404)
        self.assertEqual(self.client.get(self.url("variation_new")).status_code, 404)
        register = self.client.get(self.url("variations"))
        self.assertContains(register, "data-empty-none")
        self.assertNotContains(register, "data-new-variation")
        self.assertNotContains(self.client.get(self.url("detail")), 'data-position="original"')
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "draft")


class BoqScreenTests(ScreenSetup):
    def test_the_bill_of_quantities_locks_and_points_at_a_variation_order(self):
        self.client.force_login(self.owner)
        free = self.client.get(self.url("boq"))
        self.assertNotContains(free, "data-boq-locked")
        self.assertContains(free, "data-boq-form")
        self.two_certificates()
        locked = self.client.get(self.url("boq"))
        self.assertContains(locked, "data-boq-locked")
        self.assertNotContains(locked, "data-boq-form")  # no "add an item" form any more
        self.assertContains(locked, "data-new-variation")
        self.assertNotContains(locked, 'value="delete"')
        refused = self.client.post(self.url("boq"), {"action": "edit", "line": str(self.a.pk), "description": "خرسانة مسلحة", "quantity": "450", "rate": "1500"})
        self.assertContains(refused, contract.MESSAGES["ar"]["boq_locked"])
        self.assertEqual(contract.boq_total(self.project), D("1000000.00"))

    def test_an_item_a_variation_order_added_is_marked_with_its_number(self):
        self.two_certificates()
        self.approve(self.draft())
        self.client.force_login(self.owner)
        html = self.client.get(self.url("boq")).content.decode()
        self.assertEqual(element(raw_element(html, f'data-boq-line="{self.new_item().line_number}"'), "data-variation-item"), "أمر تغيير 1")
        self.assertEqual(element(raw_element(html, f'data-boq-line="{self.a.line_number}"'), "data-variation-item"), "")  # an original item carries no chip
        self.assertEqual(element(html, "data-boq-total"), "1,090,000.00")


class LanguageTests(ScreenSetup):
    def setUp(self):
        super().setUp()
        self.two_certificates()
        self.order = self.draft(extension_days="14")
        self.sign(self.order)
        self.client.force_login(self.owner)

    def screens(self, lang):
        return {"register": self.url("variations", lang=lang), "form": self.url("variation_new", lang=lang),
                "edit": self.url("variation_edit", self.order.pk, lang=lang), "order": self.url("variation", self.order.pk, lang=lang),
                "print": self.url("variation_print", self.order.pk, lang=lang)}

    def test_every_screen_opens_in_arabic_rtl_and_in_english_ltr(self):
        for lang, direction, marker in (("ar", "rtl", "أمر تغيير"), ("en", "ltr", "ariation order")):
            for name, url in self.screens(lang).items():
                with self.subTest(lang=lang, screen=name):
                    page = self.client.get(url)
                    self.assertEqual(page.status_code, 200)
                    self.assertContains(page, f'dir="{direction}"')
                    self.assertContains(page, marker)  # the screen's own words, in the page's language

    def test_no_arabic_sentence_opens_with_an_english_word_and_no_arabic_leaks_into_the_english_pages(self):
        from printing.company import company_details

        # the shell's language switch, the project's code, the company's name and the currency code are data, not this page's words
        data = (self.project.code, "English", company_details()["name"], company_details()["currency"])
        for name, url in self.screens("ar").items():
            html = re.sub(r"<(script|style)\b.*?</\1>", " ", self.client.get(url).content.decode(), flags=re.S)
            for text in sentences(html):
                if text.startswith(data):
                    continue
                with self.subTest(screen=name, text=text[:40]):
                    self.assertFalse(LATIN_START.match(text), text)
        for name, url in self.screens("en").items():
            visible = visible_text(re.sub(r"<bdi>.*?</bdi>", " ", self.client.get(url).content.decode(), flags=re.S))
            with self.subTest(screen=name):
                self.assertNotIn("أوامر التغيير", visible)
                self.assertNotIn("أمر تغيير", visible)
                self.assertNotIn("اعتماد", visible)

    def test_the_english_pages_carry_the_english_figures_and_words(self):
        html = self.client.get(self.screens("en")["order"]).content.decode()
        self.assertEqual(element(html, 'data-variation-status="draft"'), "Draft")
        self.assertEqual(element(html, "data-variation-value"), "+90,000.00")
        self.assertEqual(element(html, "data-variation-extension"), "14 days")
        self.assertEqual(element(html, 'data-total="after"'), "1,090,000.00")
        register = self.client.get(self.screens("en")["register"]).content.decode()
        self.assertEqual(element(register, 'data-summary="revised"'), "1,000,000.00")  # nothing approved yet
        self.assertEqual(element(register, 'data-summary="pending"'), "+90,000.00")

    def test_the_dates_are_in_words_and_the_controls_are_named_for_a_phone(self):
        for name in ("register", "order", "print"):
            html = self.client.get(self.screens("ar")[name]).content.decode()
            with self.subTest(screen=name):
                self.assertNotRegex(visible_text(re.sub(r"<(input|select|option|textarea)\b[^>]*>", "", html)), r"\d{4}-\d{2}-\d{2}")
        self.assertIn("11 نوفمبر 2026", visible_text(self.client.get(self.screens("ar")["order"]).content.decode()))
        form = self.client.get(self.screens("ar")["form"]).content.decode()
        for field in ("vo-title", "vo-date", "vo-reason", "vo-days"):
            self.assertIn(f'for="{field}"', form)
            self.assertIn(f'id="{field}"', form)
        self.assertIn("hs-sticky-save", form)  # the save row sticks above the floating tab bar on a phone
        self.assertIn('aria-label="الكمية — خرسانة مسلحة"', form)
        self.assertIn('capture="environment"', self.client.get(self.screens("ar")["order"]).content.decode())

    def test_the_list_and_the_order_carry_the_shared_row_roles_and_no_pre_written_labels(self):
        listing = self.client.get(self.screens("ar")["register"]).content.decode()
        self.assertIn("data-list-rows", listing)
        for role in ("title", "sub", "amount", "meta"):
            self.assertIn(f'data-row="{role}"', listing)
        for name, url in self.screens("ar").items():
            with self.subTest(screen=name):
                self.assertNotRegex(self.client.get(url).content.decode(), PRE_LABELLED)


class ScannerTests(TestCase):
    databases = {"default", "feedback"}  # makemigrations reads the history of every database

    def test_no_new_template_pre_fills_a_data_label(self):
        for name in TEMPLATES:
            source = Path(settings.BASE_DIR, "templates/projects", name).read_text(encoding="utf-8")
            with self.subTest(template=name):
                self.assertNotRegex(source, PRE_LABELLED)
                self.assertNotIn("data-label", source)

    def test_the_stylesheet_is_tokens_only_and_every_token_is_defined(self):
        css = Path(settings.BASE_DIR, "static/hesba/css/projects.css").read_text(encoding="utf-8")
        self.assertIn(".vo-chip", css)
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(css, r"rgba?\(")
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", Path(settings.BASE_DIR, "static/hesba/css/tokens.css").read_text(encoding="utf-8")))
        self.assertEqual(sorted(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined), [])

    def test_every_template_pins_the_new_stylesheet_tag_and_none_keeps_the_old_one(self):
        folder = Path(settings.BASE_DIR, "templates/projects")
        for path in folder.glob("*.html"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(template=path.name):
                self.assertNotIn("projects.css' %}?v=c2-1", source)
                if "projects.css" in source:
                    self.assertIn("projects.css' %}?v=c1-vo1", source)
        for name in ("variations.html", "variation_form.html", "variation.html", "variation_print.html"):
            self.assertIn("projects.css' %}?v=c1-vo1", (folder / name).read_text(encoding="utf-8"))

    def test_the_schema_has_no_pending_change(self):
        from django.core.management import call_command

        call_command("makemigrations", "--check", "--dry-run", verbosity=0)
