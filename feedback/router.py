"""DEMO-FEEDBACK: the feedback app lives in its own database, everything else never does."""

APP = "feedback"
ALIAS = "feedback"


class FeedbackRouter:
    def db_for_read(self, model, **hints):
        return ALIAS if model._meta.app_label == APP else None

    def db_for_write(self, model, **hints):
        return ALIAS if model._meta.app_label == APP else None

    def allow_relation(self, obj1, obj2, **hints):
        if APP in (obj1._meta.app_label, obj2._meta.app_label):
            return obj1._meta.app_label == obj2._meta.app_label
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label == APP:
            return db == ALIAS
        if db == ALIAS:
            return False
        return None
