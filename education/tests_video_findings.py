"""What the education demos showed while the walkthrough video was recorded, and must not show again.

Each test is one numbered finding from that recording (the numbers are the findings file's). The demos are built the way the demo landing builds
them (``config.demo_templates``: setup, then ``seed_sample_business``), once for each test class, with the clock frozen at a Cairo wall-clock moment
so no test depends on the hour it runs or on a day off. Every test reads its own element, never a whole page for a short value.

Not covered here on purpose: 13 (backed out: recording only finished classes moves a session-paid teacher's month by one class and the education
panel's «classes taken» tile, so it is reported, not done), 14 (a company paying for its employees' courses is a real case), 17 (the dashboard's sales belong to every activity),
18 (no teacher role: the permission core is protected), 21 (the cards sheet is a print page).
"""

import re
from datetime import date, datetime, timedelta
from io import StringIO
from unittest import mock
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import UserProfile

from . import demo as education_demo
from .models import Course, DayRecord, Enrollment, PickupPerson, Student, StudyGroup

CAIRO = ZoneInfo("Africa/Cairo")
PASSWORD = "Demo-pass-1"


def cairo(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=CAIRO)


#: Saturday 14 Nov 2026 (the day of the recording). No night Egypt changes its clock.
ONE_AM = cairo(2026, 11, 14, 1, 0)
MID_MORNING = cairo(2026, 11, 14, 10, 40)
JUST_AFTER_MIDNIGHT = cairo(2026, 11, 14, 0, 10)


def frozen(moment):
    return mock.patch.object(timezone, "now", return_value=moment)


def element(html, pattern):
    """The one element a test reads: the first match of ``pattern``, or a failure that says which."""

    found = re.search(pattern, html, re.S)
    assert found is not None, pattern
    return found.group(0)


def text_of(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


@override_settings(DEMO_MODE=True, DEMO_PASSWORD=PASSWORD, DEBUG=True)
class VideoDemoCase(TestCase):
    """One seeded education demo of one sub-activity at one moment, built once for the class (the demo landing's build)."""

    sub_activity = "training"
    moment = ONE_AM

    @classmethod
    def setUpTestData(cls):
        from config.demo_templates import complete_demo_setup, seed_sample_business

        with frozen(cls.moment):
            call_command("bootstrap_client", display_name="تجربة", client_code="DEMO", password=PASSWORD, verbosity=0)
            call_command("seed_demo_users", password=PASSWORD, force=True, verbosity=0, stdout=StringIO())
            complete_demo_setup("education", cls.sub_activity)
            assert seed_sample_business(), cls.sub_activity
            cls.today = timezone.localdate()

    def setUp(self):
        clock = frozen(self.moment)
        clock.start()
        self.addCleanup(clock.stop)
        self.assertTrue(self.client.login(username="owner", password=PASSWORD))  # under the frozen clock, or the session is expired at a later moment
        self.owner = get_user_model().objects.get(username="owner")

    def get(self, url, lang="ar"):
        response = self.client.get(url, {"lang": lang} if "?" not in url else None)
        self.assertEqual(response.status_code, 200, url)
        return response.content.decode()


def tab_label(html, key):
    return re.search(rf'data-tab="{key}"[^>]*>.*?<span class="hs-tab__label">([^<]*)</span>', html, re.S).group(1)


# ---- a training centre: words, search, demo data -------------------------------------------------------------------------------------------


class TrainingWordsAndSearchTests(VideoDemoCase):
    sub_activity = "training"

    def test_item_1_the_payment_link_is_called_a_link_in_plain_words_on_the_fees_and_dues_screens(self):
        """The note is the screens' own element; the capability's label is the one the note quotes."""

        from payments import services as pay
        from settings_core import capabilities

        label = capabilities.CAPABILITIES["payment_links"]
        self.assertEqual((label["ar"], label["en"]), ("رابط الدفع أونلاين", "Online payment link"))
        for name in ("fees", "dues"):
            with self.subTest(screen=name):
                html = self.get(reverse(f"education:{name}") + "?lang=ar")
                note = text_of(element(html, r'<p[^>]*data-pay-note>.*?</p>'))
                self.assertIn("رابط الدفع مقفول؛ شغّله من الإعدادات ← الخصائص («رابط الدفع أونلاين»).", note)
        self.assertEqual(pay.MESSAGES["ar"]["off"], "رابط الدفع مقفول؛ شغّله من الإعدادات ← الخصائص («رابط الدفع أونلاين»).")

    def test_item_1_nothing_an_education_screen_says_about_payment_links_uses_the_banned_words(self):
        from . import paylinks

        for word, text in ((key, value) for key, value in paylinks.WORDS["ar"].items() if isinstance(value, str)):
            for banned in ("لينك", "دوس"):
                self.assertNotIn(banned, text, word)

    def test_item_2_the_certificate_names_who_issued_it_by_their_display_name(self):
        from .models import Certificate

        certificate = Certificate.objects.get()
        html = self.get(reverse("education:certificate", args=[certificate.pk]) + "?lang=ar")
        block = element(html, r"<div><dt>أصدرها</dt><dd>.*?</dd></div>")
        self.assertEqual(text_of(block), "أصدرها أ. منى عبد الرحمن")
        self.assertNotIn("owner", block)

    def test_item_3_the_staff_files_login_picker_reads_the_name_with_the_login_isolated_after_it(self):
        from staff.models import Employee

        employee = Employee.objects.order_by("pk").first()
        html = self.get(reverse("staff:detail", args=[employee.pk]) + "?lang=ar")
        picker = element(html, r'<select id="id_user" name="user">.*?</select>')
        manager = get_user_model().objects.get(username="manager")
        option = element(picker, rf'<option value="{manager.pk}"[^>]*>.*?</option>')
        self.assertEqual(text_of(option), "أ. إيمان عبد الغني (⁦manager⁩)")

    def test_item_4_the_roles_screen_names_the_gift_cards_group_in_arabic_and_in_english(self):
        from settings_core.operational_views import PERMISSION_SECTIONS

        self.assertEqual(PERMISSION_SECTIONS["giftcards"], ("كروت الهدايا", "Gift cards"))
        for lang, expected in (("ar", "كروت الهدايا"), ("en", "Gift cards")):
            html = self.get(reverse("settings_core:roles") + f"?lang={lang}")
            owner = element(html, r'<article class="op-card" data-role="owner">.*?</article>')
            names = [text_of(name) for name in re.findall(r"<summary><b>(.*?)</b>", owner, re.S)]
            self.assertIn(expected, names, lang)
            self.assertFalse([name for name in names if name == "giftcards"], names)  # the slug is gone

    def test_item_6_the_phone_bar_says_trainees_once_and_names_the_accounts_tab_for_what_it_holds(self):
        html = self.get(reverse("dashboard_snapshot") + "?lang=ar")
        self.assertEqual(tab_label(html, "education"), "المتدربين")
        self.assertEqual(tab_label(html, "customers"), "الحسابات")
        english = self.get(reverse("dashboard_snapshot") + "?lang=en")
        self.assertEqual((tab_label(english, "education"), tab_label(english, "customers")), ("Trainees", "Accounts"))

    def test_item_8_and_23_the_guardians_phone_finds_their_child_in_either_digits(self):
        """The list shows the guardian's phone in the child's row: copying it must find the child (and the guardian's name too)."""

        families = {"01201234503": {"سلمى طارق", "آدم طارق"}, "01001234505": {"حمزة إبراهيم"}}
        for phone, children in families.items():
            for typed in (phone, phone.translate(str.maketrans("0123456789", "٠١٢٣٤٥٦٧٨٩"))):
                response = self.client.get(reverse("education:students"), {"lang": "ar", "q": typed})
                rows = re.findall(r'<tr data-student="\d+".*?</tr>', response.content.decode(), re.S)
                self.assertEqual({re.search(r'<td class="op-cell-text">(.*?)</td>', row).group(1) for row in rows}, children, typed)
        by_name = self.client.get(reverse("education:students"), {"lang": "ar", "q": "طارق فهمي"}).content.decode()
        self.assertEqual(len(re.findall(r'<tr data-student="\d+"', by_name)), 2)

    def test_item_8_a_search_that_finds_nothing_says_so_and_offers_to_clear_it(self):
        html = self.client.get(reverse("education:students"), {"lang": "ar", "q": "99999"}).content.decode()
        empty = element(html, r'<td colspan="7" class="op-empty">.*?</td>')
        self.assertEqual(text_of(empty), "مفيش نتيجة للبحث ده. امسح البحث")
        self.assertIn(f'href="{reverse("education:students")}?lang=ar"', element(empty, r"<a[^>]*data-clear-search>"))
        english = self.client.get(reverse("education:students"), {"lang": "en", "q": "99999"}).content.decode()
        self.assertEqual(text_of(element(english, r'<td colspan="7" class="op-empty">.*?</td>')), "No results for that search. Clear the search")

    def test_item_8_with_no_search_the_list_is_the_list_and_no_empty_message_shows(self):
        html = self.get(reverse("education:students") + "?lang=ar")
        self.assertEqual(len(re.findall(r'<tr data-student="\d+"', html)), 7)
        self.assertNotIn("data-no-match", html)

    def test_item_5_a_group_already_called_a_group_is_announced_once_and_any_other_name_keeps_the_word(self):
        """The language centre's levels need a level-1 group, a level-2 group of the course and students in the first: the training demo has them too."""

        low = StudyGroup.objects.get(name="مجموعة السبت والثلاثاء")
        high = StudyGroup.objects.get(name="مجموعة الأحد والأربعاء")
        first_of_next_month = (self.today.replace(day=28) + timedelta(days=4)).replace(day=1)
        here = list(Enrollment.objects.filter(group=low, status="active").values_list("student_id", flat=True).order_by("student_id"))
        self.assertGreaterEqual(len(here), 2)
        url = reverse("education:group", args=[low.pk])
        first = self.client.post(url, {"action": "move_up", "students": [here[0]], "target": high.pk, "on_date": first_of_next_month.isoformat(), "lang": "ar"}, follow=True)
        self.assertEqual([str(m) for m in first.context["messages"]], ["اتنقل 1 طالب إلى مجموعة الأحد والأربعاء."])
        StudyGroup.objects.filter(pk=high.pk).update(name="دفعة المساء")
        second = self.client.post(url, {"action": "move_up", "students": [here[1]], "target": high.pk, "on_date": first_of_next_month.isoformat(), "lang": "ar"}, follow=True)
        self.assertEqual([str(m) for m in second.context["messages"]], ["اتنقل 1 طالب لمجموعة دفعة المساء."])
        StudyGroup.objects.filter(pk=high.pk).update(name="مجموعة الأحد والأربعاء")

    def test_item_9_students_are_at_the_level_of_their_group_not_graduates_in_a_current_group(self):
        stages = ("المستوى الأول", "المستوى الثاني")
        for student in Student.objects.all():
            first = Enrollment.objects.filter(student=student, group__level__isnull=False, group__name__startswith="مجموعة").order_by("group_id").first().group
            self.assertEqual(student.stage, stages[first.level - 1], student.name)
        self.assertFalse(Student.objects.filter(stage="خريجين").exists())

    def test_item_7_the_other_demo_logins_are_greeted_by_name_and_a_name_a_visitor_typed_is_kept(self):
        from config import demo_templates

        expected = demo_templates.DEMO_LOGINS["education"]
        self.assertEqual(set(expected), {"manager", "cashier", "accountant", "stock_keeper"})
        for username, name in expected.items():
            self.assertTrue(name.startswith("أ. "), name)
            self.assertEqual(UserProfile.objects.get(user__username=username).display_name, name, username)
        UserProfile.objects.filter(user__username="cashier").update(display_name="اسم كتبه الزائر")
        demo_templates.name_the_demo_owner("education", "training")
        self.assertEqual(UserProfile.objects.get(user__username="cashier").display_name, "اسم كتبه الزائر")
        UserProfile.objects.filter(user__username="cashier").update(display_name=expected["cashier"])

    def test_item_15_no_teacher_has_a_shop_customers_appointment(self):
        from appointments.models import Appointment
        from staff.models import Employee

        self.assertEqual(Appointment.objects.count(), 0)
        teacher = Employee.objects.order_by("pk").first()
        html = self.get(reverse("staff:detail", args=[teacher.pk]) + "?lang=ar")
        self.assertNotIn('data-work="upcoming"', html)  # the «مواعيده الجاية» table is not drawn: there is nothing to list

    def test_item_16_every_student_has_a_birth_date_of_an_adult_and_the_file_has_no_red_warning(self):
        for student in Student.objects.all():
            self.assertIsNotNone(student.date_of_birth, student.name)
            age = (self.today - student.date_of_birth).days / 365.25
            self.assertTrue(19 <= age < 31, (student.name, age))
        html = self.get(reverse("education:student", args=[Student.objects.order_by("pk").first().pk]) + "?lang=ar")
        card = element(html, r"<section[^>]*data-portal-card>.*?</section>")
        self.assertIn('data-portal-kind="date"', card)  # the portal's question is the birth date, as the product intends
        self.assertNotIn("data-portal-phone-only", card)

    def test_item_22_the_teachers_work_range_is_one_left_to_right_isolate(self):
        from staff.models import Employee

        teacher = Employee.objects.order_by("pk").first()
        html = self.get(reverse("staff:detail", args=[teacher.pk]) + "?lang=ar")
        heading = element(html, r"<h2>شغله الشهر ده.*?</h2>")
        isolate = element(heading, r"<bdi[^>]*data-work-range>.*?</bdi>")
        self.assertIn('dir="ltr"', isolate)
        self.assertEqual(text_of(isolate), "2026-11-01 – 2026-11-14")
        self.assertEqual(len(re.findall(r"<bdi", heading)), 1)  # one isolate, not a bdi for each date with the dash and brackets left to drift between them


# ---- a language centre: names and levels agree ----------------------------------------------------------------------------------------------


class LanguageCentreTests(VideoDemoCase):
    sub_activity = "languages"

    def test_item_10_no_course_name_says_a_level_that_one_of_its_groups_is_not_at(self):
        for course in Course.objects.all():
            levels = {group.level for group in course.groups.exclude(name__startswith="قاعة المذاكرة").exclude(name=education_demo.BATCH_NAME)}
            for number in re.findall(r"مستوى (\d)", course.name):
                self.assertEqual(levels, {int(number)}, course.name)
        english = Course.objects.get(name="كورس إنجليزي عام")
        self.assertEqual(sorted(group.level for group in english.groups.filter(name__startswith="مجموعة")), [1, 2])  # the levels feature is as it was

    def test_item_10_the_placement_test_is_not_a_monthly_paid_course_and_the_other_courses_are_as_they_were(self):
        from master_data.models import Item

        self.assertEqual(sorted(Course.objects.values_list("name", flat=True)), ["ألماني A1", "كورس إنجليزي عام"])
        self.assertTrue(Item.objects.filter(item_name="اختبار تحديد مستوى", is_stock_tracked=False).exists())  # still a service the centre sells
        self.assertEqual({course.basis for course in Course.objects.all()}, {"monthly"})

    def test_item_9_students_of_a_language_centre_have_the_level_of_their_group(self):
        self.assertEqual(set(Student.objects.values_list("stage", flat=True)), {"المستوى الأول", "المستوى الثاني"})


# ---- a nursery -------------------------------------------------------------------------------------------------------------------------------


class NurseryTests(VideoDemoCase):
    sub_activity = "nursery"
    moment = MID_MORNING

    def test_item_11_a_childs_file_has_no_marks_card(self):
        child = Student.objects.order_by("pk").first()
        html = self.get(reverse("education:student", args=[child.pk]) + "?lang=ar")
        self.assertNotIn("data-marks-card", html)
        self.assertIn("data-portal-card", html)  # the other cards of the file are still there

    def test_item_12_pickup_people_have_full_names_of_the_right_gender_and_the_relation_in_its_field(self):
        mothers = {"سمر إبراهيم", "داليا حسين", "رانيا عبد الحليم"}
        fathers = {"خالد عبد الغفار", "أحمد صلاح"}
        for child in Student.objects.all():
            people = list(PickupPerson.objects.filter(student=child).order_by("pk"))
            self.assertEqual(len(people), 2, child.name)
            payer, other = people
            self.assertNotIn("—", other.name)
            self.assertGreaterEqual(len(other.name.split()), 2, other.name)
            opposite = "الأم" if child.relation == "الأب" else "الأب"
            self.assertEqual(other.relation, opposite, child.name)
            self.assertIn(other.name, mothers if other.relation == "الأم" else fathers, child.name)
            self.assertEqual((payer.relation, payer.name), (child.relation, child.payer.name.split(" (")[0]))

    def test_item_12_arrivals_are_spread_over_a_believable_morning(self):
        records = list(DayRecord.objects.order_by("arrived_at"))
        self.assertEqual(len(records), 6)
        times = [timezone.localtime(record.arrived_at).strftime("%H:%M") for record in records]
        self.assertEqual(times, ["07:20", "07:35", "07:50", "08:05", "08:20", "08:40"])

    def test_item_16_every_child_is_between_three_and_five_years_old(self):
        for child in Student.objects.all():
            self.assertIsNotNone(child.date_of_birth)
            self.assertTrue(3 <= (self.today - child.date_of_birth).days / 365.25 < 6, (child.name, child.date_of_birth))
        html = self.get(reverse("education:student", args=[Student.objects.order_by("pk").first().pk]) + "?lang=ar")
        self.assertNotIn("data-portal-phone-only", html)


class NurseryBuiltJustAfterMidnightTests(VideoDemoCase):
    sub_activity = "nursery"
    moment = JUST_AFTER_MIDNIGHT

    def test_item_12_a_build_before_the_morning_still_spreads_the_arrivals_and_never_puts_one_in_the_future_or_yesterday(self):
        records = list(DayRecord.objects.order_by("arrived_at"))
        self.assertEqual(len(records), 6)
        for record in records:
            local = timezone.localtime(record.arrived_at)
            self.assertEqual(local.date(), self.today)
            self.assertLessEqual(record.arrived_at, self.moment)
        self.assertGreaterEqual(len({timezone.localtime(record.arrived_at).strftime("%H:%M") for record in records}), 4)


class ArrivalsAndBirthDatesAtAnyMomentTests(SimpleTestCase):
    """The two helpers the seed uses, at the edges (no database, no demo)."""

    def test_the_arrival_slots_are_used_once_the_morning_has_passed_and_the_spread_before_it(self):
        self.assertEqual([education_demo.arrival_time(n, 6, cairo(2026, 11, 14, 12, 0)).astimezone(CAIRO).strftime("%H:%M") for n in range(6)],
                         ["07:20", "07:35", "07:50", "08:05", "08:20", "08:40"])
        for now in (cairo(2026, 11, 14, 0, 2), cairo(2026, 11, 14, 5, 30), cairo(2026, 11, 14, 7, 45), cairo(2026, 11, 14, 8, 30)):
            times = [education_demo.arrival_time(n, 6, now) for n in range(6)]
            self.assertEqual(times, sorted(times), now)
            self.assertTrue(all(cairo(2026, 11, 14, 0, 0) <= each <= now for each in times), now)

    def test_a_birth_date_is_a_whole_number_of_years_old_on_every_kind_of_day(self):
        for today in (date(2026, 1, 1), date(2026, 2, 28), date(2028, 2, 29), date(2026, 3, 14), date(2026, 11, 14), date(2026, 12, 31)):
            for sub, low, high in (("nursery", 3, 5), ("tutoring_center", 14, 15), ("training", 21, 30), ("languages", 21, 30)):
                for n in range(7):
                    born = education_demo.birth_date(sub, n, today)
                    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
                    self.assertTrue(low <= age <= high, (sub, n, today, born, age))
                    self.assertLessEqual(born, today)


# ---- layout: the stylesheets (the pixels are measured in a browser; these keep the rules that do the work) ------------------------------------


class PhoneLayoutStylesheetTests(SimpleTestCase):
    @staticmethod
    def read(path):
        from django.contrib.staticfiles import finders

        with open(finders.find(path), encoding="utf-8") as handle:
            return handle.read()

    def test_item_19_the_nursery_board_column_is_the_forms_width_and_only_nursery_selectors_were_added(self):
        css = self.read("hesba/css/education_scan.css")
        added = css.split("the scan screen (.es-scan) keeps its rules above. */", 1)[1]
        self.assertIn(".es-nursery{grid-template-columns:minmax(0,1fr)", added)
        self.assertIn(".es-nursery .es-box{inline-size:100%;min-width:0}", added)
        rules = re.findall(r"([^{}]+)\{[^{}]*\}", re.sub(r"/\*.*?\*/", "", added, flags=re.S))
        self.assertTrue(rules)
        for selectors in rules:
            for selector in selectors.split(","):
                self.assertRegex(selector.strip(), r"^(\.es-nursery|\[data-nursery-board\])", selector)  # the scan screen's own rules are untouched
        self.assertIn("min-height:var(--hs-control-height)", added)  # every control a full touch target

    def test_item_19_the_nursery_and_scan_pages_load_the_new_version_of_the_stylesheet(self):
        from pathlib import Path

        from django.conf import settings

        for name in ("nursery.html", "scan.html"):
            template = Path(settings.BASE_DIR, "templates", "education", name).read_text(encoding="utf-8")
            self.assertIn("education_scan.css' %}?v=e1-3", template, name)

    def test_item_20_the_pay_row_stacks_on_a_phone_and_the_pickup_forms_stack_in_the_nursery(self):
        css = self.read("hesba/css/education_teachers.css")
        self.assertRegex(css, r"@media \(max-width:700px\)\{\s*\.edu-inline-form\[data-pay-form\]\{display:grid")
        self.assertIn("inline-size:100%", css)
        self.assertIn("min-height:var(--hs-control-height)", css)
        scan = self.read("hesba/css/education_scan.css")
        self.assertIn("[data-nursery-board] .edu-inline{display:grid", scan)

    def test_item_19_and_20_the_new_rules_use_only_defined_tokens_and_no_colour_literals(self):
        defined = set(re.findall(r"(--hs-[\w-]+)\s*:", self.read("hesba/css/tokens.css")))
        for sheet in ("hesba/css/education_scan.css", "hesba/css/education_teachers.css"):
            css = self.read(sheet)
            self.assertEqual(re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(", css), [], sheet)
            self.assertEqual(set(re.findall(r"var\((--hs-[\w-]+)", css)) - defined, set(), sheet)


class TeachersAndNurseryPagesLoadTheirStylesTests(VideoDemoCase):
    sub_activity = "training"

    def test_item_20_the_teachers_page_loads_the_pay_row_stylesheet(self):
        html = self.get(reverse("education:teachers") + "?lang=ar")
        self.assertRegex(element(html, r'<link[^>]*education_teachers\.css[^>]*>'), r"\?v=ev-1")
        form = element(html, r'<form class="edu-inline-form"[^>]*data-pay-form="\d+">')
        self.assertIn("edu-inline-form", form)


class EnglishRendersTheNewWordsTests(VideoDemoCase):
    sub_activity = "training"

    def test_the_english_screens_still_render_with_the_new_elements(self):
        client = Client()
        self.assertTrue(client.login(username="owner", password=PASSWORD))
        for url in (reverse("education:students") + "?lang=en&q=zzz", reverse("education:fees") + "?lang=en", reverse("education:dues") + "?lang=en"):
            self.assertEqual(client.get(url).status_code, 200, url)
