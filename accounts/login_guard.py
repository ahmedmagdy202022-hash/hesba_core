"""SEC-001: lock sign-in for a while after repeated wrong passwords.

Five wrong passwords for one username within 15 minutes lock that username
for 15 minutes; twenty from one address lock the address. A correct password
during the lock is refused too (otherwise the lock only slows the guessing),
and a successful sign-in clears the username's failures. Locks are audited.
"""

from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from .models import LoginFailure


def _setting(name, default):
    return getattr(settings, name, default)


def window():
    return timedelta(minutes=_setting("LOGIN_LOCK_MINUTES", 15))


def client_ip(request):
    return (request.META.get("REMOTE_ADDR") or "")[:64]


def is_locked(username, ip):
    since = timezone.now() - window()
    recent = LoginFailure.objects.filter(created_at__gte=since)
    by_user = recent.filter(username__iexact=username).count() if username else 0
    by_ip = recent.filter(ip_address=ip).count() if ip else 0
    return by_user >= _setting("LOGIN_MAX_FAILURES", 5) or by_ip >= _setting("LOGIN_MAX_FAILURES_PER_IP", 20)


def record_failure(username, ip):
    LoginFailure.objects.create(username=(username or "")[:150], ip_address=ip)
    if is_locked(username, ip):
        AuditLog.objects.create(event_type=AuditEventType.LOGIN, actor=None, module="accounts", action="login_locked",
                                object_type="auth.User", object_id=(username or "")[:100], after_data={"ip": ip},
                                reason="Too many wrong passwords; sign-in paused.")
    # Old rows are useless; keep the table small.
    LoginFailure.objects.filter(created_at__lt=timezone.now() - timedelta(days=1)).delete()


class GuardedAuthenticationForm(AuthenticationForm):
    error_messages = dict(AuthenticationForm.error_messages, locked="locked")

    def clean(self):
        username = (self.data.get("username") or "").strip()
        ip = client_ip(self.request) if self.request else ""
        if is_locked(username, ip):
            raise forms.ValidationError(self.error_messages["locked"], code="locked")
        try:
            return super().clean()
        except forms.ValidationError:
            record_failure(username, ip)
            if is_locked(username, ip):
                raise forms.ValidationError(self.error_messages["locked"], code="locked")
            raise


class GuardedLoginView(LoginView):
    form_class = GuardedAuthenticationForm

    def form_valid(self, form):
        LoginFailure.objects.filter(username__iexact=form.get_user().get_username()).delete()
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form")
        context["login_locked"] = bool(form is not None and form.errors and any(e.code == "locked" for e in form.non_field_errors().as_data()))
        context["lock_minutes"] = _setting("LOGIN_LOCK_MINUTES", 15)
        return context
