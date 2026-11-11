"""M1 (HG-089): visit templates per specialty.

A template is a starting text for the visit form (``models.VisitTemplate``): the complaint, examination, diagnosis and
instructions a doctor usually writes for a common problem, and optionally some medicine lines. It never holds a patient, an
animal, vitals, a date or an appointment, and a visit never points at it: applying one is done in the browser and only puts
ordinary text and ordinary lines on the form (``static/hesba/js/visit_templates.js``); the visit is then saved by
``services.save_visit`` exactly as before, the medicine lines through ``prescribing.resolve``.

* Access follows the visit's own rule (HG-084): every screen and the JSON endpoint need a clinical permission
  (``services.can_write``); an employee link alone gives nothing. A doctor uses their own templates and the shared ones; a
  doctor changes only their own; a holder of ``medical.write_records`` also writes the shared ones and may fix another
  doctor's. Another doctor's personal template is never offered on a visit.
* The built-in starting templates (``visit_template_seeds``) are written once on first use. The keys already written are
  kept in one ``SystemSetting`` row, so a template a person edited or deleted is never overwritten or brought back.
* «احفظ ده كقالب» takes the four texts and the medicine lines from the form, never anything else, and blanks the
  patient's name, file number and phone and the animal's name wherever they were typed into those texts.

No price, stock, invoice, cashbox or ledger row is read or written here.
"""

import json
import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission

from . import prescribing, services, visit_template_seeds
from .models import VisitTemplate

SEED_SETTING = "medical.visit_templates.seeded"
TEXT_FIELDS = ("complaint", "examination", "diagnosis", "treatment")
TEXT_LIMIT = 5000
NAME_LIMIT = 120
#: Templates per person (and shared ones together): keeps the sheet's list, which is sent in one answer, small.
MAX_PER_SCOPE = 200
#: The most templates one answer carries.
MAX_ANSWER = 400
LANGS = ("ar", "en")

SPECIALTIES = {
    "general": {"ar": "باطنة وعام", "en": "General and internal"},
    "paediatrics": {"ar": "أطفال", "en": "Paediatrics"},
    "obgyn": {"ar": "نساء وتوليد", "en": "Obstetrics and gynaecology"},
    "ent": {"ar": "أنف وأذن وحنجرة", "en": "Ear, nose and throat"},
    "eye": {"ar": "عيون", "en": "Eye"},
    "skin": {"ar": "جلدية", "en": "Skin"},
    "ortho": {"ar": "عظام", "en": "Orthopaedics"},
    "dental": {"ar": "أسنان", "en": "Dental"},
    "vet": {"ar": "بيطري", "en": "Veterinary"},
}
HUMAN = tuple(code for code in SPECIALTIES if code != "vet")

WORDS = {
    "ar": {
        "title": "قوالب الكشف", "intro": "نصوص جاهزة للشكاوى المتكررة بتملّا الكشف في ثانية. بتملّا الخانات الفاضية بس، وعمرها ما بتمسح اللي كتبته.",
        "new": "قالب جديد", "edit": "تعديل القالب", "back": "قوالب الكشف", "save": "احفظ", "cancel": "إلغاء", "saved": "اتحفظ القالب.",
        "scope_mine": "قوالبي", "scope_shared": "المشتركة", "scope_others": "قوالب الدكاترة",
        "search": "دوّر بالاسم", "specialty": "التخصص", "all_specialties": "كل التخصصات", "text_lang": "لغة النص", "all_langs": "كل اللغات",
        "lang_ar": "عربي", "lang_en": "إنجليزي", "filter": "فلترة", "find": "بحث",
        "name": "اسم القالب", "name_hint": "مثال: برد وإنفلونزا", "owner": "ليه", "status": "الحالة", "on": "شغال", "off": "مقفول",
        "shared": "مشترك", "mine": "بتاعي", "starter": "جاهز من حسبة", "someone": "دكتور تاني", "lines_n": "{n} دوا",
        "scope": "مين يستخدمه", "scope_mine_opt": "أنا بس", "scope_shared_opt": "كل الدكاترة (مشترك)",
        "active": "شغّال (بيظهر وقت الكشف)", "texts": "النصوص", "texts_hint": "نفس خانات الكشف. اسيب الخانة فاضية لو القالب مش هيملاها.",
        "instructions": "تعليمات للمريض", "meds_hint": "الأدوية اختيارية. بتنزل على الروشتة زي أي دوا تكتبه بإيدك، والدكتور هو اللي يقرر.",
        "no_patient": "القالب مابيتخزنش فيه اسم مريض ولا قياسات ولا تواريخ.",
        "up": "طلّعه فوق", "down": "نزّله تحت", "turn_off": "اقفله", "turn_on": "شغّله", "copy": "انسخه لقوالبي", "delete": "امسح",
        "delete_q": "تمسح «{name}»؟", "delete_yes": "أيوه، امسح", "copied": "اتنسخ القالب لقوالبك.", "deleted": "اتمسح القالب.",
        "turned_off": "اتقفل القالب.", "turned_on": "اتشغّل القالب.", "moved": "اتغيّر الترتيب.",
        "none": "مفيش قوالب هنا لسه.", "none_filtered": "مفيش قوالب بالبحث ده.", "clear": "امسح البحث",
        "not_yours": "القالب ده بتاع دكتور تاني؛ يعدّله هو أو صاحب صلاحية تسجيل الكشوفات.",
        "shared_note": "القالب ده مشترك؛ بيعدّله صاحب صلاحية تسجيل الكشوفات، وتقدر تنسخه لقوالبك.",
        "e_name": "اكتب اسم القالب.", "e_long": "اسم القالب أطول من {n} حرف.", "e_lang": "لغة النص مش صحيحة.", "e_specialty": "التخصص مش صحيح.",
        "e_empty": "اكتب نص واحد على الأقل في القالب، أو ضيف دوا.", "e_limit": "وصلت للحد الأقصى ({n} قالب). امسح أو اقفل قوالب مش محتاجها.",
        "e_name_taken": "عندك قالب بنفس الاسم والتخصص واللغة.",
        # the visit form
        "pick": "اختار قالب", "pick_label": "اختار قالب كشف جاهز", "save_as": "احفظ ده كقالب", "save_as_label": "احفظ نصوص وأدوية الكشف ده كقالب ليك",
        "sheet_title": "اختار قالب كشف", "close": "اقفل", "sheet_search": "دوّر في القوالب", "sheet_placeholder": "اكتب اسم القالب",
        "loading": "بدوّر…", "count_one": "قالب واحد", "count_two": "قالبين", "count": "{n} قوالب", "count_many": "{n} قالب",
        "empty_title": "مفيش قوالب", "empty_body": "جرّب كلمة تانية أو تخصص تاني، أو احفظ كشف كقالب.", "error_title": "مقدرناش نجيب القوالب",
        "error_body": "اتأكد من النت وجرّب تاني.", "retry": "جرّب تاني", "manage": "قوالب الكشف", "footer": "القالب بيملّا الخانات الفاضية بس ويضيف أدويته بعد اللي كتبته.",
        "applied": "طبّقنا «{name}».", "filled": "اتملّا: {fields}.", "kept": "اتساب زي ما هو: {fields}.", "added": "اتضاف {n} دوا.",
        "added_cut": "اتضاف {n} دوا ومقدرناش نضيف {m} (الحد {max}).", "nothing_new": "الخانات اللي في القالب مليانة بالفعل.",
        "save_title": "احفظ ده كقالب", "save_name": "اسم القالب", "save_btn": "احفظ القالب", "save_hint": "بيتحفظ الكلام والأدوية بس، ليك أنت. مفيش اسم مريض ولا قياسات ولا تواريخ.",
        "save_empty": "اكتب نص في الكشف أو ضيف دوا الأول.", "save_done": "اتحفظ القالب «{name}».", "review": "راجعه وعدّله", "save_failed": "مقدرناش نحفظ القالب.",
        "f_complaint": "الشكوى", "f_examination": "الفحص", "f_diagnosis": "التشخيص", "f_treatment": "التعليمات",
    },
    "en": {
        "title": "Visit templates", "intro": "Ready texts for common complaints that fill a visit in a second. They fill empty boxes only and never overwrite what you typed.",
        "new": "New template", "edit": "Edit template", "back": "Visit templates", "save": "Save", "cancel": "Cancel", "saved": "Template saved.",
        "scope_mine": "Mine", "scope_shared": "Shared", "scope_others": "Doctors' templates",
        "search": "Search by name", "specialty": "Specialty", "all_specialties": "All specialties", "text_lang": "Text language", "all_langs": "All languages",
        "lang_ar": "Arabic", "lang_en": "English", "filter": "Filter", "find": "Search",
        "name": "Template name", "name_hint": "For example: Cold and flu", "owner": "For", "status": "Status", "on": "On", "off": "Off",
        "shared": "Shared", "mine": "Mine", "starter": "Starter by Hesba", "someone": "Another doctor", "lines_n": "{n} medicines",
        "scope": "Who uses it", "scope_mine_opt": "Only me", "scope_shared_opt": "Every doctor (shared)",
        "active": "On (offered when recording a visit)", "texts": "Texts", "texts_hint": "The visit form's own boxes. Leave a box empty if the template should not fill it.",
        "instructions": "Instructions for the patient", "meds_hint": "Medicines are optional. They land on the prescription like a medicine typed by hand, and the doctor decides.",
        "no_patient": "A template never stores a patient's name, vitals or dates.",
        "up": "Move up", "down": "Move down", "turn_off": "Switch off", "turn_on": "Switch on", "copy": "Copy to mine", "delete": "Delete",
        "delete_q": "Delete “{name}”?", "delete_yes": "Yes, delete", "copied": "The template was copied to yours.", "deleted": "Template deleted.",
        "turned_off": "Template switched off.", "turned_on": "Template switched on.", "moved": "Order changed.",
        "none": "No templates here yet.", "none_filtered": "No templates match this search.", "clear": "Clear the search",
        "not_yours": "This template belongs to another doctor; they or a holder of the records permission can change it.",
        "shared_note": "This template is shared; a holder of the records permission changes it, and you can copy it to yours.",
        "e_name": "Enter the template's name.", "e_long": "The name is longer than {n} characters.", "e_lang": "The text language is not valid.", "e_specialty": "The specialty is not valid.",
        "e_empty": "Write at least one text in the template, or add a medicine.", "e_limit": "You reached the limit ({n} templates). Delete or switch off some you do not need.",
        "e_name_taken": "You already have a template with the same name, specialty and language.",
        "pick": "Choose a template", "pick_label": "Choose a ready visit template", "save_as": "Save this as a template", "save_as_label": "Save this visit's texts and medicines as a template of yours",
        "sheet_title": "Choose a visit template", "close": "Close", "sheet_search": "Search the templates", "sheet_placeholder": "Type the template's name",
        "loading": "Searching…", "count_one": "1 template", "count_two": "2 templates", "count": "{n} templates", "count_many": "{n} templates",
        "empty_title": "No templates", "empty_body": "Try another word or specialty, or save a visit as a template.", "error_title": "Could not load the templates",
        "error_body": "Check the connection and try again.", "retry": "Try again", "manage": "Visit templates", "footer": "A template fills empty boxes only and adds its medicines after yours.",
        "applied": "Applied “{name}”.", "filled": "Filled: {fields}.", "kept": "Left as it was: {fields}.", "added": "{n} medicines added.",
        "added_cut": "{n} medicines added; {m} could not be added (limit {max}).", "nothing_new": "The boxes this template covers are already filled.",
        "save_title": "Save this as a template", "save_name": "Template name", "save_btn": "Save template", "save_hint": "Only the text and medicines are saved, for you. No patient name, vitals or dates.",
        "save_empty": "Write something in the visit or add a medicine first.", "save_done": "Saved the template “{name}”.", "review": "Review and edit it", "save_failed": "Could not save the template.",
        "f_complaint": "Complaint", "f_examination": "Examination", "f_diagnosis": "Diagnosis", "f_treatment": "Instructions",
    },
}


def words(lang):
    return WORDS["en" if lang == "en" else "ar"]


# ---- who may do what ----

def is_manager(user):
    """Practice-wide (owner, manager): writes the shared templates and may fix another doctor's."""

    return user_has_permission(user, services.WRITE)


def can_edit(user, template):
    return services.can_write(user) and (is_manager(user) or (template.owner_id is not None and template.owner_id == user.pk))


def _is_vet():
    from .vet import is_vet

    return is_vet()


def _is_dental():
    from . import dental

    return dental.is_dental()


def allowed_specialties():
    """A vet's templates are the vet's and a human clinic's never are: a vet install offers ``vet`` only, any other none of it."""

    return ("vet",) if _is_vet() else HUMAN


def default_specialty():
    """The specialty the sheet starts on: the install's own where it is known (a vet, a dental clinic), else none (all)."""

    return "vet" if _is_vet() else ("dental" if _is_dental() else "")


def specialty_options(lang):
    lang = "en" if lang == "en" else "ar"
    return [{"code": code, "label": SPECIALTIES[code][lang]} for code in allowed_specialties()]


def label(code, lang):
    return SPECIALTIES.get(code, {}).get("en" if lang == "en" else "ar", code)


# ---- the built-in starting templates ----

def _seed_specialties():
    return ("vet",) if _is_vet() else (("dental",) if _is_dental() else HUMAN)


def seed():
    """Write the starting templates this install has not been given yet. Returns how many rows were written.

    Only creates rows. The keys written are remembered in one ``SystemSetting`` JSON row, so one a person edited stays as they
    left it and one they deleted is not brought back. Safe to call on every open of the sheet or the manager."""

    from settings_core.models import SystemSetting

    wanted = [row for row in visit_template_seeds.rows() if row[1] in _seed_specialties()]
    known = SystemSetting.objects.filter(key=SEED_SETTING).values_list("value", flat=True).first()
    if known is not None and all(row[0] in set(_keys(known)) for row in wanted):
        return 0
    with transaction.atomic():
        setting, _ = SystemSetting.objects.select_for_update().get_or_create(key=SEED_SETTING, defaults={
            "value": "[]", "data_type": SystemSetting.DataType.JSON, "active": True,
            "description": "Visit templates (M1): the keys of the built-in starting templates already written, so none is ever overwritten or brought back."})
        done = set(_keys(setting.value))
        missing = [row for row in wanted if row[0] not in done]
        if not missing:
            return 0
        VisitTemplate.objects.bulk_create([
            VisitTemplate(name=name, lang=lang, specialty=specialty, complaint=complaint, examination=examination, treatment=instructions,
                          seed_key=key, position=position)
            for key, specialty, lang, name, complaint, examination, instructions, position in missing])
        setting.value = json.dumps(sorted(done | {row[0] for row in missing}))
        setting.save(update_fields=["value", "updated_at"])
    return len(missing)


def _keys(raw):
    try:
        found = json.loads(raw or "[]")
    except ValueError:
        return []
    return [key for key in found if isinstance(key, str)] if isinstance(found, list) else []


# ---- reading and checking a template ----

def _text(value, limit=TEXT_LIMIT):
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()[:limit]


def line_json(line, lang):
    """One medicine line as the template keeps it (``prescribing.resolve`` made ``line``; nothing here is saved)."""

    return {"reference": line.reference_id, "name": line.name(lang), "dose": line.dose, "frequency": line.frequency,
            "duration": line.duration, "route": line.route, "notes": line.notes}


def lines_from_post(post, template_lang, lang, errors):
    """The posted ``rx_*`` rows checked as a visit's lines are (names cut, at most 20, a reference that is not an active row
    becomes a typed line), then reduced to the JSON a template keeps."""

    resolved = prescribing.resolve(prescribing.lines_from_post(post), None, lang, errors)
    return [line_json(line, template_lang) for line in resolved]


def raw_lines(template):
    """The stored lines as the rows ``prescribing.resolve`` reads (what the editor draws and the sheet applies)."""

    found = []
    for item in template.lines if isinstance(template.lines, list) else []:
        if isinstance(item, dict):
            found.append({"id": "", "reference": str(item.get("reference") or ""), "name": _text(item.get("name"), 255),
                          **{field: _text(item.get(field), limit) for field, limit in prescribing.LIMITS.items() if field != "name"}})
    return found


def editor_context(raw, lang):
    """The «الأدوية» part of the template editor: ``prescribing.form_context`` without a patient (no allergy words)."""

    from pharmacy.models import DrugReference

    lang = "en" if lang == "en" else "ar"
    resolved = prescribing.resolve(raw, None, lang, [])
    return {"rx": {"lines": [prescribing.display(line, lang, frozenset(), number) for number, line in enumerate(resolved, 1)], "words": prescribing.WORDS[lang],
                   "chips": prescribing.CHIPS[lang], "list_loaded": DrugReference.objects.filter(is_active=True).exists(),
                   "max_lines": prescribing.MAX_LINES, "blank": prescribing.display(prescribing._blank(), lang)}}


def clean(data, user, lang, template=None):
    """The validated fields of a template, or a ``ValidationError`` with every message (Arabic or English by ``lang``)."""

    w, errors = words(lang), []
    name = _text(data.get("name"), 400)
    if not name:
        errors.append(w["e_name"])
    elif len(name) > NAME_LIMIT:
        errors.append(w["e_long"].format(n=NAME_LIMIT))
    text_lang = data.get("lang") or (template.lang if template else ("en" if lang == "en" else "ar"))
    if text_lang not in LANGS:
        errors.append(w["e_lang"])
    specialty = data.get("specialty") or (template.specialty if template else "")
    keep = template is not None and specialty == template.specialty  # a template saved before the install switched keeps its own
    if specialty not in SPECIALTIES or (specialty not in allowed_specialties() and not keep):
        errors.append(w["e_specialty"])
    texts = {field: _text(data.get(field)) for field in TEXT_FIELDS}
    lines = data.get("lines") or []
    if not any(texts.values()) and not lines:
        errors.append(w["e_empty"])
    if errors:
        raise ValidationError(errors)
    owner = template.owner if template is not None else user
    if is_manager(user) and "scope" in data:
        owner = None if data.get("scope") == "shared" else (template.owner if template is not None and template.owner_id else user)
    return {"name": name, "lang": text_lang, "specialty": specialty, "owner": owner, "lines": lines, **texts}


def _audit(user, action, template, **after):
    kind = AuditEventType.CREATE if action.startswith("create") else (AuditEventType.DELETE if action.startswith("delete") else AuditEventType.UPDATE)
    AuditLog.objects.create(event_type=kind, actor=user, module="medical", action=action, object_type="medical.VisitTemplate", object_id=str(template.pk),
                            after_data={"name": template.name, "specialty": template.specialty, "shared": template.owner_id is None,
                                        "lines": len(template.lines or []), **after})


def _room(owner, lang):
    if VisitTemplate.objects.filter(owner=owner).count() >= MAX_PER_SCOPE:
        raise ValidationError(words(lang)["e_limit"].format(n=MAX_PER_SCOPE))


def _next_position(owner, specialty, text_lang):
    last = VisitTemplate.objects.filter(owner=owner, specialty=specialty, lang=text_lang).order_by("-position").values_list("position", flat=True).first()
    return (last or 0) + 10


def save(data, user, template=None, lang="ar"):
    """Create or update a template. ``data["lines"]`` is the JSON list of medicine lines (see ``lines_from_post``)."""

    if not services.can_write(user):
        raise PermissionDenied("Visit templates need a clinical permission.")
    if template is not None and not can_edit(user, template):
        raise PermissionDenied(words(lang)["not_yours"])
    fields = clean(data, user, lang, template)
    clash = VisitTemplate.objects.filter(owner=fields["owner"], name=fields["name"], specialty=fields["specialty"], lang=fields["lang"])
    if template is not None:
        clash = clash.exclude(pk=template.pk)
    if clash.exists():
        raise ValidationError(words(lang)["e_name_taken"])
    creating = template is None
    new_owner = fields["owner"].pk if fields["owner"] is not None else None
    if creating or template.owner_id != new_owner:
        _room(fields["owner"], lang)
    moved = creating or (template.owner_id, template.specialty, template.lang) != (new_owner, fields["specialty"], fields["lang"])
    template = template or VisitTemplate(created_by=user)
    for field in ("name", "lang", "specialty", "owner", "lines", *TEXT_FIELDS):
        setattr(template, field, fields[field])
    if "active" in data:
        template.active = bool(data["active"])
    if moved:
        template.position = _next_position(fields["owner"], fields["specialty"], fields["lang"])
    with transaction.atomic():
        template.save()
        _audit(user, "create_visit_template" if creating else "update_visit_template", template)
    return template


def scrub(text, needles):
    """``text`` with every literal ``needles`` (a patient's name, file number, phone, an animal's name) replaced by «…»."""

    for needle in sorted({n.strip() for n in needles if n and len(n.strip()) >= 3}, key=len, reverse=True):
        text = re.sub(re.escape(needle), "…", text, flags=re.IGNORECASE)
    return text


def save_from_form(post, user, lang):
    """«احفظ ده كقالب»: a personal template from the visit form's four texts and medicine lines, and nothing else.

    The patient (and animal) the form is for are used only to blank their own name, file number and phone where the doctor
    typed them into a text; they are never stored. Vitals, dates, doctor and appointment are not read at all."""

    from .models import Animal, PatientFile
    from .views import _patients

    needles = []
    patient = _patients().filter(pk=int(post["patient"])).first() if str(post.get("patient") or "").isdigit() else None
    if patient is not None:
        file = PatientFile.objects.filter(customer=patient).first()
        needles += [patient.name, patient.phone or "", file.file_number if file else ""]
        if str(post.get("animal") or "").isdigit():
            needles += [name for name in Animal.objects.filter(pk=int(post["animal"]), owner=patient).values_list("name", flat=True)]
    errors = []
    text_lang = "en" if post.get("text_lang") == "en" else ("ar" if post.get("text_lang") == "ar" else ("en" if lang == "en" else "ar"))
    data = {"name": post.get("name"), "lang": text_lang, "specialty": post.get("specialty") or default_specialty() or allowed_specialties()[0],
            "lines": lines_from_post(post, text_lang, lang, errors)}
    for field in TEXT_FIELDS:
        data[field] = scrub(_text(post.get(field)), needles)
    if errors:
        raise ValidationError(errors)
    return save(data, user, None, lang)


# ---- the manager's actions ----

def _group(template):
    return VisitTemplate.objects.filter(owner_id=template.owner_id, specialty=template.specialty, lang=template.lang).order_by("position", "pk")


def move(template, direction, user, lang="ar"):
    """Swap ``template`` with its neighbour in the same group (same owner, specialty and language); positions are renumbered."""

    if not can_edit(user, template):
        raise PermissionDenied(words(lang)["not_yours"])
    rows = list(_group(template))
    at = next(index for index, row in enumerate(rows) if row.pk == template.pk)
    to = at - 1 if direction == "up" else at + 1
    if 0 <= to < len(rows):
        rows[at], rows[to] = rows[to], rows[at]
    with transaction.atomic():
        for number, row in enumerate(rows, 1):
            if row.position != number * 10:
                VisitTemplate.objects.filter(pk=row.pk).update(position=number * 10)
        _audit(user, "move_visit_template", template)


def set_active(template, on, user, lang="ar"):
    if not can_edit(user, template):
        raise PermissionDenied(words(lang)["not_yours"])
    template.active = bool(on)
    with transaction.atomic():
        template.save(update_fields=["active", "updated_at"])
        _audit(user, "update_visit_template", template, active=template.active)


def delete(template, user, lang="ar"):
    if not can_edit(user, template):
        raise PermissionDenied(words(lang)["not_yours"])
    with transaction.atomic():
        _audit(user, "delete_visit_template", template)
        template.delete()


def copy(template, user, lang="ar"):
    """A personal copy of any template this person can use (their own, or a shared one) for them to change."""

    if not services.can_write(user):
        raise PermissionDenied("Visit templates need a clinical permission.")
    if template.owner_id not in (None, user.pk) and not is_manager(user):
        raise PermissionDenied(words(lang)["not_yours"])
    _room(user, lang)
    name, taken = template.name, VisitTemplate.objects.filter(owner=user, specialty=template.specialty, lang=template.lang)
    while taken.filter(name=name).exists():
        name = f"{name} ({'copy' if template.lang == 'en' else 'نسخة'})"[:NAME_LIMIT]
    twin = VisitTemplate.objects.create(name=name, lang=template.lang, specialty=template.specialty, owner=user, complaint=template.complaint,
                                        examination=template.examination, diagnosis=template.diagnosis, treatment=template.treatment,
                                        lines=list(template.lines or []), created_by=user,
                                        position=_next_position(user, template.specialty, template.lang))
    _audit(user, "create_visit_template", twin, copied_from=template.pk)
    return twin


# ---- what the pages read ----

def usable_for(user):
    """The templates this person may apply on a visit: their own first, then the shared ones, each in its set order."""

    rows = list(VisitTemplate.objects.filter(active=True, specialty__in=allowed_specialties()).filter(Q(owner__isnull=True) | Q(owner=user))[:MAX_ANSWER * 2])
    order = list(SPECIALTIES)
    rows.sort(key=lambda row: (row.owner_id is None, order.index(row.specialty) if row.specialty in order else 99, row.position, row.pk))
    return rows[:MAX_ANSWER]


def listing(user, scope="mine", specialty="", query="", text_lang=""):
    """The manager's rows: ``scope`` is ``mine``, ``shared`` or (managers only) ``others``."""

    rows = VisitTemplate.objects.all()
    if scope == "shared":
        rows = rows.filter(owner__isnull=True)
    elif scope == "others" and is_manager(user):
        rows = rows.filter(owner__isnull=False).exclude(owner=user)
    else:
        rows = rows.filter(owner=user)
    if specialty in SPECIALTIES:
        rows = rows.filter(specialty=specialty)
    if text_lang in LANGS:
        rows = rows.filter(lang=text_lang)
    if query:
        rows = rows.filter(name__icontains=query)
    found = list(rows.select_related("owner")[:MAX_ANSWER])
    order = list(SPECIALTIES)
    found.sort(key=lambda row: (order.index(row.specialty) if row.specialty in order else 99, row.lang, row.position, row.pk))
    return found


def payload(user, lang, keys=frozenset()):
    """The sheet's answer: every usable template with its texts and its medicine lines ready to put on the form.

    A line that points at a drug row that is still active comes back in the drug picker's shape (``prescribing.search_json``:
    names, ingredient, strength, form; no price), so the form draws it as a picked line; a line whose row was switched off or
    deleted comes back as a typed line with its stored name. ``keys`` are the allergy words of the patient the form is for
    (``views_rx._keys``): only a drug's own matching word is returned, never the allergy text."""

    from pharmacy.models import DrugReference

    lang = "en" if lang == "en" else "ar"
    rows = usable_for(user)
    wanted = set()
    for template in rows:
        wanted |= {ref for ref in (_ref(item) for item in raw_lines(template)) if ref}
    refs = {row.pk: row for row in DrugReference.objects.filter(pk__in=wanted, is_active=True)} if wanted else {}
    today = timezone.localdate()
    out = []
    for template in rows:
        lines = []
        for item in raw_lines(template):
            ref = refs.get(_ref(item))
            lines.append({"name": item["name"], "dose": item["dose"], "frequency": item["frequency"], "duration": item["duration"], "route": item["route"],
                          "notes": item["notes"], "reference": ref.pk if ref else "",
                          "row": prescribing.search_json(ref, lang, keys, today) if ref else None})
        out.append({"id": template.pk, "name": template.name, "lang": template.lang, "specialty": template.specialty,
                    "specialty_label": label(template.specialty, lang), "mine": template.owner_id == user.pk, "shared": template.owner_id is None,
                    "starter": bool(template.seed_key), "complaint": template.complaint, "examination": template.examination,
                    "diagnosis": template.diagnosis, "treatment": template.treatment, "lines": lines})
    return out


def _ref(item):
    from pharmacy import picker

    return picker.reference_id(item.get("reference"))


def form_context(lang):
    """What the visit form needs to draw the template control and its two sheets (the templates themselves arrive by JSON)."""

    lang = "en" if lang == "en" else "ar"
    return {"vt": {"words": words(lang), "specialties": specialty_options(lang), "default_specialty": default_specialty()}}
