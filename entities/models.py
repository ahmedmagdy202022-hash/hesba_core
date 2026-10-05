"""ENT-001 (HG-031): the units a group runs — a factory, each shop — each with
its own books and costing, all under one installation.

A location and a cashbox each belong to one entity; documents take their
entity from the location or cashbox they use. An installation with a single
entity (every install before ENT-001) behaves exactly as before.
"""

from django.conf import settings
from django.db import models


class EntityKind(models.TextChoices):
    BRANCH = "branch", "Branch"
    COMPANY = "company", "Separate company"


class Entity(models.Model):
    code = models.CharField(max_length=20, unique=True)
    name_ar = models.CharField(max_length=255)
    name_en = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=20, choices=EntityKind.choices, default=EntityKind.BRANCH)
    # Blank = the installation's own activity (settings_core.ClientProfile).
    activity_slug = models.CharField(max_length=40, blank=True)
    sub_activity_slug = models.CharField(max_length=40, blank=True)
    tax_registration_number = models.CharField(max_length=50, blank=True)
    document_prefix = models.CharField(max_length=10, blank=True)
    is_main = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-is_main", "code"]
        verbose_name = "Entity"
        verbose_name_plural = "Entities"
        constraints = [
            models.UniqueConstraint(fields=["is_main"], condition=models.Q(is_main=True), name="one_main_entity"),
        ]

    def __str__(self):
        return f"{self.code} - {self.name_ar}"

    def display(self, lang="ar"):
        return (self.name_en or self.name_ar) if lang == "en" else self.name_ar


class EntityMembership(models.Model):
    """Which entities a user works in, and which one their screens open on.

    Recorded now; enforced by ENT-002 (HG-034). Owners and group managers see
    the whole group whatever is recorded here.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="entity_memberships")
    entity = models.ForeignKey(Entity, on_delete=models.CASCADE, related_name="memberships")
    is_default = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "entity"], name="unique_user_entity")]
