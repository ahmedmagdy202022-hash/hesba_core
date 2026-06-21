from django.conf import settings
from django.db import models


class ActivityType(models.TextChoices):
    STORE = "store", "Store"
    SERVICES = "services", "Services"
    TELECOM = "telecom", "Telecom"
    CONTRACTING = "contracting", "Contracting"
    MIXED = "mixed", "Mixed"


class ClosingFrequency(models.TextChoices):
    MONTHLY = "monthly", "Monthly"
    QUARTERLY = "quarterly", "Quarterly"
    SEMI_ANNUAL = "semi_annual", "Semi-annual"
    ANNUAL = "annual", "Annual"


class ClientProfile(models.Model):
    """Client identity/settings stored inside the client's own database.

    Hesba Core stays separate from client operational ownership: every client
    database must be owned by the client, while this model only describes the
    active installation settings for that database.
    """

    client_code = models.CharField(max_length=50, unique=True)
    legal_name = models.CharField(max_length=255)
    display_name = models.CharField(max_length=255)
    activity_type = models.CharField(
        max_length=30,
        choices=ActivityType.choices,
        default=ActivityType.STORE,
    )
    edition_code = models.CharField(max_length=100, default="HESBA_LITE_STORE_SERVICES")
    default_currency = models.CharField(max_length=10, default="EGP")
    default_language = models.CharField(max_length=10, default="ar")
    timezone = models.CharField(max_length=64, default="Africa/Cairo")
    fiscal_year_start_month = models.PositiveSmallIntegerField(default=1)
    default_closing_frequency = models.CharField(
        max_length=20,
        choices=ClosingFrequency.choices,
        default=ClosingFrequency.QUARTERLY,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Client Profile"
        verbose_name_plural = "Client Profiles"

    def __str__(self):
        return f"{self.client_code} - {self.display_name}"


class SystemSetting(models.Model):
    class DataType(models.TextChoices):
        STRING = "string", "String"
        INTEGER = "integer", "Integer"
        DECIMAL = "decimal", "Decimal"
        BOOLEAN = "boolean", "Boolean"
        JSON = "json", "JSON"

    key = models.CharField(max_length=120, unique=True)
    value = models.TextField(blank=True)
    data_type = models.CharField(max_length=20, choices=DataType.choices, default=DataType.STRING)
    description = models.TextField(blank=True)
    is_sensitive = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["key"]
        verbose_name = "System Setting"
        verbose_name_plural = "System Settings"

    def __str__(self):
        return self.key


class FeatureFlag(models.Model):
    code = models.CharField(max_length=120, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    enabled = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code"]
        verbose_name = "Feature Flag"
        verbose_name_plural = "Feature Flags"

    def __str__(self):
        return self.code


class SupportAccessGrant(models.Model):
    """Temporary limited support access grant.

    Operational data access must stay disabled unless the client grants a
    temporary reasoned access window. Every real use must also be audited.
    """

    granted_to_identifier = models.CharField(max_length=150)
    reason = models.TextField()
    starts_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="support_access_grants_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Support Access Grant"
        verbose_name_plural = "Support Access Grants"

    def __str__(self):
        return f"{self.granted_to_identifier} until {self.expires_at}"
