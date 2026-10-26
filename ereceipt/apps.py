from django.apps import AppConfig


class EreceiptConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ereceipt"
    verbose_name = "E-receipts (B2C)"

    def ready(self):
        from . import services

        services.connect()
