from django.core.exceptions import ValidationError

from .models import Period, PeriodStatus


def get_period_for_date(action_date):
    """Return the period covering a date."""

    return Period.objects.filter(start_date__lte=action_date, end_date__gte=action_date).order_by("-start_date").first()


def ensure_period_is_open(action_date):
    """Guard transaction posting against closed periods.

    Transaction posting services can call this before writing movements. Closed
    periods must remain read-only unless an owner reopens them with reason and
    audit logic in a later checkpoint.
    """

    period = get_period_for_date(action_date)
    if period is None:
        raise ValidationError("No period found for this date.")
    if period.status == PeriodStatus.CLOSED:
        raise ValidationError("This period is closed and read-only.")
    return period
