"""FB-27: the one line a screen adds before it runs an action the owner may hold for approval."""

from django.contrib import messages

from .services import hold

SENT = {
    "ar": "العملية دي محتاجة موافقة. الطلب اتبعت ومستني حد يوافق عليه، ولحد ما يتوافق مفيش حاجة اتغيرت.",
    "en": "This needs a second person's approval. The request was sent; nothing has changed until it is approved.",
}


def held(request, action, object_id, data, label=""):
    """True when the action now waits for approval instead of running (the message says so)."""

    if hold(request.user, action, object_id, data, label) is None:
        return False
    lang = "en" if (request.POST.get("lang") or request.GET.get("lang")) == "en" else "ar"
    messages.info(request, SENT[lang])
    return True
