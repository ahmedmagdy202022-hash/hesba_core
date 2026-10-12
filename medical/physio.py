"""MED-005: physiotherapy session packages, on the shared package engine (PKG-UNIFY).

A package (say ten sessions at 3000) is sold on one ordinary draft sales
invoice; its revenue counts when that invoice is posted, like any service.
Sessions are used one by one once the invoice is posted, never below zero.
A session entered by mistake is cancelled (kept, and audited), which gives it
back. A package whose invoice was cancelled or returned can no longer be used.

Since PKG-UNIFY these are the same packages a salon sells
(``agreements.packages``): one table, one set of rules, a number and an
optional expiry date for each. This module keeps the clinic's own wording and
its own rule for who records a session: the therapist (a user linked to an
employee) or anyone with the medical records permission, not only the counter.
"""

from django.utils import timezone

from agreements import packages as engine
from agreements.models import Package, PackageUse
from permissions.services import user_has_permission

SELL = engine.MANAGE

#: The shared engine's states in the clinic's words (``used_up`` is "finished" here).
STATUS = {"draft": "draft", "active": "active", "used_up": "finished", "expired": "expired", "void": "void"}


def is_physio():
    from entities.current import effective_activity

    return effective_activity() == ("medical", "physio")


def _may_record(user):
    from .services import can_write

    return user_has_permission(user, SELL) or can_write(user)


def used(package):
    return engine.used(package)


def refunded(invoice):
    """A posted sales return against the package's invoice gave the money back: the package is over."""

    return invoice.returns.filter(status="posted").exists()


def state(package):
    """{"used", "left", "status"}: status is draft (invoice not posted), active, finished, expired or void (cancelled or returned)."""

    count = engine.used(package)
    return {"used": count, "left": max(package.sessions - count, 0), "status": STATUS[engine.state(package, timezone.localdate())]}


def packages_of(patient):
    return Package.objects.filter(customer=patient).select_related("service", "invoice")


def sell(patient, data, user, lang="ar"):
    """Sell a package: one draft invoice for it. ``data``: service, sessions, price (default sessions × the service's price), notes, expires_on."""

    return engine.sell_package({**data, "customer": patient}, user, lang)


def sell_insured(patient, data, user, lang="ar"):
    """MED-009: sell a package billed with the patient's insurance: the insurer's invoice and the patient's share.

    The package rides on the patient's invoice (the insurer's when it pays it all), so it opens once that is
    posted, like any package. Returns the package; its ``InsuredBill`` is the one on that invoice."""

    from . import insurance

    insurance.live_cover(patient, lang=lang)  # say why before anything is made

    def make_invoice(customer, rows, notes, user_, location):
        row = rows[0]
        bill = insurance.insured_drafts(customer, [{"service": row["item"], "price": row["unit_sale_price"], "label": row["description"]}],
                                        notes, "package", user_, lang)
        return bill.patient_invoice or bill.insurer_invoice

    return engine.sell_package({**data, "customer": patient}, user, lang, make_invoice=make_invoice)


def use(package, user, *, used_on=None, therapist=None, visit=None, notes="", lang="ar"):
    """Use one session. Refused while the invoice is a draft, once it is cancelled or returned, or when nothing is left.

    ``visit`` is accepted for callers of the earlier API; the session is recorded against the package only."""

    return engine.use_session(package, user, used_on=used_on, employee=therapist, notes=notes, lang=lang, authorized=_may_record(user))


def cancel_use(session, user, lang="ar"):
    return engine.cancel_use(session, user, lang, authorized=_may_record(user))


__all__ = ["Package", "PackageUse", "SELL", "cancel_use", "is_physio", "packages_of", "refunded", "sell", "sell_insured", "state", "use", "used"]
