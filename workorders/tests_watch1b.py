"""Watch round 1b on the workshop: who may be chosen as the technician (MT-05) and the report in the reports hub (SH-12)."""

from django.urls import reverse

from staff.tests_watch1b import select_options

from . import tests as base


class TechnicianListTests(base.WorkshopSetup):
    def setUp(self):
        super().setUp()
        from staff.services import save_employee

        self.sameer = save_employee({"name": "سمير عادل", "title": "مندوب مبيعات"}, self.owner)
        self.hala = save_employee({"name": "هالة مصطفى", "title": "مندوبة"}, self.owner)
        self.client.force_login(self.owner)

    def test_the_receiving_form_and_an_order_offer_technicians_and_no_sales_rep(self):
        names = select_options(self.client.get(reverse("workorders:new")).content.decode(), "technician")
        self.assertIn("أحمد سامي", names)
        self.assertNotIn("سمير عادل", names)
        self.assertNotIn("هالة مصطفى", names)
        order = self.order()
        names = select_options(self.client.get(reverse("workorders:detail", args=[order.pk])).content.decode(), "technician")
        self.assertIn("أحمد سامي", names)
        self.assertNotIn("هالة مصطفى", names)

    def test_a_new_order_cannot_be_given_to_a_rep(self):
        from .models import WorkOrder

        self.client.post(reverse("workorders:new"), {"customer": self.customer.pk, "device": "ثلاجة", "problem": "x", "warranty_days": "30", "technician": self.hala.pk})
        self.assertIsNone(WorkOrder.objects.get(device="ثلاجة").technician_id)

    def test_an_order_already_given_to_such_a_person_keeps_them_on_its_page_and_on_save(self):
        order = self.order(technician=self.sameer)
        url = reverse("workorders:detail", args=[order.pk])
        html = self.client.get(url).content.decode()
        self.assertIn("سمير عادل", select_options(html, "technician"))
        self.assertIn(f'<option value="{self.sameer.pk}" selected>', html)
        self.assertNotIn("هالة مصطفى", select_options(html, "technician"))
        self.client.post(url, {"action": "details", "technician": self.sameer.pk, "diagnosis": "المروحة مكسورة"})
        order.refresh_from_db()
        self.assertEqual((order.technician_id, order.diagnosis), (self.sameer.pk, "المروحة مكسورة"))
