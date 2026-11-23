"""HG-070 (S4): refuse to start with a gateway setting that could record money it should not."""

from django.conf import settings
from django.core.checks import Error, register


@register()
def gateway_settings(app_configs, **kwargs):
    errors = []
    if getattr(settings, "PAYMENT_GATEWAY_MOCK", False) and not settings.DEBUG:
        errors.append(Error(
            "PAYMENT_GATEWAY_MOCK is on while DEBUG is off: the mock gateway lets anyone with a link mark it paid.",
            hint="Switch PAYMENT_GATEWAY_MOCK off on a real server; it is for development only.", id="payments.E001"))
    if getattr(settings, "PAYMOB_LIVE", False):
        from .providers import live_keys

        names = ("PAYMOB_API_KEY", "PAYMOB_HMAC_SECRET", "PAYMOB_INTEGRATION_ID", "PAYMOB_IFRAME_ID")
        missing = [name for name, value in zip(names, live_keys()) if not str(value).strip()]
        if missing:
            errors.append(Error(f"PAYMOB_LIVE is on but {', '.join(missing)} is missing from the server's environment.",
                                hint="Set every Paymob key, or switch PAYMOB_LIVE off.", id="payments.E002"))
    return errors
