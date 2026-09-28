"""SEC-001: lock sign-in for a while after repeated wrong passwords.

Five wrong passwords for one username within 15 minutes lock that username
for 15 minutes; twenty from one address lock the address. A correct password
during the lock is refused too (otherwise the lock only slows the guessing),
and a successful sign-in clears the username's failures. Locks are audited.
"""

import time
from datetime import timedelta

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model, login as auth_login
from django.contrib.auth.decorators import login_not_required
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from audit.models import AuditEventType, AuditLog

from . import two_factor
from .models import LoginFailure


# SEC-002: who passed the password step and is waiting to enter the app code.
PENDING_KEY = "hesba_two_factor_pending"


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
        user = form.get_user()
        if two_factor.device_for(user) is not None:
            # The password alone does not sign in; failures are cleared only after the code.
            self.request.session.cycle_key()
            lang = "en" if self.request.POST.get("hesba_lang") == "en" else "ar"
            self.request.session[PENDING_KEY] = {"user": user.pk, "backend": getattr(user, "backend", ""), "at": time.time(), "next": self.get_success_url(), "lang": lang}
            return redirect(f"{reverse('login_verify')}?lang={lang}")
        LoginFailure.objects.filter(username__iexact=user.get_username()).delete()
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        form = context.get("form")
        context["login_locked"] = bool(form is not None and form.errors and any(e.code == "locked" for e in form.non_field_errors().as_data()))
        context["lock_minutes"] = _setting("LOGIN_LOCK_MINUTES", 15)
        return context


VERIFY_WORDS = {
    "ar": {"title": "التحقق بخطوتين", "intro": "افتح تطبيق المصادقة على موبايلك واكتب الكود المكوّن من 6 أرقام اللي ظاهر لحسبة.",
           "code": "كود التحقق", "button": "تأكيد الدخول", "wrong": "الكود غلط أو انتهى وقته. اكتب الكود الظاهر دلوقتي.",
           "recovery": "مش معاك الموبايل؟ اكتب كود من أكواد الاسترجاع اللي حفظتها وقت التفعيل.",
           "locked": "محاولات غلط كتير. استنى {minutes} دقيقة وسجّل دخول من الأول.", "back": "الرجوع لتسجيل الدخول", "page": "التحقق بخطوتين - حسبة"},
    "en": {"title": "Two-step sign-in", "intro": "Open the authenticator app on your phone and type the 6-digit code shown for Hesba.",
           "code": "Verification code", "button": "Confirm sign-in", "wrong": "Wrong or expired code. Type the code shown now.",
           "recovery": "No phone with you? Type one of the recovery codes you saved when you switched this on.",
           "locked": "Too many wrong attempts. Wait {minutes} minutes and sign in again.", "back": "Back to sign in", "page": "Two-step sign-in - Hesba"},
}


def _pending(request):
    pending = request.session.get(PENDING_KEY)
    if not pending or time.time() - pending.get("at", 0) > _setting("TWO_FACTOR_PENDING_SECONDS", 300):
        request.session.pop(PENDING_KEY, None)
        return None, None
    user = get_user_model().objects.filter(pk=pending.get("user"), is_active=True).first()
    if user is None or two_factor.device_for(user) is None:
        request.session.pop(PENDING_KEY, None)
        return None, None
    return pending, user


@login_not_required
@never_cache
@csrf_protect
def login_verify(request):
    """Second step of sign-in for a user with an authenticator app."""

    pending, user = _pending(request)
    if pending is None:
        return redirect(settings.LOGIN_REDIRECT_URL if request.user.is_authenticated else "login")
    lang = "en" if (request.POST.get("lang") or request.GET.get("lang") or pending.get("lang")) == "en" else "ar"
    words = VERIFY_WORDS[lang]
    username, ip = user.get_username(), client_ip(request)
    state = ""
    if is_locked(username, ip):
        request.session.pop(PENDING_KEY, None)
        state = "locked"
    elif request.method == "POST":
        if two_factor.verify(user, request.POST.get("code")):
            request.session.pop(PENDING_KEY, None)
            LoginFailure.objects.filter(username__iexact=username).delete()
            auth_login(request, user, backend=pending.get("backend") or None)
            target = pending.get("next") or settings.LOGIN_REDIRECT_URL
            if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
                target = settings.LOGIN_REDIRECT_URL
            return redirect(target)
        record_failure(username, ip)
        if is_locked(username, ip):
            request.session.pop(PENDING_KEY, None)
            state = "locked"
        else:
            state = "wrong"
    return render(request, "registration/login_verify.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "state": state,
        "locked_text": words["locked"].format(minutes=_setting("LOGIN_LOCK_MINUTES", 15)),
    }, status=200)
