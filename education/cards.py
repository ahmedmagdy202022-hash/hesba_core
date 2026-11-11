"""E1: the student's card. A credit-card-size card with the centre's name, the student's name, code, stage and group, and the
student code as a QR.

The QR holds the code and nothing else (``ST-00012``). It is a convenience for staff at the door, not a credential: it opens no page
and no guardian or public screen may accept it. So the card carries no phone, date of birth or payer, and the page never reads them.
"""


from . import services
from .models import Enrollment, EnrollmentStatus, Student, StudyGroup

PER_PAGE = 10  # an A4 sheet of 2 x 5 cards of 85.6 x 54 mm


def _groups_text(student, only=None):
    names = [row.group.name for row in student.enrollments.filter(status=EnrollmentStatus.ACTIVE, group__active=True).select_related("group")
             .order_by("group__name")] if only is None else [only.name]
    return " · ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")


def cards_for(*, student=None, group=None):
    """[{student, code, stage, group}] for one student, or for the active students running in one group (name order)."""

    if student is not None:
        return [{"student": student, "code": student.code, "stage": student.stage, "group": _groups_text(student)}]
    rows = (Enrollment.objects.filter(group=group, status=EnrollmentStatus.ACTIVE, student__active=True).select_related("student")
            .order_by("student__name", "-start_date", "-pk"))
    seen, cards = set(), []
    for row in rows:
        if row.student_id not in seen:
            seen.add(row.student_id)
            cards.append({"student": row.student, "code": row.student.code, "stage": row.student.stage, "group": group.name})
    return cards


def pages(cards):
    return [cards[start:start + PER_PAGE] for start in range(0, len(cards), PER_PAGE)]


def find_student(pk):
    return services.scoped(Student.objects).filter(pk=pk).first()


def find_group(pk):
    return services.scoped(StudyGroup.objects).select_related("course").filter(pk=pk).first()
