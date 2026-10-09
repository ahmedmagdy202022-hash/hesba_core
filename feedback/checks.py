"""R2: a showcase says out loud when testers' notes cannot be read or kept.

Run by Django's checks, so the warnings appear in Render's build log
(``check --deploy``) and in every start (``migrate``). A tester's note is
always in the server log too (``views.send``); these say why it may be the
only copy.
"""

from django.conf import settings
from django.core.checks import Warning, register


@register()
def feedback_is_readable_and_kept(app_configs=None, **kwargs):
    if not getattr(settings, "DEMO_MODE", False):
        return []
    found = []
    if not getattr(settings, "FEEDBACK_VIEW_TOKEN", ""):
        found.append(Warning(
            "FEEDBACK_VIEW_TOKEN is not set: the feedback inbox (/demo/feedback/inbox/) stays closed and "
            "testers' notes can only be read in the server log.",
            hint="Add FEEDBACK_VIEW_TOKEN in the service's environment (Render: Environment -> Generate).",
            id="feedback.W001",
        ))
    if not getattr(settings, "FEEDBACK_DATABASE_URL", ""):
        found.append(Warning(
            "FEEDBACK_DATABASE_URL is not set: notes are kept in a local file that is lost on every restart or deploy.",
            hint="Point FEEDBACK_DATABASE_URL at a PostgreSQL database (e.g. a free Supabase project).",
            id="feedback.W002",
        ))
    return found
