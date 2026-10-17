"""AN1 (Ahmed's notes, 17 Oct 2026): every list and report shows a page at a time, and the reader picks how many rows.

``page_of(request, items)`` cuts ``items`` (a queryset or a list) into pages of
10, 20, 50 or 100 rows. The size comes from ``?per=``; a size picked once is
remembered for the visit, so every other list opens the same way. The
``{% pager page %}`` tag (``hesba_paging``) draws the size choice and the
previous/next links, keeping the screen's filters. Totals are never taken from
a page: views that show a total keep computing it from all the rows.
"""

from django.core.paginator import Paginator

SIZES = (10, 20, 50, 100)
DEFAULT_SIZE = 20
SESSION_KEY = "hesba_page_size"


def page_size(request):
    raw = (request.GET.get("per") or "").strip()
    if raw.isdigit() and int(raw) in SIZES:
        size = int(raw)
        session = getattr(request, "session", None)
        if session is not None and session.get(SESSION_KEY) != size:
            session[SESSION_KEY] = size
        return size
    session = getattr(request, "session", None)
    remembered = session.get(SESSION_KEY) if session is not None else None
    return remembered if remembered in SIZES else DEFAULT_SIZE


def page_of(request, items):
    """The requested page of ``items``, at the reader's page size."""

    number = request.GET.get("page") or request.POST.get("page")
    return Paginator(items, page_size(request)).get_page(number)
