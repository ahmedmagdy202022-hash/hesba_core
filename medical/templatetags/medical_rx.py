"""M2 (HG-086): a saved visit's medicine lines, for the patient file and the animal card (no view change needed there)."""

from django import template

from medical import prescribing

register = template.Library()


@register.simple_tag
def visit_medicines(visit, lang="ar"):
    """The visit's lines as ``prescribing.display`` dicts, numbered from 1, with the allergy advice; [] without lines."""

    return prescribing.for_visit(visit, lang)


@register.inclusion_tag("medical/_rx_saved.html")
def saved_medicines(visit, lang="ar"):
    return {"lines": prescribing.for_visit(visit, lang), "words": prescribing.WORDS["en" if lang == "en" else "ar"]}
