"""APPT-001: appointments & visits, billed through the ordinary sales engine."""

from datetime import datetime, time, timedelta
from decimal import Decimal as D
from urllib.parse import unquote

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from audit.models import AuditLog
from hesba_testing.factories import make_customer, make_item, make_location, make_seeded_role, make_user, make_user_profile
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from sales.models import CustomerLedgerEntry, SalesInvoice
from sales.services import create_sales_return, post_sales_invoice
from staff.services import save_employee

from . import services
from .models import Appointment, AppointmentStatus


TODAY = timezone.localdate()


def person(role_code, username):
    user = make_user(username=username)
    make_user_profile(user=user, role=make_seeded_role(role_code))
    return user


def at(hour, minute=0, day=None):
    return timezone.make_aware(datetime.combine(day or TODAY, time(hour, minute)))


class AppointmentSetup(TestCase):
    def setUp(self):
        self.profile = prepared_client(activity="services", sub_activity="maintenance",
                                       modules="customers,items_services,sales_operations,cashboxes,reports,employees_technicians,appointments_visits")
        self.owner = person(RoleCode.OWNER, "appt_owner")
        make_location(location_code="SHOP", is_default=True)
        self.customer = make_customer(customer_code="C-1", name="مدام سعاد", phone="01001234567")
        self.service = make_item(item_code="SRV-AC", item_name="صيانة تكييف", default_sale_price="300.00", is_stock_tracked=False)
        self.tech = save_employee({"name": "أحمد الفني", "commission_percent": "10"}, self.owner)

    def book(self, hour=10, minutes=60, employee="tech", **extra):
        data = {"customer": self.customer, "employee": self.tech if employee == "tech" else employee, "service": self.service,
                "starts_at": at(hour), "duration_minutes": minutes}
        data.update(extra)
        return services.book(data, self.owner)


class BookingTests(AppointmentSetup):
    def test_booking_takes_the_service_price_and_numbers_by_day(self):
        first = self.book()
        self.assertEqual((first.number, first.price, first.status), (f"AP-{TODAY:%Y%m%d}-001", D("300.00"), AppointmentStatus.BOOKED))
        self.assertEqual(self.book(hour=12, price="250").price, D("250.00"))
        self.assertTrue(AuditLog.objects.filter(action="book_appointment").exists())

    def test_an_employee_cannot_be_in_two_places(self):
        self.book(hour=10, minutes=60)
        for hour, minute in ((10, 30), (9, 30)):
            with self.subTest(start=f"{hour}:{minute}"), self.assertRaisesMessage(ValidationError, "عنده ميعاد تاني من 10:00 لـ 11:00"):
                services.book({"customer": self.customer, "employee": self.tech, "service": self.service, "starts_at": at(hour, minute), "duration_minutes": 60}, self.owner)
        self.book(hour=11)  # back to back is fine
        self.book(hour=10, employee=None)  # no employee, no clash
        cancelled = Appointment.objects.get(starts_at=at(11))
        services.set_status(cancelled, AppointmentStatus.CANCELLED, self.owner)
        self.book(hour=11, minutes=30)  # a cancelled slot is free again

    def test_refusals(self):
        for extra, text in (({"customer": None}, "اختار العميل"), ({"duration_minutes": 2}, "المدة لازم"), ({"price": "-1"}, "السعر"),
                            ({"kind": "visit", "address": ""}, "الزيارة محتاجة عنوان")):
            with self.subTest(extra=extra), self.assertRaisesMessage(ValidationError, text):
                self.book(**extra)
        self.customer.address = "12 ش التحرير"
        self.customer.save()
        self.assertEqual(self.book(kind="visit").address, "12 ش التحرير")  # the customer's own address by default

    def test_statuses_move_forward_only(self):
        appointment = self.book()
        services.set_status(appointment, AppointmentStatus.CONFIRMED, self.owner)
        services.set_status(appointment, AppointmentStatus.ARRIVED, self.owner)
        with self.assertRaises(ValidationError):
            services.set_status(appointment, AppointmentStatus.BOOKED, self.owner)
        with self.assertRaises(ValidationError):
            services.set_status(appointment, AppointmentStatus.NO_SHOW, self.owner)
        services.set_status(appointment, AppointmentStatus.DONE, self.owner)
        with self.assertRaisesMessage(ValidationError, "خلص أو اتلغى"):
            services.reschedule(appointment, {"customer": self.customer, "starts_at": at(15), "duration_minutes": 30}, self.owner)


class BillingTests(AppointmentSetup):
    def test_bill_makes_one_draft_through_sales_and_posting_charges_the_customer(self):
        appointment = self.book()
        with self.assertRaisesMessage(ValidationError, "لازم يكون خلص أو جاري"):
            services.bill(appointment, self.owner)
        services.set_status(appointment, AppointmentStatus.ARRIVED, self.owner)
        invoice = services.bill(appointment, self.owner)
        appointment.refresh_from_db()
        self.assertEqual((invoice.status, invoice.total_amount, invoice.customer, appointment.status, appointment.invoice), ("draft", D("300.00"), self.customer, AppointmentStatus.DONE, invoice))
        self.assertEqual(services.bill(appointment, self.owner), invoice)  # once only
        self.assertEqual(invoice.notes, f"ميعاد {appointment.number}")  # TRACK-ACTS W18: no English note on an Arabic invoice
        self.assertEqual(SalesInvoice.objects.count(), 1)
        self.assertFalse(CustomerLedgerEntry.objects.exists())  # nothing moves until the invoice is posted
        post_sales_invoice(invoice.pk, self.owner)
        self.assertEqual(CustomerLedgerEntry.objects.get().due_increase, D("300.00"))

    def test_performance_counts_posted_sales_net_of_tax_and_returns(self):
        first, second, third = self.book(hour=9), self.book(hour=11), self.book(hour=13)
        for appointment in (first, second):
            services.set_status(appointment, AppointmentStatus.DONE, self.owner)
            post_sales_invoice(services.bill(appointment, self.owner).pk, self.owner)
        services.set_status(third, AppointmentStatus.DONE, self.owner)
        services.bill(third, self.owner)  # a draft earns nothing
        invoice = Appointment.objects.get(pk=second.pk).invoice
        create_sales_return("SR-1", TODAY, invoice.pk, [{"source_line": invoice.lines.get(), "quantity": 1}], "refund", self.owner)
        row = services.performance(TODAY, TODAY)[0]
        self.assertEqual((row["done"], row["sales"], row["commission"]), (3, D("300.00"), D("30.00")))

    def test_billing_needs_a_service(self):
        appointment = self.book(service=None, price="100")
        services.set_status(appointment, AppointmentStatus.DONE, self.owner)
        with self.assertRaisesMessage(ValidationError, "اختار الخدمة"):
            services.bill(appointment, self.owner)


class ScreenTests(AppointmentSetup):
    def test_book_from_the_agenda_see_it_remind_and_bill(self):
        self.client.force_login(self.owner)
        page = self.client.post(reverse("appointments:agenda"), {"day": TODAY.isoformat(), "time": "14:30", "duration_minutes": "45", "customer": str(self.customer.pk),
                                                                 "service": str(self.service.pk), "employee": str(self.tech.pk), "kind": "appointment"})
        appointment = Appointment.objects.get()
        self.assertRedirects(page, f"{reverse('appointments:agenda')}?lang=ar&day={TODAY.isoformat()}", fetch_redirect_response=False)
        agenda = self.client.get(reverse("appointments:agenda"), {"day": TODAY.isoformat()})
        self.assertContains(agenda, f'data-appointment-row="{appointment.number}"')
        self.assertContains(agenda, "محجوز")  # Arabic status label, not "Booked"
        detail = self.client.get(reverse("appointments:detail", args=[appointment.pk]))
        link = unquote(detail.context["reminder"])
        self.assertTrue(link.startswith("https://wa.me/201001234567?text="))
        self.assertIn("14:30", link)
        self.client.post(reverse("appointments:detail", args=[appointment.pk]), {"action": "status", "status": "arrived"})
        billed = self.client.post(reverse("appointments:detail", args=[appointment.pk]), {"action": "bill"})
        appointment.refresh_from_db()
        self.assertRedirects(billed, f"{reverse('sales:detail', args=[appointment.invoice.pk])}?lang=ar", fetch_redirect_response=False)
        self.assertContains(self.client.get(reverse("appointments:performance")), 'data-performance-row="E-0001"')

    def test_overlap_shows_on_the_form(self):
        self.book(hour=14)
        self.client.force_login(self.owner)
        page = self.client.post(reverse("appointments:agenda"), {"day": TODAY.isoformat(), "time": "14:15", "customer": str(self.customer.pk), "employee": str(self.tech.pk)})
        self.assertContains(page, "data-appointment-error")
        self.assertEqual(Appointment.objects.count(), 1)

    def test_permissions_and_module_gate(self):
        keeper = person(RoleCode.STOCK_KEEPER, "appt_keeper")
        self.client.force_login(keeper)
        self.assertIn(self.client.post(reverse("appointments:agenda"), {"day": TODAY.isoformat(), "time": "10:00", "customer": str(self.customer.pk)}).status_code, (302, 403))
        self.assertFalse(Appointment.objects.exists())
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(reverse("dashboard_snapshot")), reverse("appointments:agenda"))
        from settings_core.setup_services import set_module_enabled

        set_module_enabled(self.profile, "appointments_visits", False, self.owner)
        self.assertNotEqual(self.client.get(reverse("appointments:agenda")).status_code, 200)


class EntityScopeTests(TestCase):
    """HG-074: an appointment belongs to the entity it was booked in, and its bill lands in that entity's store and books.

    A group with a main shop and a branch. The branch manager sees and acts on the branch's appointments only; the
    main shop's answer 404. Rows with no entity (booked before HG-074) belong to the main entity. The employee's
    diary stays one across the group."""

    @classmethod
    def setUpTestData(cls):
        from entities.models import Entity, EntityMembership
        from entities.services import save_entity
        from hesba_testing.factories import make_cashbox, stock_in
        from inventory.services import recalculate_item_average_cost

        cls.profile = prepared_client(activity="services", sub_activity="maintenance",
                                      modules="customers,items_services,sales_operations,inventory,cashboxes,reports,employees_technicians,appointments_visits")
        cls.owner = person(RoleCode.OWNER, "scope_owner")
        cls.main_store = make_location(location_code="SHOP", is_default=True)  # no entity given: the main entity's (ENT-001)
        cls.main = Entity.objects.get(is_main=True)
        cls.branch = save_entity({"code": "BR2", "name_ar": "فرع المعادي"}, cls.owner)
        cls.branch_store = make_location(location_code="BR-SHOP", name_ar="مخزن الفرع", entity=cls.branch, is_selling_location=True)
        cls.main_cash = make_cashbox(cashbox_code="C-main", name_ar="خزنة المحل", entity=cls.main)
        cls.branch_cash = make_cashbox(cashbox_code="C-branch", name_ar="خزنة الفرع", entity=cls.branch)
        cls.customer = make_customer(customer_code="C-1", name="مدام سعاد", phone="01001234567")
        cls.service = make_item(item_code="SRV-AC", item_name="صيانة تكييف", default_sale_price="300.00", is_stock_tracked=False)
        cls.part = make_item(item_code="PART-1", item_name="فلتر تكييف", default_sale_price="120.00", is_stock_tracked=True)
        stock_in(cls.part, cls.branch_store, 5, "40.00")
        recalculate_item_average_cost(cls.part)
        cls.tech = save_employee({"name": "أحمد الفني", "commission_percent": "10"}, cls.owner)
        cls.manager = person(RoleCode.MANAGER, "scope_branch_manager")
        EntityMembership.objects.create(user=cls.manager, entity=cls.branch, is_default=True)

    def book(self, entity, hour, day=None, **extra):
        from entities.current import working_in

        data = {"customer": self.customer, "employee": self.tech, "service": self.service, "starts_at": at(hour, day=day), "duration_minutes": 60}
        data.update(extra)
        with working_in(entity):
            return services.book(data, self.owner)

    def legacy(self, hour, day=None, **extra):
        """A row booked before HG-074: no entity."""

        values = {"customer": self.customer, "employee": None, "starts_at": at(hour, day=day), "duration_minutes": 30, "created_by": self.owner}
        values.update(extra)
        return Appointment.objects.create(number=f"AP-OLD-{hour}", **values)

    def done_and_posted(self, appointment, biller_entity=None):
        from entities.current import working_in

        services.set_status(appointment, AppointmentStatus.DONE, self.owner)
        with working_in(biller_entity):
            invoice = services.bill(appointment, self.owner)
        post_sales_invoice(invoice.pk, self.owner)
        invoice.refresh_from_db()
        return invoice

    def as_owner(self, entity=None):
        self.client.force_login(self.owner)
        self.client.post(reverse("entities:switch"), {"entity": entity.pk if entity else "", "next": "/dashboard/"})

    def test_booking_stamps_the_entity_and_the_agenda_follows_it(self):
        from entities.current import working_in

        main_one, branch_one, old = self.book(self.main, 9), self.book(self.branch, 11), self.legacy(13)
        self.assertEqual((main_one.entity, branch_one.entity, old.entity), (self.main, self.branch, None))
        for entity, expected in ((self.branch, [branch_one]), (self.main, [main_one, old]), (None, [main_one, branch_one, old])):
            with self.subTest(entity=entity), working_in(entity):
                self.assertEqual(list(services.day_agenda(TODAY)), expected)
        self.client.force_login(self.manager)
        agenda = self.client.get(reverse("appointments:agenda"), {"day": TODAY.isoformat()})
        self.assertContains(agenda, f'data-appointment-row="{branch_one.number}"')
        self.assertNotContains(agenda, f'data-appointment-row="{main_one.number}"')
        self.assertNotContains(agenda, f'data-appointment-row="{old.number}"')
        self.as_owner(self.main)
        agenda = self.client.get(reverse("appointments:agenda"), {"day": TODAY.isoformat()})
        self.assertContains(agenda, f'data-appointment-row="{old.number}"')  # a pre-HG-074 row belongs to the main entity
        self.assertNotContains(agenda, f'data-appointment-row="{branch_one.number}"')
        self.as_owner()
        self.assertContains(self.client.get(reverse("appointments:agenda"), {"day": TODAY.isoformat()}), f'data-appointment-row="{branch_one.number}"')
        # Booked from the branch's screen: the branch's.
        self.client.force_login(self.manager)
        self.client.post(reverse("appointments:agenda"), {"day": TODAY.isoformat(), "time": "15:00", "customer": str(self.customer.pk), "service": str(self.service.pk), "kind": "appointment"})
        self.assertEqual(Appointment.objects.get(starts_at=at(15)).entity, self.branch)

    def test_another_entitys_appointment_is_out_of_reach_and_unchanged(self):
        main_one = self.book(self.main, 9)
        url = reverse("appointments:detail", args=[main_one.pk])
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(url).status_code, 404)
        for data in ({"action": "status", "status": "arrived"}, {"action": "bill"}, {"action": "bill_insured"},
                     {"day": TODAY.isoformat(), "time": "16:00", "customer": str(self.customer.pk), "duration_minutes": "30"}):
            with self.subTest(data=data):
                self.assertEqual(self.client.post(url, data).status_code, 404)
        main_one.refresh_from_db()
        self.assertEqual((main_one.status, main_one.invoice, main_one.starts_at), (AppointmentStatus.BOOKED, None, at(9)))
        self.assertEqual(SalesInvoice.objects.count(), 0)
        self.as_owner(self.branch)
        self.assertEqual(self.client.get(url).status_code, 404)  # the owner working in the branch, too
        self.as_owner()
        self.assertEqual(self.client.get(url).status_code, 200)  # the whole group sees it
        self.as_owner(self.main)
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_the_bill_lands_in_the_appointments_entity_not_the_billers(self):
        from cashboxes.models import CashboxMovement
        from entities.current import working_in
        from entities.scope import customer_entries_q
        from hesba_testing.factories import recalculate_invoice_totals
        from inventory.models import StockMovement
        from ledger.models import JournalLine
        from ledger.projector import Projector

        branch_one = self.book(self.branch, 10, service=self.part)
        services.set_status(branch_one, AppointmentStatus.DONE, self.owner)
        with working_in(self.main):  # billed by someone working in the main shop
            invoice = services.bill(branch_one, self.owner)
        self.assertEqual((invoice.selling_location, invoice.selling_location.entity, invoice.total_amount), (self.branch_store, self.branch, D("120.00")))
        invoice.cashbox = self.branch_cash
        recalculate_invoice_totals(invoice, paid_now="20.00")
        post_sales_invoice(invoice.pk, self.owner)
        movement = StockMovement.objects.get(sales_invoice=invoice)
        self.assertEqual((movement.location, movement.location.entity, movement.quantity), (self.branch_store, self.branch, D("1.000")))
        cash = CashboxMovement.objects.get(sales_invoice=invoice)
        self.assertEqual((cash.cashbox, cash.cashbox.entity, cash.amount), (self.branch_cash, self.branch, D("20.00")))
        due = CustomerLedgerEntry.objects.get(sales_invoice=invoice)
        self.assertEqual(due.due_increase, D("100.00"))
        with working_in(self.branch):
            self.assertEqual(list(CustomerLedgerEntry.objects.filter(customer_entries_q())), [due])  # in the branch's customer ledger
        with working_in(self.main):
            self.assertFalse(CustomerLedgerEntry.objects.filter(customer_entries_q()).exists())  # not the main shop's
        Projector().rebuild()
        lines = JournalLine.objects.filter(entry__source_type="sales_invoice", entry__source_id=invoice.pk)
        self.assertTrue(lines.exists())
        self.assertEqual(set(lines.values_list("entity", flat=True)), {self.branch.pk})  # the branch's books, not the main shop's
        # A pre-HG-074 row billed from the branch takes a main-entity store: it belongs to the main entity (HG-034).
        old = self.legacy(13)
        services.set_status(old, AppointmentStatus.DONE, self.owner)
        with working_in(self.branch):
            with self.assertRaisesMessage(ValidationError, "اختار الخدمة"):
                services.bill(old, self.owner)
            old.service = self.service
            old.save(update_fields=["service"])
            self.assertEqual(services.bill(old, self.owner).selling_location, self.main_store)

    def test_billing_refuses_when_the_appointments_entity_has_no_selling_store(self):
        from entities.current import working_in
        from entities.services import save_entity

        third = save_entity({"code": "BR3", "name_ar": "فرع بدون مخزن", "name_en": "Storeless branch"}, self.owner)
        appointment = self.book(third, 15)
        services.set_status(appointment, AppointmentStatus.DONE, self.owner)
        with working_in(self.main):  # the biller's own entity has a store; the appointment's has none
            with self.assertRaisesMessage(ValidationError, "مفيش مخزن بيع نشط في كيان الميعاد (فرع بدون مخزن)"):
                services.bill(appointment, self.owner)
            with self.assertRaisesMessage(ValidationError, "The appointment's entity (Storeless branch) has no active selling location"):
                services.bill(appointment, self.owner, lang="en")
        appointment.refresh_from_db()
        self.assertIsNone(appointment.invoice)
        self.assertFalse(SalesInvoice.objects.exists())

    def test_performance_per_entity_adds_up_to_the_group(self):
        from entities.current import working_in

        self.done_and_posted(self.book(self.main, 9))
        self.done_and_posted(self.book(self.branch, 11), biller_entity=self.main)
        old = self.legacy(13, employee=self.tech, service=self.service, price=D("300.00"))
        self.done_and_posted(old, biller_entity=self.branch)
        figures = {}
        for key, entity in (("main", self.main), ("branch", self.branch), ("group", None)):
            with working_in(entity):
                row = services.performance(TODAY, TODAY)[0]
                figures[key] = (row["done"], row["sales"], row["commission"])
        self.assertEqual(figures, {"main": (2, D("600.00"), D("60.00")), "branch": (1, D("300.00"), D("30.00")), "group": (3, D("900.00"), D("90.00"))})
        self.client.force_login(self.manager)
        page = self.client.get(reverse("appointments:performance"))
        self.assertContains(page, 'data-performance-row="E-0001"')
        self.assertEqual(page.context["totals"]["sales"], D("300.00"))

    def test_the_same_employee_still_clashes_across_entities_and_numbers_run_group_wide(self):
        from entities.current import working_in

        first = self.book(self.main, 10)
        with working_in(self.branch), self.assertRaisesMessage(ValidationError, "عنده ميعاد تاني من 10:00 لـ 11:00"):
            services.book({"customer": self.customer, "employee": self.tech, "service": self.service, "starts_at": at(10, 30), "duration_minutes": 30}, self.owner)
        second = self.book(self.branch, 12)
        self.assertEqual((first.number, second.number), (f"AP-{TODAY:%Y%m%d}-001", f"AP-{TODAY:%Y%m%d}-002"))

    def test_messaging_gathers_each_entitys_appointments_as_its_own(self):
        from messaging import services as messaging
        from messaging.models import MessageKind, OutboxMessage

        tomorrow = TODAY + timedelta(days=1)
        main_one, branch_one, old = self.book(self.main, 11, day=tomorrow), self.book(self.branch, 12, day=tomorrow), self.legacy(13, day=tomorrow)
        messaging.build(TODAY)
        rows = {row.key: row.entity for row in OutboxMessage.objects.filter(kind=MessageKind.APPOINTMENT)}
        self.assertEqual(rows, {f"appointment:{main_one.pk}": self.main, f"appointment:{old.pk}": self.main,  # the main entity's keys as before
                                f"appointment:{branch_one.pk}@{self.branch.pk}": self.branch})

    def test_the_drag_calendar_keeps_to_the_entity(self):
        """S10's move and week endpoints read through the same scope (HG-074)."""

        main_one, branch_one = self.book(self.main, 9), self.book(self.branch, 11)
        self.client.force_login(self.manager)
        week = self.client.get(reverse("appointments:week_json"), {"start": TODAY.isoformat()}).json()
        ids = [row["id"] for group in week["staff"] for row in group["appointments"]]
        self.assertEqual(ids, [branch_one.pk])
        response = self.client.post(reverse("appointments:move", args=[main_one.pk]), {"start": at(14).isoformat()})
        self.assertEqual(response.status_code, 404)
        main_one.refresh_from_db()
        self.assertEqual(main_one.starts_at, at(9))

    def test_the_migration_copies_the_entity_from_the_bill(self):
        from importlib import import_module

        from django.apps import apps

        billed = self.book(self.branch, 10)
        self.done_and_posted(billed, biller_entity=self.main)
        unbilled = self.book(self.branch, 12)
        Appointment.objects.update(entity=None)  # as rows stood before HG-074
        import_module("appointments.migrations.0002_appointment_entity").copy_entity_from_bill(apps, None)
        billed.refresh_from_db()
        unbilled.refresh_from_db()
        self.assertEqual((billed.entity, unbilled.entity), (self.branch, None))  # billed: where its bill landed; unbilled: the main entity's


class MoveTests(AppointmentSetup):
    """S10: the JSON backend of drag-to-reschedule."""

    def setUp(self):
        super().setUp()
        self.other_tech = save_employee({"name": "منى الفنية"}, self.owner)
        self.client.force_login(self.owner)

    def move(self, appointment, start, staff=None, client=None, **extra):
        data = {"start": start.isoformat() if hasattr(start, "isoformat") else start, **extra}
        if staff is not None:
            data["staff"] = str(staff.pk)
        return (client or self.client).post(reverse("appointments:move", args=[appointment.pk]), data)

    def test_move_keeps_the_duration_and_answers_with_the_new_times(self):
        appointment = self.book(hour=10, minutes=45)
        response = self.move(appointment, at(14))
        body = response.json()
        appointment.refresh_from_db()
        self.assertEqual((response.status_code, body["ok"], body["id"], body["staff"]), (200, True, appointment.pk, self.tech.pk))
        self.assertEqual((appointment.starts_at, appointment.duration_minutes, appointment.employee), (at(14), 45, self.tech))
        self.assertEqual((body["start"], body["end"]), (at(14).isoformat(), at(14, 45).isoformat()))
        self.assertNotIn("price", body)

    def test_overlap_is_409_in_arabic_and_english_and_changes_nothing(self):
        self.book(hour=10, minutes=60)
        appointment = self.book(hour=13)
        for lang, text in (("ar", "عنده ميعاد تاني من 10:00 لـ 11:00"), ("en", "already has an appointment from 10:00 to 11:00")):
            with self.subTest(lang=lang):
                response = self.move(appointment, at(10, 30), lang=lang)
                self.assertEqual(response.status_code, 409)
                self.assertFalse(response.json()["ok"])
                self.assertIn(text, response.json()["error"])
        appointment.refresh_from_db()
        self.assertEqual((appointment.starts_at, appointment.employee), (at(13), self.tech))
        self.assertFalse(AuditLog.objects.filter(action="move_appointment").exists())

    def test_it_never_clashes_with_itself(self):
        appointment = self.book(hour=10, minutes=60)
        self.assertEqual(self.move(appointment, at(10, 30)).status_code, 200)

    def test_changing_the_employee(self):
        appointment = self.book(hour=10)
        response = self.move(appointment, at(10), staff=self.other_tech)
        appointment.refresh_from_db()
        self.assertEqual((response.status_code, response.json()["staff"], appointment.employee), (200, self.other_tech.pk, self.other_tech))

    def test_changing_the_employee_into_an_overlap_is_refused(self):
        services.book({"customer": self.customer, "employee": self.other_tech, "service": self.service, "starts_at": at(10), "duration_minutes": 60}, self.owner)
        appointment = self.book(hour=10)
        response = self.move(appointment, at(10, 15), staff=self.other_tech)
        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 409)
        self.assertIn("منى الفنية", response.json()["error"])
        self.assertEqual((appointment.starts_at, appointment.employee), (at(10), self.tech))

    def test_a_past_time_is_accepted_exactly_as_booking_accepts_it(self):
        yesterday = at(10, day=TODAY - timedelta(days=1))
        self.assertEqual(self.book(hour=10, starts_at=yesterday).starts_at, yesterday)  # booking has no past-time rule
        appointment = self.book(hour=12)
        self.assertEqual(self.move(appointment, yesterday.replace(hour=12)).status_code, 200)

    def test_done_cancelled_and_no_show_cannot_move(self):
        for status in (AppointmentStatus.DONE, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW):
            with self.subTest(status=status):
                appointment = self.book(hour=8, employee=None)
                Appointment.objects.filter(pk=appointment.pk).update(status=status)
                response = self.move(appointment, at(16), lang="en")
                self.assertEqual(response.status_code, 400)
                self.assertIn("done or cancelled", response.json()["error"])
                appointment.refresh_from_db()
                self.assertEqual(appointment.starts_at, at(8))
                appointment.delete()

    def test_bad_input_is_400_and_bilingual(self):
        appointment = self.book(hour=10)
        for start, lang, text in (("", "ar", "اكتب ميعاد جديد صحيح"), ("not-a-date", "en", "Enter a valid new date and time"),
                                  ("2026-13-45T10:00", "en", "Enter a valid new date and time")):
            with self.subTest(start=start):
                response = self.move(appointment, start, lang=lang)
                self.assertEqual(response.status_code, 400)
                self.assertIn(text, response.json()["error"])
        url = reverse("appointments:move", args=[appointment.pk])
        response = self.client.post(url, {"start": at(11).isoformat(), "staff": "9999", "lang": "en"})
        self.assertEqual((response.status_code, response.json()["error"]), (400, "That employee does not exist or is not active."))
        self.tech.active = False
        self.tech.save()
        self.assertEqual(self.client.post(url, {"start": at(11).isoformat(), "staff": str(self.tech.pk)}).status_code, 400)
        appointment.refresh_from_db()
        self.assertEqual(appointment.starts_at, at(10))
        self.assertEqual(self.client.post(reverse("appointments:move", args=[99999]), {"start": at(11).isoformat()}).status_code, 404)

    def test_only_post_is_allowed(self):
        appointment = self.book()
        self.assertEqual(self.client.get(reverse("appointments:move", args=[appointment.pk])).status_code, 405)

    def test_without_the_permission_it_is_403_and_nothing_moves(self):
        appointment = self.book(hour=10)
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "move_keeper"))
        response = self.move(appointment, at(15))
        self.assertEqual(response.status_code, 403)
        appointment.refresh_from_db()
        self.assertEqual(appointment.starts_at, at(10))

    def test_csrf_is_enforced(self):
        from django.test import Client

        appointment = self.book(hour=10)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.owner)
        self.assertEqual(self.move(appointment, at(15), client=strict).status_code, 403)
        appointment.refresh_from_db()
        self.assertEqual(appointment.starts_at, at(10))
        token = strict.get(reverse("appointments:agenda")).cookies["csrftoken"].value
        response = strict.post(reverse("appointments:move", args=[appointment.pk]), {"start": at(15).isoformat()}, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)

    def test_an_audit_entry_records_before_and_after(self):
        appointment = self.book(hour=10)
        self.move(appointment, at(15), staff=self.other_tech)
        entry = AuditLog.objects.get(action="move_appointment")
        self.assertEqual((entry.actor, entry.module, entry.object_id), (self.owner, "appointments", str(appointment.pk)))
        self.assertEqual((parse_datetime(entry.before_data["starts_at"]), entry.before_data["employee"]), (at(10), str(self.tech.pk)))
        self.assertEqual((parse_datetime(entry.after_data["starts_at"]), entry.after_data["employee"]), (at(15), str(self.other_tech.pk)))

    def test_moving_touches_no_money(self):
        appointment = self.book(hour=10)
        self.move(appointment, at(15))
        appointment.refresh_from_db()
        self.assertEqual((appointment.price, appointment.invoice_id), (D("300.00"), None))
        self.assertFalse(SalesInvoice.objects.exists())

    def test_a_viewer_without_book_gets_a_json_403_and_nothing_moves(self):
        from permissions.services import user_has_permission

        from .views import BOOK, VIEW

        appointment = self.book(hour=10)
        accountant = person(RoleCode.ACCOUNTANT, "move_accountant")
        self.assertTrue(user_has_permission(accountant, VIEW) and not user_has_permission(accountant, BOOK))
        self.client.force_login(accountant)
        for lang, text in (("ar", "تعديل المواعيد لصاحب الصلاحية بس"), ("en", "Only users with the permission can change appointments")):
            with self.subTest(lang=lang):
                response = self.move(appointment, at(15), lang=lang)
                self.assertEqual(response.status_code, 403)
                self.assertEqual(response["Content-Type"], "application/json")
                self.assertFalse(response.json()["ok"])
                self.assertIn(text, response.json()["error"])
        appointment.refresh_from_db()
        self.assertEqual(appointment.starts_at, at(10))

    def test_an_arrived_appointment_that_is_already_billed_cannot_move(self):
        appointment = self.book(hour=10)
        services.set_status(appointment, AppointmentStatus.ARRIVED, self.owner)
        services.bill(appointment, self.owner)
        Appointment.objects.filter(pk=appointment.pk).update(status=AppointmentStatus.ARRIVED)  # in progress, yet invoiced
        response = self.move(appointment, at(15), lang="en")
        self.assertEqual(response.status_code, 400)
        self.assertIn("done or cancelled", response.json()["error"])
        appointment.refresh_from_db()
        self.assertEqual(appointment.starts_at, at(10))

    def test_a_naive_start_is_read_as_cairo_time(self):
        appointment = self.book(hour=10)
        response = self.move(appointment, f"{TODAY.isoformat()}T15:30:00")
        appointment.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(appointment.starts_at.tzinfo), "UTC")
        self.assertEqual(appointment.starts_at, at(15, 30))
        self.assertEqual(timezone.localtime(appointment.starts_at).strftime("%H:%M %Z"), "15:30 EET")
        self.assertEqual(response.json()["start"], at(15, 30).isoformat())


class WeekJsonTests(AppointmentSetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.monday = TODAY - timedelta(days=TODAY.weekday())

    def get(self, **params):
        return self.client.get(reverse("appointments:week_json"), {"start": self.monday.isoformat(), **params})

    def test_groups_the_week_per_employee_without_money(self):
        other = save_employee({"name": "منى الفنية"}, self.owner)
        inside = self.book(hour=10, starts_at=at(10, day=self.monday + timedelta(days=2)))
        self.book(hour=9, employee=other, starts_at=at(9, day=self.monday))
        self.book(hour=9, employee=None, starts_at=at(9, day=self.monday + timedelta(days=6)))
        self.book(hour=9, starts_at=at(9, day=self.monday + timedelta(days=7)))  # next week
        self.book(hour=9, starts_at=at(9, day=self.monday - timedelta(days=1)))  # last week
        body = self.get().json()
        self.assertEqual((body["start"], body["end"]), (self.monday.isoformat(), (self.monday + timedelta(days=6)).isoformat()))
        by_staff = {group["staff"]: group for group in body["staff"]}
        self.assertEqual(set(by_staff), {self.tech.pk, other.pk, None})
        row = by_staff[self.tech.pk]["appointments"]
        self.assertEqual(len(row), 1)
        day = self.monday + timedelta(days=2)
        self.assertEqual(row[0], {"id": inside.pk, "staff": self.tech.pk, "start": at(10, day=day).isoformat(), "end": at(11, day=day).isoformat(),
                                  "customer": "مدام سعاد", "service": "صيانة تكييف", "status": "booked", "status_label": "محجوز"})
        self.assertEqual(len(by_staff[None]["appointments"]), 1)
        text = self.get().content.decode()
        for forbidden in ("price", "invoice", "300"):
            self.assertNotIn(forbidden, text)

    def test_english_labels_and_a_cancelled_one_still_shows_its_status(self):
        appointment = self.book(hour=10, starts_at=at(10, day=self.monday))
        services.set_status(appointment, AppointmentStatus.CANCELLED, self.owner)
        row = self.get(lang="en").json()["staff"][0]["appointments"][0]
        self.assertEqual((row["status"], row["status_label"]), ("cancelled", "Cancelled"))

    def test_bad_start_is_400_and_a_missing_one_means_this_week(self):
        self.assertEqual(self.get(start="2026-13-99").status_code, 400)
        self.assertEqual(self.client.get(reverse("appointments:week_json")).json()["start"], TODAY.isoformat())

    def test_it_needs_the_view_permission(self):
        self.client.force_login(person(RoleCode.STOCK_KEEPER, "week_keeper"))
        self.assertEqual(self.get().status_code, 403)

    def test_the_query_count_does_not_grow_with_the_rows(self):
        day = self.monday + timedelta(days=1)
        for hour in (8, 9):
            self.book(hour=hour, minutes=30, starts_at=at(hour, day=day))
        with CaptureQueriesContext(connection) as queries:
            self.assertEqual(len(self.get().json()["staff"][0]["appointments"]), 2)
        for hour in (10, 11, 12, 13):
            self.book(hour=hour, minutes=30, employee=None, starts_at=at(hour, day=day))
        other = save_employee({"name": "منى الفنية"}, self.owner)
        self.book(hour=14, employee=other, starts_at=at(14, day=day))
        with self.assertNumQueries(len(queries)):
            self.assertEqual(sum(len(group["appointments"]) for group in self.get().json()["staff"]), 7)
