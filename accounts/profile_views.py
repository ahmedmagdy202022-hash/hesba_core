from django.shortcuts import render


STRINGS = {
    "ar": {"page_title": "الملف الشخصي", "dashboard": "لوحة القيادة", "language": "English", "profile": "ملفي الشخصي", "role": "الدور", "permissions": "صلاحياتي", "none": "لا توجد صلاحيات تشغيل مرتبطة بهذا المستخدم."},
    "en": {"page_title": "Profile", "dashboard": "Dashboard", "language": "العربية", "profile": "My profile", "role": "Role", "permissions": "My permissions", "none": "No operational permissions are linked to this user."},
}


def profile(request):
    lang = "en" if request.GET.get("lang") == "en" else "ar"
    user_profile = getattr(request.user, "hesba_profile", None)
    role = user_profile.role if user_profile and user_profile.active else None
    permissions = []
    if role and role.active:
        permissions = role.rolepermission_set.filter(
            allow=True, permission__active=True
        ).select_related("permission").order_by("permission__module", "permission__code")
    return render(
        request,
        "accounts/profile.html",
        {
            "lang": lang,
            "dir": "ltr" if lang == "en" else "rtl",
            "words": STRINGS[lang],
            "page_title": STRINGS[lang]["page_title"],
            "user_profile": user_profile,
            "role": role,
            "role_permissions": permissions,
        },
    )

