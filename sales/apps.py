from django.apps import AppConfig


class SalesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "sales"
    verbose_name = "Sales"

    def ready(self):
        # R2b (HG-110): a cancelled invoice cancels its split payments. Connected here, before the deposits hook (an app
        # later in INSTALLED_APPS) removes the invoice's settlement rows, which is how the split payments are found.
        from . import tenders

        tenders.connect()
