"""BACKUP-003: every night, the encrypted backup goes to the client's own Google Drive.

* The client connects **their** Google account once (Settings -> Backups). Hesba
  asks only for ``drive.file``: it can see and change the files it created
  itself (the backups folder) and nothing else in that Drive.
* Nothing is uploaded unless backups are encrypted to the owner's key
  (BACKUP-002), so what sits in Drive is unreadable without that key, to
  Google and to whoever runs the server alike.
* The newest ``BACKUP_DRIVE_KEEP`` backups are kept in the "Hesba Backups"
  folder; older ones are removed.
* It runs from ``manage.py nightly_backup`` (cron) or from ``/ops/nightly/``
  with the ``NIGHTLY_TOKEN`` for hosts without cron: an external scheduler
  calls it once a day, and it does nothing if today's backup is already up.

Plain HTTPS with the standard library; no Google client library is needed.
"""

import json
import secrets
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from io import StringIO
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.db import transaction
from django.utils import timezone

from audit.models import AuditEventType, AuditLog

from .backup_crypto import SUFFIX, configured_public_key
from .models import SystemSetting


SCOPE = "https://www.googleapis.com/auth/drive.file"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
FILES_URL = "https://www.googleapis.com/drive/v3/files"
UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,size"
FOLDER_NAME = "Hesba Backups"
FOLDER_MIME = "application/vnd.google-apps.folder"

REFRESH_KEY = "backup.drive.refresh_token"
FOLDER_KEY = "backup.drive.folder_id"
LAST_OK_KEY = "backup.drive.last_upload"
LAST_FILE_KEY = "backup.drive.last_file"
LAST_ERROR_KEY = "backup.drive.last_error"


class DriveError(RuntimeError):
    """Google refused or could not be reached; the message is safe to show."""


def _setting(key):
    return SystemSetting.objects.filter(key=key, active=True).values_list("value", flat=True).first() or ""


def _store(key, value, sensitive=False, description=""):
    SystemSetting.objects.update_or_create(key=key, defaults={"value": value, "active": True, "is_sensitive": sensitive, "description": description})


def client_configured():
    return bool(getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "") and getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", ""))


def is_connected():
    return bool(_setting(REFRESH_KEY))


def status():
    return {
        "configured": client_configured(),
        "connected": is_connected(),
        "last_upload": _setting(LAST_OK_KEY),
        "last_file": _setting(LAST_FILE_KEY),
        "last_error": _setting(LAST_ERROR_KEY),
    }


def _http(method, url, data=None, headers=None):
    """One HTTPS call; returns (status, body bytes). Isolated so tests replace it."""

    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise DriveError(f"Google could not be reached: {exc}") from exc


def _json(method, url, data=None, headers=None, ok=(200,)):
    code, body = _http(method, url, data, headers)
    if code not in ok:
        detail = body[:300].decode("utf-8", "replace")
        raise DriveError(f"Google answered {code}: {detail}")
    return json.loads(body or b"{}")


def auth_url(redirect_uri, state):
    query = urllib.parse.urlencode({
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state, "include_granted_scopes": "true",
    })
    return f"{AUTH_URL}?{query}"


def new_state():
    return secrets.token_urlsafe(24)


def _form(values):
    return urllib.parse.urlencode(values).encode()


def connect(code, redirect_uri, user):
    """Finish the Google consent: keep the refresh token in the client's own database."""

    token = _json("POST", TOKEN_URL, _form({
        "code": code, "client_id": settings.GOOGLE_OAUTH_CLIENT_ID, "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }), {"Content-Type": "application/x-www-form-urlencoded"})
    refresh = token.get("refresh_token")
    if not refresh:
        raise DriveError("Google did not return a long-lived token; remove Hesba from your Google account's third-party access and connect again.")
    with transaction.atomic():
        _store(REFRESH_KEY, refresh, sensitive=True, description="BACKUP-003: Google Drive access (drive.file only) for the nightly encrypted backup.")
        SystemSetting.objects.filter(key=FOLDER_KEY).delete()
        _audit(user, "connect_backup_drive", {})


def disconnect(user):
    refresh = _setting(REFRESH_KEY)
    if refresh:
        try:
            _http("POST", REVOKE_URL, _form({"token": refresh}), {"Content-Type": "application/x-www-form-urlencoded"})
        except DriveError:
            pass  # forgetting the token locally is what matters; revoking is a courtesy
    with transaction.atomic():
        SystemSetting.objects.filter(key__in=[REFRESH_KEY, FOLDER_KEY]).delete()
        _audit(user, "disconnect_backup_drive", {})


def _access_token():
    token = _json("POST", TOKEN_URL, _form({
        "refresh_token": _setting(REFRESH_KEY), "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET, "grant_type": "refresh_token",
    }), {"Content-Type": "application/x-www-form-urlencoded"})
    return token["access_token"]


def _folder(access):
    auth = {"Authorization": f"Bearer {access}"}
    folder_id = _setting(FOLDER_KEY)
    if folder_id:
        code, body = _http("GET", f"{FILES_URL}/{urllib.parse.quote(folder_id)}?fields=id,trashed", headers=auth)
        if code == 200 and not json.loads(body).get("trashed"):
            return folder_id
    created = _json("POST", f"{FILES_URL}?fields=id", json.dumps({"name": FOLDER_NAME, "mimeType": FOLDER_MIME}).encode(),
                    {**auth, "Content-Type": "application/json"})
    _store(FOLDER_KEY, created["id"], description="BACKUP-003: the Drive folder holding the encrypted backups.")
    return created["id"]


def _upload(access, folder_id, path):
    boundary = f"hesba-{uuid.uuid4().hex}"
    metadata = json.dumps({"name": path.name, "parents": [folder_id]}).encode()
    body = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(), metadata,
        f"\r\n--{boundary}\r\nContent-Type: application/octet-stream\r\n\r\n".encode(), path.read_bytes(),
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    return _json("POST", UPLOAD_URL, body, {"Authorization": f"Bearer {access}", "Content-Type": f"multipart/related; boundary={boundary}"})


def _prune(access, folder_id, keep):
    auth = {"Authorization": f"Bearer {access}"}
    query = urllib.parse.urlencode({
        "q": f"'{folder_id}' in parents and trashed = false and name contains '{SUFFIX}'",
        "orderBy": "createdTime desc", "fields": "files(id,name)", "pageSize": 1000,
    })
    files = _json("GET", f"{FILES_URL}?{query}", headers=auth).get("files", [])
    removed = 0
    for old in files[keep:]:
        if old["name"].startswith("hesba-") and old["name"].endswith(SUFFIX):
            _http("DELETE", f"{FILES_URL}/{urllib.parse.quote(old['id'])}", headers=auth)
            removed += 1
    return removed


def _audit(user, action, after):
    AuditLog.objects.create(event_type=AuditEventType.UPDATE, actor=user if getattr(user, "is_authenticated", False) else None,
                            module="settings", action=action, object_type="settings_core.SystemSetting", object_id="backup.drive", after_data=after)


def uploaded_today():
    last = _setting(LAST_OK_KEY)
    return bool(last) and last[:10] == timezone.localdate().isoformat()


def run_nightly(user=None, force=False):
    """Make an encrypted backup and send it to Drive. Returns a small report dict."""

    if not force and uploaded_today():
        return {"status": "skipped", "reason": "already uploaded today"}
    if configured_public_key() is None:
        # Never upload anything readable.
        raise DriveError("Create the backup key first (Settings -> Backups); nothing is uploaded unencrypted.")
    if not client_configured() or not is_connected():
        raise DriveError("Google Drive is not connected.")
    with tempfile.TemporaryDirectory() as folder:
        call_command("backup_data", dir=folder, keep=1, stdout=StringIO())
        path = next(Path(folder).glob(f"hesba-*{SUFFIX}"))
        try:
            access = _access_token()
            folder_id = _folder(access)
            uploaded = _upload(access, folder_id, path)
            removed = _prune(access, folder_id, getattr(settings, "BACKUP_DRIVE_KEEP", 30))
        except (DriveError, KeyError, ValueError) as exc:
            _store(LAST_ERROR_KEY, f"{timezone.now().isoformat(timespec='minutes')} {exc}"[:500])
            _audit(user, "backup_drive_failed", {"error": str(exc)[:300]})
            raise DriveError(str(exc)) from exc
    now = timezone.localtime().isoformat(timespec="minutes")
    with transaction.atomic():
        _store(LAST_OK_KEY, now)
        _store(LAST_FILE_KEY, uploaded.get("name", path.name))
        SystemSetting.objects.filter(key=LAST_ERROR_KEY).delete()
        _audit(user, "backup_drive_uploaded", {"file": uploaded.get("name", path.name), "size": uploaded.get("size"), "removed_old": removed})
    return {"status": "uploaded", "file": uploaded.get("name", path.name), "removed_old": removed}
