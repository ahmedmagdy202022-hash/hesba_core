"""M2 (HG-086): the demo clinic's prescriptions.

Once the demo reference list is in the clinic's template (``config.demo_templates.demo_reference_list``, the same
hand-written list as the pharmacy demo, illustrative prices that are never shown here), two of the sample visits get
medicine lines through the real ``services.save_visit``, so a visitor opens a real prescription. Every other field of
those visits is passed back as it is. Idempotent: nothing is done once any visit has a line.
"""

#: (the visit's diagnosis, its instructions, [(reference row, dose, how often, for how long, notes)]): the newest visit
#: with that diagnosis gets the lines. The patient with «التهاب حلق» is allergic to penicillin: a macrolide, not amoxicillin.
PLAN = (
    ("ارتفاع ضغط الدم", "متابعة الضغط يوميًا وتقليل الملح",
     (("DEMO-061", "قرص", "مرة يوميًا بعد الأكل", "مستمر", "الصبح"),)),
    ("التهاب حلق", "راحة وسوائل دافئة بكثرة",
     (("DEMO-026", "قرص", "مرة يوميًا قبل الأكل", "3 أيام", "قبل الأكل بساعة"),
      ("DEMO-001", "قرص", "كل 8 ساعات", "عند اللزوم", "لو الحرارة فوق 38"))),
)


def _data(visit):
    """The visit's own fields, as the visit form would post them back."""

    def text(value):
        return "" if value is None else str(value)

    return {"visit_date": visit.visit_date.isoformat(), "follow_up_date": visit.follow_up_date.isoformat() if visit.follow_up_date else "",
            "complaint": visit.complaint, "examination": visit.examination, "diagnosis": visit.diagnosis, "treatment": visit.treatment,
            "blood_pressure": visit.blood_pressure, "pulse": text(visit.pulse), "temperature": text(visit.temperature),
            "weight": text(visit.weight), "height": text(visit.height)}


def prescribe_demo_visits():
    """Give the plan's visits their lines. Returns how many visits got lines (0 when already done or nothing matches)."""

    from django.db import transaction

    from pharmacy.demo import DEMO_SOURCE
    from pharmacy.models import DrugReference

    from .models import ClinicalVisit, VisitMedicine
    from .services import save_visit

    if VisitMedicine.objects.exists():
        return 0
    wanted = {line[0] for _diagnosis, _text, lines in PLAN for line in lines}
    rows = {row.source_ref: row for row in DrugReference.objects.filter(source=DEMO_SOURCE, source_ref__in=wanted, is_active=True)}
    done = 0
    with transaction.atomic():
        for diagnosis, instructions, lines in PLAN:
            visit = ClinicalVisit.objects.filter(diagnosis=diagnosis, animal__isnull=True).select_related("patient", "created_by").first()
            medicines = [{"reference": str(rows[ref].pk), "dose": dose, "frequency": frequency, "duration": duration, "notes": notes}
                         for ref, dose, frequency, duration, notes in lines if ref in rows]
            if visit is None or not medicines:
                continue
            save_visit(visit.patient, {**_data(visit), "treatment": instructions, "medicines": medicines}, visit.created_by, visit)
            done += 1
    return done
