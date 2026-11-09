from django.apps import AppConfig


class AiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "ai"
    verbose_name = "AI provider layer"

    def ready(self):
        from . import checks  # noqa: F401 - registers the AI settings check
