from django import template

from settings_core.module_gate import closed_module


register = template.Library()


@register.simple_tag
def printing_enabled():
    """False when the owner switched the PDF printing module off."""

    return closed_module("/print/") is None


@register.simple_tag(takes_context=True)
def share_links(context, kind, obj, party=None):
    """SHARE-001: {url, whatsapp} for a document the customer can open without an account."""

    from printing.company import company_details
    from printing.share import share_url, whatsapp_link
    from settings_core.templatetags.hesba_format import money

    request = context.get("request")
    if request is None or obj is None:
        return None
    lang = context.get("lang", "ar")
    url = share_url(request, kind, obj.pk, lang)
    company = company_details()
    name = getattr(party, "name", "") or ""
    number = getattr(obj, "invoice_number", None) or getattr(obj, "return_number", None) or getattr(obj, "payment_number", None) or ""
    amount = getattr(obj, "total_amount", None) or getattr(obj, "amount", None)
    if lang == "en":
        what = {"sales_invoice": "your invoice", "sales_return": "your return note", "customer_payment": "your receipt", "customer_statement": "your account statement"}[kind]
        text = f"Hello {name}, here is {what} {number} from {company['name']}".strip()
        if amount is not None:
            text += f" ({money(amount)} {company['currency']})"
        text += f": {url}"
    else:
        what = {"sales_invoice": "فاتورتك", "sales_return": "إشعار المرتجع", "customer_payment": "إيصال الدفع", "customer_statement": "كشف حسابك"}[kind]
        text = f"أهلاً {name}، دي {what} {number} من {company['name']}".replace("  ", " ")
        if amount is not None:
            text += f" بإجمالي {money(amount)} {company['currency']}"
        text += f": {url}"
    return {"url": url, "whatsapp": whatsapp_link(getattr(party, "whatsapp", "") or getattr(party, "phone", ""), text)}
