"""Contracting C10 (HG-135): the owner's (or the consultant's) page behind a signed project link.

The office makes the link (``make_link``) from the project page and sends it. The owner opens it on a phone with no account
and reads, from the project's own figure functions and nothing else:

* the contract: original value, approved variation orders, revised value (``variations.figures``);
* certified to date, retention held, the advance left, due now and under collection (``contract.position``);
* each POSTED certificate with its statement: the lines (``contract.certificate_rows``), this certificate's deductions and
  the deductions to date (``contract.to_date``), printable on its own (``?c=<number>``);
* the schedule's progress (``schedule.snapshot``) when the project has one;
* the variation orders: the approved ones, and the drafts waiting for the owner, each of which the owner may approve.

It never shows cost, budget, subcontractors, profit, purchases, notes, staff names or any draft certificate. The daily report
photos (C2b) are not shown: a per-project switch would need a column, and the photos would need a public file route.

The link is a ``printing.share`` token of kind ``project`` bound to the project's customer (``o``). Every request reads it
again and refuses (the one expired page, the same status) a forged, altered or expired token, a token with no customer, a
token whose customer is no longer the project's, a cancelled project, or the Projects module switched off. Throttled per
address (page loads) and per address and per token (approvals) in Django's default cache, as the booking, payment and lab
pages are. No caching, no indexing and no referrer leave the page.

The owner's approval goes through ``variations.approve_by_owner``: the same approval core as the office's, refused when the
order changed since the page was shown (its fingerprint, compared after the order row is locked). It moves no money.
"""

import hashlib

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST

from audit.models import AuditEventType, AuditLog
from permissions.services import user_has_permission

from . import access, contract, schedule, variations
from .models import Project, ProjectStatus, VariationStatus
from .services import OPEN

KIND = "project"
VIEW = "sales.view_sales_invoices"  # the right the certificates' print and the contract figures need

# (name, limit, window in seconds), per process as the other public pages
PAGE_MINUTE = ("page", 60, 60)  # any page load, per address: blunts a flood
APPROVE_ADDRESS = ("approve_ip", 30, 3600)  # approvals per address per hour (generous: many visitors can share an address behind the proxy)
APPROVE_TOKEN = ("approve_token", 10, 3600)  # approvals per link per hour

WORDS = {
    "ar": {
        "sub": "متابعة المشروع", "eyebrow": "مشروع", "site": "الموقع", "owner": "صاحب المشروع", "start": "البداية", "end": "النهاية المخططة",
        "contract_h": "العقد", "original": "قيمة العقد الأصلية", "variations": "أوامر التغيير المعتمدة", "revised": "قيمة العقد بعد التعديل",
        "money_h": "الموقف المالي", "certified": "قيمة الأعمال المعتمدة لحد النهارده", "of_contract": "من قيمة العقد", "retention": "ضمان الأعمال المحجوز",
        "advance_left": "الدفعة المقدمة اللي لسه ما اتخصمتش", "due_now": "المستحق دلوقتي", "under_collection": "تحت التحصيل (شيكات)",
        "certs_h": "المستخلصات", "no_certs": "لسه مفيش مستخلصات معتمدة.", "cert_n": "مستخلص رقم {n}", "date": "التاريخ", "invoice": "الفاتورة",
        "net": "الصافي المستحق", "show": "عرض المستخلص", "print_one": "نسخة للطباعة",
        "code": "البند", "description": "الوصف", "unit": "الوحدة", "rate": "الفئة", "previous": "السابق", "current": "الحالي", "cumulative": "الإجمالي",
        "current_value": "قيمة الحالي", "cumulative_value": "القيمة الإجمالية",
        "this_cert": "المستخلص ده", "to_date": "لحد المستخلص ده", "gross": "قيمة الأعمال", "tax": "الضريبة", "invoice_total": "إجمالي الفاتورة",
        "ret_line": "ضمان الأعمال ({rate}%)", "recovery": "استرداد الدفعة المقدمة", "deductions_h": "الخصومات",
        "schedule_h": "الجدول الزمني", "planned": "المخطط لحد النهارده", "actual": "المنفّذ", "planned_end": "النهاية المخططة بعد التمديد",
        "task": "البند", "task_actual": "المنفّذ", "task_planned": "المخطط",
        "vo_h": "أوامر التغيير", "vo_n": "أمر تغيير رقم {n}", "vo_waiting": "في انتظار موافقة حضرتك", "vo_approved": "معتمد",
        "vo_value": "قيمة الأمر", "vo_days": "تمديد المدة", "days_n": "{n} يوم", "vo_reason": "سبب التغيير", "kind_change": "تعديل كمية", "kind_new": "بند جديد",
        "vo_change": "التغيير في الكمية", "vo_by_owner": "اعتمده", "vo_approved_on": "تاريخ الاعتماد",
        "approve_h": "اعتماد أمر التغيير", "approve_p": "اكتب اسم حضرتك، وبعدين اضغط «اعتماد الأمر». الاعتماد بيتسجّل باسمك وتاريخه، وأي تعديل بعد كده بيكون بأمر تغيير تاني.",
        "name": "اسم حضرتك", "approve": "اعتماد الأمر", "approved_ok": "اتسجّل اعتماد حضرتك لأمر التغيير رقم {n}.",
        "not_waiting": "أمر التغيير ده مبقاش في انتظار الاعتماد.", "cannot": "مينفعش يتسجّل الاعتماد من هنا دلوقتي. تواصل معانا.",
        "busy": "محاولات كتير متتالية. جرّب تاني بعد شوية، أو تواصل معانا.",
        "print": "اطبع أو احفظ PDF", "back": "رجوع للمشروع",
        "sign_engineer": "مهندس الموقع", "sign_owner": "الاستشاري / المالك", "sign_contractor": "المقاول",
        # the office's side
        "no_base_url": "عنوان الموقع مش متظبط (PUBLIC_BASE_URL)؛ ظبّطه الأول عشان الرابط يتبعت.",
        "denied": "رابط المالك لصاحب صلاحية الفواتير بس، لأن الرابط بيقدر يعتمد أوامر التغيير باسم المالك.",
        "cancelled": "المشروع ده ملغي؛ مينفعش يتعمل له رابط.",
        "wa_text": "أهلاً، ده رابط متابعة مشروع {project} من {company}: العقد والمستخلصات والجدول الزمني. الرابط صالح لمدة {days} يوم: {url}",
    },
    "en": {
        "sub": "Project follow-up", "eyebrow": "Project", "site": "Site", "owner": "Project owner", "start": "Start", "end": "Planned end",
        "contract_h": "The contract", "original": "Original contract value", "variations": "Approved variation orders", "revised": "Revised contract value",
        "money_h": "The account", "certified": "Work certified to date", "of_contract": "of the contract", "retention": "Retention held",
        "advance_left": "Advance not yet recovered", "due_now": "Due now", "under_collection": "Under collection (cheques)",
        "certs_h": "Certificates", "no_certs": "No posted certificate yet.", "cert_n": "Certificate {n}", "date": "Date", "invoice": "Invoice",
        "net": "Net payable", "show": "Show the certificate", "print_one": "Printable copy",
        "code": "Item", "description": "Description", "unit": "Unit", "rate": "Rate", "previous": "Previous", "current": "This", "cumulative": "Cumulative",
        "current_value": "This value", "cumulative_value": "Cumulative value",
        "this_cert": "This certificate", "to_date": "To date", "gross": "Work value", "tax": "VAT", "invoice_total": "Invoice total",
        "ret_line": "Retention ({rate}%)", "recovery": "Advance recovered", "deductions_h": "Deductions",
        "schedule_h": "The schedule", "planned": "Planned to date", "actual": "Done", "planned_end": "Planned end with extensions",
        "task": "Task", "task_actual": "Done", "task_planned": "Planned",
        "vo_h": "Variation orders", "vo_n": "Variation order {n}", "vo_waiting": "Waiting for your approval", "vo_approved": "Approved",
        "vo_value": "Order value", "vo_days": "Extension of time", "days_n": "{n} days", "vo_reason": "Reason for the change", "kind_change": "Quantity change", "kind_new": "New item",
        "vo_change": "Change in quantity", "vo_by_owner": "Approved by", "vo_approved_on": "Approved on",
        "approve_h": "Approve the variation order", "approve_p": "Type your name, then tap «Approve the order». The approval is recorded with your name and the date; any later change is another variation order.",
        "name": "Your name", "approve": "Approve the order", "approved_ok": "Your approval of variation order {n} is recorded.",
        "not_waiting": "This variation order is no longer waiting for approval.", "cannot": "The approval cannot be recorded from here right now. Please contact us.",
        "busy": "Too many tries. Please wait a while and try again, or contact us.",
        "print": "Print or save as PDF", "back": "Back to the project",
        "sign_engineer": "Site engineer", "sign_owner": "Consultant / owner", "sign_contractor": "Contractor",
        "no_base_url": "The site address (PUBLIC_BASE_URL) is not set; set it first so the link can be sent.",
        "denied": "Only someone who may create invoices can make the owner's link, because the link can approve variation orders as the owner.",
        "cancelled": "This project is cancelled; no link can be made for it.",
        "wa_text": "Hello, here is the follow-up link of project {project} from {company}: the contract, the certificates and the schedule. The link works for {days} days: {url}",
    },
}


def _words(lang):
    return WORDS["en" if lang == "en" else "ar"]


# ---- the office's side: making the link ----

def can_share(user):
    """Who may make the owner's link: whoever may read the contract and the certificates (``sales.view_sales_invoices``) AND
    approve a variation order on the staff side (``variations.can_decide``). The link approves variation orders as the owner, so
    a view-only user must not be able to make one and approve through it (S2's rule for its quote link, HG-073)."""

    return user_has_permission(user, VIEW) and variations.can_decide(user)


def make_link(project, user, lang="ar"):
    """{url, whatsapp, days}: the owner's link, built from PUBLIC_BASE_URL only (never the request's host) and bound to the project's customer."""

    from django.conf import settings

    from printing.company import company_details
    from printing.share import public_url, whatsapp_link

    words = _words(lang)
    if not can_share(user):
        raise PermissionDenied(words["denied"])
    if project.status == ProjectStatus.CANCELLED:
        raise ValidationError(words["cancelled"])
    url = public_url(KIND, project.pk, lang, owner=project.customer_id)
    if url is None:
        raise ValidationError(words["no_base_url"])
    days = getattr(settings, "SHARE_LINK_DAYS", 60)
    customer = project.customer
    text = words["wa_text"].format(project=project.name, company=company_details()["name"], days=days, url=url)
    AuditLog.objects.create(event_type=AuditEventType.EXPORT, actor=user, module="projects", action="share_owner_link", object_type="projects.Project",
                            object_id=str(project.pk), after_data={"project": project.code, "customer": project.customer_id, "days": days})
    return {"url": url, "whatsapp": whatsapp_link(customer.whatsapp or customer.phone, text), "days": days}


# ---- throttling (the booking, payment and lab pages' pattern: Django's default cache, per process) ----

def _address(request):
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def _digest(token):
    return hashlib.sha256(str(token).encode()).hexdigest()


def _key(bucket, who):
    name, limit, window = bucket
    return f"projects-owner:{name}:{who}:{int(timezone.now().timestamp()) // window}"


def _count(bucket, who):
    """Take one slot; the new count (atomic in the cache, so two requests never read the same number)."""

    key = _key(bucket, who)
    if cache.add(key, 1, bucket[2] * 2):
        return 1
    try:
        return cache.incr(key)
    except ValueError:  # expired between the two calls
        cache.add(key, 1, bucket[2] * 2)
        return 1


def _over(bucket, who):
    return _count(bucket, who) > bucket[1]


# ---- reading the link ----

def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _private(response):
    """No caching, no indexing and no referrer: the address itself is the owner's key."""

    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "same-origin"  # as S2: the stricter "no referrer" policy makes browsers post "Origin: null" (CSRF 403)
    return response


def _expired(request, lang):
    from printing.share import expired_page

    return _private(expired_page(request, lang))


def _busy(request, lang):
    response = _expired(request, lang)
    response.status_code = 429
    return response


def target(token):
    """The project the link opens, or None for anything that must show nothing: a forged, altered or expired token, another
    kind, no customer bound, a customer who is no longer the project's, a cancelled project, or the module switched off."""

    from printing.share import read_token_data

    data = read_token_data(token)
    if data is None or data["k"] != KIND or not isinstance(data.get("o"), int):
        return None
    project = Project.objects.select_related("customer").filter(pk=data["id"]).first()
    if project is None or project.customer_id != data["o"] or project.status == ProjectStatus.CANCELLED or not access.module_open():
        return None
    return project


def _entity(project):
    """The entity holding the project's money (None before any, or on a single-entity install): the page reads as if working
    there, so the figures are the project's own entity's, never another's."""

    from entities.models import Entity
    from entities.services import is_multi_entity

    if not is_multi_entity():
        return None
    pk = contract.project_entity(project)
    return Entity.objects.filter(pk=pk).first() if pk else None


def _token_of(request):
    return request.resolver_match.kwargs.get("token", "") if request.resolver_match else ""


# ---- the page ----

def _company(lang):
    from printing.company import company_details
    from reports.aging import whatsapp_number

    company = company_details()
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "company": company, "phone": company["phone"],
            "whatsapp": whatsapp_number(company["phone"]), "pw": _words(lang)}


def _posted(project):
    return contract.live_certificates(project).filter(invoice__status="posted").select_related("invoice").prefetch_related("invoice__returns").order_by("number")


def _statement(certificate, lang, to_date=None):
    """One posted certificate as its printed statement shows it, plus the deductions to date."""

    words = _words(lang)
    invoice = certificate.invoice
    return {"certificate": certificate, "invoice": invoice, "lines": contract.certificate_rows(certificate), "net": contract.net_payable(certificate),
            "to_date": contract.to_date(certificate) if to_date is None else to_date, "title": words["cert_n"].format(n=certificate.number),
            "ret_label": words["ret_line"].format(rate=format(certificate.retention_rate.normalize(), "f"))}


def _orders(project, lang):
    """The variation orders the owner sees: the approved ones and the drafts waiting (never a rejected or cancelled one)."""

    from .views_variations import signed_money, signed_qty

    words = _words(lang)
    rows = []
    for order in project.variation_orders.filter(status__in=(VariationStatus.APPROVED, VariationStatus.DRAFT)).select_related("decided_by").order_by("number"):
        loaded = list(order.lines.select_related("boq_line"))  # one reading: what the page shows is what the fingerprint seals
        lines = []
        for row in variations.line_rows(order, loaded):
            line = row["line"]
            lines.append({"line": line, "kind": words["kind_new" if row["new"] else "kind_change"], "change_text": signed_qty(line.quantity_change),
                          "rate": row["rate"], "amount_text": signed_money(row["amount"])})
        draft = order.status == VariationStatus.DRAFT
        rows.append({"order": order, "title": words["vo_n"].format(n=order.number), "draft": draft, "lines": lines,
                     "value_text": signed_money(variations.order_value(order, loaded)),
                     "days_text": words["days_n"].format(n=order.extension_days) if order.extension_days else "",
                     "fingerprint": variations.owner_fingerprint(order, loaded) if draft else "",
                     "by_owner": order.decided_by is not None and order.decided_by.username == variations.OWNER_LINK_USERNAME})
    return rows


def _context(request, token, project, lang, message="", message_ok=False):
    here = reverse("share_document", args=[token])
    other = "ar" if lang == "en" else "en"
    figures = variations.figures(project)
    position = contract.position(project)
    posted = list(_posted(project))
    running = contract.to_date_running(posted)
    data = {**_company(lang), "project": project, "switch_url": f"{here}?lang={other}", "here": f"{here}?lang={lang}", "message": message,
            "message_ok": message_ok, "contract": {"original": figures["original"], "approved": figures["approved"], "revised": figures["revised"]},
            "position": position, "statements": [_statement(c, lang, running[c.pk]) for c in posted], "orders": _orders(project, lang),
            "can_approve": project.status in OPEN, "planned_end": figures["planned_end"], "token": token}
    data["waiting"] = [row for row in data["orders"] if row["draft"]]
    data["approved_orders"] = [row for row in data["orders"] if not row["draft"]]
    if schedule.has_tasks(project):
        data["schedule"] = _schedule(project)
    return data


def _schedule(project):
    """The schedule's progress as the owner reads it: planned and done to date, the planned end, and each task's two percents."""

    from .views_schedule import _pct

    snap = schedule.snapshot(project, timezone.localdate())
    return {"planned": _pct(snap["planned"]), "actual": _pct(snap["actual"]), "planned_end": snap["planned_end"],
            "rows": [{"name": row["task"].name, "actual": _pct(row["actual"]), "planned": _pct(row["planned"])} for row in snap["rows"]]}


def _render(request, token, project, lang, message="", message_ok=False, status=200):
    from entities.current import working_in

    with working_in(_entity(project)):
        context = _context(request, token, project, lang, message, message_ok)
        return _private(render(request, "projects/owner_portal.html", context, status=status))


def _print(request, token, project, lang, number):
    from entities.current import working_in

    with working_in(_entity(project)):
        certificate = _posted(project).filter(number=number).first() if str(number).isdigit() else None
        if certificate is None:
            return _expired(request, lang)  # a draft, a cancelled, another project's or no certificate: nothing is shown
        here = reverse("share_document", args=[token])
        context = {**_company(lang), "project": project, "s": _statement(certificate, lang), "back_url": f"{here}?lang={lang}",
                   "switch_url": f"{here}?lang={'ar' if lang == 'en' else 'en'}&c={certificate.number}"}
        return _private(render(request, "projects/owner_certificate.html", context))


def page(request, pk):
    """GET (called by the share dispatcher for a ``project`` token): the owner's page, or one certificate's printable
    statement with ``?c=<number>``. Every failure is the one expired page."""

    lang = _lang(request)
    if request.method != "GET":
        return _private(HttpResponseNotAllowed(["GET"]))
    if _over(PAGE_MINUTE, _address(request)):
        return _busy(request, lang)
    token = _token_of(request)
    project = target(token)
    if project is None or project.pk != pk:
        return _expired(request, lang)
    if request.GET.get("c"):
        return _print(request, token, project, lang, request.GET.get("c"))
    message = ""
    done = request.GET.get("done", "")
    if done.isdigit():
        order = project.variation_orders.filter(pk=done, status=VariationStatus.APPROVED).first()
        message = _words(lang)["approved_ok"].format(n=order.number) if order is not None else ""
    return _render(request, token, project, lang, message, message_ok=bool(message))


@require_POST
@csrf_protect
def approve(request, token, oid):
    """POST: the owner approves one draft variation order of the link's project, with the typed name and the fingerprint the
    page showed. A refused approval shows the page again with an owner's message (never a staff one)."""

    from entities.current import working_in

    lang = _lang(request)
    words = _words(lang)
    if _over(APPROVE_ADDRESS, _address(request)):
        return _busy(request, lang)
    project = target(token)
    if project is None:
        return _expired(request, lang)
    if _over(APPROVE_TOKEN, _digest(token)):
        return _busy(request, lang)
    order = project.variation_orders.filter(pk=oid).exclude(status__in=(VariationStatus.REJECTED, VariationStatus.CANCELLED)).first()
    if order is None:
        return _expired(request, lang)  # another project's order, or one the owner never sees
    if order.status != VariationStatus.DRAFT:
        return _render(request, token, project, lang, words["not_waiting"], status=409)
    try:
        with working_in(_entity(project)):
            variations.approve_by_owner(project, order, name=request.POST.get("name"), fingerprint=request.POST.get("fingerprint"), lang=lang)
    except ValidationError as error:
        code = getattr(error, "code", None)
        if code in ("owner_changed", "owner_name"):
            message = error.messages[0]
        elif code == "not_draft":
            message = words["not_waiting"]
        else:
            message = words["cannot"]  # the office's wording (permissions, signed copies, support) never reaches the owner
        return _render(request, token, project, lang, message, status=409)
    return _private(HttpResponseRedirect(f"{reverse('share_document', args=[token])}?lang={lang}&done={order.pk}", status=303))
