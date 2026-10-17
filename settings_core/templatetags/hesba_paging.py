"""AN1: one pager for every list and report (``{% pager page %}``)."""

from django import template

from config.paging import SIZES

register = template.Library()

WORDS = {
    "ar": {"show": "اعرض", "per": "في الصفحة", "of": "من", "rows": "سطر", "prev": "السابق", "next": "التالي", "page": "صفحة"},
    "en": {"show": "Show", "per": "per page", "of": "of", "rows": "rows", "prev": "Previous", "next": "Next", "page": "Page"},
}


def _link(request, **changes):
    query = request.GET.copy()
    for key, value in changes.items():
        if value is None:
            query.pop(key, None)
        else:
            query[key] = value
    return "?" + query.urlencode()


@register.inclusion_tag("partials/pager.html", takes_context=True)
def pager(context, page):
    request = context["request"]
    lang = "en" if (context.get("lang") or request.GET.get("lang")) == "en" else "ar"
    paginator = page.paginator
    per = paginator.per_page
    return {
        "page": page,
        "words": WORDS[lang],
        "count": paginator.count,
        "first": page.start_index(),
        "last": page.end_index(),
        "sizes": [{"size": size, "url": _link(request, per=size, page=None), "current": size == per} for size in SIZES],
        "prev_url": _link(request, page=page.previous_page_number()) if page.has_previous() else "",
        "next_url": _link(request, page=page.next_page_number()) if page.has_next() else "",
        "show_sizes": paginator.count > SIZES[0],
    }
