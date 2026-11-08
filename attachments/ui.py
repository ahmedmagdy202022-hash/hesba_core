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
        "blocked": "" if can_add else (words["closed"] if can_attach else words["read_only"]),
        "upload_url": f"{reverse('attachments:work_order_upload', args=[order.pk])}?lang={lang}",
        "signature_url": f"{reverse('attachments:work_order_signature', args=[order.pk])}?lang={lang}",
        "default_signature_stage": (Stage.DELIVER if order.status == "ready" else Stage.RECEIVE).value,
        "empty_signature": words["no_signature"],
    }
