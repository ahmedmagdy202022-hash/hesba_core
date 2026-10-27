"""T8 (live part): sending e-receipts to the Tax Authority's receipt API (ETA eReceipt SDK).

* **Configuration.** The portal (``ETA_ENVIRONMENT``, ``preprod`` or ``prod``) is the one the e-invoice uses. The
  till's own secrets come from the deployment's environment, never the database: ``ETA_RECEIPT_CLIENT_ID``,
  ``ETA_RECEIPT_CLIENT_SECRET`` and ``ETA_RECEIPT_PRESHARED_KEY``. The device's serial number, OS version and model
  framework, which the portal registered with the till, are on the device record.
* **Login** (Authenticate POS): ``POST {identity}/connect/token`` with the client credentials, and the device in the
  headers ``posserial``, ``pososversion``, ``posmodelframework`` and ``presharedkey``. A token lasts about an hour.
* **Submit** (Submit Receipt Documents): ``POST {api}/api/v1/receiptsubmissions`` with ``{"receipts": [...]}``. Each
  receipt goes exactly as it was hashed, oldest first, so the device's chain arrives in order. The answer (202) names
  the accepted receipts (``uuid``, ``longId``) and the rejected ones with the authority's reason.
* **Follow up** (Get Receipt Submission): ``GET {api}/api/v1/receiptsubmissions/{submissionUuid}/details`` turns
  a sent receipt into valid or invalid.
* **Signature.** The SDK lists an issuer signature (``signatures``, type ``I``) and says its validation is not
  deployed yet. When the client's signer is set up for the e-invoice (``ETA_SIGNER_URL``), the batch is signed the
  same way; until then receipts go without one.

Network calls go through ``einvoice.portal._http``, which is what the tests replace.
"""

import json
import time
import urllib.parse

from decouple import config
from django.db import transaction
from django.utils import timezone

from einvoice import portal as eta

from .models import Device, Receipt, ReceiptStatus

BATCH = 100
_TOKEN = {"value": "", "expires": 0.0, "key": ""}


def settings():
    return {
        "environment": eta.settings()["environment"],
        "client_id": config("ETA_RECEIPT_CLIENT_ID", default="").strip(),
        "client_secret": config("ETA_RECEIPT_CLIENT_SECRET", default="").strip(),
        "preshared_key": config("ETA_RECEIPT_PRESHARED_KEY", default="").strip(),
    }


def missing(device=None):
    """What is still needed before receipts can be sent: setting names, and the device fields the portal checks."""

    values = settings()
    needs = [] if values["environment"] in eta.ENVIRONMENTS else ["ETA_ENVIRONMENT"]
    needs += [name for name, key in (("ETA_RECEIPT_CLIENT_ID", "client_id"), ("ETA_RECEIPT_CLIENT_SECRET", "client_secret"),
                                     ("ETA_RECEIPT_PRESHARED_KEY", "preshared_key")) if not values[key]]
    device = device if device is not None else Device.objects.filter(active=True).order_by("pk").first()
    if device is None:
        needs.append("device")
    else:
        needs += [name for name in ("os_version", "model_framework") if not getattr(device, name)]
    return needs


def _headers(device):
    return {"posserial": device.serial_number, "pososversion": device.os_version, "posmodelframework": device.model_framework,
            "presharedkey": settings()["preshared_key"]}


def token(device, force=False):
    values = settings()
    key = f"{values['environment']}:{values['client_id']}:{device.serial_number}"
    if not force and _TOKEN["value"] and _TOKEN["key"] == key and _TOKEN["expires"] > time.time() + 60:
        return _TOKEN["value"]
    status, payload = eta._http("POST", eta._urls()["identity"] + "/connect/token", headers=_headers(device),
                                form={"grant_type": "client_credentials", "client_id": values["client_id"], "client_secret": values["client_secret"]})
    if status != 200 or not payload.get("access_token"):
        raise eta.PortalError("login refused", status, payload)
    _TOKEN.update(value=payload["access_token"], expires=time.time() + int(payload.get("expires_in", 3600)), key=key)
    return _TOKEN["value"]


def _api(device, method, path, raw=None):
    def call(fresh):
        headers = {**_headers(device), "Authorization": f"Bearer {token(device, force=fresh)}"}
        return eta._http(method, eta._urls()["api"] + path, raw=raw, headers=headers)

    status, payload = call(False)
    if status == 401:  # the token expired early: once more with a fresh one
        status, payload = call(True)
    return status, payload


def _batch_text(receipts):
    """The submission as JSON text, each receipt exactly as hashed (a JSON round trip could rewrite its numbers)."""

    body = '{"receipts": [' + ", ".join(receipt.document_text for receipt in receipts) + "]"
    if eta.settings()["signer_url"] and eta.settings()["signer_token"]:
        signed = eta.sign({"receipts": [receipt.document for receipt in receipts]})
        body += ', "signatures": ' + json.dumps(signed["signatures"], ensure_ascii=False)
    return body + "}"


def _devices(status):
    """Every device with receipts in ``status``: a till replaced by a new serial still sends and follows up its own
    (Codex #281), not only the one issuing new sales."""

    return list(Device.objects.filter(receipts__status=status).distinct().order_by("pk"))


def send(limit=BATCH):
    """Send each device's queued receipts, oldest first. Returns {"accepted", "rejected", "message"}.

    Nothing is sent while a setting is missing. A refused or unreachable call leaves the receipts queued with the
    reason, to go with the next try inside their 24 hours."""

    needs = missing()
    if needs:
        return {"accepted": 0, "rejected": 0, "message": "missing:" + ",".join(needs)}
    total = {"accepted": 0, "rejected": 0, "message": ""}
    for device in _devices(ReceiptStatus.QUEUED):
        if missing(device):
            total["message"] = "missing:" + ",".join(missing(device))
            continue
        result = _send_device(device, limit)
        total["accepted"] += result["accepted"]
        total["rejected"] += result["rejected"]
        total["message"] = result["message"] or total["message"]
    return total


def _send_device(device, limit):
    with transaction.atomic():
        # One sending per device at a time (Codex #281): a second run waits here rather than sending later receipts
        # ahead of a batch still on its way, so the chain always reaches the authority in order.
        Device.objects.select_for_update().get(pk=device.pk)
        receipts = list(Receipt.objects.filter(device=device, status=ReceiptStatus.QUEUED).exclude(document_text="")
                        .order_by("issued_at", "pk")[:limit])
        if not receipts:
            return {"accepted": 0, "rejected": 0, "message": ""}
        try:
            status, payload = _api(device, "POST", "/api/v1/receiptsubmissions", raw=_batch_text(receipts))
        except eta.PortalError as exc:
            return _keep(receipts, exc.message)
        if status not in (200, 202):
            if status == 422:  # the same batch went in under ten minutes ago: the next try sends it
                return _keep(receipts, f"duplicate:{payload.get('error', {}).get('message', '') if isinstance(payload.get('error'), dict) else ''}")
            return _keep(receipts, f"refused:{status} {eta.error_text(payload)}".strip())
        by_uuid = {receipt.uuid: receipt for receipt in receipts}
        now = timezone.now()
        accepted = rejected = 0
        for row in payload.get("acceptedDocuments") or []:
            receipt = by_uuid.get(row.get("uuid"))
            if receipt is not None:
                receipt.status, receipt.long_id, receipt.message = ReceiptStatus.SUBMITTED, str(row.get("longId") or "")[:200], ""
                receipt.submission_uuid, receipt.submitted_at = str(payload.get("submissionUUID") or "")[:64], now
                receipt.save(update_fields=["status", "long_id", "message", "submission_uuid", "submitted_at"])
                accepted += 1
        for row in payload.get("rejectedDocuments") or []:
            receipt = by_uuid.get(row.get("uuid"))
            if receipt is not None:
                receipt.status, receipt.message = ReceiptStatus.INVALID, eta.error_text(row) or "rejected"
                receipt.submission_uuid, receipt.submitted_at = str(payload.get("submissionUUID") or "")[:64], now
                receipt.save(update_fields=["status", "message", "submission_uuid", "submitted_at"])
                rejected += 1
    return {"accepted": accepted, "rejected": rejected, "message": ""}


def _keep(receipts, message):
    for receipt in receipts:
        receipt.message = message[:1000]
        receipt.save(update_fields=["message"])
    return {"accepted": 0, "rejected": 0, "message": message}


def submission_error_text(error):
    """One line from a Get Receipt Submission error (Codex #281): its ``error`` text (or ``errorAr``), with the
    ``propertyPath`` it names, then any ``innerError`` the same way."""

    parts = []
    while isinstance(error, dict) and len(parts) < 5:
        text = str(error.get("error") or error.get("errorAr") or error.get("message") or error.get("errorCode") or "")
        path = str(error.get("propertyPath") or "")
        if text or path:
            parts.append(f"{path}: {text}" if path and text else text or path)
        error = error.get("innerError")
    return " · ".join(parts)


def follow_up(limit=BATCH):
    """Turn sent receipts into valid or invalid from their submission's details. Returns how many changed."""

    if missing():
        return 0
    changed = 0
    for device in _devices(ReceiptStatus.SUBMITTED):
        if missing(device):
            continue
        sent = Receipt.objects.filter(device=device, status=ReceiptStatus.SUBMITTED).exclude(submission_uuid="")
        # Codex #281: the model's default ordering would put its columns in the DISTINCT and repeat each submission
        # once per receipt; ordering by the submission alone makes each one a single call.
        for submission in list(sent.order_by("submission_uuid").values_list("submission_uuid", flat=True).distinct()[:limit]):
            try:
                status, payload = _api(device, "GET", f"/api/v1/receiptsubmissions/{urllib.parse.quote(submission)}/details?PageSize=100")
            except eta.PortalError:
                continue
            if status != 200:
                continue
            for row in payload.get("receipts") or []:
                verdict = {"Valid": ReceiptStatus.VALID, "Invalid": ReceiptStatus.INVALID}.get(str(row.get("status")))
                if verdict is None:
                    continue
                errors = "; ".join(submission_error_text(item.get("error")) or str(item.get("stepNameAr") or item.get("stepName") or "")
                                   for item in row.get("errors") or [] if isinstance(item, dict))
                changed += sent.filter(submission_uuid=submission, uuid=row.get("uuid")).update(status=verdict, message=errors[:1000])
    return changed
