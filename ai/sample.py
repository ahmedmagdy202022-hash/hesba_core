"""AI demo (HG-078 deviation 7): the label every mock answer carries where a person sees it.

While this server answers with the in-process mock (``ai.providers.is_mock``: development, or a showcase install with
``DEMO_MODE``), the voice summary card (A1), the «اسأل حسبة» page (A2) and the invoice photo pages (A3) say plainly that
the answer is a sample and that nothing was sent to an AI service. With a live provider, or with AI off, there is no
label: ``notice`` returns None.

The label says what is sample and what is real on each surface: A1's figures and text come from the reports (only the
audio is a silent sample), A2's figure comes from the reports (only the routing is by keywords), and A3's reading is a
fixed sample invoice whatever the photo (``ai.providers.mock.SAMPLE_READING``).
"""

from . import providers

#: The sample invoice photo a tester can upload on the demo (the mock reads the same sample whatever the photo).
SAMPLE_INVOICE_IMAGE = "hesba/img/demo/sample-invoice.jpg"

WORDS = {
    "ar": {
        "title": "رد تجريبي — مفيش اتصال حقيقي",
        "voice_summary": "الصوت عيّنة ساكتة، مفيش كلام بيتسمع. النص والأرقام من تقاريرك فعلاً.",
        "ask": "التقرير اتختار بالكلمات من غير موديل ذكاء اصطناعي. الرقم من تقاريرك فعلاً.",
        "invoice_photo": "القراية دي فاتورة نموذج ثابتة، مهما كانت الصورة اللي رفعتها.",
        "sample_image": "نزّل صورة فاتورة نموذج تجرّب بيها",
    },
    "en": {
        "title": "Sample reply — no real AI call",
        "voice_summary": "The audio is a silent sample; nothing is spoken. The text and figures really come from your reports.",
        "ask": "The report was picked by keywords, with no AI model. The figure really comes from your reports.",
        "invoice_photo": "This reading is a fixed sample invoice, whatever photo you upload.",
        "sample_image": "Download a sample invoice photo to try",
    },
}

FEATURES = ("voice_summary", "ask", "invoice_photo")


def notice(feature, lang="ar"):
    """``{"title", "detail"}`` for ``feature`` while this server answers with the mock, else None."""

    if feature not in FEATURES:
        raise ValueError(f"Unknown AI feature {feature!r}.")
    if not providers.is_mock():
        return None
    words = WORDS["en" if lang == "en" else "ar"]
    result = {"title": words["title"], "detail": words[feature]}
    if feature == "invoice_photo":
        from django.templatetags.static import static

        result.update(image_url=static(SAMPLE_INVOICE_IMAGE), image_label=words["sample_image"])
    return result
