from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payments"
    verbose_name = "Online payment links"

    def ready(self):
        from . import checks  # noqa: F401 - registers the gateway settings check
