"""E9 (HG-104) screens: a group's shared content (recorded videos, files, the address of an online class) and the three one-tap changes.

Whoever may take the group's attendance (a manager, or the group's own teacher) sees and changes a group's content, the same people as the
exam screens, and no new permission exists. The order of the guards is the exam screens': not an education install (404), then the record
in the entity being worked in (404), then the permission (403). Hiding, showing and removing are POST only (a GET is a 405), CSRF
protected, and every change goes through ``education.content``, which owns the link rules. Nothing here sends a message or stores a file.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import content, services
from .models import ContentKind, StudyGroup
from .views import _base, _lang

WORDS = {
    "ar": {"content": "محتوى المجموعة", "add": "ضيف محتوى", "all": "كل المحتوى", "none": "لسه مفيش محتوى للمجموعة دي.", "title": "العنوان", "kind": "النوع",
           "url": "الرابط", "url_hint": "رابط آمن يبدأ بـ https من يوتيوب أو درايف أو زوم أو ميت أو أي موقع تاني.", "note": "ملاحظة (اختياري)", "starts_at": "ميعاد الحصة",
           "starts_hint": "ميعاد بداية الحصة الأونلاين. بتختفي من عند ولي الأمر بعد يوم من ميعادها.", "save": "احفظ", "saved": "المحتوى اتحفظ.",
           "edit": "تعديل", "hide": "إخفاء", "show": "إظهار", "remove": "إزالة", "confirm_remove": "تأكيد الإزالة", "hidden_ok": "المحتوى اتخفى عن أولياء الأمور.",
           "shown_ok": "المحتوى ظهر لأولياء الأمور.", "removed_ok": "المحتوى اتشال.", "visible": "ظاهر لأولياء الأمور", "hidden": "مخفي",
           "open": "افتح الرابط", "when": "الميعاد", "status": "الحالة", "back_group": "رجوع للمجموعة", "back_content": "رجوع للمحتوى", "new_title": "محتوى جديد",
           "edit_title": "تعديل المحتوى", "hint": "المركز بيحفظ العنوان والرابط بس، مش الملفات ولا الفيديوهات. شارك الرابط على إنه «غير مدرج» أو «لأشخاص محددين» "
                                                   "عشان محدش غير المجموعة يفتحه. ولي الأمر بيشوف المنشور بس، لأبنائه المسجّلين في المجموعة.",
           "kinds": {"video": "فيديو مسجّل", "file": "ملف", "online": "حصة أونلاين"}},
    "en": {"content": "Group content", "add": "Add content", "all": "All content", "none": "No content for this group yet.", "title": "Title", "kind": "Kind",
           "url": "Link", "url_hint": "An https:// link from YouTube, Drive, Zoom, Meet or any other site.", "note": "Note (optional)", "starts_at": "Class time",
           "starts_hint": "When the online class starts. It disappears for the guardian a day after that time.", "save": "Save", "saved": "Content saved.",
           "edit": "Edit", "hide": "Hide", "show": "Show", "remove": "Remove", "confirm_remove": "Confirm removal", "hidden_ok": "The content is hidden from guardians.",
           "shown_ok": "The content is now shown to guardians.", "removed_ok": "The content was removed.", "visible": "Shown to guardians", "hidden": "Hidden",
           "open": "Open the link", "when": "When", "status": "Status", "back_group": "Back to the group", "back_content": "Back to the content", "new_title": "New content",
           "edit_title": "Edit the content", "hint": "The centre keeps only the title and the link, not the files or the videos. Share the link as “unlisted” or “restricted to "
                                                    "specific people” so that only the group can open it. A guardian sees what is published only, for their own children's groups.",
           "kinds": {"video": "Recorded video", "file": "File", "online": "Online class"}},
}


def _install():
    if not services.is_education_install():
        raise Http404("Group content is for education activities.")


def _guard_group(request, pk):
    _install()
    group = get_object_or_404(services.scoped(StudyGroup.objects).select_related("course", "teacher"), pk=pk)
    if not content.can_edit(request.user, group):
        raise PermissionDenied(content.MESSAGES["en"]["forbidden"])
    return group


def _guard_item(request, pk, live_only=False):
    _install()
    rows = content.scoped_content().select_related("group__course", "group__teacher")
    item = get_object_or_404(rows.filter(active=True) if live_only else rows, pk=pk)
    if not content.can_edit(request.user, item.group):
        raise PermissionDenied(content.MESSAGES["en"]["forbidden"])
    return item


def _page(request, lang, **extra):
    return _base(request, lang, "groups", cw=WORDS[lang], kinds=[(value, WORDS[lang]["kinds"][value]) for value in ContentKind.values], **extra)


def _labelled(rows, lang):
    for row in rows:
        row.kind_label = WORDS[lang]["kinds"][row.kind]
        row.when, row.time = content.local_parts(row.starts_at)  # an online class shows its own day and time; any other row, the day it was added
        if row.when is None:
            row.when = timezone.localtime(row.created_at).date()
    return rows


def group_card(request, group, lang):
    """The context the group page adds for its content card: nothing at all (no card) unless this user may change this group's content."""

    if not content.can_edit(request.user, group):
        return {}
    return {"cw": WORDS[lang], "content_rows": _labelled(content.group_items(group), lang), "content_card": True}


def _back(group, lang):
    return redirect(f"{reverse('education:content', args=[group.pk])}?lang={lang}")


def group_content(request, pk):
    group = _guard_group(request, pk)
    lang = _lang(request)
    return render(request, "education/content.html", _page(request, lang, group=group, content_rows=_labelled(content.group_items(group), lang),
                                                           page_title=WORDS[lang]["content"], content_card=True))


def _form(request, group, item, lang):
    error = ""
    if request.method == "POST":
        try:
            content.save_content(group, request.POST, request.user, item, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, WORDS[lang]["saved"])
            return _back(group, lang)
    words = WORDS[lang]
    starts = ""
    if item is not None and item.starts_at:
        starts = timezone.localtime(item.starts_at).strftime("%Y-%m-%dT%H:%M")
    return render(request, "education/content_form.html", _page(request, lang, group=group, item=item, post=request.POST if error else None, error=error, starts_value=starts,
                                                                page_title=words["edit_title"] if item else words["new_title"]))


def content_new(request, pk):
    group = _guard_group(request, pk)
    return _form(request, group, None, _lang(request))


def content_edit(request, pk):
    item = _guard_item(request, pk, live_only=True)
    return _form(request, item.group, item, _lang(request))


def _change(request, pk, do, done):
    item = _guard_item(request, pk)
    lang = _lang(request)
    try:
        do(item, request.user, lang)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, WORDS[lang][done])
    return _back(item.group, lang)


@require_POST
def content_hide(request, pk):
    return _change(request, pk, lambda item, user, lang: content.set_published(item, False, user, lang), "hidden_ok")


@require_POST
def content_show(request, pk):
    return _change(request, pk, lambda item, user, lang: content.set_published(item, True, user, lang), "shown_ok")


@require_POST
def content_remove(request, pk):
    return _change(request, pk, content.remove, "removed_ok")
