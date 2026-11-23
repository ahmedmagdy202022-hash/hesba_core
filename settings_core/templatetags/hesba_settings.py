"""SETTINGS-DESIGN: the shared frame of every settings screen (settings_core/settings_shell.py)."""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def settings_shell(context):
    from settings_core import settings_shell as shell

    request = context.get("request")
    lang = context.get("lang") or "ar"
    if request is None or not request.user.is_authenticated:
        return {"groups": [], "identity": None, "words": shell.WORDS["en" if lang == "en" else "ar"]}
    active = shell.active_section(request)
    return {"groups": shell.sections(request.user, lang, active), "identity": shell.identity(lang), "active": active,
            "words": shell.WORDS["en" if lang == "en" else "ar"]}
