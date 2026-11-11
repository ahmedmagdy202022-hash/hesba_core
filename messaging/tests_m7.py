"""M7 (HG-090): the vaccination reminder — a vet clinic's message that an animal's next dose is due.

What these pin: the kind is off until the owner switches it on; one message per dose, at the lead time and not before;
never again once it was sent or skipped; withdrawn before anyone sends it when the next dose is recorded first (or the
date passes); nothing for a business that is not a vet; the text carries the owner, the animal, the vaccine, the date
and the clinic and nothing clinical or linked; the template accepts the two new words and no clinical one; and the
migration only changes the choices of the two ``kind`` columns.
"""

import importlib
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import migrations
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from entities.models import Entity
from hesba_testing.factories import make_customer
from medical import vaccines, vet
from medical.models import Vaccination, Vaccine
from medical.tests_vaccines import Clinic
from permissions.models import RoleCode
from reports.tests_dashboard import prepared_client
from settings_core.templatetags.hesba_format import day_text

from . import services
from .models import MessageKind, MessageTemplate, OutboxMessage, OutboxStatus

SECRET = "ZZNOTE خمول بعد الجرعة"


class Reminders(Clinic):
    def setUp(self):
        super().setUp()
        self.mona.whatsapp = ""
        self.mona.save()

    def turn_on(self, **extra):
        return services.save_template(MessageKind.VACCINE, {"text": services.template(MessageKind.VACCINE).text, "active": "1", "every_days": "7", **extra}, self.owner)

    def due_in(self, days, animal=None, vaccine=None, **extra):
        """A dose due ``days`` days from today."""

        return vaccines.record(animal or self.rocky, {"vaccine": vaccine or self.rabies, "given_on": self.today - timedelta(days=10),
                                                      "next_due": (self.today + timedelta(days=days)).isoformat(), **extra}, self.owner)

    def messages(self):
        return OutboxMessage.objects.filter(kind=MessageKind.VACCINE)


class GatheringTests(Reminders):
    def test_it_is_off_until_the_owner_switches_it_on(self):
        self.due_in(2)
        self.assertFalse(services.template(MessageKind.VACCINE).active)
        self.assertEqual(services.build(), 0)
        self.turn_on()
        self.assertEqual(services.build(), 1)
        services.save_template(MessageKind.VACCINE, {"text": services.template(MessageKind.VACCINE).text, "every_days": "7"}, self.owner)  # unticked: off
        services.build()
        self.assertEqual(self.messages().count(), 0)  # nothing of this kind is left to send

    def test_one_message_per_dose_at_the_lead_time_and_not_before(self):
        self.turn_on()
        far = self.due_in(services.VACCINE_AHEAD_DAYS + 1)
        self.assertEqual(services.build(), 0)  # four days ahead: too early
        self.assertEqual(services.build(self.today + timedelta(days=1)), 1)  # three days ahead now
        message = self.messages().get()
        self.assertEqual(message.key, f"vaccine:{far.pk}")
        self.assertEqual(services.build(self.today + timedelta(days=1)), 0)  # the same day again adds nothing
        self.assertEqual(self.messages().count(), 1)

    def test_the_default_lead_is_three_days_and_the_due_day_itself_is_included(self):
        self.assertEqual(services.VACCINE_AHEAD_DAYS, 3)
        self.turn_on()
        self.due_in(0)
        self.due_in(3, animal=self.mishmish, vaccine=self.fvrcp)
        self.assertEqual(services.build(), 2)

    def test_the_text_names_the_owner_the_animal_the_vaccine_the_date_and_the_clinic_and_nothing_else(self):
        self.turn_on()
        dose = self.due_in(2, note=SECRET, batch="LOT-77", given_by=self.karim)
        self.assertEqual(services.build(), 1)
        message = self.messages().get()
        self.assertEqual((message.phone, message.status, message.name), ("201001234567", OutboxStatus.PENDING, "منى علي"))
        for expected in ("منى علي", "روكي", "سعار", day_text(self.today + timedelta(days=2), "ar"), "Demo Store"):
            self.assertIn(expected, message.text)
        for forbidden in ("ZZNOTE", "خمول", "LOT-77", "كريم", "http", "/medical/"):
            self.assertNotIn(forbidden, message.text)
        self.assertEqual(message.link, f"/appointments/?day={dose.next_due.isoformat()}")  # reception's agenda, never a clinical page, never in the text
        self.assertEqual(message.entity, Entity.objects.get(is_main=True))

    def test_a_cashier_opening_the_outbox_is_never_led_to_a_clinical_page(self):
        from django.urls import reverse

        self.turn_on()
        self.due_in(2)
        self.client.force_login(self.desk)
        page = self.client.get(reverse("messaging:outbox") + "?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-kind="vaccine"')
        self.assertNotContains(page, "/medical/animals/")
        self.assertNotContains(page, "ZZNOTE")

    def test_the_date_is_written_in_words_not_iso(self):
        self.turn_on()
        due = self.today + timedelta(days=2)
        self.due_in(2)
        services.build()
        text = self.messages().get().text
        self.assertIn(day_text(due, "ar"), text)
        self.assertNotIn(due.isoformat(), text)

    def test_a_dose_with_a_whatsapp_number_goes_to_it_and_one_without_any_number_is_skipped(self):
        self.turn_on()
        self.mona.whatsapp = "01112223334"
        self.mona.save()
        self.due_in(2)
        no_number = make_customer(customer_code="V-9", name="بلا رقم")
        lonely = vet.save_animal(no_number, {"name": "لوني", "species": "dog"}, self.owner)
        self.due_in(2, animal=lonely)
        self.assertEqual(services.build(), 1)
        self.assertEqual(self.messages().get().phone, "201112223334")

    def test_an_animal_that_no_longer_comes_and_an_owner_switched_off_get_nothing(self):
        self.turn_on()
        self.due_in(2)
        vet.save_animal(self.mona, {"name": "روكي", "species": "dog", "active": False}, self.owner, self.rocky)
        self.assertEqual(services.build(), 0)
        vet.save_animal(self.mona, {"name": "روكي", "species": "dog", "active": True}, self.owner, self.rocky)
        self.mona.active = False
        self.mona.save()
        self.assertEqual(services.build(), 0)

    def test_a_one_off_and_an_overdue_dose_get_nothing(self):
        self.turn_on()
        vaccines.record(self.rocky, {"vaccine": self.puppy, "given_on": self.today - timedelta(days=3)}, self.owner)  # no next dose
        self.due_in(-2)  # already overdue: on the due list, not messaged
        self.assertEqual(services.build(), 0)

    def test_the_english_business_gets_the_english_wording(self):
        self.profile.default_language = "en"
        self.profile.save()
        self.turn_on()
        self.due_in(2)
        services.build()
        text = self.messages().get().text
        self.assertIn("Hello", text)
        self.assertIn(day_text(self.today + timedelta(days=2), "en"), text)
        self.assertIn("Demo Store", text)
        self.assertIn("Rabies", text)  # the vaccine's English name
        self.assertNotIn("سعار", text)

    def test_it_is_gathered_by_the_hourly_run_without_whatsapp_keys(self):
        self.turn_on()
        self.due_in(2)
        result = services.run_scheduled()
        self.assertEqual((result["status"], result["added"]), ("gathered", 1))


class OnceTests(Reminders):
    def test_each_dose_is_reminded_once_even_after_the_repeat_days(self):
        self.turn_on(every_days="1")
        self.due_in(3)
        self.assertEqual(services.build(), 1)
        services.mark(self.messages().get(), OutboxStatus.SENT, self.owner)
        self.assertEqual(services.build(self.today + timedelta(days=1)), 0)  # every_days is 1, yet this dose was sent
        self.assertEqual(services.build(self.today + timedelta(days=2)), 0)
        self.assertEqual(self.messages().count(), 1)

    def test_a_skipped_one_is_not_queued_again_the_next_day(self):
        self.turn_on(every_days="1")
        self.due_in(3)
        services.build()
        services.mark(self.messages().get(), OutboxStatus.SKIPPED, self.owner)
        self.assertEqual(services.build(self.today + timedelta(days=1)), 0)

    def test_the_next_dose_of_the_same_vaccine_is_a_new_reminder_when_it_comes_due(self):
        self.turn_on(every_days="1")
        first = self.due_in(2)
        services.build()
        services.mark(self.messages().get(), OutboxStatus.SENT, self.owner)
        second = vaccines.record(self.rocky, {"vaccine": self.rabies, "given_on": self.today, "next_due": (self.today + timedelta(days=20)).isoformat()}, self.owner)
        self.assertEqual(services.build(self.today + timedelta(days=18)), 1)  # three days before the new due date
        self.assertEqual(set(self.messages().values_list("key", flat=True)), {f"vaccine:{first.pk}", f"vaccine:{second.pk}"})

    def test_two_animals_of_one_owner_are_two_messages(self):
        self.turn_on()
        self.due_in(2)
        self.due_in(2, animal=self.mishmish, vaccine=self.fvrcp)
        self.assertEqual(services.build(), 2)


class WithdrawnTests(Reminders):
    def test_a_dose_recorded_before_the_message_is_sent_withdraws_it(self):
        self.turn_on()
        self.due_in(2)
        services.build()
        self.assertEqual(self.messages().filter(status=OutboxStatus.PENDING).count(), 1)
        vaccines.record(self.rocky, {"vaccine": self.rabies, "given_on": self.today}, self.owner)  # the owner came in early
        services.build()
        self.assertEqual(self.messages().count(), 0)  # gone before anyone could send it

    def test_removing_that_dose_brings_the_reminder_back(self):
        self.turn_on()
        self.due_in(2)
        services.build()
        later = vaccines.record(self.rocky, {"vaccine": self.rabies, "given_on": self.today}, self.owner)
        services.build()
        self.assertEqual(self.messages().count(), 0)
        vaccines.remove(later, self.owner)
        self.assertEqual(services.build(), 1)

    def test_another_vaccine_does_not_withdraw_it(self):
        self.turn_on()
        self.due_in(2)
        services.build()
        vaccines.record(self.rocky, {"vaccine": self.deworm, "given_on": self.today}, self.owner)
        services.build()
        self.assertEqual(self.messages().filter(status=OutboxStatus.PENDING).count(), 1)

    def test_a_date_that_passed_unsent_is_withdrawn_not_sent_late(self):
        self.turn_on()
        self.due_in(2)
        services.build()
        services.build(self.today + timedelta(days=3))  # the day after it was due
        self.assertEqual(self.messages().count(), 0)

    def test_a_waiting_message_moves_to_today_with_the_same_text(self):
        self.turn_on()
        dose = self.due_in(3)
        services.build()
        services.build(self.today + timedelta(days=1))
        message = self.messages().get()
        self.assertEqual((message.day, message.key), (self.today + timedelta(days=1), f"vaccine:{dose.pk}"))
        self.assertIn(day_text(dose.next_due, "ar"), message.text)

    def test_a_message_already_sent_stays_in_the_record_when_the_next_dose_is_given(self):
        self.turn_on()
        self.due_in(2)
        services.build()
        services.mark(self.messages().get(), OutboxStatus.SENT, self.owner)
        vaccines.record(self.rocky, {"vaccine": self.rabies, "given_on": self.today}, self.owner)
        services.build()
        self.assertEqual(self.messages().filter(status=OutboxStatus.SENT).count(), 1)  # history is never deleted


class WhereTests(TestCase):
    def test_a_clinic_that_is_not_a_vet_gets_nothing_and_never_sees_the_template(self):
        from staff.services import save_employee  # noqa: F401

        prepared_client("medical", "clinic", "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians")
        owner = _owner()
        owner_customer = make_customer(customer_code="V-C", name="منى", phone="01001234567")
        from medical.models import Animal

        animal = Animal.objects.create(owner=owner_customer, name="روكي", species="dog", created_by=owner)
        vaccine = Vaccine.objects.create(name_ar="سعار", interval_days=365, created_by=owner)
        today = timezone.localdate()
        Vaccination.objects.create(animal=animal, vaccine=vaccine, given_on=today - timedelta(days=360), next_due=today + timedelta(days=2), created_by=owner)
        services.save_template(MessageKind.VACCINE, {"text": services.DEFAULT_TEXTS[MessageKind.VACCINE], "active": "1", "every_days": "7"}, owner)
        self.assertEqual(services.build(), 0)
        self.assertNotIn(MessageKind.VACCINE, [row.kind for row in services.templates()])

    def test_a_shop_gets_nothing_and_never_sees_the_template(self):
        prepared_client("commercial", "retail")
        owner = _owner()
        self.assertEqual(services.build(), 0)
        self.assertNotIn(MessageKind.VACCINE, [row.kind for row in services.templates()])
        self.assertTrue(owner)

    def test_the_vet_sees_the_template_to_edit(self):
        prepared_client("medical", "vet")
        kinds = [row.kind for row in services.templates()]
        self.assertIn(MessageKind.VACCINE, kinds)
        self.assertNotIn(MessageKind.CAMPAIGN, kinds)

    def test_a_vet_entity_of_a_group_gets_its_own_messages_and_names_the_clinic(self):
        from entities.current import working_in
        from entities.services import save_entity

        prepared_client("medical", "clinic", "customers,items_services,sales_operations,cashboxes,reports,appointments_visits,employees_technicians")
        owner = _owner()
        branch = save_entity({"code": "VET2", "name_ar": "عيادة المعادي البيطرية", "activity_slug": "medical", "sub_activity_slug": "vet"}, owner)
        owner_customer = make_customer(customer_code="V-C", name="منى", phone="01001234567")
        from medical.models import Animal

        animal = Animal.objects.create(owner=owner_customer, name="روكي", species="dog", created_by=owner)
        today = timezone.localdate()
        with working_in(branch):
            vaccine = vaccines.save_vaccine({"name_ar": "سعار", "species": "dog", "interval_days": "365"}, owner)
            dose = vaccines.record(animal, {"vaccine": vaccine, "given_on": today - timedelta(days=360), "next_due": (today + timedelta(days=2)).isoformat()}, owner)
        services.save_template(MessageKind.VACCINE, {"text": services.DEFAULT_TEXTS[MessageKind.VACCINE], "active": "1", "every_days": "7"}, owner)
        self.assertEqual(services.build(), 1)
        message = OutboxMessage.objects.get(kind=MessageKind.VACCINE)
        self.assertEqual((message.entity, message.key), (branch, f"vaccine:{dose.pk}@{branch.pk}"))
        self.assertIn("عيادة المعادي البيطرية", message.text)
        self.assertIn(MessageKind.VACCINE, [row.kind for row in services.templates()])  # a vet entity makes the kind editable


class TemplateTests(Reminders):
    def test_the_two_new_words_are_accepted_and_filled(self):
        row = self.turn_on(text="أهلاً {name}، {animal} محتاج {vaccine} يوم {date} — {company}")
        self.assertIn("{animal}", row.text)
        self.due_in(2)
        services.build()
        text = self.messages().get().text
        self.assertTrue(text.startswith("أهلاً منى علي، روكي محتاج سعار يوم "))
        self.assertTrue(text.endswith("— Demo Store"))
        self.assertNotIn("{", text)

    def test_a_clinical_word_is_refused(self):
        for word in ("{diagnosis}", "{note}", "{batch}", "{doctor}", "{treatment}"):
            with self.subTest(word=word), self.assertRaises(ValidationError):
                services.save_template(MessageKind.VACCINE, {"text": f"أهلاً {word}", "active": "1", "every_days": "7"}, self.owner)

    def test_the_defaults_use_only_allowed_words_and_the_kind_carries_no_link(self):
        import re

        for text in (services.DEFAULT_TEXTS[MessageKind.VACCINE], services.DEFAULT_TEXTS_EN[MessageKind.VACCINE]):
            self.assertEqual([w for w in re.findall(r"\{[^}]*\}", text) if w not in services.PLACEHOLDERS], [])
            self.assertNotIn("{link}", text)
        self.assertNotIn(MessageKind.VACCINE, services.LINK_KINDS)
        self.assertEqual(set(services.KIND_LABELS["ar"]), set(MessageKind.values))
        self.assertEqual(set(services.KIND_LABELS["en"]), set(MessageKind.values))

    def test_the_existing_kinds_keep_their_wording_and_switches(self):
        for kind in (MessageKind.DUE, MessageKind.INSTALMENT, MessageKind.APPOINTMENT, MessageKind.REFILL, MessageKind.WORKORDER):
            self.assertTrue(services.template(kind).active, kind)
        self.assertFalse(services.template(MessageKind.FOLLOWUP).active)
        self.assertFalse(services.template(MessageKind.VACCINE).active)

    def test_the_outbox_page_edits_it(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("messaging:outbox") + "?lang=ar")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'data-template="vaccine"')
        self.assertContains(page, "{animal}")
        self.assertEqual(MessageTemplate.objects.filter(kind=MessageKind.VACCINE).count(), 0)
        posted = self.client.post(reverse("messaging:outbox") + "?lang=ar", {"lang": "ar", "action": "template", "kind": "vaccine", "text": "أهلاً {name}، {animal} {vaccine} {date}",
                                                          "every_days": "7", "active": "1"})
        self.assertIn(posted.status_code, (200, 302))
        self.assertTrue(MessageTemplate.objects.get(kind=MessageKind.VACCINE).active)


def _owner():
    from hesba_testing.factories import make_seeded_role, make_user, make_user_profile

    user = make_user(username="m7_owner")
    make_user_profile(user=user, role=make_seeded_role(RoleCode.OWNER))
    return user


class MigrationTests(SimpleTestCase):
    """0006 changes the choices of the two ``kind`` columns and nothing else (the same shape as 0005, HG-087)."""

    def setUp(self):
        self.module = importlib.import_module("messaging.migrations.0006_vaccine_kind")
        self.previous = importlib.import_module("messaging.migrations.0005_followup_kind")

    def test_it_follows_0005_and_has_only_two_alter_fields_on_kind(self):
        migration = self.module.Migration
        self.assertEqual(migration.dependencies, [("messaging", "0005_followup_kind")])
        self.assertEqual(len(migration.operations), 2)
        for operation in migration.operations:
            self.assertIsInstance(operation, migrations.AlterField)
            self.assertEqual(operation.name, "kind")
        self.assertEqual([op.model_name for op in migration.operations], ["messagetemplate", "outboxmessage"])
        self.assertFalse(any(isinstance(op, (migrations.CreateModel, migrations.AddField, migrations.RemoveField, migrations.RunPython, migrations.RunSQL))
                             for op in migration.operations))

    def test_the_only_change_is_one_more_choice_with_the_same_length_and_options(self):
        for before, after in zip(self.previous.Migration.operations, self.module.Migration.operations):
            old, new = before.field, after.field
            self.assertEqual((old.max_length, new.max_length), (12, 12))
            self.assertEqual((old.unique, old.db_index), (new.unique, new.db_index))
            self.assertEqual(len(new.choices), len(old.choices) + 1)
            self.assertEqual([c for c in new.choices if c[0] != "vaccine"], list(old.choices))
            self.assertIn(("vaccine", "Vaccination due"), new.choices)
            self.assertEqual(list(new.choices), [(value, label) for value, label in MessageKind.choices])

    def test_the_kind_fits_the_column(self):
        self.assertLessEqual(len(MessageKind.VACCINE.value), 12)


class MakeMigrationsCleanTests(TestCase):
    databases = {"default", "feedback"}  # makemigrations reads the history of every database

    def test_the_models_and_the_migrations_agree(self):
        call_command("makemigrations", "--check", "--dry-run", verbosity=0)
