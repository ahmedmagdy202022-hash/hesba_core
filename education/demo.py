"""E2: the education demo's exams, kept out of the big demo seed so later education tasks do not all edit the same block."""

from datetime import timedelta

from django.utils import timezone

from . import attendance, exams, nursery


def seed_exams(groups, owner):
    """Two exams in the first group, with marks: a published monthly exam and an unpublished quiz, one student absent from the quiz.

    Marks go through ``exams.record_marks`` like a teacher's would. Nothing for a nursery (it has no exams)."""

    if nursery.is_nursery() or not groups:
        return []
    today = timezone.localdate()
    group = groups[0]
    plan = (("امتحان الشهر", "monthly", 9, "50", (46, 41, 33, 38, 27)), ("كويز الأسبوع", "quiz", 3, "20", (18, 15, None, 12.5, 9)))
    made = []
    for title, kind, ago, out_of, scores in plan:
        exam = exams.save_exam(group, {"title": title, "kind": kind, "held_on": today - timedelta(days=ago), "out_of": out_of}, owner)
        roster = attendance.roster_on(group, exam.held_on)
        entries = {}
        for row, score in zip(roster, scores):
            entries[row.student_id] = {"absent": True, "note": "غايب بعذر"} if score is None else {"score": str(score)}
        exams.record_marks(exam, entries, owner)
        made.append(exam)
    exams.set_published(made[0], True, owner)  # the monthly exam is released; the quiz is still the teacher's
    return made
