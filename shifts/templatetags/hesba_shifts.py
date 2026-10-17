"""AN4: the "open a shift first" bar on the screens that post a sale."""

from django import template

from cashboxes.models import Cashbox
from entities import scope as entity_scope

from ..gate import shift_missing


register = template.Library()


@register.inclusion_tag("shifts/_gate.html", takes_context=True)
def shift_gate(context):
    request = context.get("request")
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or not shift_missing(user):
        return {"show": False}
    cashboxes = entity_scope.cashboxes(Cashbox.objects).filter(active=True)
    return {
        "show": True, "lang": context.get("lang", "ar"), "next": request.get_full_path(), "csrf_token": context.get("csrf_token"),
        "cashboxes": cashboxes, "default_cashbox": cashboxes.filter(is_default=True).first() or cashboxes.first(),
    }
