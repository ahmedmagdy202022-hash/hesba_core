"""FB2-QUICKPARTY: "+ new supplier / customer / warehouse" from inside an invoice.

The invoice's own <select> gets one more choice (the "+ new …" box of
quick_option.js). Typing a name there makes the record on the spot, with its
automatic code, and picks it, so the invoice is never left half typed. Only
someone allowed to add that kind of record is offered it, and the endpoint
checks the same permission. A supplier or a customer opens its full form in a
box instead (FB-26), so the phone, address and opening balance go in at once;
the name-only endpoint stays for a warehouse and for older pages.
"""

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST

from config.numbering import next_in_series
from permissions.services import user_has_permission
from settings_core.module_gate import closed_module
from settings_core.vocabulary import term

from .models import Customer, Location, Supplier

#: kind -> (model, code field, prefix, permission)
KINDS = {
    "supplier": (Supplier, "supplier_code", "S-", "master_data.manage_parties"),
    "customer": (Customer, "customer_code", "C-", "master_data.manage_parties"),
    "location": (Location, "location_code", "WH-", "master_data.manage_locations"),
}
#: invoice field -> (kind, what the warehouse must allow there)
FIELDS = {"supplier": ("supplier", ""), "customer": ("customer", ""),
          "receiving_location": ("location", "receiving"), "selling_location": ("location", "selling")}
#: ?use= -> the warehouse flag the invoice's list filters on
USES = {"receiving": "is_receiving_location", "selling": "is_selling_location"}
#: kind -> URL name; each sits under its own master-data section, so a switched-off module closes it too (module_gate)
URL_NAMES = {"supplier": "master_data:supplier_quick", "customer": "master_data:customer_quick", "location": "master_data:location_quick"}
#: FB-26: kinds whose "+ new …" opens their full form in a box, so the phone, address and the rest go in at once
FORM_URL_NAMES = {"supplier": "master_data:supplier_create", "customer": "master_data:customer_create"}

WORDS = {
    "ar": {"supplier": "+ مورد جديد…", "location": "+ مخزن جديد…", "name": "اكتب الاسم.", "denied": "مش مسموحلك تضيف ده.", "retry": "محصلش حفظ؛ جرّب تاني."},
    "en": {"supplier": "+ New supplier…", "location": "+ New warehouse…", "name": "Enter the name.", "denied": "You are not allowed to add this.", "retry": "Could not save; try again."},
}


def offer_quick_add(form, user, lang):
    """Give the invoice's supplier / customer / warehouse list its "+ new …" choice, when this user may add one."""

    lang = "en" if lang == "en" else "ar"
    for field_name, (kind, use) in FIELDS.items():
        field = form.fields.get(field_name)
        if field is None or not user_has_permission(user, KINDS[kind][3]):
            continue
        url = reverse(URL_NAMES[kind])
        if closed_module(url):
            continue  # e.g. a shop without a customer list sells to walk-ins (AUDIT-3)
        if use:
            url = f"{url}?use={use}"
        # The customer is the patient, the student, the pet owner…: the activity's own word.
        label = f"+ {term('new_customer', lang)}…" if kind == "customer" else WORDS[lang][kind]
        field.widget.attrs.update({"data-quick-add": label, "data-quick-add-url": url})
        if kind in FORM_URL_NAMES:
            field.widget.attrs["data-quick-add-form"] = reverse(FORM_URL_NAMES[kind])
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
        changed = [] if existing.active else ["active"]
        existing.active = True
        # A warehouse picked from a sales (or purchase) invoice must be one that invoice accepts.
        flag = USES.get(request.GET.get("use", "")) if kind == "location" else None
        if flag and not getattr(existing, flag):
            setattr(existing, flag, True)
            changed.append(flag)
        if changed:
            existing.save(update_fields=changed)
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
