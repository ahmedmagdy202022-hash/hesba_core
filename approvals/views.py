"""FB-27: the approvals screen — what waits for you, what you asked for, and (for the owner) what needs approval."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import redirect, render

from .actions import ACTIONS, label
from .models import ApprovalRequest
from . import services

WORDS = {
    "ar": {"title": "الموافقات", "intro": "العمليات اللي صاحب الحساب اختار إنها محتاجة موافقة شخص تاني: اللي بيطلبها غير اللي بيوافق عليها، ومفيش حاجة بتتغير قبل الموافقة.",
           "waiting": "مستني موافقتك", "none_waiting": "مفيش طلبات مستنياك.", "mine": "طلباتك", "none_mine": "مبعتّش طلبات.",
           "action": "العملية", "document": "المستند", "reason": "السبب", "by": "طلبها", "at": "وقت الطلب", "state": "الحالة", "note": "ملاحظة",
           "approve": "وافق ونفّذ", "reject": "ارفض", "withdraw": "اسحب الطلب", "reject_note": "سبب الرفض (اختياري)",
           "settings": "إيه اللي محتاج موافقة", "settings_intro": "علّم العمليات اللي محتاجة موافقة. المالك والمدير (أو أي دور معاه صلاحية الموافقة) بيعملوها على طول؛ الباقي بيبعت طلب.",
           "save": "احفظ", "saved": "اتحفظ اللي محتاج موافقة.", "done": "اتوافق واتنفّذ.", "failed": "اتوافق بس ماتنفّذش: {reason}",
           "rejected": "اترفض الطلب.", "withdrawn": "اتسحب الطلب.", "owner_only": "اختيار العمليات اللي محتاجة موافقة لصاحب الحساب بس.",
           "approval_not_waiting": "الطلب ده مش مستني موافقة دلوقتي.", "approval_own_request": "مينفعش توافق على طلبك؛ لازم حد تاني.",
           "open": "افتح المستند",
           "status": {"pending": "مستني", "done": "اتوافق واتنفّذ", "rejected": "اترفض", "failed": "اتوافق وماتنفّذش", "withdrawn": "اتسحب"}},
    "en": {"title": "Approvals", "intro": "The actions the account owner chose to need a second person: the one who asks is never the one who approves, and nothing changes before the approval.",
           "waiting": "Waiting for you", "none_waiting": "Nothing is waiting for you.", "mine": "Your requests", "none_mine": "You have not sent any requests.",
           "action": "Action", "document": "Document", "reason": "Reason", "by": "Asked by", "at": "Asked at", "state": "State", "note": "Note",
           "approve": "Approve and do it", "reject": "Reject", "withdraw": "Withdraw", "reject_note": "Why (optional)",
           "settings": "What needs approval", "settings_intro": "Tick the actions that need approval. The owner and the manager (or any role allowed to approve) do them directly; everyone else sends a request.",
           "save": "Save", "saved": "Saved what needs approval.", "done": "Approved and done.", "failed": "Approved, but it could not be done: {reason}",
           "rejected": "Request rejected.", "withdrawn": "Request withdrawn.", "owner_only": "Only the account owner chooses what needs approval.",
           "approval_not_waiting": "This request is not waiting for approval now.", "approval_own_request": "You cannot approve your own request; someone else must.",
           "open": "Open the document",
           "status": {"pending": "Waiting", "done": "Approved and done", "rejected": "Rejected", "failed": "Approved, not done", "withdrawn": "Withdrawn"}},
}


def _row(request, lang, words):
    return {"request": request, "action": label(request.action, lang), "url": ACTIONS[request.action].url(request.object_id) if request.action in ACTIONS else "",
            "reason": request.data.get("reason", ""), "status": words["status"].get(request.status, request.status)}


def _visible(approval):
    action = ACTIONS.get(approval.action)
    return action is not None and action.visible(approval.object_id)


@login_required
def inbox(request):
    from sales.short_sales import is_owner

    lang = "en" if (request.POST.get("lang") or request.GET.get("lang")) == "en" else "ar"
    words = WORDS[lang]
    owner = is_owner(request.user)
    if request.method == "POST":
        what = request.POST.get("do", "")
        if what == "settings":
            if not owner:
                raise PermissionDenied(words["owner_only"])
            services.set_held_actions(request.POST.getlist("actions"), request.user)
            messages.success(request, words["saved"])
            return redirect(f"/approvals/?lang={lang}")
        raw = request.POST.get("request", "")
        approval = ApprovalRequest.objects.filter(pk=raw).first() if raw.isdigit() else None
        if approval is None:
            raise Http404
        try:
            if what == "withdraw":
                services.withdraw(approval.pk, request.user)
                messages.success(request, words["withdrawn"])
            elif what in ("approve", "reject"):
                if not _visible(approval):  # another entity's document stays out of reach
                    raise Http404
                if what == "approve":
                    approval = services.approve(approval.pk, request.user)
                    if approval.status == "done":
                        messages.success(request, words["done"])
                    else:
                        from settings_core.ui_messages import translate

                        messages.error(request, words["failed"].format(reason=translate(approval.note, lang)))
                else:
                    services.reject(approval.pk, request.user, request.POST.get("note", ""))
                    messages.success(request, words["rejected"])
        except ValidationError as exc:
            messages.error(request, "; ".join(words.get(message, message) for message in exc.messages))
        return redirect(f"/approvals/?lang={lang}")
    waiting = [_row(item, lang, words) for item in services.waiting_for(request.user).select_related("requested_by")[:200] if _visible(item)]
    mine = [_row(item, lang, words) for item in ApprovalRequest.objects.filter(requested_by=request.user).select_related("decided_by")[:50]]
    held = services.held_actions()
    choices = [{"code": code, "label": label(code, lang), "on": code in held} for code in ACTIONS]
    return render(request, "approvals/inbox.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["title"], "section": "settings",
        "waiting": waiting, "mine": mine, "can_approve": services.can_approve(request.user), "owner": owner, "choices": choices,
    })
