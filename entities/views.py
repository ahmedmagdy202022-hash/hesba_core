"""ENT-001 screens: the group's entities, and «where is this item?» across them."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from master_data.models import Item
from permissions.decorators import require_permission
from permissions.services import user_has_permission
from settings_core import setup_catalog as catalog

from . import services
from .models import Entity, EntityKind

WORDS = {
    "ar": {
        "title": "الكيانات والفروع", "intro": "كل كيان (مصنع، محل، فرع) ليه مخازنه وخزنه وحساباته، وكلهم تحت نفس الشركة.",
        "new": "كيان جديد", "edit": "تعديل", "code": "الكود", "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي", "kind": "النوع",
        "kind_branch": "فرع (نفس الشركة)", "kind_company": "شركة مستقلة (سجل وضريبة منفصلين)", "activity": "النشاط",
        "same_activity": "نفس نشاط الشركة", "tax": "الرقم الضريبي", "prefix": "بادئة المستندات", "active": "نشط", "main": "الرئيسي",
        "locations": "المخازن", "cashboxes": "الخزن", "save": "حفظ", "saved": "اتحفظ الكيان.", "back": "رجوع", "empty": "لسه مفيش كيانات غير الرئيسي.",
        "where_title": "فين الصنف ده؟", "where_intro": "الكمية المتاحة من كل صنف في كل كيان وكل مخزن في الشركة.",
        "search": "دوّر بالاسم أو الكود أو الباركود", "find": "بحث", "item": "الصنف", "entity": "الكيان", "location": "المخزن",
        "quantity": "الكمية", "value": "القيمة بالتكلفة", "total": "الإجمالي في الشركة", "nowhere": "مش موجود في أي مخزن دلوقتي.",
        "no_items": "مفيش أصناف مطابقة.", "where_link": "فين الصنف ده؟",
    },
    "en": {
        "title": "Entities & branches", "intro": "Each entity (factory, shop, branch) has its own stores, cashboxes and books, all under one company.",
        "new": "New entity", "edit": "Edit", "code": "Code", "name_ar": "Arabic name", "name_en": "English name", "kind": "Kind",
        "kind_branch": "Branch (same company)", "kind_company": "Separate company (own registration and tax)", "activity": "Activity",
        "same_activity": "Same as the company", "tax": "Tax registration number", "prefix": "Document prefix", "active": "Active", "main": "Main",
        "locations": "Stores", "cashboxes": "Cashboxes", "save": "Save", "saved": "Entity saved.", "back": "Back", "empty": "No entities besides the main one yet.",
        "where_title": "Where is this item?", "where_intro": "What is on hand of each item in every entity and store of the company.",
        "search": "Search by name, code or barcode", "find": "Search", "item": "Item", "entity": "Entity", "location": "Store",
        "quantity": "Quantity", "value": "Value at cost", "total": "Company total", "nowhere": "Not on hand anywhere right now.",
        "no_items": "No matching items.", "where_link": "Where is this item?",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"]}
    context.update(extra)
    return context


@require_permission("settings.view_settings")
def entity_list(request):
    rows = [{"entity": e, "locations": e.locations.filter(active=True).count(), "cashboxes": e.cashboxes.filter(active=True).count()}
            for e in Entity.objects.all()]
    services.main_entity()
    return render(request, "entities/list.html", _context(request, rows=rows, can_manage=user_has_permission(request.user, "settings.manage_settings")))


def _activity_choices(lang):
    choices = []
    for activity in catalog.ACTIVITY_LABELS:
        for sub in catalog.SUB_ACTIVITY_LABELS.get(activity, {}):
            choices.append((f"{activity}:{sub}", f"{catalog.activity_label(activity, lang)} · {catalog.sub_activity_label(activity, sub, lang)}"))
    return choices


@require_permission("settings.manage_settings")
def entity_edit(request, pk=None):
    lang = _lang(request)
    entity = get_object_or_404(Entity, pk=pk) if pk else None
    error = ""
    if request.method == "POST":
        data = request.POST.dict()
        activity, _, sub = (data.get("activity") or "").partition(":")
        data.update(activity_slug=activity, sub_activity_slug=sub, active=request.POST.get("active") == "on")
        try:
            services.save_entity(data, request.user, entity, lang)
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            messages.success(request, WORDS[lang]["saved"])
            return redirect(f"{reverse('entities:list')}?lang={lang}")
    current = f"{entity.activity_slug}:{entity.sub_activity_slug}" if entity and entity.activity_slug else ""
    return render(request, "entities/form.html", _context(request, entity=entity, error=error, post=request.POST,
                                                          kinds=[(EntityKind.BRANCH, WORDS[lang]["kind_branch"]), (EntityKind.COMPANY, WORDS[lang]["kind_company"])],
                                                          activities=_activity_choices(lang), current_activity=current))


@require_permission("inventory.view_group_stock")
def where_is(request):
    """Every matching item, with what each entity and store holds of it."""

    query = request.GET.get("q", "").strip()
    items = Item.objects.filter(active=True, is_stock_tracked=True)
    if query:
        items = items.filter(Q(item_code__icontains=query) | Q(item_name__icontains=query) | Q(barcode=query))
    if request.GET.get("item", "").isdigit():
        items = items.filter(pk=int(request.GET["item"]))
    page = Paginator(items.order_by("item_name"), 25).get_page(request.GET.get("page"))
    can_cost = user_has_permission(request.user, "inventory.view_cost")
    stock = services.group_stock(list(page.object_list), include_cost=can_cost)
    rows = []
    for item in page.object_list:
        places = stock.get(item.pk, [])
        total = sum((p["quantity"] for p in places), 0)
        rows.append({"item": item, "places": places, "total": total, "value": sum((p.get("value", 0) for p in places), 0)})
    return render(request, "entities/where.html", _context(request, rows=rows, page=page, query=query, can_cost=can_cost,
                                                           multi=services.is_multi_entity(), page_title=WORDS[_lang(request)]["where_title"]))
