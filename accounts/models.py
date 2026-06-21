from django.conf import settings
from django.db import models

from permissions.models import Role


class UserProfile(models.Model):
    """Application profile for Django users inside one client database."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="hesba_profile",
    )
    role = models.ForeignKey(
        Role,
        on_delete=models.PROTECT,
        related_name="user_profiles",
        null=True,
        blank=True,
    )
    display_name = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    active = models.BooleanField(default=True)
    is_support_user = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__username"]
        verbose_name = "User Profile"
        verbose_name_plural = "User Profiles"

    def __str__(self):
        return self.display_name or self.user.get_username()
