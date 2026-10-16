"""FB2-QUICKPARTY: "+ new supplier / customer / warehouse" from inside an invoice.

The invoice's own <select> gets one more choice (the "+ new …" box of
quick_option.js). Typing a name there makes the record on the spot, with its
automatic code, and picks it, so the invoice is never left half typed. Only
someone allowed to add that kind of record is offered it, and the endpoint
checks the same permission. Anything beyond the name (phone, address, opening
balance) is added later from the record's own screen.
"""

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from config.numbering import next_in_series
from permissions.services import user_has_permission
from settings_core.setup_services import enabled_modules
from settings_core.vocabulary import term

from .models import Customer, Location, Supplier

#: kind -> (model, code field, prefix, permission)
KINDS = {
    "supplier": (Supplier, "supplier_code", "S-", "master_data.manage_parties"),
    "customer": (Customer, "customer_code", "C-", "master_data.manage_parties"),
    "location": (Location, "location_code", "WH-", "master_data.manage_locations"),
}
#: invoice field -> kind
FIELDS = {"supplier": "supplier", "customer": "customer", "receiving_location": "location", "selling_location": "location"}

WORDS = {
    "ar": {"supplier": "+ مورد جديد…", "location": "+ مخزن جديد…", "name": "اكتب الاسم.", "denied": "مش مسموحلك تضيف ده.", "retry": "محصلش حفظ؛ جرّب تاني."},
    "en": {"supplier": "+ New supplier…", "location": "+ New warehouse…", "name": "Enter the name.", "denied": "You are not allowed to add this.", "retry": "Could not save; try again."},
}


def offer_quick_add(form, user, lang):
    """Give the invoice's supplier / customer / warehouse list its "+ new …" choice, when this user may add one."""

    lang = "en" if lang == "en" else "ar"
    for field_name, kind in FIELDS.items():
        field = form.fields.get(field_name)
        if field is None or not user_has_permission(user, KINDS[kind][3]):
            continue
        if kind == "customer" and "customers" not in enabled_modules():
            continue  # a shop without a customer list sells to walk-ins (AUDIT-3)
        # The customer is the patient, the student, the pet owner…: the activity's own word.
        label = f"+ {term('new_customer', lang)}…" if kind == "customer" else WORDS[lang][kind]
        field.widget.attrs.update({"data-quick-add": label, "data-quick-add-url": reverse("master_data:quick_record", kwargs={"kind": kind})})
    return form


@require_POST
def quick_record(request, kind):
    from entities.current import current_entity

    lang = "en" if (request.POST.get("lang") or request.GET.get("lang")) == "en" else "ar"
    words = WORDS[lang]
    if kind not in KINDS:
        return JsonResponse({"ok": False, "error": words["denied"]}, status=404)
    model, code_field, prefix, permission = KINDS[kind]
    if not user_has_permission(request.user, permission):
        return JsonResponse({"ok": False, "error": words["denied"]}, status=403)
    name = (request.POST.get("name") or "").strip()[:255]
    if not name:
        return JsonResponse({"ok": False, "error": words["name"]}, status=400)

    if kind == "location":
        entity = current_entity()
        same = Location.objects.filter(Q(name_ar__iexact=name) | Q(name_en__iexact=name))
        if entity is not None:
            same = same.filter(entity=entity)
        fields = {"name_ar": name, "entity": entity}
    else:
        same = model.objects.filter(name__iexact=name)
        fields = {"name": name}
    existing = same.first()
    if existing is not None:  # the same name twice is almost always a double click or a forgotten record
        if not existing.active:
            existing.active = True
            existing.save(update_fields=["active"])
        return JsonResponse({"ok": True, "id": existing.pk, "label": str(existing)})

    for _ in range(5):  # two people adding at the same moment can draw the same next code
        try:
            with transaction.atomic():
                record = model.objects.create(**{code_field: next_in_series(model, code_field, prefix)}, **fields)
            break
        except IntegrityError:
            continue
    else:
        return JsonResponse({"ok": False, "error": words["retry"]}, status=409)
    return JsonResponse({"ok": True, "id": record.pk, "label": str(record)})
