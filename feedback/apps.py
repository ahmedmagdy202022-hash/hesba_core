from django.apps import AppConfig


class FeedbackConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "feedback"
    verbose_name = "Tester feedback"

    def ready(self):
        from . import checks  # noqa: F401  (registers the R2 warnings)
