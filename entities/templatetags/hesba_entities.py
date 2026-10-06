"""{% entity_bar as bar %}: the tabs for switching entity (only when there are several)."""

from django import template

register = template.Library()


@register.simple_tag(takes_context=True)
def entity_bar(context):
    from entities.current import allowed_entities, current_entity
    from entities.services import is_multi_entity

    request = context.get("request")
    if request is None or not getattr(request.user, "is_authenticated", False) or not is_multi_entity():
        return None
    allowed = allowed_entities(request.user)
    lang = "en" if context.get("lang") == "en" else "ar"
    current = current_entity()
    tabs = []
    if len(allowed) == len(allowed_entities(None)):
        tabs.append({"pk": "", "label": "Whole group" if lang == "en" else "المجموعة كلها", "current": current is None})
    for entity in allowed:
        tabs.append({"pk": entity.pk, "label": (entity.name_en if lang == "en" and entity.name_en else entity.name_ar), "current": current is not None and current.pk == entity.pk})
    return {"tabs": tabs, "next": request.get_full_path(), "label": "Working in" if lang == "en" else "شغال في"}
