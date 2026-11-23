"""R1 step 2: «بدائل مش عندك» on the drug page, read in the template so the page's view stays as it is."""

from django import template

from pharmacy import picker

register = template.Library()


@register.simple_tag
def unstocked_alternatives(item, lang="ar"):
    """{"rows": reference rows with the item's active ingredient that the pharmacy does not have, "words": the strings}."""

    return {"rows": picker.unstocked_alternatives(item), "words": picker.UI["en" if lang == "en" else "ar"]}
