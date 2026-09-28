"""BACKUP-002: the owner creates the key their backups are locked with."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.cache import never_cache

from audit.models import AuditEventType, AuditLog
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from .backup_crypto import SETTING_KEY, configured_public_key, fingerprint_text, load_public, new_key_pair
from .models import SystemSetting


WORDS = {
    "ar": {
        "page_title": "النسخ الاحتياطي", "title": "مفتاح النسخ الاحتياطي", "back": "العودة للإعدادات",
        "lead": "كل نسخة احتياطية بتتقفل بمفتاح يخصك إنت بس. البرنامج بيحتفظ بنصّ المفتاح اللي بيقفل، والنص اللي بيفتح بيظهرلك مرة واحدة ومش بيتحفظ في أي مكان — ولا حتى عند حسبة.",
        "on": "النسخ الاحتياطي مشفّر", "off": "النسخ الاحتياطي مش مشفّر لسه", "fingerprint": "بصمة المفتاح", "since": "من",
        "create": "إنشاء مفتاح", "replace": "إنشاء مفتاح جديد بدل الحالي", "password": "كلمة السر بتاعتك (للتأكيد)",
        "replace_warning": "النسخ القديمة هتفضل محتاجة المفتاح القديم. متمسحهوش.", "bad_password": "كلمة السر غلط.",
        "key_title": "مفتاحك الخاص — احفظه دلوقتي", "key_help": "ده الشيء الوحيد اللي بيفتح النسخ الاحتياطية. هيظهر مرة واحدة بس. نزّله كملف واحفظه في مكانين (فلاشة + حسابك على Google Drive) أو اطبعه واحفظه في مكان آمن. لو ضاع، النسخ المشفّرة بيه مش هتتفتح أبدًا — ولا حسبة تقدر تفتحها.",
        "download": "تنزيل المفتاح كملف", "copy": "نسخ المفتاح", "saved": "حفظته في مكان آمن", "view_only": "إنشاء المفتاح لصاحب الحساب بس.",
        "created": "اتعمل مفتاح النسخ الاحتياطي. من دلوقتي كل نسخة بتتشفّر بيه.",
    },
    "en": {
        "page_title": "Backups", "title": "Backup key", "back": "Back to settings",
        "lead": "Every backup is locked with a key that is yours alone. Hesba keeps the part of the key that locks; the part that opens is shown to you once and is stored nowhere — not even at Hesba.",
        "on": "Backups are encrypted", "off": "Backups are not encrypted yet", "fingerprint": "Key fingerprint", "since": "since",
        "create": "Create key", "replace": "Create a new key instead", "password": "Your password (to confirm)",
        "replace_warning": "Older backups will still need the old key. Do not delete it.", "bad_password": "Wrong password.",
        "key_title": "Your private key — save it now", "key_help": "This is the only thing that opens your backups, and it is shown once. Download it and keep it in two places (a USB stick and your Google Drive) or print it and keep it safe. If it is lost, backups made with it can never be opened — not even by Hesba.",
        "download": "Download the key file", "copy": "Copy key", "saved": "I saved it somewhere safe", "view_only": "Only the account owner can create the key.",
        "created": "Backup key created. From now on every backup is encrypted with it.",
    },
}


def _lang(request):
    return "en" if request.GET.get("lang") == "en" or request.POST.get("lang") == "en" else "ar"


@never_cache
@require_permission("settings.view_settings")
def backup_key(request):
    lang = _lang(request)
    words = WORDS[lang]
    can_manage = user_has_permission(request.user, "settings.manage_settings")
    private_text, error = None, ""
    if request.method == "POST":
        if not can_manage:
            raise PermissionDenied("Creating the backup key needs settings.manage_settings.")
        if not request.user.check_password(request.POST.get("password") or ""):
            error = words["bad_password"]
        else:
            public_text, private_text = new_key_pair()
            with transaction.atomic():
                previous = configured_public_key()
                SystemSetting.objects.update_or_create(key=SETTING_KEY, defaults={
                    "value": public_text, "data_type": SystemSetting.DataType.STRING, "active": True,
                    "description": "BACKUP-002: public key backups are encrypted to. The private key is kept by the owner only.",
                })
                AuditLog.objects.create(
                    event_type=AuditEventType.UPDATE, actor=request.user, module="settings", action="create_backup_key",
                    object_type="settings_core.SystemSetting", object_id=SETTING_KEY,
                    before_data={"fingerprint": fingerprint_text(previous)} if previous else {},
                    after_data={"fingerprint": fingerprint_text(load_public(public_text))},
                )
            messages.success(request, words["created"])
    setting = SystemSetting.objects.filter(key=SETTING_KEY, active=True).first()
    public_key = configured_public_key()
    return render(request, "settings_core/backup_key.html", {
        "lang": lang, "dir": "ltr" if lang == "en" else "rtl", "words": words, "page_title": words["page_title"],
        "can_manage": can_manage, "error": error, "private_text": private_text,
        "fingerprint": fingerprint_text(public_key) if public_key else "", "since": setting.updated_at if setting else None,
        "back_url": f"{reverse('settings_core:overview')}?lang={lang}",
    })
