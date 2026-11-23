"""UX Wave 2: the toast after a save names the record and offers the next step as a button.

``say_saved(request, text)`` queues the message with the ``hs-saved`` tag; the operations and
master-data base templates draw a message with that tag as a teal toast, followed by the
``saved_actions`` the landing view puts in its context ([{"url", "label"}], built by the view,
never from what the user typed). The text is escaped like any message.
"""

from django.contrib import messages

TAG = "hs-saved"


def say_saved(request, text):
    messages.success(request, text, extra_tags=TAG)


def amount_text(value, lang="ar"):
    """«1,234.50 ج.م» / «1,234.50 EGP»: an amount with the screen's currency unit."""

    from settings_core.currency import currency_label
    from settings_core.templatetags.hesba_format import currency_code, money

    unit = currency_label(currency_code(), lang)
    return f"{money(value)} {unit}".strip()


def saved_pk(request):
    """The ``?saved=<pk>`` a save redirected with, or None."""

    raw = (request.GET.get("saved") or "").strip()
    return int(raw) if raw.isdigit() else None


def payment_actions(request, lang, kind):
    """After a collection ("customer") or a supplier payment ("supplier") is saved: its receipt and the next one."""

    from django.urls import reverse

    from entities import scope as entity_scope
    from printing.templatetags.hesba_print import printing_enabled

    pk = saved_pk(request)
    if pk is None:
        return []
    if kind == "customer":
        from sales.models import CustomerPayment as model

        scope, print_url, new_url = entity_scope.CUSTOMER_PAYMENT, "printing:customer_payment", "sales:payment_create"
        again = "Another collection" if lang == "en" else "تحصيل تاني"
    else:
        from purchases.models import SupplierPayment as model

        scope, print_url, new_url = entity_scope.SUPPLIER_PAYMENT, "printing:supplier_payment", "purchases:payment_create"
        again = "Another payment" if lang == "en" else "دفعة تانية"
    payment = entity_scope.scope(model.objects.all(), scope).filter(pk=pk).first()
    if payment is None:
        return []
    actions = []
    if printing_enabled():
        actions.append({"url": f"{reverse(print_url, args=[payment.pk])}?lang={lang}", "label": "Print the receipt" if lang == "en" else "اطبع الإيصال", "blank": True})
    actions.append({"url": f"{reverse(new_url)}?lang={lang}", "label": again})
    return actions
