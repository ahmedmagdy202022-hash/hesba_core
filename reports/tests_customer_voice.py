"""The customer's own pages and messages speak like a premium product (Ahmed, 12 Nov 2026; audit PR 1).

What a customer, a patient or a parent reads never says «المحل» (the business is «إحنا» or its own name), never says «لينك»
(it is «رابط»), never addresses them with «ليك / بيك / معاك» (it is «حضرتك»), never says «بتاع», and spells «ميعاد» the one way.
A gendered absence, late or pick-up message is worded so it reads correctly for a boy and for a girl.

The scan is deliberately narrow: only the customer-facing templates and the customer-facing message builders listed here. The
owner's and staff's screens keep their own wording («لينك», «المحل»…), so nothing here walks the whole repository.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ARABIC = "ء-ي"
SHOP = re.compile(rf"(?<![{ARABIC}])(?:[وفبل]?ال|لل)?محل(?![{ARABIC}])")  # «المحل / للمحل / بالمحل / محل», never «محلي» or «محلل»
LINK = re.compile(r"لينك")  # «اللينك / لينكات / [لينك العميل]»
ADDRESS = re.compile(rf"(?<![{ARABIC}])(?:ليك|بيك|معاك)(?![{ARABIC}])")  # the flagged «you» forms
POSSESSIVE = re.compile(rf"(?<![{ARABIC}])(?:بتاع|بتاعت|بتاعة|بتوع)[{ARABIC}]*")
MEAD = re.compile(rf"(?<![{ARABIC}])معاد(?![{ARABIC}])")  # «ميعاد» is the spelling; «معادها» (staff screen) is left alone
GENDERED = ("كان غايب", "كانت غايبة", "غايب", "وصل متأخر", "وصلت متأخرة", "مشي من", "مشي مع")
FORMS = {"shop": SHOP, "link": LINK, "address": ADDRESS, "possessive": POSSESSIVE, "mead": MEAD}

#: Templates only a customer, patient or parent opens (the error pages are read by anyone, so they are held to the same rule).
CUSTOMER_TEMPLATES = (
    "templates/payments/pay.html", "templates/payments/done.html", "templates/payments/checking.html",
    "templates/printing/share_base.html", "templates/printing/share_expired.html", "templates/workorders/public.html",
    "templates/404.html", "templates/500.html",
)
#: The login page's lock message is the one customer-facing line of that template; the rest of it is not in this PR.
LOGIN_LOCKED = re.compile(r"محاولات دخول[^<'\n]*")


def _read(relative):
    return (Path(settings.BASE_DIR) / relative).read_text(encoding="utf-8")


def _problems(text):
    return [name for name, pattern in FORMS.items() if pattern.search(text)]


def _builders():
    """(where, text) of every customer-facing sentence the PR touched: WhatsApp texts, public pages, printed terms."""

    from appointments import booking, public as appointment_public, views_booking
    from education import attendance, nursery, paylinks
    from medical import public as lab_public, views as medical_views
    from messaging import services as messaging
    from parties import views as parties_views
    from workorders import services as work_orders, views as work_order_views

    out = [(f"messaging.DEFAULT_TEXTS[{kind}]", text) for kind, text in messaging.DEFAULT_TEXTS.items()]
    out += [(f"messaging.WORKORDER_STATUS_TEXTS[{key}]", text) for key, text in messaging.WORKORDER_STATUS_TEXTS["ar"].items()]
    out += [(f"messaging.APPOINTMENT_LINK_TEXTS", messaging.APPOINTMENT_LINK_TEXTS["ar"])]
    for key in ("absent_msg", "late_msg"):
        out.append((f"education.attendance[{key}]", attendance.MESSAGES["ar"][key]))
    out.append(("education.nursery[left_msg]", nursery.MESSAGES["ar"]["left_msg"]))
    out.append(("education.paylinks[wa_text]", paylinks.WORDS["ar"]["wa_text"]))
    out.append(("parties[reminder]", parties_views.WORDS["ar"]["reminder"]))
    out.append(("medical.views[wa_text]", medical_views.WORDS["ar"]["wa_text"]))
    customer_keys = ("ask_h", "ask_date", "label_date", "label_day", "label_month", "label_year", "ask_phone", "label_phone", "open", "wrong", "format_date",
                     "format_phone", "busy", "print", "private", "dead_h", "dead", "call", "whatsapp", "wa_text_date", "wa_text_phone")
    out += [(f"medical.public[{key}]", lab_public.WORDS["ar"][key]) for key in customer_keys]
    out += [(f"appointments.public[{key}]", text) for key, text in appointment_public.WORDS["ar"].items()]
    out += [(f"appointments.views_booking.WORDS[{key}]", text) for key, text in views_booking.WORDS["ar"].items() if isinstance(text, str)]
    out += [(f"appointments.views_booking.MEDICAL_WORDS[{key}]", text) for key, text in views_booking.MEDICAL_WORDS["ar"].items() if isinstance(text, str)]
    out += [(f"appointments.views_booking.MEDICAL_MESSAGES[{key}]", text) for key, text in views_booking.MEDICAL_MESSAGES["ar"].items()]
    out += [(f"appointments.booking.MESSAGES[{key}]", booking.MESSAGES["ar"][key]) for key in ("name", "phone", "service", "staff", "when", "taken", "failed")]
    out += [(f"workorders.CUSTOMER_STATUS_LABELS[{key}]", text) for key, text in work_orders.CUSTOMER_STATUS_LABELS["ar"].items()]
    out += [(f"workorders.MESSAGES[{key}]", work_orders.MESSAGES["ar"][key])
            for key in ("link_closed", "link_changed", "link_decision", "link_done_approved", "link_done_refused", "link_same")]
    out.append(("workorders.views[terms]", work_order_views.WORDS["ar"]["terms"]))
    return out


class CustomerTemplatesTests(SimpleTestCase):
    def test_the_customer_templates_never_use_the_flagged_words(self):
        for path in CUSTOMER_TEMPLATES:
            with self.subTest(path=path):
                text = re.sub(r"\{#.*?#\}", "", _read(path), flags=re.S)  # a template comment is not read by anyone
                self.assertEqual(_problems(text), [], path)

    def test_the_login_lock_message_does_not_say_wrong_attempts(self):
        text = _read("templates/registration/login.html")
        messages = LOGIN_LOCKED.findall(text)
        self.assertEqual(len(messages), 2)  # the page and its dictionary
        for message in messages:
            self.assertNotIn("غلط", message)
            self.assertNotIn("استنى", message)
            self.assertNotIn("كلّم", message)
            self.assertEqual(_problems(message), [])


class CustomerMessageTests(SimpleTestCase):
    def test_every_customer_sentence_avoids_the_flagged_words(self):
        for where, text in _builders():
            with self.subTest(where=where):
                self.assertEqual(_problems(text), [], text)

    def test_the_absence_late_and_pickup_messages_read_correctly_for_a_boy_and_a_girl(self):
        from education import attendance, nursery
        from messaging import services as messaging

        for where, text in (("absent_msg", attendance.MESSAGES["ar"]["absent_msg"]), ("late_msg", attendance.MESSAGES["ar"]["late_msg"]),
                            ("left_msg", nursery.MESSAGES["ar"]["left_msg"]), ("ABSENCE", messaging.DEFAULT_TEXTS["absence"])):
            with self.subTest(where=where):
                for gendered in GENDERED:
                    self.assertNotIn(gendered, text)
                self.assertIn("{student}", text)
                self.assertIn("{company}", text)  # the business signs the message

    def test_the_default_texts_keep_the_placeholders_the_outbox_can_fill(self):
        from messaging import services as messaging

        for kind, text in messaging.DEFAULT_TEXTS.items():
            for placeholder in re.findall(r"\{\w+\}", text):
                self.assertIn(placeholder, messaging.PLACEHOLDERS, kind)

    def test_the_medical_follow_up_text_names_the_business(self):
        from medical import views as medical_views

        text = medical_views.WORDS["ar"]["wa_text"].format(name="منى", date="2026-11-12", company="عيادة الشروق")
        self.assertIn("ميعاد متابعة حضرتك", text)
        self.assertIn("عيادة الشروق", text)


class CustomerLabelsAreSeparateTests(SimpleTestCase):
    def test_the_customer_labels_never_talk_about_the_customer_in_the_third_person(self):
        from workorders import services

        self.assertEqual(set(services.CUSTOMER_STATUS_LABELS["ar"]), set(services.STATUS_LABELS["ar"]))
        for key, label in services.CUSTOMER_STATUS_LABELS["ar"].items():
            self.assertNotIn("العميل", label, key)
        self.assertEqual(services.CUSTOMER_STATUS_LABELS["ar"]["quoted"], "في انتظار موافقة حضرتك")

    def test_the_staff_labels_are_unchanged(self):
        from workorders import services

        staff = services.STATUS_LABELS["ar"]
        self.assertEqual((staff["quoted"], staff["approved"], staff["diagnosed"], staff["cancelled"]), ("مستني موافقة العميل", "العميل وافق", "اتشخّص", "اتلغى"))
