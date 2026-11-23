from django.apps import AppConfig


class GiftcardsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "giftcards"
    verbose_name = "Gift cards"

    def ready(self):
        from . import services

        services.connect()
