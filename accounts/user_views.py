from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from permissions.decorators import require_permission
from permissions.models import Role

from .models import UserProfile
from .user_services import MANAGE_PERMISSION, assignable_roles, create_user_account, reset_user_password, update_user_account


WORDS = {
    "ar": {
        "page_title": "المستخدمون",
        "title": "المستخدمون والكاشيرية",
        "intro": "ضيف الكاشير أو المحاسب أو أمين المخزن، واختار دوره. كل مستخدم بيغيّر كلمة السر المؤقتة أول ما يدخل.",
        "new": "مستخدم جديد",
        "username": "اسم الدخول",
        "display_name": "الاسم",
        "phone": "الموبايل",
        "role": "الدور",
        "active": "نشط",
        "status": "الحالة",
        "last_login": "آخر دخول",
        "never": "لسه ما دخلش",
        "password": "كلمة السر المؤقتة",
        "password_help": "8 حروف على الأقل ومش أرقام بس. هيطلب منه يغيّرها أول دخول.",
        "save": "حفظ",
        "edit": "تعديل",
        "reset": "كلمة سر مؤقتة جديدة",
        "reset_do": "تغيير كلمة السر",
        "back": "العودة للمستخدمين",
        "on": "نشط",
        "off": "موقوف",
        "must_change": "هيغيّر كلمة السر",
        "created": "تم إضافة المستخدم {name}.",
        "updated": "تم حفظ التعديلات.",
        "password_reset": "تم تعيين كلمة سر مؤقتة. هيغيّرها أول ما يدخل.",
        "username_required": "اكتب اسم الدخول.",
        "username_taken": "اسم الدخول ده مستخدم قبل كده.",
        "role_invalid": "اختار دور صحيح.",
        "no_self_lockout": "مينفعش توقف حسابك أو تغيّر دورك بنفسك.",
        "last_owner": "لازم يفضل صاحب حساب واحد نشط على الأقل.",
        "change_title": "تغيير كلمة السر",
        "change_intro": "لازم تغيّر كلمة السر قبل ما تكمّل.",
        "changed": "تم تغيير كلمة السر.",
        "old_password": "كلمة السر الحالية",
        "new_password1": "كلمة السر الجديدة",
        "new_password2": "تأكيد كلمة السر الجديدة",
    },
    "en": {
        "page_title": "Users",
        "title": "Users and cashiers",
        "intro": "Add a cashier, accountant or stock keeper and choose their role. Every user changes the temporary password at first sign-in.",
        "new": "New user",
        "username": "Username",
        "display_name": "Name",
        "phone": "Mobile",
        "role": "Role",
        "active": "Active",
        "status": "Status",
        "last_login": "Last sign-in",
        "never": "Never",
        "password": "Temporary password",
        "password_help": "At least 8 characters, not only digits. They must change it at first sign-in.",
        "save": "Save",
        "edit": "Edit",
        "reset": "New temporary password",
        "reset_do": "Set password",
        "back": "Back to users",
        "on": "Active",
        "off": "Disabled",
        "must_change": "Must change password",
        "created": "User {name} added.",
        "updated": "Changes saved.",
        "password_reset": "Temporary password set. They must change it at next sign-in.",
        "username_required": "Enter a username.",
        "username_taken": "This username is already taken.",
        "role_invalid": "Choose a valid role.",
        "no_self_lockout": "You cannot disable your own account or change your own role.",
        "last_owner": "At least one active owner must remain.",
        "change_title": "Change password",
        "change_intro": "You need to change your password before you continue.",
        "changed": "Password changed.",
        "old_password": "Current password",
        "new_password1": "New password",
        "new_password2": "Confirm new password",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


def _context(request, **extra):
    lang = _lang(request)
    context = {"lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": WORDS[lang], "page_title": WORDS[lang]["page_title"], "section": "settings"}
    context.update(extra)
    return context


def _role_label(role, lang):
    return (role.name_en or role.name_ar) if lang == "en" else (role.name_ar or role.code)


class UserForm(forms.Form):
    username = forms.CharField(max_length=150)
    display_name = forms.CharField(max_length=255, required=False)
    phone = forms.CharField(max_length=50, required=False)
    role = forms.ModelChoiceField(queryset=Role.objects.none())
    password = forms.CharField(widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}))
    active = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, lang="ar", editing=False, **kwargs):
        super().__init__(*args, **kwargs)
        words = WORDS[lang]
        self.fields["role"].queryset = assignable_roles()
        self.fields["role"].label_from_instance = lambda role: _role_label(role, lang)
        for name, field in self.fields.items():
            field.label = words[name]
        self.fields["password"].help_text = words["password_help"]
        if editing:
            del self.fields["password"]
            self.fields["username"].disabled = True
        else:
            del self.fields["active"]


def _error_messages(exc, lang):
    words = WORDS[lang]
    return [words.get(message, message) for message in getattr(exc, "messages", [str(exc)])]


@require_permission(MANAGE_PERMISSION)
def user_list(request):
    lang = _lang(request)
    users = get_user_model().objects.filter(is_superuser=False).select_related("hesba_profile__role").order_by("username")
    rows = []
    for user in users:
        profile = getattr(user, "hesba_profile", None)
        rows.append({
            "user": user,
            "profile": profile,
            "role": _role_label(profile.role, lang) if profile and profile.role else "—",
            "active": user.is_active and bool(profile and profile.active),
        })
    return render(request, "accounts/users/list.html", _context(request, rows=rows))


@require_permission(MANAGE_PERMISSION)
def user_create(request):
    lang = _lang(request)
    form = UserForm(request.POST or None, lang=lang)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            user = create_user_account(username=data["username"], password=data["password"], role=data["role"], actor=request.user, display_name=data["display_name"], phone=data["phone"])
        except ValidationError as exc:
            for message in _error_messages(exc, lang):
                form.add_error(None, message)
        else:
            messages.success(request, WORDS[lang]["created"].format(name=user.username))
            return redirect(f"/settings/users/?lang={lang}")
    return render(request, "accounts/users/form.html", _context(request, form=form, title=WORDS[lang]["new"]))


@require_permission(MANAGE_PERMISSION)
def user_edit(request, pk):
    lang = _lang(request)
    user = get_object_or_404(get_user_model(), pk=pk, is_superuser=False)
    profile = getattr(user, "hesba_profile", None) or UserProfile(user=user)
    initial = {"username": user.username, "display_name": profile.display_name, "phone": profile.phone, "role": profile.role_id, "active": user.is_active and profile.active}
    form = UserForm(request.POST or None, lang=lang, editing=True, initial=initial)
    reset_form = PasswordResetForm(lang=lang)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            update_user_account(user, actor=request.user, role=data["role"], active=data["active"], display_name=data["display_name"], phone=data["phone"])
        except ValidationError as exc:
            for message in _error_messages(exc, lang):
                form.add_error(None, message)
        else:
            messages.success(request, WORDS[lang]["updated"])
            return redirect(f"/settings/users/?lang={lang}")
    return render(request, "accounts/users/form.html", _context(request, form=form, reset_form=reset_form, edited=user, title=user.username))


class PasswordResetForm(forms.Form):
    password = forms.CharField(widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}))

    def __init__(self, *args, lang="ar", **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["password"].label = WORDS[lang]["password"]
        self.fields["password"].help_text = WORDS[lang]["password_help"]


@require_permission(MANAGE_PERMISSION)
def user_reset_password(request, pk):
    lang = _lang(request)
    user = get_object_or_404(get_user_model(), pk=pk, is_superuser=False)
    if request.method == "POST":
        form = PasswordResetForm(request.POST, lang=lang)
        if form.is_valid():
            try:
                reset_user_password(user, password=form.cleaned_data["password"], actor=request.user)
            except ValidationError as exc:
                for message in _error_messages(exc, lang):
                    messages.error(request, message)
            else:
                messages.success(request, WORDS[lang]["password_reset"])
    return redirect(f"/settings/users/{user.pk}/?lang={lang}")


def change_password(request):
    """Any signed-in user changes their own password; clears the must-change flag."""

    lang = _lang(request)
    words = WORDS[lang]
    form = PasswordChangeForm(request.user, request.POST or None)
    for name in ("old_password", "new_password1", "new_password2"):
        form.fields[name].label = words[name]
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        UserProfile.objects.filter(user=user).update(must_change_password=False)
        messages.success(request, words["changed"])
        target = request.POST.get("next") or "/start/"
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
            target = "/start/"
        return redirect(target)
    profile = getattr(request.user, "hesba_profile", None)
    return render(
        request,
        "accounts/users/change_password.html",
        _context(request, form=form, forced=bool(profile and profile.must_change_password), next=request.GET.get("next", request.POST.get("next", "")), page_title=words["change_title"]),
    )
