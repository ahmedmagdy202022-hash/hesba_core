"""S1 (UI): the "Photos & signature" card on the work-order page.

Presentation only. Every rule (who sees, who adds, who deletes, what is closed) is read from the service layer's own
owner registry, so the card and the endpoints can never disagree. Nothing here writes anything.
"""

from django.core.exceptions import PermissionDenied
from django.urls import reverse

from . import services
from .models import Kind, Stage

WORDS = {
    "ar": {
        "title": "الصور والتوقيع", "intro": "صوّر الجهاز واخد توقيع العميل. كل ده بيتحفظ على الأمر.",
        "stage_receive": "عند الاستلام", "stage_deliver": "عند التسليم", "stage_other": "أخرى",
        "photos": "الصور", "documents": "مستندات PDF", "signatures": "توقيع العميل",
        "camera": "صوّر", "choose": "اختار صور أو PDF", "none": "لسه مفيش مرفقات في المرحلة دي.",
        "uploading": "جاري الرفع…", "uploaded": "اتحفظ.", "photo_of": "صورة", "open": "افتح",
        "delete": "امسح", "delete_confirm": "تمسح المرفق ده؟", "deleting": "جاري المسح…",
        "sign_help": "العميل يوقّع بصباعه أو بالماوس جوه الإطار.", "sign_here": "وقّع هنا", "sign_for": "التوقيع لمرحلة",
        "clear": "مسح", "save_signature": "حفظ التوقيع", "saved_on": "اتحفظ", "pad_label": "مساحة توقيع العميل",
        "consent_receive": "بتوقيعه العميل بيأكد إنه سلّم الجهاز بالحالة اللي في الصور.",
        "consent_deliver": "بتوقيعه العميل بيأكد إنه استلم جهازه بعد الإصلاح.",
        "signature_alt": "توقيع العميل", "failed": "حصلت مشكلة، جرّب تاني.", "network": "مفيش اتصال دلوقتي، جرّب تاني.",
    },
    "en": {
        "title": "Photos & signature", "intro": "Photograph the device and take the customer's signature. Everything is kept on the order.",
        "stage_receive": "At receiving", "stage_deliver": "At delivery", "stage_other": "Other",
        "photos": "Photos", "documents": "PDF documents", "signatures": "Customer's signature",
        "camera": "Take photo", "choose": "Choose photos or a PDF", "none": "No attachments at this stage yet.",
        "uploading": "Uploading…", "uploaded": "Saved.", "photo_of": "Photo", "open": "Open",
        "delete": "Delete", "delete_confirm": "Delete this attachment?", "deleting": "Deleting…",
        "sign_help": "The customer signs inside the frame with a finger or the mouse.", "sign_here": "Sign here", "sign_for": "Signature for",
        "clear": "Clear", "save_signature": "Save signature", "saved_on": "Saved", "pad_label": "Customer signature area",
        "consent_receive": "By signing, the customer confirms the device was handed in in the condition shown in the photos.",
        "consent_deliver": "By signing, the customer confirms they received their device after the repair.",
        "signature_alt": "Customer's signature", "failed": "Something went wrong, try again.", "network": "No connection right now, try again.",
    },
}

STAGES = (Stage.RECEIVE, Stage.DELIVER, Stage.OTHER)


def card(order, user, lang):
    """Everything the card needs, or None when this user may not see this order's attachments (the card is then hidden)."""

    lang = "en" if lang == "en" else "ar"
    rules = services.OWNERS[services.owner_label(order)]
    try:
        rows = list(services.attachments_for(order, user, lang))
    except (PermissionDenied, services.OwnerNotFound):
        return None
    words = services.MESSAGES[lang]
    closed = rules["closed"](order)
    can_attach = bool(rules["can_attach"](user, order))
    can_delete = bool(rules["can_delete"](user, order))
    signature_final = bool(rules["signature_final"](order))
    groups = []
    for stage in STAGES:
        mine = [row for row in rows if row.stage == stage]
        if stage == Stage.OTHER and not mine:
            continue  # the card offers receive and deliver; "other" shows only what another client already stored
        items = [{"row": row, "url": reverse("attachments:file", args=[row.pk]), "delete_url": f"{reverse('attachments:delete', args=[row.pk])}?lang={lang}",
                  "can_delete": can_delete and not (row.kind == Kind.SIGNATURE and signature_final)} for row in mine]
        groups.append({"stage": stage.value, "label": WORDS[lang][f"stage_{stage.value}"], "count": len(items),
                       "photos": [i for i in items if i["row"].kind == Kind.PHOTO],
                       "documents": [i for i in items if i["row"].kind == Kind.DOCUMENT],
                       "signatures": [i for i in items if i["row"].kind == Kind.SIGNATURE]})
    can_add = can_attach and not closed
    return {
        "groups": groups, "can_add": can_add, "total": len(rows), "words": WORDS[lang],
        # A closed order just has no add controls: its «closed» refusal comes from the service when someone actually tries to add
        # (it used to sit under the delivery success message and read like an error). Only a user who may never add is told so.
        "blocked": "" if (can_add or (can_attach and closed)) else words["read_only"],
        "upload_url": f"{reverse('attachments:work_order_upload', args=[order.pk])}?lang={lang}",
        "signature_url": f"{reverse('attachments:work_order_signature', args=[order.pk])}?lang={lang}",
        "default_signature_stage": (Stage.DELIVER if order.status == "ready" else Stage.RECEIVE).value,
        "empty_signature": words["no_signature"],
    }


# ---- M5 (HG-083): the clinic's attachments card, on a patient's file and on a visit ----
# The same card look and the same script as the work order's. The categories ride on the ``stage`` column (so a work order keeps its
# own receive / deliver words); the labels below are shown for medical owners only.

MEDICAL_WORDS = {
    "ar": {
        "title": "المرفقات: أشعة وتحاليل وصور",
        "intro_visit": "ارفع أشعة وتحاليل وصور وتقارير الكشف ده. بتتحفظ على الكشف وبتظهر في ملف المريض.",
        "intro_file": "ارفع الملفات اللي المريض بيجيبها ومالهاش كشف معين: أشعة وتحاليل وصور وتقارير.",
        "formats": "صور JPG أو PNG أو WEBP وملفات PDF بس، لحد {limit} للملف. ملفات DICOM مش مدعومة. الصورة الكبيرة بتتصغّر قبل الرفع؛ لو محتاج الأشعة بجودتها الكاملة ارفعها PDF.",
        "category": "نوع المرفق", "scan": "أشعة", "lab": "تحاليل", "photo": "صور", "report": "تقارير",
        "camera": "صوّر", "choose": "اختار صور أو PDF", "none": "لسه مفيش مرفقات.", "open": "افتح", "pdf": "PDF",
        "by": "رفعه", "delete": "امسح", "photo_of": "صورة",
        "blocked": "إضافة المرفقات للدكتور أو لصاحب صلاحية تسجيل الكشوفات بس.",
        "visits_title": "مرفقات الكشوفات", "visits_intro": "اللي اتضاف على كشوفات المريض، الأحدث الأول.", "visit_of": "كشف",
        "visits_more": "ده آخر {n} مرفق.", "visits_all": "اعرض كل المرفقات", "visits_none": "مفيش مرفقات على الكشوفات لسه.",
    },
    "en": {
        "title": "Attachments: scans, lab reports and photos",
        "intro_visit": "Attach this visit's scans, lab reports, photos and reports. They stay on the visit and show on the patient's file.",
        "intro_file": "Attach what the patient brings that belongs to no particular visit: scans, lab reports, photos and reports.",
        "formats": "JPG, PNG or WEBP images and PDF files only, up to {limit} each. DICOM files are not supported. A large image is made smaller before it is uploaded; for a scan at full quality, upload it as a PDF.",
        "category": "Category", "scan": "Scan / X-ray", "lab": "Lab report", "photo": "Photo", "report": "Report",
        "camera": "Take photo", "choose": "Choose photos or a PDF", "none": "No attachments yet.", "open": "Open", "pdf": "PDF",
        "by": "Added by", "delete": "Delete", "photo_of": "Photo",
        "blocked": "Only a doctor, or someone who may record visits, can add attachments.",
        "visits_title": "Visit attachments", "visits_intro": "What was added to this patient's visits, newest first.", "visit_of": "Visit",
        "visits_more": "These are the latest {n} attachments.", "visits_all": "Show all attachments", "visits_none": "No attachments on the visits yet.",
    },
}

#: How many visit attachments the patient file lists (one query, newest first).
PATIENT_VISITS_LIMIT = 30
#: «اعرض كل المرفقات» on the patient file (``?files=all``): everything, up to a bound that keeps the page finite.
PATIENT_VISITS_ALL = 500


def _who(user):
    return (user.get_full_name() or user.get_username()) if user is not None else ""


def _medical_item(row, lang, can_delete):
    words = MEDICAL_WORDS[lang]
    return {"row": row, "url": reverse("attachments:file", args=[row.pk]), "is_image": row.content_type_name.startswith("image/"),
            "delete_url": f"{reverse('attachments:delete', args=[row.pk])}?lang={lang}", "can_delete": can_delete,
            "category": words.get(row.stage, row.stage), "who": _who(row.uploaded_by)}


def medical_card(owner, user, lang, anchor="med-attachments"):
    """The card for a visit or a patient's file, or None when this user may not see its attachments (the card is then hidden).
    Every rule is read from the service layer's own registry, so the card and the endpoints cannot disagree."""

    lang = "en" if lang == "en" else "ar"
    label = services.owner_label(owner)
    rules = services.OWNERS[label]
    try:
        rows = list(services.attachments_for(owner, user, lang))
    except (PermissionDenied, services.OwnerNotFound):
        return None
    words = dict(MEDICAL_WORDS[lang], formats=MEDICAL_WORDS[lang]["formats"].format(limit=services._human(services.max_bytes(), lang)))  # the live cap
    can_delete = bool(rules["can_delete"](user, owner))
    can_add = bool(rules["can_attach"](user, owner)) and not rules["closed"](owner)
    upload_name = "attachments:visit_upload" if label == "medical.clinicalvisit" else "attachments:patient_file_upload"
    return {
        "anchor": anchor, "words": words, "can_add": can_add, "total": len(rows), "blocked": "" if can_add else words["blocked"],
        "intro": words["intro_visit"] if label == "medical.clinicalvisit" else words["intro_file"],
        "categories": [{"value": stage.value, "label": words[stage.value]} for stage in services.MEDICAL_STAGES],
        "items": [_medical_item(row, lang, can_delete) for row in reversed(rows)],  # newest first
        "upload_url": f"{reverse(upload_name, args=[owner.pk])}?lang={lang}",
    }


def patient_visit_files(patient, user, lang, show_all=False):
    """The attachments on this patient's visits, newest first, for the patient file: two queries however many visits there are.
    None when this user may not see visit attachments. Read-only; each row links to the visit it belongs to.

    The latest ``PATIENT_VISITS_LIMIT`` by default; ``show_all`` lists them all here, because a user who reads the records
    without writing them cannot open a visit and would otherwise never reach the older files."""

    from medical.models import ClinicalVisit

    lang = "en" if lang == "en" else "ar"
    visits = {pk: day for pk, day in ClinicalVisit.objects.filter(patient=patient).values_list("pk", "visit_date")}
    try:
        cap = PATIENT_VISITS_ALL if show_all else PATIENT_VISITS_LIMIT
        rows = services.attachments_for_many("medical.clinicalvisit", visits, user, limit=cap + 1)
    except (PermissionDenied, services.OwnerNotFound):
        return None
    words = MEDICAL_WORDS[lang]
    more = len(rows) > cap
    items = []
    for row in rows[:cap]:
        item = _medical_item(row, lang, False)
        item.update({"visit_pk": row.object_id, "visit_date": visits[row.object_id]})
        items.append(item)
    return {"items": items, "words": words, "more": words["visits_more"].format(n=cap) if more else "", "all_link": more and not show_all}
