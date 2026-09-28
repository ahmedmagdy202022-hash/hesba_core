"""BACKUP-002: the owner creates the key their backups are locked with."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache

from audit.models import AuditEventType, AuditLog
from permissions.decorators import require_permission
from permissions.services import user_has_permission

from . import drive_backup
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
        "drive": "Google Drive بتاعك", "drive_lead": "كل ليلة النسخة المشفّرة بتترفع على Google Drive بتاعك في فولدر «Hesba Backups». حسبة بتقدر تشوف الفولدر ده بس، ومش بتشوف أي حاجة تانية في الـ Drive.",
        "drive_connect": "ربط Google Drive", "drive_disconnect": "فصل Google Drive", "backup_now": "اعمل نسخة وارفعها دلوقتي",
        "drive_on": "مربوط", "drive_off": "مش مربوط", "last_upload": "آخر رفع", "never": "لسه", "last_error": "آخر مشكلة",
        "drive_needs_key": "اعمل مفتاح النسخ الاحتياطي الأول — مفيش حاجة بتترفع من غير تشفير.",
        "drive_unavailable": "ربط Google Drive مش متفعّل على السيرفر ده لسه.",
        "drive_connected": "اتربط Google Drive. أول نسخة هتترفع الليلة، أو دوس «اعمل نسخة وارفعها دلوقتي».",
        "drive_disconnected": "اتفصل Google Drive. النسخ اللي اترفعت قبل كده فاضلة في الـ Drive بتاعك.",
        "drive_uploaded": "اترفعت النسخة: {file}", "drive_failed": "الرفع ماتمّش: {error}", "drive_denied": "الربط اتلغى أو اتأخر. جرّب تاني.",
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
        "drive": "Your Google Drive", "drive_lead": "Every night the encrypted backup is uploaded to your Google Drive, in a “Hesba Backups” folder. Hesba can see that folder only, nothing else in your Drive.",
        "drive_connect": "Connect Google Drive", "drive_disconnect": "Disconnect Google Drive", "backup_now": "Back up and upload now",
        "drive_on": "Connected", "drive_off": "Not connected", "last_upload": "Last upload", "never": "Not yet", "last_error": "Last problem",
        "drive_needs_key": "Create the backup key first — nothing is uploaded unencrypted.",
        "drive_unavailable": "Google Drive is not enabled on this server yet.",
        "drive_connected": "Google Drive connected. The first backup goes up tonight, or press “Back up and upload now”.",
        "drive_disconnected": "Google Drive disconnected. Backups already uploaded stay in your Drive.",
        "drive_uploaded": "Backup uploaded: {file}", "drive_failed": "Upload failed: {error}", "drive_denied": "The connection was cancelled or timed out. Try again.",
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
    action = request.POST.get("action", "create_key") if request.method == "POST" else ""
    if action and not can_manage:
        raise PermissionDenied("Backup settings need settings.manage_settings.")
    if action == "drive_connect":
        if not drive_backup.client_configured() or configured_public_key() is None:
            messages.error(request, words["drive_unavailable"] if not drive_backup.client_configured() else words["drive_needs_key"])
            return redirect(f"{reverse('settings_core:backup_key')}?lang={lang}")
        state = drive_backup.new_state()
        request.session["hesba_drive_state"] = {"state": state, "lang": lang}
        return redirect(drive_backup.auth_url(_callback_uri(request), state))
    if action == "drive_disconnect":
        drive_backup.disconnect(request.user)
        messages.success(request, words["drive_disconnected"])
        return redirect(f"{reverse('settings_core:backup_key')}?lang={lang}")
    if action == "backup_now":
        try:
            report = drive_backup.run_nightly(user=request.user, force=True)
        except drive_backup.DriveError as exc:
            messages.error(request, words["drive_failed"].format(error=exc))
        else:
            messages.success(request, words["drive_uploaded"].format(file=report["file"]))
        return redirect(f"{reverse('settings_core:backup_key')}?lang={lang}")
    if action == "create_key":
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
        "drive": drive_backup.status(),
    })


def _callback_uri(request):
    return request.build_absolute_uri(reverse("settings_core:backup_drive_callback"))


@never_cache
@require_permission("settings.manage_settings")
def drive_callback(request):
    """Google sends the owner back here after they allow (or refuse) Drive access."""

    pending = request.session.pop("hesba_drive_state", None) or {}
    lang = pending.get("lang", "ar")
    words = WORDS[lang]
    target = f"{reverse('settings_core:backup_key')}?lang={lang}"
    state, code = request.GET.get("state", ""), request.GET.get("code", "")
    import hmac

    if not pending or not code or not hmac.compare_digest(state.encode(), pending.get("state", "").encode()):
        messages.error(request, words["drive_denied"])
        return redirect(target)
    try:
        drive_backup.connect(code, _callback_uri(request), request.user)
    except drive_backup.DriveError as exc:
        messages.error(request, words["drive_failed"].format(error=exc))
    else:
        messages.success(request, words["drive_connected"])
    return redirect(target)
