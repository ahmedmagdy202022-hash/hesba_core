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
           "rx": "روشتة", "clinic_signature": "توقيع الطبيب", "follow_due": "متابعة مستحقة"},
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
           "rx": "Prescription", "clinic_signature": "Doctor's signature", "follow_due": "Follow-up due"},
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
    return render(request, "medical/patients.html", _base(lang, rows=rows, query=query))


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
    return render(request, "medical/file.html", _base(lang, patient=patient, file=file, age=services.age(file), visits=visits, upcoming=upcoming,
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
