"""{% app_shell as shell %}: the navigation the signed-in shell draws."""

from django import template

from settings_core.setup_services import usable_modules

from reports.navigation import app_shell as build_app_shell


register = template.Library()


@register.simple_tag(takes_context=True)
def app_shell(context):
    request = context["request"]
    lang = "en" if context.get("lang") == "en" else "ar"
    return build_app_shell(request.user, lang, set(usable_modules()), request.path)
