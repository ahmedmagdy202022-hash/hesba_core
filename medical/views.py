"""MED-001 screens: patients, the patient's file, a visit, the prescription."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer

from . import services
from .models import BloodType, ClinicalVisit, Gender

WORDS = {
    "ar": {"title": "الملفات الطبية", "intro": "ملف لكل مريض: بياناته، وحساسيته وأمراضه المزمنة، وكل كشف بالشكوى والتشخيص والعلاج.",
           "search": "دوّر بالاسم أو التليفون أو رقم الملف", "find": "بحث", "file_no": "رقم الملف", "patient": "المريض", "phone": "التليفون",
           "age": "السن", "last_visit": "آخر كشف", "open": "افتح الملف", "none": "مفيش مرضى لسه. المريض بيتسجل من «المرضى» أو وقت الحجز.",
           "years": "سنة", "back": "الملفات الطبية", "profile": "بيانات المريض", "edit_profile": "تعديل البيانات", "save": "حفظ",
           "dob": "تاريخ الميلاد", "gender": "النوع", "blood": "فصيلة الدم", "allergies": "الحساسية", "chronic": "أمراض مزمنة",
           "medications": "أدوية بياخدها", "emergency": "للطوارئ (اسم وتليفون)", "notes": "ملاحظات", "no_allergies": "مفيش حساسية مسجّلة",
           "allergy_alert": "تنبيه حساسية", "visits": "الكشوفات", "new_visit": "+ كشف جديد", "no_visits": "مفيش كشوفات مسجّلة لسه.",
           "visit": "الكشف", "date": "التاريخ", "doctor": "الدكتور", "complaint": "الشكوى", "examination": "الفحص",
           "vitals": "العلامات الحيوية", "bp": "الضغط", "pulse": "النبض", "temp": "الحرارة", "weight": "الوزن (كجم)", "height": "الطول (سم)",
           "diagnosis": "التشخيص", "treatment": "العلاج / الروشتة", "follow_up": "ميعاد المتابعة", "print": "اطبع الروشتة", "edit": "تعديل",
           "upcoming": "المواعيد الجاية", "account": "الحساب والفواتير", "saved": "اتحفظ.", "visit_saved": "اتسجل الكشف.",
           "male": "ذكر", "female": "أنثى", "unknown": "—", "choose": "اختار", "from_appointment": "من الموعد",
           "rx": "روشتة", "clinic_signature": "توقيع الطبيب", "follow_due": "متابعة مستحقة",
           "tab_files": "الملفات", "tab_follow": "المتابعات", "tab_report": "تقرير العيادة",
           "report": "تقرير العيادة", "report_intro": "الكشوفات والمتابعات والمرضى الجداد لكل دكتور، والإيراد لكل دكتور وخدمة من الفواتير المرحّلة (قبل الضريبة وبعد المرتجعات).",
           "from": "من", "to": "لحد", "go": "اعرض", "visits_n": "كشوفات", "first_n": "كشف أول مرة", "follow_n": "متابعات",
           "patients_n": "مرضى اتشافوا", "new_n": "مرضى جداد", "returning_n": "مرضى قدام", "revenue": "الإيراد", "per_doctor": "لكل دكتور",
           "per_service": "لكل خدمة", "service": "الخدمة", "count": "العدد", "average": "المتوسط", "no_doctor": "من غير دكتور",
           "no_service": "من غير خدمة", "no_report": "مفيش كشوفات ولا مواعيد اتفوترت في الفترة دي.", "bad_period": "الفترة مش مظبوطة؛ اتعرض الشهر ده.",
           "follow_title": "المتابعات", "follow_intro": "اللي ميعاد متابعته فات ومجاش ومفيش له حجز، واللي ميعاده جاي في الأسبوع ده ولسه ماحجزش.",
           "missed": "متابعات فاتت", "coming": "متابعات جاية (7 أيام)", "due": "ميعاد المتابعة", "late": "متأخر", "days": "يوم",
           "last_seen": "آخر كشف", "remind": "فكّره على واتساب", "book": "احجز", "no_missed": "مفيش متابعات فاتت.", "no_coming": "مفيش متابعات جاية من غير حجز.",
           "wa_text": "أهلًا {name}، معاد متابعتك كان {date}. تحب نحجزلك ميعاد؟"},
    "en": {"title": "Patient files", "intro": "One file per patient: their details, allergies and chronic conditions, and every visit with its complaint, diagnosis and treatment.",
           "search": "Search by name, phone or file number", "find": "Search", "file_no": "File no.", "patient": "Patient", "phone": "Phone",
           "age": "Age", "last_visit": "Last visit", "open": "Open file", "none": "No patients yet. A patient is added from Patients or when booking.",
           "years": "yrs", "back": "Patient files", "profile": "Patient details", "edit_profile": "Edit details", "save": "Save",
           "dob": "Date of birth", "gender": "Sex", "blood": "Blood type", "allergies": "Allergies", "chronic": "Chronic conditions",
           "medications": "Current medication", "emergency": "Emergency contact", "notes": "Notes", "no_allergies": "No allergies recorded",
           "allergy_alert": "Allergy alert", "visits": "Visits", "new_visit": "+ New visit", "no_visits": "No visits recorded yet.",
           "visit": "Visit", "date": "Date", "doctor": "Doctor", "complaint": "Complaint", "examination": "Examination",
           "vitals": "Vital signs", "bp": "Blood pressure", "pulse": "Pulse", "temp": "Temperature", "weight": "Weight (kg)", "height": "Height (cm)",
           "diagnosis": "Diagnosis", "treatment": "Treatment / prescription", "follow_up": "Follow-up date", "print": "Print prescription", "edit": "Edit",
           "upcoming": "Upcoming appointments", "account": "Account and invoices", "saved": "Saved.", "visit_saved": "Visit recorded.",
           "male": "Male", "female": "Female", "unknown": "—", "choose": "Choose", "from_appointment": "From appointment",
           "rx": "Prescription", "clinic_signature": "Doctor's signature", "follow_due": "Follow-up due",
           "tab_files": "Files", "tab_follow": "Follow-ups", "tab_report": "Clinic report",
           "report": "Clinic report", "report_intro": "Visits, follow-ups and new patients per doctor, and revenue per doctor and service from posted invoices (before tax, after returns).",
           "from": "From", "to": "To", "go": "Show", "visits_n": "Visits", "first_n": "First visits", "follow_n": "Follow-ups",
           "patients_n": "Patients seen", "new_n": "New patients", "returning_n": "Returning patients", "revenue": "Revenue", "per_doctor": "Per doctor",
           "per_service": "Per service", "service": "Service", "count": "Count", "average": "Average", "no_doctor": "No doctor",
           "no_service": "No service", "no_report": "No visits and no billed appointments in this period.", "bad_period": "The period was not valid; showing this month.",
           "follow_title": "Follow-ups", "follow_intro": "Patients whose follow-up date passed without a visit or a booking, and those due this week who have not booked yet.",
           "missed": "Missed follow-ups", "coming": "Coming follow-ups (7 days)", "due": "Follow-up date", "late": "Late", "days": "days",
           "last_seen": "Last visit", "remind": "Remind on WhatsApp", "book": "Book", "no_missed": "No missed follow-ups.", "no_coming": "No coming follow-ups without a booking.",
           "wa_text": "Hello {name}, your follow-up was due on {date}. Shall we book you a time?"},
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _guard(request, write=False):
    if not services.is_medical_install():
        raise Http404("Patient files are for medical activities.")
    allowed = services.can_write(request.user) if write else services.can_view(request.user)
    if not allowed:
        raise PermissionDenied("Patient files need medical.view_records.")


def _base(lang, **extra):
    return {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["title"], "section": "medical", **extra}


#: The clinic's whole picture is for those who hold the records permission by role, not for a doctor reading their own patients.
REPORT_VIEW = services.VIEW
#: What the clinic earned is for those who see the business-wide sales.
MONEY_VIEW = "reports.view_all_sales_report"


def _tabs(request, current):
    from permissions.services import user_has_permission

    return {"tab": current, "shows_report": user_has_permission(request.user, REPORT_VIEW)}


def _period(request, words):
    from datetime import date

    today = timezone.localdate()
    first, last = today.replace(day=1), today
    note = ""
    raw_from, raw_to = request.GET.get("from"), request.GET.get("to")
    if raw_from or raw_to:
        try:
            first = date.fromisoformat(raw_from) if raw_from else first
            last = date.fromisoformat(raw_to) if raw_to else last
            if first > last or (last - first).days > 366:
                raise ValueError
        except ValueError:
            first, last, note = today.replace(day=1), today, words["bad_period"]
    return first, last, note


def report(request):
    from permissions.services import user_has_permission

    from . import insights

    if not services.is_medical_install():
        raise Http404("The clinic report is for medical activities.")
    if not user_has_permission(request.user, REPORT_VIEW):
        raise PermissionDenied("The clinic report needs medical.view_records.")
    lang = _lang(request)
    words = WORDS[lang]
    first, last, note = _period(request, words)
    with_money = user_has_permission(request.user, MONEY_VIEW)
    data = insights.period_report(first, last, with_money=with_money)
    return render(request, "medical/report.html", _base(lang, first=first, last=last, note=note, with_money=with_money, today=timezone.localdate(),
                                                        **data, **_tabs(request, "report")))


def follow_ups(request):
    from permissions.services import user_has_permission
    from printing.share import whatsapp_link

    from . import insights

    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    today = timezone.localdate()
    missed, coming = insights.missed_follow_ups(today), insights.coming_follow_ups(today)
    if not user_has_permission(request.user, REPORT_VIEW):  # a doctor sees the patients they asked to come back
        own = getattr(request.user, "employee", None)
        missed = [row for row in missed if own is not None and row["visit"].doctor_id == own.pk]
        coming = [row for row in coming if own is not None and row["visit"].doctor_id == own.pk]
    for row in missed + coming:
        row["whatsapp"] = whatsapp_link(row["patient"].phone, words["wa_text"].format(name=row["patient"].name, date=row["due"].isoformat()))
    return render(request, "medical/follow_ups.html", _base(lang, missed=missed, coming=coming, today=today, **_tabs(request, "follow")))


def _patients():
    return Customer.objects.exclude(customer_code="WALK-IN")


def patients(request):
    _guard(request)
    lang = _lang(request)
    query = (request.GET.get("q") or "").strip()
    rows = _patients().filter(active=True).select_related("patient_file").annotate(last=Max("clinical_visits__visit_date"))
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(patient_file__file_number__icontains=query) | Q(customer_code__icontains=query))
    rows = list(rows.order_by("-last", "name")[:200])
    today = timezone.localdate()
    for row in rows:
        file = getattr(row, "patient_file", None)
        row.file_number = file.file_number if file else ""
        row.age = services.age(file, today)
    return render(request, "medical/patients.html", _base(lang, rows=rows, query=query, **_tabs(request, "files")))


def patient_file(request, pk):
    _guard(request)
    lang = _lang(request)
    words = WORDS[lang]
    patient = get_object_or_404(_patients(), pk=pk)
    file = services.file_for(patient)
    error = ""
    can_write = services.can_write(request.user)
    if request.method == "POST":
        if not can_write:
            raise PermissionDenied("Editing a patient file needs medical.write_records.")
        try:
            services.save_profile(file, request.POST, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('medical:file', args=[patient.pk])}?lang={lang}")
    visits = list(ClinicalVisit.objects.filter(patient=patient).select_related("doctor", "created_by", "appointment"))
    upcoming = patient.appointments.filter(starts_at__gte=timezone.now(), status__in=("booked", "confirmed")).select_related("employee").order_by("starts_at")[:5]
    from . import dental

    return render(request, "medical/file.html", _base(lang, patient=patient, file=file, age=services.age(file), visits=visits, upcoming=upcoming,
                                                      is_dental=dental.is_dental(), dental_label=DENTAL_WORDS[lang]["title"],
                                                      can_write=can_write, error=error, genders=Gender.values, bloods=BloodType.values,
                                                      user_id=request.user.pk, today=timezone.localdate()))


def _doctors():
    from staff.models import Employee

    return Employee.objects.filter(active=True).order_by("name")


def visit_edit(request, pk=None, visit_pk=None):
    _guard(request, write=True)
    lang = _lang(request)
    words = WORDS[lang]
    visit = get_object_or_404(ClinicalVisit.objects.select_related("patient"), pk=visit_pk) if visit_pk else None
    patient = visit.patient if visit else get_object_or_404(_patients(), pk=pk)
    appointment = None
    if not visit and request.GET.get("appointment"):
        appointment = patient.appointments.filter(pk=request.GET.get("appointment")).select_related("employee").first()
    error = ""
    if request.method == "POST":
        data = request.POST.dict()
        data["doctor"] = _doctors().filter(pk=request.POST.get("doctor") or 0).first()
        if not visit and request.POST.get("appointment"):
            data["appointment"] = patient.appointments.filter(pk=request.POST.get("appointment")).first()
        try:
            services.save_visit(patient, data, request.user, visit, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["visit_saved"])
            return redirect(f"{reverse('medical:file', args=[patient.pk])}?lang={lang}")
    own_doctor = getattr(request.user, "employee", None)
    default_doctor = visit.doctor_id if visit else (appointment.employee_id if appointment and appointment.employee_id else (own_doctor.pk if own_doctor else None))
    return render(request, "medical/visit_form.html", _base(lang, patient=patient, visit=visit, appointment=appointment, error=error, post=request.POST,
                                                            doctors=_doctors(), default_doctor=default_doctor,
                                                            default_date=(visit.visit_date if visit else timezone.localdate())))


def prescription(request, visit_pk):
    _guard(request)
    lang = _lang(request)
    visit = get_object_or_404(ClinicalVisit.objects.select_related("patient", "doctor"), pk=visit_pk)
    from printing.company import company_details

    file = services.file_for(visit.patient)
    return render(request, "medical/prescription.html", _base(lang, visit=visit, file=file, age=services.age(file, visit.visit_date), company=company_details()))


DENTAL_WORDS = {
    "ar": {"title": "خريطة الأسنان", "intro": "حالة كل سنة بترقيم FDI، وخطة العلاج على مراحل. الخطوات اللي اتعملت بتتفوتر في فاتورة بيع عادية.",
           "upper": "الفك العلوي", "lower": "الفك السفلي", "right": "يمين", "left": "شمال", "permanent": "الأسنان الدائمة", "primary": "الأسنان اللبني",
           "show_primary": "اعرض الأسنان اللبني", "show_permanent": "اعرض الأسنان الدائمة", "tooth": "السنة", "condition": "الحالة",
           "notes": "ملاحظات", "save_tooth": "سجّل حالة السنة", "plan": "خطة العلاج", "stage": "المرحلة", "service": "الخدمة", "price": "السعر",
           "doctor": "الدكتور", "status": "الحالة", "done_on": "اتعملت يوم", "invoice": "الفاتورة", "whole_mouth": "الفم كله",
           "add_step": "ضيف خطوة", "mark_done": "اتعملت", "cancel": "إلغاء", "no_steps": "مفيش خطة علاج لسه.", "planned_total": "مخطط",
           "done_total": "اتعمل", "unbilled_total": "اتعمل ولسه ماتفوترش", "bill": "اعمل فاتورة باللي اتعمل", "billed": "اتعملت الفاتورة {number} (مسودة). رحّلها من صفحة الفواتير.",
           "saved": "اتحفظ.", "back": "ملف المريض", "choose": "اختار", "open_invoice": "افتح", "legend": "دليل الألوان"},
    "en": {"title": "Dental chart", "intro": "Each tooth's condition in FDI numbering, and the treatment plan in stages. Done steps are billed on an ordinary sales invoice.",
           "upper": "Upper jaw", "lower": "Lower jaw", "right": "Right", "left": "Left", "permanent": "Permanent teeth", "primary": "Primary teeth",
           "show_primary": "Show primary teeth", "show_permanent": "Show permanent teeth", "tooth": "Tooth", "condition": "Condition",
           "notes": "Notes", "save_tooth": "Record the tooth", "plan": "Treatment plan", "stage": "Stage", "service": "Service", "price": "Price",
           "doctor": "Doctor", "status": "Status", "done_on": "Done on", "invoice": "Invoice", "whole_mouth": "Whole mouth",
           "add_step": "Add step", "mark_done": "Done", "cancel": "Cancel", "no_steps": "No treatment plan yet.", "planned_total": "Planned",
           "done_total": "Done", "unbilled_total": "Done, not billed yet", "bill": "Invoice the done steps", "billed": "Invoice {number} was made (draft). Post it from the invoices page.",
           "saved": "Saved.", "back": "Patient file", "choose": "Choose", "open_invoice": "Open", "legend": "Colour key"},
}


def dental_chart(request, pk):
    from master_data.models import Item
    from permissions.services import user_has_permission

    from . import dental
    from .models import StepStatus, ToothCondition, TreatmentStep

    _guard(request)
    if not dental.is_dental():
        raise Http404("The dental chart is for dental clinics.")
    lang = _lang(request)
    words = DENTAL_WORDS[lang]
    patient = get_object_or_404(_patients(), pk=pk)
    can_write = services.can_write(request.user)
    can_bill = user_has_permission(request.user, dental.BILL)
    services_ = Item.objects.filter(active=True, is_stock_tracked=False).order_by("item_name")
    url = f"{reverse('medical:dental', args=[patient.pk])}?lang={lang}"
    error = ""
    if request.method == "POST":
        if not can_write:
            raise PermissionDenied("Editing the dental chart needs medical.write_records.")
        action = request.POST.get("action")
        try:
            if action == "tooth":
                dental.set_tooth(patient, request.POST.get("tooth"), request.POST.get("condition"), request.user, request.POST.get("notes"), lang)
            elif action == "plan":
                dental.plan_step(patient, {"service": services_.filter(pk=request.POST.get("service") or 0).first(), "tooth": request.POST.get("tooth"),
                                           "price": request.POST.get("price"), "stage": request.POST.get("stage"), "notes": request.POST.get("notes"),
                                           "doctor": _doctors().filter(pk=request.POST.get("doctor") or 0).first()}, request.user, lang)
            elif action in ("done", "cancel"):
                step = get_object_or_404(TreatmentStep, pk=request.POST.get("step") or 0, patient=patient)
                if action == "done":
                    dental.complete_step(step, request.user, doctor=_doctors().filter(pk=request.POST.get("doctor") or 0).first(), lang=lang)
                else:
                    dental.cancel_step(step, request.user, lang)
            elif action == "bill":
                invoice = dental.bill_done(patient, request.user, lang)
                messages.success(request, words["billed"].format(number=invoice.invoice_number))
                return redirect(url)
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(url + ("&primary=1" if request.POST.get("primary") else ""))
    primary = request.GET.get("primary") == "1" or request.POST.get("primary") == "1"
    records = dental.chart(patient)
    labels = dental.CONDITION_LABELS[lang]
    layout = dental.PRIMARY if primary else dental.PERMANENT

    def cells(row):
        return [{"tooth": tooth, "condition": records[tooth].condition if tooth in records else "healthy",
                 "label": labels[records[tooth].condition if tooth in records else "healthy"],
                 "notes": records[tooth].notes if tooth in records else ""} for tooth in row]

    rows = {"upper_right": cells(layout[0]), "upper_left": cells(layout[1]), "lower_right": cells(layout[2]), "lower_left": cells(layout[3])}
    steps = list(TreatmentStep.objects.filter(patient=patient).select_related("service", "doctor", "invoice"))
    status_labels = dental.STATUS_LABELS[lang]
    for step in steps:
        step.status_label = status_labels[step.status]
        step.billed = step.invoice_id is not None and step.invoice.status != "cancelled"
    teeth = sorted(tooth for row in layout for tooth in row)
    return render(request, "medical/dental.html", _base(lang, page_title=words["title"], dw=words, patient=patient, rows=rows, primary=primary,
                                                        conditions=[(value, labels[value]) for value in ToothCondition.values], teeth=teeth,
                                                        steps=steps, totals=dental.plan_totals(patient), services=services_, doctors=_doctors(),
                                                        can_write=can_write, can_bill=can_bill, error=error, post=request.POST, planned=StepStatus.PLANNED))
