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
        self.assertIn("الصفحة دي مش متاحة لحسابك", body)
        self.assertNotIn("<h1>403 Forbidden</h1>", body)
        self.assertIn('dir="rtl"', body)

    def test_the_403_page_follows_the_language_and_never_prints_the_developer_message(self):
        self.client.force_login(plain("vf_nobody_en"))
        body = self.client.get(reverse("staff:my_day"), {"lang": "en"}).content.decode()
        self.assertIn('<html lang="en" dir="ltr">', body)
        self.assertIn("This page is not available to your account", body)
        self.assertNotIn("This account is not linked to a technician", body)  # the PermissionDenied text stays in the log

    def test_a_post_by_someone_with_no_employee_record_is_still_refused(self):
        self.assertEqual(self.post(self.owner, kind="work_order", pk=1, to="in_repair").status_code, 403)


class OwnerOnMyDaySalonTests(MyDaySetup):
    sub = "beauty"

    def test_in_a_salon_the_owner_is_sent_to_the_agenda(self):
        response = self.get(self.owner)
        self.assertRedirects(response, f"{reverse('appointments:agenda')}?lang=ar", fetch_redirect_response=False)


class EmployeePageAgreesWithTheTechniciansReportTests(MyDaySetup):
    """f03: the page said «مفيش شغل» while the technicians' report listed his delivered orders, because the page had no work-orders source."""

    def deliver_two(self):
        from decimal import Decimal

        from django.utils import timezone

        from cashboxes.models import Cashbox
        from hesba_testing.factories import make_cashbox, make_item
        from workorders import services

        self.tech.commission_percent = Decimal("10.00")
        self.tech.save(update_fields=["commission_percent"])
        make_cashbox(cashbox_code="VF-CB", name_ar="خزنة", is_default=True)
        labour = make_item(item_code="VF-LAB", item_name="مصنعية", default_sale_price=Decimal("300.00"), is_stock_tracked=False)
        for _ in range(2):
            order = self.order()
            services.add_line(order, labour, self.owner)
            for status in ("diagnosed", "in_repair", "ready"):
                order = services.move(order, status, self.owner)
            services.deliver(order, self.owner, paid_now=str(services.totals(order)["total"]), cashbox=Cashbox.objects.get(cashbox_code="VF-CB"))
        return (timezone.localdate().replace(day=1), timezone.localdate())

    def test_the_employee_page_shows_what_the_report_shows_for_the_same_month(self):
        from workorders import services

        month = self.deliver_two()
        row = next(row for row in services.technician_report(*month) if row["technician"] == self.tech)
        self.assertEqual((row["count"], str(row["commission"])), (2, "60.00"))
        self.client.force_login(self.owner)
        page = self.client.get(reverse("staff:detail", args=[self.tech.pk]), {"lang": "ar"})
        self.assertContains(page, f'data-work-wo-count>{row["count"]}<')
        self.assertContains(page, "60.00</strong>")  # the commission cell, from the report's own row
        self.assertNotContains(page, "data-work-none")
        self.assertContains(page, f'href="{reverse("workorders:report")}?lang=ar"')

    def test_the_team_list_counts_the_delivered_orders_too(self):
        from staff.work import team_month

        self.deliver_two()
        done = team_month([self.tech])[self.tech.pk]
        self.assertEqual((done["done"], str(done["commission"])), (2, "60.00"))

    def test_an_employee_with_no_delivered_order_still_says_there_is_no_work(self):
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("staff:detail", args=[self.other.pk])), "data-work-none")
