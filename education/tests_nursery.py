"""EDU-005: the nursery's day — check-in, and pick-up only by someone on the child's list."""

from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client

from . import nursery, services
from .models import DayRecord, PickupPerson
from .tests import CENTER, person


class NurserySetup(TestCase):
    sub = "nursery"

    def setUp(self):
        prepared_client("education", self.sub, CENTER)
        self.owner = person(RoleCode.OWNER, "nur_owner")
        self.child = services.save_student({"name": "ليلى أحمد", "payer_name": "أحمد سمير", "payer_phone": "01001234501"}, self.owner)
        self.mother = nursery.save_person(self.child, {"name": "منى حسن", "relation": "الأم", "phone": "01112223334", "id_number": "29001011234567"}, self.owner)


class PickupTests(NurserySetup):
    def test_a_child_is_checked_in_and_handed_only_to_someone_on_the_list(self):
        record = nursery.check_in(self.child, self.owner, brought_by="الأب")
        self.assertEqual((record.day, record.arrived_with, record.left_at), (timezone.localdate(), "الأب", None))
        with self.assertRaisesMessage(ValidationError, "اتسجل حضوره بالفعل"):  # a second tap makes no second morning
            nursery.check_in(self.child, self.owner)
        stranger_child = services.save_student({"name": "عمر", "payer_name": "سامي", "payer_phone": "01009998887"}, self.owner)
        someone_else = nursery.save_person(stranger_child, {"name": "سامي", "phone": "01009998887"}, self.owner)
        with self.assertRaisesMessage(ValidationError, "مش في قايمة"):
            nursery.check_out(self.child, someone_else, self.owner)
        nursery.save_person(self.child, {"name": self.mother.name, "phone": self.mother.phone, "active": False}, self.owner, self.mother)
        self.mother.refresh_from_db()
        with self.assertRaisesMessage(ValidationError, "مش في قايمة"):  # taken off the list: no longer allowed
            nursery.check_out(self.child, self.mother, self.owner)
        grandpa = nursery.save_person(self.child, {"name": "حسن علي", "relation": "الجد", "phone": "01223334445"}, self.owner)
        later = timezone.now() + timedelta(minutes=1)
        out = nursery.check_out(self.child, grandpa, self.owner, at=later)
        self.assertEqual((out.picked_by, out.left_at), (grandpa, later))
        with self.assertRaisesMessage(ValidationError, "مشي بالفعل"):
            nursery.check_out(self.child, grandpa, self.owner)
        self.assertEqual(AuditLog.objects.filter(action__in=("nursery_check_in", "nursery_check_out")).count(), 2)
        link = nursery.left_message(out)
        self.assertTrue(link.startswith("https://wa.me/201001234501?text="))

    def test_leaving_needs_a_morning_and_someone_on_the_list(self):
        with self.assertRaisesMessage(ValidationError, "لسه ماتسجلش"):
            nursery.check_out(self.child, self.mother, self.owner)
        alone = services.save_student({"name": "ياسين", "payer_name": "كريم", "payer_phone": "01005556667"}, self.owner)
        nursery.check_in(alone, self.owner)
        with self.assertRaisesMessage(ValidationError, "مفيش حد مسموح"):
            nursery.check_out(alone, None, self.owner)
        with self.assertRaisesMessage(ValidationError, "رقم تليفونه"):
            nursery.save_person(alone, {"name": "كريم"}, self.owner)

    def test_the_door_is_for_the_office_or_staff_and_the_list_for_the_office(self):
        from staff.services import save_employee
        from hesba_testing.factories import make_seeded_role, make_user, make_user_profile

        cashier = person(RoleCode.CASHIER, "nur_cashier")
        with self.assertRaises(PermissionDenied):
            nursery.check_in(self.child, cashier)
        nanny_user = make_user(username="nur_nanny")
        make_user_profile(user=nanny_user, role=make_seeded_role(RoleCode.CASHIER))
        nanny = save_employee({"name": "دادة سعاد"}, self.owner)
        nanny.user = nanny_user
        nanny.save(update_fields=["user"])
        nanny_user.refresh_from_db()
        self.assertTrue(nursery.check_in(self.child, nanny_user).pk)  # staff take children in and out
        with self.assertRaises(PermissionDenied):
            nursery.save_person(self.child, {"name": "حد", "phone": "0100"}, nanny_user)
        self.assertEqual(PickupPerson.objects.count(), 1)

    def test_the_board_counts_who_is_in_who_left_and_who_is_not_here(self):
        second = services.save_student({"name": "آدم", "payer_name": "رامي", "payer_phone": "01007778889"}, self.owner)
        services.save_student({"name": "زين", "payer_name": "هشام", "payer_phone": "01004445556"}, self.owner)
        nursery.check_in(self.child, self.owner)
        nursery.check_in(second, self.owner)
        mom2 = nursery.save_person(second, {"name": "ريم", "phone": "01116667778"}, self.owner)
        nursery.check_out(second, mom2, self.owner, at=timezone.now() + timedelta(minutes=1))
        rows, counts = nursery.board()
        self.assertEqual(counts, {"all": 3, "in": 1, "left": 1, "away": 1})
        self.assertEqual({row["student"].name: bool(row["record"]) for row in rows}, {"ليلى أحمد": True, "آدم": True, "زين": False})


class ScreenTests(NurserySetup):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)
        self.url = reverse("education:nursery")

    def test_the_door_screen_checks_in_and_hands_over_with_a_message_to_the_parent(self):
        page = self.client.get(self.url)
        self.assertContains(page, "data-nursery-tab")
        self.assertContains(page, 'data-state="away"')
        self.client.post(self.url, {"action": "in", "student": self.child.pk, "brought_by": "الأب"})
        self.assertContains(self.client.get(self.url), 'data-state="in"')
        response = self.client.post(self.url, {"action": "out", "student": self.child.pk, "person": self.mother.pk}, follow=True)
        self.assertContains(response, 'data-state="left"')
        self.assertContains(response, "data-tell-parent")
        self.assertContains(response, "ليلى أحمد مشي مع منى حسن")
        self.assertEqual(DayRecord.objects.get().picked_by, self.mother)

    def test_the_childs_file_lists_who_may_pick_them_up(self):
        url = reverse("education:student", args=[self.child.pk])
        page = self.client.get(url)
        self.assertContains(page, "data-pickup-people")
        self.assertContains(page, "منى حسن")
        self.client.post(url, {"action": "pickup_add", "name": "حسن علي", "relation": "الجد", "phone": "01223334445"})
        self.client.post(url, {"action": "pickup_off", "person": self.mother.pk})
        self.assertEqual(list(nursery.allowed(self.child).values_list("name", flat=True)), ["حسن علي"])


class NotANurseryTests(NurserySetup):
    sub = "tutoring_center"

    def test_a_tutoring_centre_has_no_door_screen(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("education:nursery")).status_code, 404)
        self.assertNotContains(self.client.get(reverse("education:today")), "data-nursery-tab")
        self.assertNotContains(self.client.get(reverse("education:student", args=[self.child.pk])), "data-pickup-people")
