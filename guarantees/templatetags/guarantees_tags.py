"""Contracting C4 (HG-123): the guarantees card on a project's page, so ``projects/views.py`` stays untouched.

Managers (``cashboxes.manage_cashboxes`` or ``sales.create_sales_invoice``) see each letter's amount and open it; a site
engineer (``projects.view_projects``) sees the kind, bank, expiry and alert only, with no amount and no link. Anyone
else sees nothing."""

from django import template

from guarantees import services

register = template.Library()


@register.inclusion_tag("guarantees/_project_card.html", takes_context=True)
def project_guarantees(context, project):
    request = context.get("request")
    user = getattr(request, "user", None)
    lang = "en" if context.get("lang") == "en" else "ar"
    if user is None or project is None or not services.can_see_card(user):
        return {"show": False}
    manager = services.can_manage(user)
    on, days = services.today(), services.alert_days()
    rows = [{"g": g, "kind": services.label("kinds", g.kind, lang), "status": services.label("statuses", g.status, lang),
             "alert": services.alert(g, on, days), "alert_label": services.label("alerts", services.alert(g, on, days), lang)}
            for g in services.project_guarantees(project)]
    if not rows and not manager:
        return {"show": False}
    from guarantees.views import WORDS

    return {"show": True, "rows": rows, "manager": manager, "lang": lang, "words": WORDS[lang], "project": project}
