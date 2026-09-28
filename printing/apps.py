from django.apps import AppConfig


class PrintingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "printing"
    verbose_name = "Printing"

    def ready(self):
        from .snapshots import connect

        connect()  # PRINT-003: company details frozen on each posted document
