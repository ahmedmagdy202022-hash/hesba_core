"""MED-006: the vet's animal file (HG-043, design A).

The owner is an ordinary customer (they book, are billed and pay, and their
statement stays where it is). Each owner can bring several animals; a visit
names the animal it was for, and the animal's card shows its own history.
Nothing here moves money.
"""

from datetime import date

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from .models import Animal, Gender, Species

SPECIES_LABELS = {
    "ar": {"dog": "كلب", "cat": "قطة", "bird": "طائر", "rabbit": "أرنب", "horse": "حصان", "cattle": "ماشية", "sheep_goat": "خراف / ماعز", "other": "حيوان تاني"},
    "en": {"dog": "Dog", "cat": "Cat", "bird": "Bird", "rabbit": "Rabbit", "horse": "Horse", "cattle": "Cattle", "sheep_goat": "Sheep / goat", "other": "Other"},
}
MESSAGES = {
    "ar": {"name": "اكتب اسم الحيوان.", "species": "اختار نوع الحيوان.", "sex": "النوع مش صحيح.", "date": "تاريخ الميلاد مش مظبوط.",
           "future": "تاريخ الميلاد لسه مجاش.", "not_theirs": "الحيوان ده مش بتاع العميل ده."},
    "en": {"name": "Enter the animal's name.", "species": "Choose the species.", "sex": "The sex is not valid.", "date": "The date of birth is not valid.",
           "future": "The date of birth is still to come.", "not_theirs": "This animal does not belong to this customer."},
}
FIELDS = ("name", "species", "breed", "sex", "date_of_birth", "colour", "microchip", "neutered", "allergies", "notes", "active")


def is_vet():
    from entities.current import effective_activity

    return effective_activity() == ("medical", "vet")


def _words(lang):
    return MESSAGES["en" if lang == "en" else "ar"]


def _snapshot(animal):
    return {field: (getattr(animal, field).isoformat() if isinstance(getattr(animal, field), date) else getattr(animal, field)) for field in FIELDS}


@transaction.atomic
def save_animal(owner, data, user, animal=None, lang="ar"):
    words = _words(lang)
    name = (data.get("name") or "").strip()[:80]
    if not name:
        raise ValidationError(words["name"])
    species = data.get("species") or ""
    if species not in Species.values:
        raise ValidationError(words["species"])
    sex = data.get("sex") or ""
    if sex and sex not in Gender.values:
        raise ValidationError(words["sex"])
    born = None
    if data.get("date_of_birth"):
        try:
            born = date.fromisoformat(str(data.get("date_of_birth")))
        except ValueError:
            raise ValidationError(words["date"])
        if born > timezone.localdate():
            raise ValidationError(words["future"])
    if animal is not None and animal.owner_id != owner.pk:
        raise ValidationError(words["not_theirs"])
    creating = animal is None
    before = {} if creating else _snapshot(animal)
    animal = animal or Animal(owner=owner, created_by=user)
    animal.name, animal.species, animal.sex, animal.date_of_birth = name, species, sex, born
    for field, limit in (("breed", 80), ("colour", 60), ("microchip", 40)):
        setattr(animal, field, (data.get(field) or "").strip()[:limit])
    for field in ("allergies", "notes"):
        setattr(animal, field, (data.get(field) or "").strip()[:5000])
    animal.neutered = bool(data.get("neutered"))
    animal.active = bool(data.get("active", True))
    animal.save()
    AuditLog.objects.create(event_type=AuditEventType.CREATE if creating else AuditEventType.UPDATE, actor=user, module="medical",
                            action="save_animal", object_type="medical.Animal", object_id=str(animal.pk), before_data=before,
                            after_data={"owner": owner.pk, **_snapshot(animal)})
    return animal


def age_text(animal, lang="ar", today=None):
    """'3 سنين' / '5 شهور' style, or '' without a date of birth."""

    if not animal.date_of_birth:
        return ""
    today = today or timezone.localdate()
    months = (today.year - animal.date_of_birth.year) * 12 + today.month - animal.date_of_birth.month - (today.day < animal.date_of_birth.day)
    if months >= 24:
        return f"{months // 12} {'years' if lang == 'en' else 'سنين'}"
    if months >= 12:
        return "1 year" if lang == "en" else "سنة"
    return f"{max(months, 0)} {'months' if lang == 'en' else 'شهور'}"
