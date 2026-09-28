"""SEARCH-001: the search page; a single exact hit opens directly."""

from django.shortcuts import redirect, render

from .services import search


def search_view(request):
    lang = "en" if request.GET.get("lang") == "en" else "ar"
    query = (request.GET.get("q") or "").strip()
    groups = search(request.user, query, lang)
    rows = [row for group in groups for row in group["rows"]]
    if len(rows) == 1 and request.GET.get("go") == "1":
        return redirect(f"{rows[0]['url']}?lang={lang}")
    words = {
        "ar": {"title": "بحث", "placeholder": "اسم، تليفون، كود، باركود، رقم فاتورة أو سيريال", "button": "بحث", "short": "اكتب حرفين على الأقل.", "none": "مفيش نتايج لـ «{q}».", "hint": "اختصار: اضغط / أو Ctrl+K من أي شاشة."},
        "en": {"title": "Search", "placeholder": "Name, phone, code, barcode, invoice or serial number", "button": "Search", "short": "Type at least two characters.", "none": "No results for “{q}”.", "hint": "Shortcut: press / or Ctrl+K on any screen."},
    }[lang]
    return render(request, "search/results.html", {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"],
                                                    "q": query, "groups": groups, "none": words["none"].format(q=query)})
