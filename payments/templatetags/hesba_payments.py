"""HG-070 (S4): the «ابعت لينك دفع» box on the invoice and work-order pages, and the customer's pay button."""

from django import template
from django.core.exceptions import ValidationError
from django.urls import reverse

register = template.Library()


@register.inclusion_tag("payments/_invoice_box.html", takes_context=True)
def payment_link_box(context, invoice, next_url=""):
    from permissions.services import user_has_permission
    from printing.share import whatsapp_link
    from sales.pos import WALK_IN_CODE

    from .. import services
    from ..models import PaymentLink

    lang = context.get("lang", "ar")
    request = context.get("request")
    user = getattr(request, "user", None)
    shown = bool(invoice) and invoice.status == "posted" and services.enabled()
    box = {"shown": shown, "lang": lang, "invoice": invoice, "next": next_url, "csrf_token": context.get("csrf_token")}
    if not shown:
        return box
    live = services.live_link(invoice)
    due = services.outstanding(invoice)
    problem = ""
    if invoice.customer is None or invoice.customer.customer_code == WALK_IN_CODE:
        problem = services.MESSAGES[lang]["walk_in"]
    elif services.connected() is None:
        problem = services.MESSAGES[lang]["not_connected"]
    else:
        try:
            services.gateway_box(services.entity_of(invoice), lang)
        except ValidationError as error:
            problem = error.messages[0]
    url = services.link_url(live, lang) if live else None
    words = ("ادفع فاتورة {number} بمبلغ {amount} من هنا: {url}" if lang == "ar" else "Pay invoice {number} ({amount}) here: {url}")
    box.update({
        "can_make": user_has_permission(user, services.RECEIVE), "due": due, "live": live, "url": url, "problem": problem,
        "whatsapp": whatsapp_link(invoice.customer.whatsapp or invoice.customer.phone, words.format(number=invoice.invoice_number, amount=live.amount, url=url)) if url else "",
        "history": [(link, services.status_label(link, lang)) for link in PaymentLink.objects.filter(invoice=invoice).exclude(status="new")[:5]],
    })
    return box


@register.simple_tag
def public_pay_url(invoice, lang="ar"):
    """The live, payable link's page for the customer's own work-order page (a relative address), or ''."""

    from .. import services

    if not invoice or invoice.status != "posted" or not services.enabled():
        return ""
    live = services.live_link(invoice)
    if live is None or not services.is_payable(live):
        return ""
    return f"{reverse('pay_page', args=[services.sign_token(live)])}?lang={lang}"
