from django.apps import AppConfig


class PharmacyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pharmacy"
    verbose_name = "Pharmacy: drug data and prescriptions"
