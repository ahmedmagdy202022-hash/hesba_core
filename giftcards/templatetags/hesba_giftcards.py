"""S6 (HG-071): the «ادفع بكارت هدية» box on a posted invoice's page."""

from django import template

register = template.Library()


@register.inclusion_tag("giftcards/_invoice_box.html", takes_context=True)
def gift_card_box(context, invoice):
    from permissions.services import user_has_permission
    from settlements.services import applied_to

    from .. import services

    request = context.get("request")
    user = getattr(request, "user", None)
    lang = context.get("lang", "ar")
    shown = bool(invoice) and invoice.status == "posted" and services.enabled() and user_has_permission(user, services.SELL)
    due = (invoice.remaining_due - applied_to(invoice)) if shown else 0
    return {"shown": shown and due > 0, "lang": lang, "invoice": invoice, "due": due, "csrf_token": context.get("csrf_token")}
