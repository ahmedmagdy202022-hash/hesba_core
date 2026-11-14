"""E6 screens: the certificates list, issuing, the printed sheet, and revoking.

Every page and every POST here is for whoever manages students (``master_data.manage_parties``); the list shows grades, and a cashier
who may open student files may not see a mark. The order of the guards is the exam screens': not a training or language install (404),
then the record in the entity being worked in (404), then the permission (403). Nothing here moves money or sends a message; the
public page that answers the sheet's QR is ``education.public``.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from printing.company import company_details
from reports.navigation import display_name

from . import certificates, services
from .models import StudyGroup
from .views import _base, _lang

WORDS = {
    "ar": {"tab": "الشهادات", "title": "شهادات الإتمام",
           "intro": "شهادة مرقّمة للطالب اللي خلّص الكورس. بتتطبع A4 وعليها رمز يتأكد منه أي حد إنها صحيحة.",
           "issue": "إصدار شهادة", "back": "رجوع", "number": "رقم الشهادة", "student": "الطالب", "course": "الكورس", "group": "المجموعة", "issued": "تاريخ الإصدار",
           "grade": "التقدير", "status": "الحالة", "live": "سارية", "revoked": "ملغية", "hours": "الساعات", "attendance": "نسبة الحضور", "period": "الفترة",
           "issued_by": "أصدرها", "open": "افتح", "level": "المستوى", "search": "دوّر بالاسم أو الرقم", "find": "بحث", "all": "الكل", "no_certs": "لسه ماتصدرتش شهادات.",
           "ready_h": "جاهزين للشهادة", "ready_intro": "طلاب خلّصوا المجموعة (اتوقف تسجيلهم أو مدتها خلصت) ومعهمش شهادة سارية.",
           "no_ready": "مفيش طلاب جاهزين للشهادة دلوقتي.", "all_groups": "كل المجموعات", "show": "اعرض", "issue_btn": "إصدار الشهادة", "issue_row": "إصدار",
           "preview": "اللي هيتكتب على الشهادة", "held": "حصص اتعملت", "to": "إلى", "from": "من",
           "grade_label": "التقدير (اختياري)", "grade_hint": "بيظهر على الشهادة وعلى صفحة التحقق. لو للطالب درجة في الامتحان النهائي المنشور، بتتقترح هنا وتقدر تغيّرها.",
           "grade_from": "اتقترح من الامتحان النهائي المنشور.", "anyway": "أصدرها برضو", "warn_h": "تنبيه قبل الإصدار", "live_h": "للتسجيل ده شهادة سارية",
           "issued_done": "الشهادة اتصدرت.", "revoked_done": "الشهادة اتلغت.", "existing": "للتسجيل ده شهادة سارية بالفعل، دي هي.",
           "no_base": "عنوان الموقع (PUBLIC_BASE_URL) مش متظبط، فالشهادة هتتطبع من غير رمز التحقق. ظبّطه الأول.",
           "print": "اطبع", "pdf_hint": "تقدر تحفظها PDF من نافذة الطباعة.", "revoke_h": "إلغاء الشهادة", "revoke_reason": "سبب الإلغاء", "revoke_btn": "إلغاء الشهادة",
           "revoke_hint": "الشهادة بتفضل في السجل، وصفحة التحقق بتقول إنها ملغية من غير ما تذكر السبب.", "revoked_note": "الشهادة دي ملغية. لو اتطبعت تاني بيظهر عليها «ملغية».",
           "revoked_on": "اتلغت يوم", "reason": "السبب", "verify": "رابط التحقق", "verify_hint": "الرمز على الشهادة بيفتح الرابط ده.", "other_lang": "English",
           "next_step": "إصدار شهادة للطالب ده", "for_student": "الشهادة", "ready_count": "جاهزين للشهادة: {n}",
           "sheet_title": "شهادة إتمام", "lead": "تُمنح هذه الشهادة إلى", "for": "تقديرًا لإتمام", "sheet_hours": "عدد الساعات", "hours_unit": "ساعة",
           "sheet_number": "رقم الشهادة", "sheet_issued": "تاريخ الإصدار", "scan": "للتحقق من صحة الشهادة امسح الرمز", "sign": "اعتماد إدارة المركز", "void": "ملغية"},
    "en": {"tab": "Certificates", "title": "Completion certificates",
           "intro": "A numbered certificate for a student who finished the course. It prints on A4 with a code anyone can check.",
           "issue": "Issue a certificate", "back": "Back", "number": "Certificate number", "student": "Student", "course": "Course", "group": "Group", "issued": "Issued on",
           "grade": "Grade", "status": "Status", "live": "Valid", "revoked": "Revoked", "hours": "Hours", "attendance": "Attendance", "period": "Period",
           "issued_by": "Issued by", "open": "Open", "level": "Level", "search": "Search by name or number", "find": "Search", "all": "All", "no_certs": "No certificates issued yet.",
           "ready_h": "Ready for a certificate", "ready_intro": "Students who finished their group (the enrolment was stopped, or the term ended) and have no live certificate.",
           "no_ready": "No students are ready for a certificate right now.", "all_groups": "All groups", "show": "Show", "issue_btn": "Issue the certificate", "issue_row": "Issue",
           "preview": "What the certificate will say", "held": "Classes held", "to": "to", "from": "from",
           "grade_label": "Grade (optional)", "grade_hint": "Shown on the certificate and on the verify page. If the student has a mark on the group's published final exam, it is offered here and you can change it.",
           "grade_from": "Offered from the published final exam.", "anyway": "Issue it anyway", "warn_h": "Check before issuing", "live_h": "This enrolment already has a live certificate",
           "issued_done": "Certificate issued.", "revoked_done": "Certificate revoked.", "existing": "This enrolment already has a live certificate; here it is.",
           "no_base": "The site address (PUBLIC_BASE_URL) is not set, so the certificate will print without its verify code. Set it first.",
           "print": "Print", "pdf_hint": "In the print window you can save it as a PDF.", "revoke_h": "Revoke the certificate", "revoke_reason": "Reason for revoking", "revoke_btn": "Revoke the certificate",
           "revoke_hint": "The certificate stays in the record, and the verify page says it was revoked without giving the reason.", "revoked_note": "This certificate is revoked. If it is printed again it shows «Revoked».",
           "revoked_on": "Revoked on", "reason": "Reason", "verify": "Verify link", "verify_hint": "The code on the certificate opens this link.", "other_lang": "العربية",
           "next_step": "Issue a certificate for this student", "for_student": "Certificate", "ready_count": "Ready for a certificate: {n}",
           "sheet_title": "Certificate of Completion", "lead": "This certificate is awarded to", "for": "in recognition of completing", "sheet_hours": "Hours", "hours_unit": "hours",
           "sheet_number": "Certificate no.", "sheet_issued": "Issued on", "scan": "Scan the code to verify this certificate", "sign": "Centre management", "void": "REVOKED"},
}


def _install():
    if not certificates.enabled():
        raise Http404("Certificates are for training and language centres.")


def _permit(request, lang):
    if not certificates.can_manage(request.user):
        raise PermissionDenied(certificates.words(lang)["forbidden"])


def _guard(request):
    _install()
    _permit(request, _lang(request))


def _page(request, lang, **extra):
    return _base(request, lang, "certificates", cw=WORDS[lang], **extra)


def _certificate(request, pk):
    _install()
    certificate = get_object_or_404(certificates.scoped().select_related("enrollment__student", "enrollment__group__course", "enrollment__group__entity",
                                                                          "issued_by", "revoked_by"), pk=pk)
    _permit(request, _lang(request))
    return certificate


def _decorate(rows, lang):
    for row in rows:
        row.course_name, row.level = certificates.course_title(row)
        row.student = row.enrollment.student
    return rows


def certificate_list(request):
    _guard(request)
    lang = _lang(request)
    query, status = (request.GET.get("q") or "").strip(), request.GET.get("status") or ""
    rows = certificates.scoped().select_related("enrollment__student", "enrollment__group__course")
    if query:
        rows = rows.filter(Q(number__icontains=query) | Q(enrollment__student__name__icontains=query))
    if status in ("live", "revoked"):
        rows = rows.filter(revoked_at__isnull=(status == "live"))
    return render(request, "education/certificates.html", _page(request, lang, rows=_decorate(list(rows[:300]), lang), query=query, status=status,
                                                                  ready_count=len(certificates.ready(limit=100)), page_title=WORDS[lang]["title"]))


def certificate_issue(request):
    _install()
    lang = _lang(request)
    words = WORDS[lang]
    _permit(request, lang)
    raw = request.POST.get("enrollment") or request.GET.get("enrollment") or ""
    if not raw:
        groups = services.scoped(StudyGroup.objects).select_related("course").order_by("course__name", "name")
        chosen = groups.filter(pk=request.GET["group"]).first() if str(request.GET.get("group") or "").isdigit() else None
        return render(request, "education/certificate_issue.html", _page(request, lang, queue=certificates.ready(chosen), groups=groups, chosen=chosen,
                                                                           page_title=words["issue"]))
    enrollment = get_object_or_404(certificates.scoped_enrollments().select_related("student", "group__course"), pk=raw if str(raw).isdigit() else 0)
    error, confirm, typed = "", False, None
    if request.method == "POST":
        typed = request.POST.get("grade_text", "")
        try:
            certificate, created = certificates.issue(enrollment, request.user, grade_text=typed, acknowledged=request.POST.get("confirm") == "1", lang=lang)
        except certificates.NeedsConfirmation as exc:
            error, confirm = " ".join(exc.messages), True
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            (messages.success if created else messages.info)(request, words["issued_done"] if created else words["existing"])
            return redirect(f"{reverse('education:certificate', args=[certificate.pk])}?lang={lang}")
    info = certificates.preview(enrollment, request.user, lang)
    return render(request, "education/certificate_issue.html", _page(request, lang, enrollment=enrollment, info=info, error=error, confirm=confirm or bool(info["warnings"]),
                                                                       grade=typed if typed is not None else info["grade"], page_title=words["issue"],
                                                                       course=enrollment.group.course.name, level=enrollment.group.level))


def _person(user):
    """A person's display name, never the login name: a user with no name on the profile shows a dash."""
    name = display_name(user) if user is not None else ""
    return "" if name == user.get_username() else name


@require_http_methods(["GET", "HEAD"])
def certificate_print(request, pk):
    certificate = _certificate(request, pk)
    lang = _lang(request)
    words = WORDS[lang]
    enrollment = certificate.enrollment
    name, level = certificates.course_title(certificate)
    first, last = certificates.period(certificate)
    verify = certificates.verify_url(certificate, lang)
    other = "ar" if lang == "en" else "en"
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "cw": words, "page_title": f"{words['sheet_title']}: {enrollment.student.name}", "certificate": certificate,
               "student": enrollment.student, "group": enrollment.group, "course": name, "level": level, "first": first, "last": last, "verify_url": verify,
               "verify_path": certificates.verify_path(certificate), "company": company_details(), "business": certificates.business(certificate, lang),
               "issued_by_name": _person(certificate.issued_by),
               "back_url": f"{reverse('education:certificates')}?lang={lang}", "switch_url": f"{reverse('education:certificate', args=[certificate.pk])}?lang={other}"}
    return render(request, "education/certificate.html", context)


@require_POST
def certificate_revoke(request, pk):
    certificate = _certificate(request, pk)
    lang = _lang(request)
    target = f"{reverse('education:certificate', args=[certificate.pk])}?lang={lang}"
    try:
        certificates.revoke(certificate, request.POST.get("reason"), request.user, lang)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, WORDS[lang]["revoked_done"])
    return redirect(target)
