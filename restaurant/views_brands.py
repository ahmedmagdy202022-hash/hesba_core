"""Restaurants R11 (HG-117): the brands' set-up («البراندات»).

Seen with the invoices' view right and changed with ``master_data.manage_items``, exactly as the kitchen stations and the delivery
zones are (no new permission). A brand is only a label: nothing here posts anything or touches stock.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from master_data.models import Category
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import branches, brands, services
from .models import Brand
from .views import TABLES, VIEW, _base, _lang

BRAND_WORDS = {
    "ar": {
        "title": "البراندات", "intro": "المطبخ الواحد ممكن يبيع تحت أكتر من اسم. البراند بيظهر على الطلب وتذكرة المطبخ والشيك والإيصال؛ الفاتورة والضريبة والمخزن واحدة للشركة.",
        "name_ar": "الاسم بالعربي", "name_en": "الاسم بالإنجليزي", "header": "سطر تحت الاسم على الشيك والإيصال", "header_hint": "اختياري، سطر قصير زي الشعار أو الموقع.",
        "categories": "منيو البراند", "menu_hint": "اختار الفئات اللي البراند بيبيعها. من غير اختيار بيعرض المنيو كله.", "all_menu": "المنيو كله",
        "sort": "الترتيب", "default": "الافتراضي", "default_hint": "الصالة وأي طلب من غير اختيار بياخدوه.", "active": "شغال", "branch": "الفرع", "choose_branch": "اختار الفرع",
        "edit": "تعديل", "delete": "امسح", "delete_confirm": "تمسح البراند ده؟", "save": "حفظ", "new": "براند جديد", "none": "لسه مفيش براندات. من غير براندات، الشاشات والإيصالات زي ما هي.",
        "back": "الطاولات", "orders": "طلب", "no_categories": "المنيو لسه من غير فئات.",
    },
    "en": {
        "title": "Brands", "intro": "One kitchen can sell under several names. The brand shows on the order, the kitchen ticket, the check and the receipt; the invoice, the tax and the stock stay the company's.",
        "name_ar": "Name in Arabic", "name_en": "Name in English", "header": "A line under the name on the check and the receipt", "header_hint": "Optional, a short line such as a slogan or a site.",
        "categories": "The brand's menu", "menu_hint": "Choose the categories the brand sells. With none chosen it shows the whole menu.", "all_menu": "The whole menu",
        "sort": "Order", "default": "Default", "default_hint": "Dine-in and any order with no choice take it.", "active": "Active", "branch": "Branch", "choose_branch": "Choose the branch",
        "edit": "Edit", "delete": "Delete", "delete_confirm": "Delete this brand?", "save": "Save", "new": "New brand", "none": "No brands yet. With no brands, the screens and the receipts stay as they are.",
        "back": "Tables", "orders": "orders", "no_categories": "The menu has no categories yet.",
    },
}


@require_permission(VIEW)
def setup(request):
    lang = _lang(request)
    bw = BRAND_WORDS[lang]
    words = brands.MESSAGES[lang]
    error = ""
    grouped = branches.group_mode()
    editing = None
    # a POST acts on the row it names (a row's delete form keeps the page's ``?edit=``); ``edit`` only opens the form on a GET
    raw = str((request.POST.get("pk") if request.method == "POST" else request.GET.get("edit")) or "")
    if raw.isdigit():
        editing = get_object_or_404(brands.scoped(), pk=int(raw))  # R12: another branch's brand is a 404
    form = {"sort": "0", "active": True, "categories": []}
    if request.method == "POST":
        if not user_has_permission(request.user, TABLES):
            raise PermissionDenied("Changing the brands needs master_data.manage_items.")
        form = {"name_ar": request.POST.get("name_ar", ""), "name_en": request.POST.get("name_en", ""), "header": request.POST.get("header", ""),
                "sort": request.POST.get("sort", ""), "active": request.POST.get("active") == "on", "is_default": request.POST.get("is_default") == "on",
                "categories": [int(one) for one in request.POST.getlist("categories") if one.isdigit()], "entity": request.POST.get("entity", "")}
        try:
            if request.POST.get("action") == "delete":
                if editing is None:
                    raise ValidationError(words["foreign"])
                brands.delete_brand(editing, request.user, lang)
                messages.success(request, words["deleted"])
            else:
                entity = branches.pick(request.user, request.POST.get("entity")) if grouped else None
                if grouped and entity is None and editing is None:
                    raise ValidationError(services.MESSAGES[lang]["branch"])
                brands.save_brand(form, request.user, editing, lang, entity=entity)
                messages.success(request, words["saved"])
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            return redirect(f"{reverse('restaurant:brands')}?lang={lang}")
    elif editing:
        form = {"name_ar": editing.name_ar, "name_en": editing.name_en, "header": editing.header, "sort": editing.sort, "active": editing.active,
                "is_default": editing.is_default, "categories": list(editing.categories.values_list("pk", flat=True))}
    rows = [{"brand": one, "label": one.label(lang), "branch": one.entity.display(lang), "orders": one.used, "categories": [(c.name_en if lang == "en" and c.name_en else c.name_ar) for c in one.categories.all()]}
            for one in brands.scoped(Brand.objects.select_related("entity").prefetch_related("categories").annotate(used=Count("orders")))]
    categories = [{"pk": c.pk, "label": c.name_en if lang == "en" and c.name_en else c.name_ar} for c in Category.objects.filter(active=True).order_by("category_code")]
    return render(request, "restaurant/brands.html", _base(request, page_title=bw["title"], bw=bw, rows=rows, form=form, editing=editing, error=error, categories=categories,
                                                          can_edit=user_has_permission(request.user, TABLES), grouped=grouped,
                                                          branch_choices=branches.choices(request.user, lang) if grouped else [], chosen_branch=str(form.get("entity") or "")))
