"""Services video findings: the owner's «شغلي النهارده» (f01) and the employee page's work orders (f03)."""

from django.test import override_settings
from django.urls import reverse

from .tests_myday import MyDaySetup, plain


class OwnerOnMyDayTests(MyDaySetup):
    def test_an_owner_without_an_employee_record_is_sent_to_the_work_orders_board_in_a_workshop(self):
        response = self.get(self.owner)
        self.assertRedirects(response, f"{reverse('workorders:list')}?lang=ar", fetch_redirect_response=False)
        page = self.client.get(response["Location"])
        self.assertContains(page, "حسابك مش مربوط بموظف، فبنوريك شغل الفريق كله هنا بدل «شغلي النهارده».")

    def test_the_english_page_keeps_its_language_and_says_why_in_english(self):
        response = self.get(self.owner, lang="en")
        self.assertEqual(response["Location"], f"{reverse('workorders:list')}?lang=en")
        self.assertContains(self.client.get(response["Location"]), "Your account is not linked to an employee, so here is the whole team")

    def test_a_linked_owner_keeps_their_own_day(self):
        from .services import save_employee

        save_employee({"name": "المعلم", "user": str(self.owner.pk)}, self.owner)
        self.assertEqual(self.get(self.owner).status_code, 200)

    def test_someone_with_no_right_at_all_gets_the_branded_403_not_a_bare_one(self):
        page = self.get(plain("vf_nobody"))
        self.assertEqual(page.status_code, 403)
        body = page.content.decode()
        self.assertIn('data-error-page="403"', body)
        self.assertIn("مش مسموح لك تفتح الصفحة دي", body)
        self.assertNotIn("<h1>403 Forbidden</h1>", body)
        self.assertIn('dir="rtl"', body)

    def test_the_403_page_follows_the_language_and_never_prints_the_developer_message(self):
        self.client.force_login(plain("vf_nobody_en"))
        body = self.client.get(reverse("staff:my_day"), {"lang": "en"}).content.decode()
        self.assertIn('<html lang="en" dir="ltr">', body)
        self.assertIn("You are not allowed to open this page", body)
        self.assertNotIn("This account is not linked to a technician", body)  # the PermissionDenied text stays in the log

    def test_a_post_by_someone_with_no_employee_record_is_still_refused(self):
        self.assertEqual(self.post(self.owner, kind="work_order", pk=1, to="in_repair").status_code, 403)


class OwnerOnMyDaySalonTests(MyDaySetup):
    sub = "beauty"

    def test_in_a_salon_the_owner_is_sent_to_the_agenda(self):
        response = self.get(self.owner)
        self.assertRedirects(response, f"{reverse('appointments:agenda')}?lang=ar", fetch_redirect_response=False)
