"""MED-001 screens: patients, the patient's file, a visit, the prescription."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from master_data.models import Customer

from . import services
from .models import BloodType, ClinicalVisit, Gender, InsuranceContract

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


def _id(raw):
    """A posted id, or 0 (which finds nothing) when it is not a number."""

    return int(raw) if str(raw or "").isdigit() else 0


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

    from . import insurance

    lang = _lang(request)
    return {"tab": current, "shows_report": user_has_permission(request.user, REPORT_VIEW), "shows_lab": _is_lab(), "lab_tests_label": LAB_WORDS[lang]["tests"],
            "shows_insurance": insurance.can_view(request.user), "insurance_label": INSURANCE_WORDS[lang]["tab"]}


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
    booked = insights._booked_patients(today)  # read once for both lists
    missed, coming = insights.missed_follow_ups(today, booked), insights.coming_follow_ups(today, booked)
    if not user_has_permission(request.user, REPORT_VIEW):  # a doctor sees the patients they asked to come back
        own = getattr(request.user, "employee", None)
        missed = [row for row in missed if own is not None and row["visit"].doctor_id == own.pk]
        coming = [row for row in coming if own is not None and row["visit"].doctor_id == own.pk]
    for row in missed:  # the reminder text says the date has passed, so only the missed ones get a link
        row["whatsapp"] = whatsapp_link(row["patient"].phone, words["wa_text"].format(name=row["patient"].name, date=row["due"].isoformat()))
    return render(request, "medical/follow_ups.html", _base(lang, missed=missed, coming=coming, today=today, **_tabs(request, "follow")))


def _patients():
    return Customer.objects.exclude(customer_code="WALK-IN")


def patients(request):
    _guard(request)
    lang = _lang(request)
    query = (request.GET.get("q") or "").strip()
    rows = (_patients().filter(active=True, insurance_contract__isnull=True)  # MED-008: an insurance company is a customer, not a patient
            .select_related("patient_file").annotate(last=Max("clinical_visits__visit_date")))
    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(phone__icontains=query) | Q(patient_file__file_number__icontains=query) | Q(customer_code__icontains=query)
                           | Q(animals__name__icontains=query)).distinct()
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
    visits = list(ClinicalVisit.objects.filter(patient=patient).select_related("doctor", "created_by", "appointment", "animal"))
    upcoming = patient.appointments.filter(starts_at__gte=timezone.now(), status__in=("booked", "confirmed")).select_related("employee").order_by("starts_at")[:5]
    from . import dental, insurance

    cover_context = {}
    if insurance.can_view(request.user):  # MED-008: the patient's insurer, and changing it
        cover = insurance.PatientCover.objects.select_related("contract__insurer").filter(patient=patient).first()
        cover_context = {"shows_cover": True, "cover": cover, "cover_live": insurance.cover_for(patient) is not None if cover else False,
                         "cover_contracts": insurance.contracts(active_only=True).exclude(insurer=patient), "iw": INSURANCE_WORDS[lang],
                         "can_cover": insurance.can_manage(request.user) or can_write}
    return render(request, "medical/file.html", _base(lang, patient=patient, file=file, **cover_context, age=services.age(file), visits=visits, upcoming=upcoming,
                                                      is_dental=dental.is_dental(), dental_label=DENTAL_WORDS[lang]["title"],
                                                      is_lab=_is_lab(), lab_label=LAB_WORDS[lang]["title"],
                                                      is_physio=_is_physio(), physio_label=PHYSIO_WORDS[lang]["title"],
                                                      is_vet=_is_vet(), vw=VET_WORDS[lang], animals=_animals_of(patient, lang) if _is_vet() else [],
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
        data["doctor"] = _doctors().filter(pk=_id(request.POST.get("doctor"))).first()
        if not visit and request.POST.get("appointment"):
            data["appointment"] = patient.appointments.filter(pk=_id(request.POST.get("appointment"))).first()
        if _is_vet():
            from .models import Animal

            data["animal"] = Animal.objects.filter(pk=_id(request.POST.get("animal"))).first()  # the service refuses another owner's animal
            data["vet"] = True
        else:
            data.pop("animal", None)
        try:
            services.save_visit(patient, data, request.user, visit, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["visit_saved"])
            return redirect(f"{reverse('medical:file', args=[patient.pk])}?lang={lang}")
    own_doctor = getattr(request.user, "employee", None)
    default_doctor = visit.doctor_id if visit else (appointment.employee_id if appointment and appointment.employee_id else (own_doctor.pk if own_doctor else None))
    animals, default_animal = [], None
    if _is_vet():
        animals = list(patient.animals.filter(active=True) | patient.animals.filter(pk=visit.animal_id if visit else 0))
        default_animal = visit.animal_id if visit else (int(request.GET.get("animal")) if (request.GET.get("animal") or "").isdigit() else None)
    return render(request, "medical/visit_form.html", _base(lang, patient=patient, visit=visit, appointment=appointment, error=error, post=request.POST,
                                                            doctors=_doctors(), default_doctor=default_doctor, animals=animals,
                                                            default_animal=default_animal, animal_label=VET_WORDS[lang]["animal"],
                                                            default_date=(visit.visit_date if visit else timezone.localdate())))


def prescription(request, visit_pk):
    _guard(request)
    lang = _lang(request)
    visit = get_object_or_404(ClinicalVisit.objects.select_related("patient", "doctor", "animal"), pk=visit_pk)
    from printing.company import company_details

    from . import vet

    file = services.file_for(visit.patient)
    animal_line = ""
    if visit.animal_id:  # MED-006: the prescription is for the animal; the owner is who it is handed to
        animal_line = f"{visit.animal.name} ({vet.SPECIES_LABELS[lang].get(visit.animal.species, visit.animal.species)})"
    allergies = visit.animal.allergies if visit.animal_id else ("" if _is_vet() else file.allergies)  # an animal's own, never its owner's
    return render(request, "medical/prescription.html", _base(lang, visit=visit, file=file, age=None if visit.animal_id else services.age(file, visit.visit_date),
                                                              company=company_details(), animal_line=animal_line, animal_label=VET_WORDS[lang]["animal"],
                                                              allergies=allergies))


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
        action = request.POST.get("action")
        if action == "bill" and not can_bill or action != "bill" and not can_write:  # billing is the counter's; the chart is the doctor's
            raise PermissionDenied("Billing needs sales.create_sales_invoice; editing the chart needs medical.write_records.")
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


LAB_WORDS = {
    "ar": {"title": "نتايج التحاليل", "intro": "اكتب نتيجة كل تحليل؛ القيمة بتتقارن بالمعدل الطبيعي وقت تسجيلها.", "tests": "التحاليل والمعدلات",
           "tests_intro": "كل تحليل بوحدته ومعدله الطبيعي. تغيير المعدل مش بيغيّر نتايج اتسجلت قبل كده.", "name": "التحليل", "unit": "الوحدة",
           "low": "الحد الأدنى", "high": "الحد الأعلى", "range": "المعدل الطبيعي", "service": "الخدمة (للفاتورة)", "position": "الترتيب",
           "active": "شغال", "save": "حفظ", "add": "+ تحليل جديد", "edit": "تعديل", "day": "تاريخ العينة", "value": "النتيجة", "text": "وصف (لو النتيجة كلام)",
           "flag": "الحكم", "previous": "المرة اللي فاتت", "record": "سجّل النتايج", "history": "النتايج السابقة", "print": "اطبع النتيجة",
           "no_tests": "مفيش تحاليل متسجلة لسه. ضيفها من «التحاليل والمعدلات».", "no_results": "مفيش نتايج لسه.", "saved": "اتحفظ.",
           "back": "ملف المريض", "sheet": "تقرير نتيجة تحاليل", "patient": "المريض", "file_no": "رقم الملف", "age": "السن", "years": "سنة",
           "signature": "توقيع المسؤول", "choose": "—", "none": "—", "remove": "امسح (اتسجلت غلط)", "reason": "السبب"},
    "en": {"title": "Lab results", "intro": "Enter each test's result; the value is checked against the normal range in force when it is entered.",
           "tests": "Tests and ranges", "tests_intro": "Each test with its unit and normal range. Changing a range never changes results already entered.",
           "name": "Test", "unit": "Unit", "low": "Low limit", "high": "High limit", "range": "Normal range", "service": "Service (for the invoice)",
           "position": "Order", "active": "Active", "save": "Save", "add": "+ New test", "edit": "Edit", "day": "Sample date", "value": "Result",
           "text": "Text (if the result is a word)", "flag": "Flag", "previous": "Last time", "record": "Record the results", "history": "Earlier results",
           "print": "Print the result", "no_tests": "No tests yet. Add them under “Tests and ranges”.", "no_results": "No results yet.", "saved": "Saved.",
           "back": "Patient file", "sheet": "Laboratory report", "patient": "Patient", "file_no": "File no.", "age": "Age", "years": "yrs",
           "signature": "Signed", "choose": "—", "none": "—", "remove": "Remove (entered by mistake)", "reason": "Reason"},
}


def _is_lab():
    from . import lab

    return lab.is_lab()


def _lab_guard(request, write=False):
    from . import lab

    _guard(request, write=write)
    if not lab.is_lab():
        raise Http404("Lab results are for labs.")


def lab_tests(request):
    from master_data.models import Item

    from . import lab
    from .models import LabTest

    _lab_guard(request)
    lang = _lang(request)
    words = LAB_WORDS[lang]
    can_write = services.can_write(request.user)
    editing = get_object_or_404(LabTest, pk=_id(request.GET.get("edit") or request.POST.get("test"))) if (request.GET.get("edit") or request.POST.get("test")) else None
    error = ""
    from django.db.models import Q

    kept = Q(pk=editing.service_id) if editing and editing.service_id else Q(pk__in=[])  # its own service stays, even if inactive since
    services_ = Item.objects.filter(Q(active=True, is_stock_tracked=False) | kept).order_by("item_name")
    if request.method == "POST":
        if not can_write:
            raise PermissionDenied("Editing lab tests needs medical.write_records.")
        data = request.POST.dict()
        data["service"] = services_.filter(pk=_id(request.POST.get("service"))).first()
        data["active"] = request.POST.get("active") == "1"
        try:
            lab.save_test(data, request.user, editing, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('medical:lab_tests')}?lang={lang}")
    return render(request, "medical/lab_tests.html", _base(lang, **_tabs(request, "lab_tests"), page_title=words["tests"], lw=words, tests=LabTest.objects.select_related("service"),
                                                          editing=editing, services=services_, can_write=can_write, error=error, post=request.POST))


def lab_results(request, pk):
    from . import lab
    from .models import LabTest

    _lab_guard(request)
    lang = _lang(request)
    words = LAB_WORDS[lang]
    patient = get_object_or_404(_patients(), pk=pk)
    can_write = services.can_write(request.user)
    tests = list(LabTest.objects.filter(active=True))
    error = ""
    if request.method == "POST":
        if not can_write:
            raise PermissionDenied("Recording lab results needs medical.write_records.")
        entries = {test: (request.POST.get(f"value_{test.pk}"), request.POST.get(f"text_{test.pk}")) for test in tests}
        try:
            if request.POST.get("action") == "remove":  # entered by mistake (wrong patient or test)
                from .models import LabResult

                lab.remove_result(get_object_or_404(LabResult, pk=_id(request.POST.get("result")), patient=patient), request.user,
                                  request.POST.get("reason"), lang)
            else:
                lab.record_results(patient, request.POST.get("taken_on"), entries, request.user, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('medical:lab', args=[patient.pk])}?lang={lang}")
    labels = lab.FLAG_LABELS[lang]
    days = lab.by_day(patient)
    for _, results in days:
        for result in results:
            result.flag_label = labels[result.flag]
            result.before = lab.previous(result)
    rows = [{"test": test, "value": request.POST.get(f"value_{test.pk}", ""), "text": request.POST.get(f"text_{test.pk}", "")} for test in tests]
    return render(request, "medical/lab_results.html", _base(lang, page_title=words["title"], lw=words, patient=patient, rows=rows, days=days,
                                                            can_write=can_write, error=error, today=timezone.localdate(),
                                                            taken_on=request.POST.get("taken_on") or timezone.localdate().isoformat()))


def lab_sheet(request, pk, day):
    from datetime import date

    from printing.company import company_details

    from . import lab
    from .models import LabResult

    _lab_guard(request)
    lang = _lang(request)
    words = LAB_WORDS[lang]
    patient = get_object_or_404(_patients(), pk=pk)
    try:
        taken_on = date.fromisoformat(day)
    except ValueError:
        raise Http404("Not a date.")
    results = list(LabResult.objects.filter(patient=patient, taken_on=taken_on).select_related("test", "entered_by"))
    if not results:
        raise Http404("No results that day.")
    labels = lab.FLAG_LABELS[lang]
    for result in results:
        result.flag_label = labels[result.flag]
    file = services.file_for(patient)
    return render(request, "medical/lab_sheet.html", _base(lang, lw=words, patient=patient, file=file, age=services.age(file, taken_on),
                                                          taken_on=taken_on, results=results, company=company_details()))


PHYSIO_WORDS = {
    "ar": {"title": "باكدج الجلسات", "intro": "الباكدج بيتباع في فاتورة بيع عادية، والجلسات بتتسحب منه واحدة واحدة بعد ما الفاتورة تترحّل.",
           "sell": "بيع باكدج", "service": "الخدمة", "sessions": "عدد الجلسات", "price": "سعر الباكدج", "price_hint": "فاضي = عدد الجلسات × سعر الجلسة",
           "notes": "ملاحظات", "save": "اعمل الفاتورة", "packages": "الباكدجات", "used": "اتعمل", "left": "باقي", "status": "الحالة",
           "invoice": "الفاتورة", "use": "سجّل جلسة", "therapist": "الأخصائي", "day": "اليوم", "history": "الجلسات", "cancel": "إلغاء الجلسة",
           "no_packages": "مفيش باكدجات لسه.", "no_uses": "مفيش جلسات لسه.", "saved": "اتحفظ.", "sold": "اتعملت فاتورة الباكدج {number} (مسودة). رحّلها عشان الجلسات تشتغل.",
           "back": "ملف المريض", "choose": "اختار", "cancelled": "اتلغت", "draft": "الفاتورة مسودة", "active": "شغال", "finished": "خلص", "void": "اتلغى"},
    "en": {"title": "Session packages", "intro": "A package is sold on an ordinary sales invoice; sessions are used from it one by one once the invoice is posted.",
           "sell": "Sell a package", "service": "Service", "sessions": "Sessions", "price": "Package price", "price_hint": "Blank = sessions × the session price",
           "notes": "Notes", "save": "Make the invoice", "packages": "Packages", "used": "Used", "left": "Left", "status": "Status",
           "invoice": "Invoice", "use": "Record a session", "therapist": "Therapist", "day": "Day", "history": "Sessions", "cancel": "Cancel the session",
           "no_packages": "No packages yet.", "no_uses": "No sessions yet.", "saved": "Saved.", "sold": "Package invoice {number} was made (draft). Post it so the sessions can be used.",
           "back": "Patient file", "choose": "Choose", "cancelled": "Cancelled", "draft": "Invoice still a draft", "active": "Active", "finished": "Used up", "void": "Void"},
}


def _is_physio():
    from . import physio

    return physio.is_physio()


def session_packages(request, pk):
    from master_data.models import Item
    from permissions.services import user_has_permission

    from . import physio
    from .models import PackageSession, SessionPackage

    _guard(request)
    if not physio.is_physio():
        raise Http404("Session packages are for physiotherapy.")
    lang = _lang(request)
    words = PHYSIO_WORDS[lang]
    patient = get_object_or_404(_patients(), pk=pk)
    can_write = services.can_write(request.user)
    can_sell = user_has_permission(request.user, physio.SELL)
    services_ = Item.objects.filter(active=True, is_stock_tracked=False).order_by("item_name")
    url = f"{reverse('medical:packages', args=[patient.pk])}?lang={lang}"
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "sell":
                package = physio.sell(patient, {"service": services_.filter(pk=request.POST.get("service") or 0).first(), "sessions": request.POST.get("sessions"),
                                                "price": request.POST.get("price"), "notes": request.POST.get("notes")}, request.user, lang)
                messages.success(request, words["sold"].format(number=package.invoice.invoice_number))
                return redirect(url)
            if not can_write:
                raise PermissionDenied("Recording sessions needs medical.write_records.")
            if action == "use":
                package = get_object_or_404(SessionPackage, pk=request.POST.get("package") or 0, patient=patient)
                physio.use(package, request.user, therapist=_doctors().filter(pk=request.POST.get("therapist") or 0).first(),
                           notes=request.POST.get("notes"), lang=lang)
            elif action == "cancel":
                physio.cancel_use(get_object_or_404(PackageSession, pk=request.POST.get("session") or 0, package__patient=patient), request.user, lang)
            else:
                raise Http404("Unknown action.")
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(url)
    packages = list(SessionPackage.objects.filter(patient=patient).select_related("service", "invoice"))
    for package in packages:
        package.state = physio.state(package)
        package.status_label = words[package.state["status"]]
        package.history = list(package.uses.select_related("therapist"))
    own = getattr(request.user, "employee", None)
    return render(request, "medical/packages.html", _base(lang, page_title=words["title"], pw=words, patient=patient, packages=packages,
                                                         services=services_, doctors=_doctors(), can_write=can_write, can_sell=can_sell, error=error,
                                                         post=request.POST, own_id=own.pk if own else None))


VET_WORDS = {
    "ar": {"animals": "الحيوانات", "animal": "الحيوان", "add": "+ حيوان جديد", "name": "الاسم", "species": "النوع", "breed": "السلالة",
           "sex": "الجنس", "dob": "تاريخ الميلاد", "age": "السن", "colour": "اللون", "microchip": "رقم الشريحة", "neutered": "متعقّم",
           "allergies": "الحساسية", "notes": "ملاحظات", "active": "لسه بييجي", "save": "حفظ", "visits": "الكشوفات", "no_animals": "مفيش حيوانات متسجلة للعميل ده.",
           "no_visits": "مفيش كشوفات للحيوان ده لسه.", "owner": "صاحبه", "new_visit": "+ كشف للحيوان ده", "saved": "اتحفظ.", "edit": "تعديل البيانات",
           "male": "ذكر", "female": "أنثى", "choose": "اختار", "yes": "أيوه", "open": "افتح"},
    "en": {"animals": "Animals", "animal": "Animal", "add": "+ New animal", "name": "Name", "species": "Species", "breed": "Breed",
           "sex": "Sex", "dob": "Date of birth", "age": "Age", "colour": "Colour", "microchip": "Microchip no.", "neutered": "Neutered",
           "allergies": "Allergies", "notes": "Notes", "active": "Still coming", "save": "Save", "visits": "Visits", "no_animals": "No animals recorded for this customer.",
           "no_visits": "No visits for this animal yet.", "owner": "Owner", "new_visit": "+ Visit for this animal", "saved": "Saved.", "edit": "Edit details",
           "male": "Male", "female": "Female", "choose": "Choose", "yes": "Yes", "open": "Open"},
}


def _is_vet():
    from . import vet

    return vet.is_vet()


def _animals_of(owner, lang):
    from . import vet

    labels = vet.SPECIES_LABELS[lang]
    rows = list(owner.animals.all())
    for animal in rows:
        animal.species_label = labels.get(animal.species, animal.species)
        animal.age_text = vet.age_text(animal, lang)
    return rows


def animal_new(request, pk):
    from . import vet

    _guard(request, write=True)
    if not vet.is_vet():
        raise Http404("Animal files are for vets.")
    lang = _lang(request)
    owner = get_object_or_404(_patients(), pk=pk)
    return _animal_form(request, lang, owner, None)


def animal_card(request, pk):
    from . import vet
    from .models import Animal

    _guard(request)
    if not vet.is_vet():
        raise Http404("Animal files are for vets.")
    lang = _lang(request)
    animal = get_object_or_404(Animal.objects.select_related("owner"), pk=pk)
    if request.method == "POST":
        _guard(request, write=True)
        return _animal_form(request, lang, animal.owner, animal)
    words = VET_WORDS[lang]
    animal.species_label = vet.SPECIES_LABELS[lang].get(animal.species, animal.species)
    animal.age_text = vet.age_text(animal, lang)
    visits = list(animal.visits.select_related("doctor"))
    return render(request, "medical/animal.html", _base(lang, page_title=animal.name, vw=words, animal=animal, visits=visits, edit=request.GET.get("edit") == "1",
                                                        can_write=services.can_write(request.user), species=_species(lang), today=timezone.localdate(),
                                                        post=None))


def _species(lang):
    from . import vet
    from .models import Species

    return [(value, vet.SPECIES_LABELS[lang][value]) for value in Species.values]


def _animal_form(request, lang, owner, animal):
    from . import vet

    words = VET_WORDS[lang]
    error = ""
    if request.method == "POST":
        data = request.POST.dict()
        data["neutered"] = request.POST.get("neutered") == "1"
        data["active"] = request.POST.get("active", "1") == "1"
        try:
            animal = vet.save_animal(owner, data, request.user, animal, lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('medical:animal', args=[animal.pk])}?lang={lang}")
    return render(request, "medical/animal_form.html", _base(lang, page_title=animal.name if animal else words["add"], vw=words, owner=owner, animal=animal, error=error,
                                                             post=request.POST, species=_species(lang), today=timezone.localdate()))


QUEUE_WORDS = {
    "ar": {"title": "قائمة الانتظار", "intro": "سجّل وصول المريض ياخد رقم دوره، والدكتور ينادي على اللي عليه الدور. شاشة الانتظار بتعرض الأرقام بس.",
           "screen": "شاشة الانتظار", "appointments": "مواعيد النهارده اللي لسه ماوصلتش", "walk_in": "مريض من غير ميعاد", "search": "دوّر بالاسم أو التليفون",
           "find": "بحث", "check_in": "وصل", "queue": "الدور النهارده", "number": "الرقم", "patient": "المريض", "doctor": "الدكتور", "status": "الحالة",
           "arrived": "وصل", "wait": "مستني بقاله", "minutes": "دقيقة", "call_next": "نادي على اللي بعده", "call": "نادي", "done": "خلص", "left": "مشي",
           "back_to_wait": "رجّعه للانتظار", "record_visit": "سجّل الكشف", "none": "مفيش حد في الدور النهارده.", "none_appts": "مفيش مواعيد مستنية.",
           "now": "اتفضل", "next": "اللي عليه الدور", "waiting_n": "مستنيين", "no_one": "—", "at": "الساعة", "saved": "اتحفظ.",
           "called": "اتنادى على رقم {number}.", "checked": "{name} رقم دوره {number}."},
    "en": {"title": "Waiting list", "intro": "Check a patient in and they get their turn number; the doctor calls the next one in. The waiting screen shows numbers only.",
           "screen": "Waiting screen", "appointments": "Today's appointments not arrived yet", "walk_in": "Walk-in patient", "search": "Search by name or phone",
           "find": "Search", "check_in": "Arrived", "queue": "Today's queue", "number": "No.", "patient": "Patient", "doctor": "Doctor", "status": "Status",
           "arrived": "Arrived", "wait": "Waiting for", "minutes": "min", "call_next": "Call the next one", "call": "Call", "done": "Done", "left": "Left",
           "back_to_wait": "Back to waiting", "record_visit": "Record the visit", "none": "Nobody in the queue today.", "none_appts": "No appointments waiting.",
           "now": "Please come in", "next": "Next", "waiting_n": "waiting", "no_one": "—", "at": "at", "saved": "Saved.",
           "called": "Number {number} was called in.", "checked": "{name} is number {number}."},
}


def _queue_guard(request):
    from . import queue

    if not queue.has_queue():
        raise Http404("The waiting list is for clinics and medical centres.")
    if not queue.can_use(request.user):
        raise PermissionDenied("The waiting list needs the booking permission, or being a doctor.")


def queue_desk(request):
    from datetime import timedelta

    from appointments.models import Appointment, AppointmentStatus

    from . import queue
    from .models import QueueTicket, TicketStatus

    _queue_guard(request)
    lang = _lang(request)
    words = QUEUE_WORDS[lang]
    url = f"{reverse('medical:queue')}?lang={lang}"
    error = ""
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "check_in":
                appointment = Appointment.objects.filter(pk=_id(request.POST.get("appointment"))).select_related("employee").first()
                patient = appointment.customer if appointment else _patients().filter(pk=_id(request.POST.get("patient"))).first()
                ticket = queue.check_in(patient, request.user, appointment=appointment, doctor=_doctors().filter(pk=_id(request.POST.get("doctor"))).first(), lang=lang)
                messages.success(request, words["checked"].format(name=ticket.patient.name, number=ticket.number))
            elif action == "call_next":
                ticket = queue.call_next(request.user, lang)
                messages.success(request, words["called"].format(number=ticket.number))
            elif action in ("called", "done", "left", "waiting"):
                ticket = get_object_or_404(QueueTicket, pk=_id(request.POST.get("ticket")), day=timezone.localdate())
                queue.move(ticket, action, request.user, lang)
                messages.success(request, words["saved"])
            else:
                raise Http404("Unknown action.")
        except (ValidationError, PermissionDenied) as exc:  # another doctor's patient: say so on the page
            error = " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        else:
            return redirect(url)
    today = timezone.localdate()
    tickets = list(queue.today_tickets(today))
    labels = queue.STATUS_LABELS[lang]
    now = timezone.now()
    for ticket in tickets:
        ticket.status_label = labels[ticket.status]
        ticket.waited = int((now - ticket.arrived_at).total_seconds() // 60) if ticket.status == TicketStatus.WAITING else None
    in_queue = {ticket.appointment_id for ticket in tickets if ticket.appointment_id}
    start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    appointments = [a for a in Appointment.objects.filter(starts_at__gte=start, starts_at__lt=start + timedelta(days=1),
                                                          status__in=(AppointmentStatus.BOOKED, AppointmentStatus.CONFIRMED, AppointmentStatus.ARRIVED))
                    .select_related("customer", "employee").order_by("starts_at") if a.pk not in in_queue]
    query = (request.GET.get("q") or "").strip()
    found = list(_patients().filter(active=True).filter(Q(name__icontains=query) | Q(phone__icontains=query))[:20]) if query else []
    return render(request, "medical/queue.html", _base(lang, page_title=words["title"], qw=words, tickets=tickets, appointments=appointments, found=found,
                                                      query=query, doctors=_doctors(), error=error, is_desk=queue.is_desk(request.user),
                                                      shows_files=services.can_write(request.user), waiting=TicketStatus.WAITING, called=TicketStatus.CALLED))


def queue_screen(request):
    from . import queue

    _queue_guard(request)
    lang = _lang(request)
    from printing.company import company_details

    return render(request, "medical/queue_screen.html", _base(lang, qw=QUEUE_WORDS[lang], board=queue.board(), company=company_details(),
                                                             now=timezone.localtime()))


# ---- MED-008: medical insurance (HG-042, design A) ----

INSURANCE_WORDS = {
    "ar": {"title": "التأمين الطبي", "tab": "التأمين", "intro": "كل شركة تأمين عميل عادي عليها عقد: نسبة بتتحمّلها من الكشف (ونسبة مختلفة لأي خدمة لو حبيت) ويوم المطالبة. الكشف المؤمَّن عليه بيطلع فاتورتين عاديتين: واحدة على المريض بنصيبه، وواحدة على الشركة بالباقي. المطالبة الشهرية هي فواتير الشركة المترحّلة في الشهر، والشركة بتدفع بتحصيل عادي.",
           "insurer": "شركة التأمين", "existing": "شركة موجودة في العملاء", "or_new": "أو اسم شركة جديدة", "phone": "التليفون", "percent": "نسبة التحمّل %",
           "claim_day": "يوم المطالبة في الشهر", "notes": "ملاحظات", "active": "شغالة", "save": "حفظ", "add": "ضيف شركة تأمين", "none": "مفيش شركات تأمين لسه.",
           "members": "مرضى عليها", "owed": "عليها دلوقتي", "next_claim": "المطالبة الجاية", "open": "افتح", "saved": "اتحفظ.", "back": "التأمين الطبي",
           "contract": "العقد", "services": "خدمات بنسبة مختلفة", "services_hint": "اكتب 0 لخدمة مش مغطاة؛ امسح النسبة عشان ترجع لنسبة العقد.",
           "service": "الخدمة", "service_percent": "النسبة %", "set": "ثبّت", "no_services": "كل الخدمات بنسبة العقد.", "choose": "اختار",
           "claim": "مطالبة الشهر", "month": "الشهر", "show": "اعرض", "print": "اطبع المطالبة", "date": "اليوم", "invoice": "الفاتورة", "patient": "المريض",
           "member": "رقم العضوية", "visit_price": "سعر الكشف", "claimed": "المطلوب", "rejected": "اترفض (مرتجع)", "net": "الصافي", "patient_share": "نصيب المريض",
           "no_claim": "مفيش فواتير مترحّلة للشركة دي في الشهر ده.", "total": "الإجمالي", "count": "عدد الكشوفات",
           "unpaired": "كشوفات واحدة من فاتورتيها اتلغت والتانية لأ", "unpaired_hint": "راجع كل واحدة: يا تلغي التانية، يا تعمل فاتورة جديدة.",
           "reject_hint": "البند المرفوض بيترجع بمرتجع بيع عادي على فاتورة الشركة؛ لو المريض هيدفعه، اعمله فاتورة عادية.",
           "cover": "التأمين", "cover_none": "مفيش تأمين", "expires": "ينتهي يوم (اختياري)", "cover_saved": "اتحفظ تأمين المريض.", "cover_expired": "انتهى",
           "view_only": "إضافة الشركات وتعديلها للي عنده صلاحية إنشاء فاتورة بيع بس.",
           "claim_title": "مطالبة تأمين", "period": "الفترة", "signature": "الختم والتوقيع"},
    "en": {"title": "Medical insurance", "tab": "Insurance", "intro": "Each insurance company is an ordinary customer with a contract: the share of a visit it covers (with an own share for any service if you like) and its claim day. An insured visit makes two ordinary invoices: the patient's for their share and the insurer's for the rest. The monthly claim is the insurer's posted invoices in the month, and it pays through an ordinary collection.",
           "insurer": "Insurance company", "existing": "A company already in customers", "or_new": "or a new company's name", "phone": "Phone", "percent": "Covered share %",
           "claim_day": "Claim day of the month", "notes": "Notes", "active": "Active", "save": "Save", "add": "Add an insurance company", "none": "No insurance companies yet.",
           "members": "Patients covered", "owed": "Owes now", "next_claim": "Next claim", "open": "Open", "saved": "Saved.", "back": "Medical insurance",
           "contract": "Contract", "services": "Services at another share", "services_hint": "Type 0 for a service not covered; clear the share to go back to the contract's.",
           "service": "Service", "service_percent": "Share %", "set": "Set", "no_services": "Every service at the contract's share.", "choose": "Choose",
           "claim": "The month's claim", "month": "Month", "show": "Show", "print": "Print the claim", "date": "Day", "invoice": "Invoice", "patient": "Patient",
           "member": "Member no.", "visit_price": "Visit price", "claimed": "Claimed", "rejected": "Rejected (returned)", "net": "Net", "patient_share": "Patient's share",
           "no_claim": "No posted invoices for this company in this month.", "total": "Total", "count": "Visits",
           "unpaired": "Visits where one of the two invoices was cancelled and the other was not", "unpaired_hint": "Check each: cancel the other, or bill again.",
           "reject_hint": "A rejected line is returned by an ordinary sales return on the insurer's invoice; if the patient will pay it, make them an ordinary invoice.",
           "cover": "Insurance", "cover_none": "No insurance", "expires": "Expires on (optional)", "cover_saved": "The patient's insurance was saved.", "cover_expired": "Expired",
           "view_only": "Adding and changing companies needs the create-sales-invoice permission.",
           "claim_title": "Insurance claim", "period": "Period", "signature": "Stamp and signature"},
}


def _insurance_guard(request):
    from permissions.services import user_has_permission

    from . import insurance

    if not insurance.is_insurance_install():
        raise Http404("Medical insurance is for clinics, centres, hospitals and labs.")
    if not user_has_permission(request.user, insurance.VIEW):
        raise PermissionDenied("Medical insurance needs sales.view_sales_invoices.")


def _insurance_base(request, lang, **extra):
    from . import insurance

    return _base(lang, iw=INSURANCE_WORDS[lang], can_manage=insurance.can_manage(request.user), **_tabs(request, "insurance"), **extra)


def insurance_list(request):
    from . import insurance

    _insurance_guard(request)
    lang = _lang(request)
    words = INSURANCE_WORDS[lang]
    error = ""
    if request.method == "POST":
        data = request.POST.dict()
        data["insurer"] = _patients().filter(pk=_id(request.POST.get("insurer")), active=True).first() if request.POST.get("insurer") else None
        data["active"] = True
        try:
            contract = insurance.save_contract(data, request.user, lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('medical:insurance_contract', args=[contract.pk])}?lang={lang}")
    rows = list(insurance.contracts().annotate(member_count=Count("members")))
    today = timezone.localdate()
    for row in rows:
        row.owed = insurance.outstanding(row)
        row.next_claim = insurance.next_claim_day(row, today)
    taken = InsuranceContract.objects.values_list("insurer_id", flat=True)
    candidates = _patients().filter(active=True).exclude(pk__in=taken).exclude(patient_file__isnull=False).order_by("name")[:300]
    return render(request, "medical/insurance.html", _insurance_base(request, lang, rows=rows, candidates=candidates, unpaired=insurance.unpaired(),
                                                                     error=error, post=request.POST if error else {}))


def insurance_contract(request, pk):
    from master_data.models import Item

    from . import insurance

    _insurance_guard(request)
    lang = _lang(request)
    words = INSURANCE_WORDS[lang]
    contract = get_object_or_404(insurance.contracts(), pk=pk)
    error = ""
    if request.method == "POST":
        try:
            if request.POST.get("action") == "service":
                service = Item.objects.filter(pk=_id(request.POST.get("service")), is_stock_tracked=False).first()
                insurance.set_service_percent(contract, service, request.POST.get("percent", ""), request.user, lang)
            else:
                data = request.POST.dict()
                data["active"] = request.POST.get("active") == "1"
                insurance.save_contract(data, request.user, contract=contract, lang=lang)
        except ValidationError as exc:
            error = " ".join(exc.messages)
        else:
            messages.success(request, words["saved"])
            return redirect(f"{reverse('medical:insurance_contract', args=[contract.pk])}?lang={lang}&month={request.POST.get('month', '')}")
        contract.refresh_from_db()
    try:
        first, last = insurance.month_bounds(request.GET.get("month"), lang=lang)
    except ValidationError as exc:
        first, last = insurance.month_bounds("")
        error = error or " ".join(exc.messages)
    data = insurance.claim(contract, first, last)
    services_list = Item.objects.filter(active=True, is_stock_tracked=False).order_by("item_name")
    return render(request, "medical/insurance_contract.html", _insurance_base(
        request, lang, contract=contract, overrides=contract.services.select_related("service"), services_list=services_list, first=first, last=last,
        month=first.strftime("%Y-%m"), owed=insurance.outstanding(contract), next_claim=insurance.next_claim_day(contract), error=error, **data))


def insurance_claim_print(request, pk):
    from printing.company import company_details

    from . import insurance

    _insurance_guard(request)
    lang = _lang(request)
    contract = get_object_or_404(insurance.contracts(), pk=pk)
    try:
        first, last = insurance.month_bounds(request.GET.get("month"), lang=lang)
    except ValidationError:
        raise Http404("Not a month.")
    return render(request, "medical/insurance_claim.html", _base(lang, iw=INSURANCE_WORDS[lang], contract=contract, first=first, last=last,
                                                                 company=company_details(), **insurance.claim(contract, first, last)))


def patient_cover(request, pk):
    from . import insurance

    if request.method != "POST":
        raise Http404("Insurance is set from the patient's file.")
    _insurance_guard(request)
    lang = _lang(request)
    patient = get_object_or_404(_patients(), pk=pk)
    data = request.POST.dict()
    data["contract"] = insurance.contracts(active_only=True).filter(pk=_id(request.POST.get("contract"))).first() if request.POST.get("contract") else None
    try:
        insurance.set_cover(patient, data, request.user, lang)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, INSURANCE_WORDS[lang]["cover_saved"])
    return redirect(f"{reverse('medical:file', args=[patient.pk])}?lang={lang}")
