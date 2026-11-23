"""M2 (HG-086): the reference drug list searched from the visit form.

A clinic has no inventory, so this is gated like every clinical screen (a medical install, then
``services.can_write``), not by the pharmacy's ``drugs`` capability or ``master_data.manage_items``. The search itself is
the pharmacy picker's (``pharmacy.picker.search_reference``: the same folding, the same 2-letter minimum, at most 20 rows,
one query); only the price fields are left out, because a prescription is not priced.
"""

from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from pharmacy import picker

from . import prescribing
from .views import _guard, _id, _lang, _patients


def _keys(request):
    """The allergy words of the patient (and animal, on a vet visit) the form is for; none for anything else, never an error."""

    from .models import Animal

    patient = _patients().filter(pk=_id(request.GET.get("patient"))).first()
    if patient is None:
        return frozenset()
    animal = None
    if _id(request.GET.get("animal")):
        from .vet import is_vet

        animal = Animal.objects.filter(pk=_id(request.GET.get("animal")), owner=patient).first() if is_vet() else None
    return prescribing.allergy_keys(prescribing.allergies_of(patient, animal))


@never_cache  # one patient's allergy words for one signed-in person: never kept by a browser or a proxy
def drug_search(request):
    """GET ``q`` (and ``patient``, ``animal``): the reference rows for the visit form's sheet, as JSON (at most 20).

    Each row is the pharmacy picker's (``picker.as_json``) without ``public_price``, ``price_date``, ``price_month`` and
    ``stale``, plus ``allergy``: the drug's words found in the patient's recorded allergies (advice). ``list_empty`` says
    when no reference list is loaded at all, so the sheet can say so instead of "no results"."""

    _guard(request, write=True)  # 404 outside a medical install, 403 without a clinical permission (HG-084)
    lang = _lang(request)
    if request.method != "GET":
        return JsonResponse({"ok": False, "error": "GET only"}, status=405)
    from pharmacy.models import DrugReference

    query, rows = picker.search_reference(request.GET.get("q"))
    today = timezone.localdate()
    keys = _keys(request) if rows else frozenset()
    too_short = len(query) < picker.MIN_QUERY
    return JsonResponse({"ok": True, "query": query, "too_short": too_short, "limit": picker.SEARCH_LIMIT, "count": len(rows),
                         "list_empty": not rows and not too_short and not DrugReference.objects.filter(is_active=True).exists(),
                         "results": [prescribing.search_json(row, lang, keys, today) for row in rows]},
                        json_dumps_params={"ensure_ascii": False})
