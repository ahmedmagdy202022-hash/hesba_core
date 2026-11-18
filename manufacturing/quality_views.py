"""Manufacturing F5 (HG-136) screens: «فحص الجودة» (what waits in the hold place and the latest checks), the check form
for a finished order or a purchase line, one check with its quality certificate, the quality section of the recipe
page and the quality card of the order page.

Every rule is in ``manufacturing.quality``; the views read, call and render. Seeing needs ``inventory.view_stock`` (as
every manufacturing screen); recording a check needs ``inventory.transfer_stock``; a rejected quantity of finished
goods also needs ``inventory.adjust_stock``; the criteria need ``master_data.manage_items``. Everything is narrowed to
the entity being worked in, and another entity's order, purchase line or check returns 404."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import quality
from .models import CheckKind, CriterionKind
from .views import RECIPES, VIEW, _lang

SEE_PURCHASES = "purchases.view_purchase_invoices"
CHECK_OWNER = "manufacturing.qualitycheck"

WORDS = {
    "ar": {
        "title": "فحص الجودة", "back": "التصنيع", "orders_link": "أوامر الإنتاج",
        "intro": "المنتج اللي وصفته محتاجة إفراج بيدخل «حجز الجودة» بعد إنهاء الأمر، ومبيتباعش لحد ما يتفحص. من هنا بتسجّل نتيجة الفحص: المقبول بيتنقل للمخزن، والمرفوض بيتشطب، والباقي بيفضل محجوز.",
        "waiting": "منتظر الفحص", "empty_waiting": "مفيش كمية منتظرة الفحص دلوقتي.", "source": "المصدر", "order": "أمر إنتاج", "purchase": "فاتورة شراء",
        "item": "الصنف", "lot": "التشغيلة", "expiry": "الصلاحية", "held_qty": "الكمية المحجوزة", "since": "من يوم", "check": "افحص",
        "recent": "آخر الفحوصات", "no_checks": "لسه مفيش فحوصات متسجلة.", "number": "رقم الفحص", "date": "التاريخ", "result": "النتيجة", "kind": "النوع",
        "passed": "المقبول", "rejected": "المرفوض", "by": "بواسطة", "open": "افتح الفحص",
        "result_passed": "مقبول", "result_held": "محجوز", "result_rejected": "مرفوض", "result_partly": "مقبول جزئيًا",
        "kind_final": "منتج تام", "kind_stage": "أثناء التشغيل", "kind_incoming": "مشتريات",
        "hold_place": "مكان الحجز", "no_place": "لسه متعملش",
        "incoming_title": "فحص المشتريات قبل دخول المخزن",
        "incoming_hint": "عشان تفحص خامة قبل ما تدخل المخزن: اختار «حجز الجودة» موقع الاستلام في فاتورة الشراء. بعد ترحيل الفاتورة الكمية بتظهر هنا؛ المقبول بيتنقل للمخزن اللي تختاره، والمرفوض بيفضل في الحجز لحد ما يرجع للمورد بمرتجع شراء عادي.",
        "prepare": "جهّز مكان حجز الجودة", "prepared": "مكان حجز الجودة جاهز، وتقدر تختاره موقع استلام في فاتورة الشراء.",
        "to_return": "مرفوض ومنتظر مرتجع شراء", "open_invoice": "افتح فاتورة الشراء", "supplier": "المورد", "invoice": "الفاتورة", "quantity": "الكمية",
        "check_title": "فحص جودة", "made": "الكمية المنتجة", "received": "الكمية المستلمة", "released": "اتفرج عنه", "written_off": "اترفض",
        "still_held": "لسه محجوز", "criteria": "معايير الفحص", "criterion": "المعيار", "limits": "الحدود", "reading": "القراءة", "ok": "مطابق", "not_ok": "غير مطابق",
        "choose": "اختار", "no_criteria_recipe": "مفيش معايير متسجلة للمنتج ده؛ تقدر تضيفها من صفحة الوصفة.", "no_criteria_item": "مفيش معايير متسجلة للصنف ده.",
        "passed_qty": "الكمية المقبولة", "rejected_qty": "الكمية المرفوضة", "note": "ملاحظات", "destination": "المخزن اللي هيستلم المقبول",
        "record": "سجّل نتيجة الفحص", "hold": "استمرار الحجز", "reject_all": "ارفض الكمية كلها",
        "hint_final": "المقبول بيتنقل إلى {place} ويبقى متاح للبيع. المرفوض بيتشطب من المخزون كخسارة. والكمية اللي ملهاش قرار بتفضل محجوزة.",
        "hint_incoming": "المقبول بيتنقل للمخزن اللي تختاره. المرفوض بيفضل في حجز الجودة لحد ما يرجع للمورد بمرتجع شراء.",
        "recorded": "اتسجّل الفحص {number}.", "nothing_waiting": "مفيش كمية منتظرة الفحص هنا دلوقتي.", "history": "الفحوصات السابقة",
        "view_only": "تسجيل نتيجة الفحص لصاحب صلاحية تحويل المخزون.", "open_order": "افتح أمر الإنتاج", "open_recipe": "افتح الوصفة",
        "item_criteria": "معايير فحص الصنف ده", "item_criteria_hint": "بتتطبق على كل فاتورة شراء للصنف ده تدخل حجز الجودة.", "criteria_saved": "اتحفظت المعايير.",
        "certificate": "شهادة الجودة", "cert_intro": "ارفع شهادة الجودة أو تقرير المعمل، صورة أو ملف PDF، وتفضل محفوظة مع الفحص.",
        "cert_formats": "المسموح: صور وملفات PDF لحد {limit} للملف.", "cert_none": "لسه مفيش شهادة مرفوعة.", "camera": "صوّر الشهادة", "choose_file": "اختار ملف",
        "delete": "امسح", "open_file": "افتح", "added_by": "أضافها", "photo_of": "صورة شهادة الجودة", "pdf": "PDF",
        "release_op": "تحويل الإفراج", "reject_op": "تسوية الرفض", "at_check": "الكمية وقت الفحص", "went_to": "اتنقل إلى",
        "release_reversed": "تحويل الإفراج ده اتعكس من شاشة المخزون؛ الكمية رجعت حجز الجودة ومنتظرة فحص جديد.",
        "reject_reversed": "تسوية الرفض دي اتعكست من شاشة المخزون؛ الكمية رجعت حجز الجودة ومنتظرة فحص جديد.",
        "stays_for_return": "الكمية المرفوضة لسه في حجز الجودة لحد ما ترجع للمورد بمرتجع شراء.", "stage_note": "فحص أثناء التشغيل: المعيب اتسجل هالك على المرحلة، ومفيش حاجة اتحركت في المخزون.",
        "results": "نتيجة المعايير", "no_results": "متسجلش على الفحص ده معايير.", "check_page": "الفحص",
        "quality_title": "الجودة والإفراج", "needs_release": "منتج الوصفة دي يدخل حجز الجودة بعد الإنتاج، وميتباعش غير بعد الإفراج",
        "quality_hint": "المعايير بتظهر في مرحلة «فحص جودة» على أمر الإنتاج، وفي فحص الإفراج عن المنتج.", "criterion_ar": "اسم المعيار", "criterion_en": "الاسم بالإنجليزي",
        "pass_fail": "مطابق أو غير مطابق", "number_kind": "قراءة رقمية", "min": "الحد الأدنى", "max": "الحد الأقصى", "save_quality": "احفظ إعدادات الجودة",
        "quality_saved": "اتحفظت إعدادات الجودة.", "criteria_none": "لسه مفيش معايير.", "release_on": "محتاجة إفراج قبل البيع", "release_off": "بتدخل المخزن على طول",
        "card_title": "فحص الجودة والإفراج", "will_hold": "الوصفة دي محتاجة إفراج: بعد إنهاء الأمر المنتج هيدخل «حجز الجودة»، ومش هيتباع غير بعد الفحص.",
        "check_now": "افحص وسجّل النتيجة", "stage_criteria": "معايير فحص الجودة", "stage_criteria_hint": "سجّل نتيجة كل معيار. الوحدات اللي مطابقتش اكتبها في خانة المعيب.",
        "quality_link": "فحص الجودة", "reversed": "اتعكس",
    },
    "en": {
        "title": "Quality checks", "back": "Manufacturing", "orders_link": "Production orders",
        "intro": "A product whose recipe needs release enters the quality hold when its order finishes, and cannot be sold until it is checked. Record the check here: what passed moves to the warehouse, what was rejected is written off, and the rest stays on hold.",
        "waiting": "Waiting for a check", "empty_waiting": "Nothing is waiting for a check right now.", "source": "Source", "order": "Production order", "purchase": "Purchase invoice",
        "item": "Item", "lot": "Lot", "expiry": "Expiry", "held_qty": "Quantity on hold", "since": "Since", "check": "Check",
        "recent": "Latest checks", "no_checks": "No checks recorded yet.", "number": "Check", "date": "Date", "result": "Result", "kind": "Kind",
        "passed": "Passed", "rejected": "Rejected", "by": "By", "open": "Open the check",
        "result_passed": "Passed", "result_held": "On hold", "result_rejected": "Rejected", "result_partly": "Partly passed",
        "kind_final": "Finished goods", "kind_stage": "In process", "kind_incoming": "Purchases",
        "hold_place": "Hold place", "no_place": "Not made yet",
        "incoming_title": "Checking purchases before they enter the warehouse",
        "incoming_hint": "To check a material before it enters the warehouse, choose “Quality hold” as the receiving location on the purchase invoice. Once the invoice is posted the quantity shows here; what passed moves to the warehouse you choose, and what was rejected stays on hold until it goes back to the supplier on an ordinary purchase return.",
        "prepare": "Set up the quality hold place", "prepared": "The quality hold place is ready; you can choose it as the receiving location on a purchase invoice.",
        "to_return": "Rejected, waiting for a purchase return", "open_invoice": "Open the purchase invoice", "supplier": "Supplier", "invoice": "Invoice", "quantity": "Quantity",
        "check_title": "Quality check", "made": "Quantity made", "received": "Quantity received", "released": "Released", "written_off": "Rejected",
        "still_held": "Still on hold", "criteria": "Check criteria", "criterion": "Criterion", "limits": "Limits", "reading": "Reading", "ok": "Pass", "not_ok": "Fail",
        "choose": "Choose", "no_criteria_recipe": "This product has no criteria yet; add them on the recipe page.", "no_criteria_item": "This item has no criteria yet.",
        "passed_qty": "Passed quantity", "rejected_qty": "Rejected quantity", "note": "Notes", "destination": "Warehouse that receives what passed",
        "record": "Record the check", "hold": "Keep on hold", "reject_all": "Reject the whole quantity",
        "hint_final": "What passed moves to {place} and can be sold. What was rejected is written off from stock as a loss. A quantity with no decision stays on hold.",
        "hint_incoming": "What passed moves to the warehouse you choose. What was rejected stays in the quality hold until it goes back to the supplier on a purchase return.",
        "recorded": "Check {number} was recorded.", "nothing_waiting": "Nothing is waiting for a check here right now.", "history": "Earlier checks",
        "view_only": "Recording a check needs the stock transfer permission.", "open_order": "Open the production order", "open_recipe": "Open the recipe",
        "item_criteria": "This item's check criteria", "item_criteria_hint": "They apply to every purchase of this item received into the quality hold.", "criteria_saved": "The criteria were saved.",
        "certificate": "Quality certificate", "cert_intro": "Upload the quality certificate or the lab report, a photo or a PDF; it is kept with the check.",
        "cert_formats": "Allowed: images and PDF files, up to {limit} each.", "cert_none": "No certificate uploaded yet.", "camera": "Photograph the certificate", "choose_file": "Choose a file",
        "delete": "Delete", "open_file": "Open", "added_by": "Added by", "photo_of": "Photo of the quality certificate", "pdf": "PDF",
        "release_op": "Release transfer", "reject_op": "Reject adjustment", "at_check": "Quantity at the check", "went_to": "Moved to",
        "release_reversed": "This release was reversed from the stock screen; the quantity is back in the quality hold, waiting for a new check.",
        "reject_reversed": "This reject was reversed from the stock screen; the quantity is back in the quality hold, waiting for a new check.",
        "stays_for_return": "The rejected quantity is still in the quality hold until it goes back to the supplier on a purchase return.", "stage_note": "An in-process check: the defective units were recorded as scrap on the stage, and nothing moved in stock.",
        "results": "Criteria results", "no_results": "No criteria were recorded on this check.", "check_page": "Check",
        "quality_title": "Quality and release", "needs_release": "This recipe's product enters the quality hold after production and is sold only after release",
        "quality_hint": "The criteria show at the “Quality check” stage of a production order and at the release check of the product.", "criterion_ar": "Criterion name (Arabic)", "criterion_en": "Name in English",
        "pass_fail": "Pass or fail", "number_kind": "A measured number", "min": "Minimum", "max": "Maximum", "save_quality": "Save the quality settings",
        "quality_saved": "The quality settings were saved.", "criteria_none": "No criteria yet.", "release_on": "Needs release before sale", "release_off": "Goes straight into stock",
        "card_title": "Quality check and release", "will_hold": "This recipe needs release: when the order finishes, the product enters the quality hold and is sold only after the check.",
        "check_now": "Check and record the result", "stage_criteria": "Quality criteria", "stage_criteria_hint": "Record every criterion. Units that failed go in the defective field.",
        "quality_link": "Quality checks", "reversed": "Reversed",
    },
}


def _id(raw):
    return int(raw) if str(raw or "").isdigit() else 0


def _base(request, **extra):
    lang = _lang(request)
    words = WORDS[lang]
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "qw": words, "page_title": words["title"],
            "can_check": user_has_permission(request.user, quality.RELEASE), "can_reject": user_has_permission(request.user, quality.REJECT),
            "can_criteria": user_has_permission(request.user, RECIPES), "see_purchases": user_has_permission(request.user, SEE_PURCHASES), **extra}


def result_label(check, lang):
    return WORDS[lang][f"result_{check.result}"]


def result_class(check):
    return {"passed": "op-status-posted", "rejected": "op-status-cancelled"}.get(check.result, "op-status-draft")


def _check_row(check, lang):
    words = WORDS[lang]
    if check.order_id:
        what, source = check.order.recipe.product.item_name, check.order.number
    else:
        what, source = check.purchase_line.item.item_name, check.purchase_line.invoice.invoice_number
    return {"check": check, "what": what, "source": source, "result": result_label(check, lang), "result_class": result_class(check),
            "kind": words[f"kind_{check.kind}"], "state": quality.operation_state(check)}


def _criteria_inputs(criteria, lang, posted=None):
    """The criteria as the check form asks them: each with its limits in words and what was typed (after a refusal)."""

    posted = posted or {}
    return [{"criterion": criterion, "name": criterion.name(lang), "limits": quality.limits_text(criterion, lang), "field": f"crit_{criterion.pk}",
             "number": criterion.kind == CriterionKind.NUMBER, "value": posted.get(f"crit_{criterion.pk}", "")} for criterion in criteria]


def posted_results(request, criteria):
    return {criterion.pk: request.POST.get(f"crit_{criterion.pk}", "") for criterion in criteria}


def posted_criteria(request):
    return [{"name_ar": request.POST.get(f"crit_name_ar_{i}", ""), "name_en": request.POST.get(f"crit_name_en_{i}", ""),
             "kind": request.POST.get(f"crit_kind_{i}", ""), "min_value": request.POST.get(f"crit_min_{i}", ""), "max_value": request.POST.get(f"crit_max_{i}", "")}
            for i in range(quality.CRITERIA_ROWS)]


def _translated(exc, lang):
    from settings_core.ui_messages import translate

    return translate(" ".join(exc.messages), lang)


def _waiting_row(row, lang, see_purchases):
    subject = row["subject"]
    lot = row["lot"]
    if subject.order is not None:
        order = subject.order
        return {"kind": "order", "number": order.number, "url": reverse("manufacturing:quality_order", args=[order.pk]), "item": subject.item.item_name,
                "source_url": reverse("manufacturing:order", args=[order.pk]), "lot": lot, "waiting": row["position"]["waiting"],
                "since": order.finished_at.date() if order.finished_at else order.run.run_date, "party": ""}
    line = subject.line
    return {"kind": "purchase", "number": line.invoice.invoice_number, "url": reverse("manufacturing:quality_purchase_line", args=[line.pk]),
            "item": subject.item.item_name, "source_url": reverse("purchases:detail", args=[line.invoice_id]) if see_purchases else "", "lot": lot,
            "waiting": row["position"]["waiting"], "since": line.invoice.invoice_date, "party": line.invoice.supplier.name if see_purchases else ""}


@require_permission(VIEW)
@require_http_methods(["GET", "POST"])
def quality_screen(request):
    lang = _lang(request)
    words = WORDS[lang]
    if request.method == "POST":
        if not user_has_permission(request.user, quality.RELEASE):
            raise PermissionDenied("Setting up the quality hold place needs inventory.transfer_stock.")
        if request.POST.get("action") == "prepare":
            quality.current_hold_place(create=True)
            messages.success(request, words["prepared"])
        return redirect(f"{reverse('manufacturing:quality')}?lang={lang}")
    see_purchases = user_has_permission(request.user, SEE_PURCHASES)
    recent = (quality.scoped_checks().select_related("order__recipe__product", "purchase_line__item", "purchase_line__invoice", "checked_by",
                                                     "release_operation", "reject_operation")[:20])
    returns = [{"line": row["line"], "quantity": row["quantity"], "url": reverse("purchases:detail", args=[row["line"].invoice_id]) if see_purchases else "",
                "supplier": row["line"].invoice.supplier.name if see_purchases else ""} for row in quality.return_rows()]
    return render(request, "manufacturing/quality.html", _base(
        request, rows=[_waiting_row(row, lang, see_purchases) for row in quality.waiting_rows()], recent=[_check_row(check, lang) for check in recent],
        returns=returns, place=quality.current_hold_place()))


def _check_page(request, subject, criteria, *, heading, source_url, error, template_extra):
    lang = _lang(request)
    where = quality.position(subject)
    history = [_check_row(check, lang) for check in subject.checks().select_related(
        "order__recipe__product", "purchase_line__item", "purchase_line__invoice", "checked_by", "release_operation", "reject_operation")]
    posted = request.POST if request.method == "POST" else None
    return render(request, "manufacturing/quality_check.html", _base(
        request, subject=subject, position=where, criteria=_criteria_inputs(criteria, lang, posted), heading=heading, source_url=source_url,
        error=error, history=history, lot=quality.product_lot(subject), post=posted or {}, waiting_plain=quality.plain(where["waiting"]), today=timezone.localdate(), **template_extra))


def _decision(request, waiting):
    """(passed, rejected) from the form's three buttons: record what was typed, keep everything on hold, or reject it all."""

    decision = request.POST.get("decision")
    if decision == "hold":
        return "0", "0"
    if decision == "reject":
        return "0", str(waiting)
    return request.POST.get("passed", ""), request.POST.get("rejected", "")


@require_permission(VIEW)
@require_http_methods(["GET", "POST"])
def order_check(request, pk):
    """The check of a finished order's goods on hold."""

    lang = _lang(request)
    words = WORDS[lang]
    order = get_object_or_404(quality.scoped_orders().select_related("recipe__product", "location", "run__output_operation__destination_location"), pk=pk)
    subject = quality.order_subject(order)
    if subject is None:
        raise Http404("This order has nothing on hold.")
    criteria = subject.criteria()
    error = ""
    if request.method == "POST":
        if not user_has_permission(request.user, quality.RELEASE):
            raise PermissionDenied("Recording a quality check needs inventory.transfer_stock.")
        passed, rejected = _decision(request, quality.position(subject)["waiting"])
        try:
            check = quality.check_order(order, request.user, passed=passed, rejected=rejected, note=request.POST.get("note", ""),
                                        results=posted_results(request, criteria), lang=lang)
        except ValidationError as exc:
            error = _translated(exc, lang)
        else:
            messages.success(request, words["recorded"].format(number=check.number))
            return redirect(f"{reverse('manufacturing:quality_check', args=[check.pk])}?lang={lang}")
    return _check_page(request, subject, criteria, heading=f"{order.recipe.product.item_name} · {order.number}",
                       source_url=reverse("manufacturing:order", args=[order.pk]), error=error,
                       template_extra={"order": order, "hint": words["hint_final"].format(place=order.location), "destinations": [],
                                       "recipe_url": reverse("manufacturing:recipe", args=[order.recipe_id])})


@require_permission(VIEW)
@require_http_methods(["GET", "POST"])
def purchase_check(request, pk):
    """The incoming check of a purchase line received into the hold place, and that item's criteria."""

    lang = _lang(request)
    words = WORDS[lang]
    line = get_object_or_404(quality.scoped_lines().select_related("invoice__receiving_location", "invoice__supplier", "item"), pk=pk)
    subject = quality.purchase_subject(line)
    if subject is None:
        raise Http404("This purchase line is not in a quality hold place.")
    criteria = subject.criteria()
    error, criteria_error, criteria_posted = "", "", None
    if request.method == "POST":
        back = redirect(f"{reverse('manufacturing:quality_purchase_line', args=[line.pk])}?lang={lang}")
        if request.POST.get("action") == "criteria":
            try:
                quality.save_item_criteria(line.item, request.user, rows=posted_criteria(request), lang=lang)
            except ValidationError as exc:
                criteria_error, criteria_posted = _translated(exc, lang), posted_criteria(request)
            else:
                messages.success(request, words["criteria_saved"])
                return back
        else:
            if not user_has_permission(request.user, quality.RELEASE):
                raise PermissionDenied("Recording a quality check needs inventory.transfer_stock.")
            passed, rejected = _decision(request, quality.position(subject)["waiting"])
            destination = quality.destinations(subject.place).filter(pk=_id(request.POST.get("destination"))).first()
            try:
                check = quality.check_purchase_line(line, request.user, passed=passed, rejected=rejected, destination=destination,
                                                    note=request.POST.get("note", ""), results=posted_results(request, criteria), lang=lang)
            except ValidationError as exc:
                error = _translated(exc, lang)
            else:
                messages.success(request, words["recorded"].format(number=check.number))
                return redirect(f"{reverse('manufacturing:quality_check', args=[check.pk])}?lang={lang}")
    see_purchases = user_has_permission(request.user, SEE_PURCHASES)
    return _check_page(request, subject, criteria, heading=f"{line.item.item_name} · {line.invoice.invoice_number}",
                       source_url=reverse("purchases:detail", args=[line.invoice_id]) if see_purchases else "", error=error,
                       template_extra={"line": line, "hint": words["hint_incoming"], "destinations": list(quality.destinations(subject.place)),
                                       "criteria_error": criteria_error, "criteria_rows": quality.criteria_form_rows(criteria, criteria_posted),
                                       "criteria_kinds": criteria_kinds(lang), "criteria_action": "criteria"})


def criteria_kinds(lang):
    words = WORDS[lang]
    return [(CriterionKind.PASS_FAIL, words["pass_fail"]), (CriterionKind.NUMBER, words["number_kind"])]


# ---- one check and its certificate ----

def _when(moment, lang):
    from settings_core.templatetags.hesba_format import day_text

    local = timezone.localtime(moment)
    return f"{day_text(local.date(), lang)}, {local:%H:%M}" if lang == "en" else f"{day_text(local.date(), lang)}، {local:%H:%M}"


def certificate_card(check, user, lang):
    """The check's quality certificate: its files, whether this user may add or remove one, and the upload address."""

    from attachments import services as attachments

    words = WORDS[lang]
    can_change = bool(attachments.OWNERS[CHECK_OWNER]["can_attach"](user, check))
    items = [{"row": row, "url": f"{reverse('attachments:file', args=[row.pk])}?lang={lang}", "delete_url": f"{reverse('attachments:delete', args=[row.pk])}?lang={lang}",
              "is_image": row.content_type_name.startswith("image/"), "who": row.uploaded_by.get_full_name() or row.uploaded_by.get_username(),
              "when": _when(row.created_at, lang)} for row in attachments.attachments_for(check, user, lang)]
    return {"items": items, "can_change": can_change, "formats": words["cert_formats"].format(limit=attachments._human(attachments.max_bytes(), lang)),
            "upload_url": f"{reverse('manufacturing:quality_certificate', args=[check.pk])}?lang={lang}"}


def _scoped_check(pk):
    return get_object_or_404(quality.scoped_checks().select_related(
        "order__recipe__product", "order__location", "purchase_line__item", "purchase_line__invoice__supplier", "purchase_line__invoice__receiving_location",
        "checked_by", "release_operation", "reject_operation", "destination"), pk=pk)


@require_permission(VIEW)
def check_detail(request, pk):
    lang = _lang(request)
    check = _scoped_check(pk)
    see_purchases = user_has_permission(request.user, SEE_PURCHASES)
    if check.order_id:
        source_url = reverse("manufacturing:order", args=[check.order_id])
        form_url = reverse("manufacturing:quality_order", args=[check.order_id]) if check.kind == CheckKind.FINAL else ""
    else:
        source_url = reverse("purchases:detail", args=[check.purchase_line.invoice_id]) if see_purchases else ""
        form_url = reverse("manufacturing:quality_purchase_line", args=[check.purchase_line_id])
    results = [{"row": row, "name": row.name(lang), "limits": quality.limits_text(row, lang)} for row in check.results.all()]
    return render(request, "manufacturing/quality_check_detail.html", _base(
        request, check=check, row=_check_row(check, lang), results=results, source_url=source_url, form_url=form_url,
        att=certificate_card(check, request.user, lang), supplier=check.purchase_line.invoice.supplier.name if check.purchase_line_id and see_purchases else ""))


@csrf_exempt  # CSRF is checked inside, after the upload cap handler is installed (the attachments views' own rule)
@require_POST
def certificate_upload(request, pk):
    """The quality certificate of a check: a photo or a PDF. Fields: ``file``, ``kind`` (photo|document). Answers JSON, as every attachment upload."""

    from attachments import services as attachments, views as attachment_views

    lang = "en" if request.GET.get("lang") == "en" else "ar"  # not ``_lang``: reading POST here would close the upload handlers before the cap handler is installed
    try:
        attachments.get_owner(CHECK_OWNER, pk, request.user)
    except attachments.OwnerNotFound:
        return attachment_views._fail(lang, "not_found", 404)
    except PermissionDenied:
        return attachment_views._fail(lang, "denied", 403)
    return attachment_views._capped_upload(request, pk, CHECK_OWNER)


# ---- the recipe page and the order page ----

def recipe_quality_context(request, recipe, posted=None):
    lang = _lang(request)
    criteria = list(recipe.criteria.all())
    return {"qw": WORDS[lang], "plain_run_note": quality.MESSAGES[lang]["plain_run"], "quality_criteria": [{"criterion": c, "name": c.name(lang), "limits": quality.limits_text(c, lang)} for c in criteria],
            "criteria_rows": quality.criteria_form_rows(criteria, posted), "criteria_kinds": criteria_kinds(lang), "criteria_action": "quality",
            "can_criteria": user_has_permission(request.user, RECIPES)}


def recipe_quality_post(request, recipe, lang):
    return quality.save_recipe_quality(recipe, request.user, needs_release=request.POST.get("needs_release") == "1", rows=posted_criteria(request), lang=lang)


def order_quality_context(request, order, lang):
    """The order page's quality card and, at the quality stage, the criteria the advance form asks."""

    card = quality.order_card(order)
    posted = request.POST if request.method == "POST" and request.POST.get("action") == "advance" else None
    context = {"qw": WORDS[lang], "quality_card": card, "stage_criteria": _criteria_inputs(quality.stage_criteria(order), lang, posted),
               "can_check": user_has_permission(request.user, quality.RELEASE)}
    if card is not None:
        context["quality_checks"] = [{"check": check, "result": result_label(check, lang), "result_class": result_class(check),
                                      "kind": WORDS[lang][f"kind_{check.kind}"], "state": quality.operation_state(check)} for check in card["checks"]]
    return context


def advance_order(request, order, lang):
    """The order page's «خلّص المرحلة دي»: through ``quality.advance``, which records the criteria at the quality stage."""

    criteria = quality.stage_criteria(order)
    return quality.advance(order, request.user, results=posted_results(request, criteria), good=request.POST.get("good"), defect=request.POST.get("defect"),
                           note=request.POST.get("note", ""), minutes=request.POST.get("minutes"), lang=lang)

