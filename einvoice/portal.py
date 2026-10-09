"""ETA-002: talking to the Egyptian Tax Authority's e-invoicing system.

Three pieces, each replaceable in tests:

* **Configuration** comes from the deployment's environment, never the
  database, so a client secret is not in backups or on any screen:
  ``ETA_ENVIRONMENT`` (``preprod`` or ``prod``), ``ETA_CLIENT_ID``,
  ``ETA_CLIENT_SECRET``, ``ETA_SIGNER_URL`` and ``ETA_SIGNER_TOKEN``.
* **Signing.** Document version 1.0 must carry the taxpayer's CAdES-BES
  signature, made with the e-signature token (a USB token or an HSM) that
  never leaves the client's hands. Hesba serializes the document the way the
  authority specifies (``serialize``) and asks a signer the client runs next
  to the token to sign that text. The contract is in docs/ETA_INTEGRATION.md:
  ``POST {ETA_SIGNER_URL}`` with ``{"serialized": "..."}`` and a bearer
  ``ETA_SIGNER_TOKEN``, answering ``{"signature": "<base64 CMS>"}``.
* **The API:** a client-credentials token, then submit, read the details of
  a document and cancel it (ETA SDK v1.0, sdk.invoicing.eta.gov.eg).

Network calls go through ``_http`` only, which is what the tests replace.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from decouple import config

ENVIRONMENTS = {
    "preprod": {"identity": "https://id.preprod.eta.gov.eg", "api": "https://api.preprod.invoicing.eta.gov.eg"},
    "prod": {"identity": "https://id.eta.gov.eg", "api": "https://api.invoicing.eta.gov.eg"},
}
TIMEOUT = 30
_TOKEN = {"value": "", "expires": 0.0, "key": ""}


class PortalError(Exception):
    """The authority or the signer refused or could not be reached; ``message`` is for the screen."""

    def __init__(self, message, status=None, payload=None):
        super().__init__(message)
        self.message, self.status, self.payload = message, status, payload or {}


def settings():
    environment = config("ETA_ENVIRONMENT", default="preprod").strip().lower()
    return {
        "environment": environment if environment in ENVIRONMENTS else "preprod",
        "client_id": config("ETA_CLIENT_ID", default="").strip(),
        "client_secret": config("ETA_CLIENT_SECRET", default="").strip(),
        "signer_url": config("ETA_SIGNER_URL", default="").strip(),
        "signer_token": config("ETA_SIGNER_TOKEN", default="").strip(),
    }


def missing():
    """What the deployment still needs before it can send: a list of setting names."""

    values = settings()
    return [name for name, key in (("ETA_CLIENT_ID", "client_id"), ("ETA_CLIENT_SECRET", "client_secret"), ("ETA_SIGNER_URL", "signer_url"))
            if not values[key]]


def _http(method, url, *, body=None, form=None, headers=None):
    """(status, parsed JSON or {}) for one HTTP call. Raises PortalError when unreachable."""

    headers = dict(headers or {})
    data = None
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - fixed https endpoints
            raw = response.read()
            status = response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PortalError(f"unreachable: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        payload = {"raw": raw[:500].decode("utf-8", "replace")}
    return status, payload


# ---- signing ----

def serialize(value, name=None):
    """The authority's canonical text of a document, which is what gets signed.

    A simple value is written in double quotes. Each property of an object is
    its name in capitals, in quotes, then its serialized value. An array writes
    its name once, then each element preceded by the array's name again.
    Numbers keep the text they have in the JSON sent.
    """

    if isinstance(value, dict):
        out = []
        for key, item in value.items():
            upper = f'"{key.upper()}"'
            if isinstance(item, list):
                out.append(upper)
                out.extend(upper + serialize(element) for element in item)
            else:
                out.append(upper + serialize(item))
        return "".join(out)
    if isinstance(value, str):
        return f'"{value}"'
    return f'"{json.dumps(value)}"'


def sign(document):
    """The document with its issuer signature, from the client's signer."""

    values = settings()
    if not values["signer_url"]:
        raise PortalError("no signer")
    unsigned = {key: item for key, item in document.items() if key != "signatures"}
    headers = {"Authorization": f"Bearer {values['signer_token']}"} if values["signer_token"] else {}
    status, payload = _http("POST", values["signer_url"], body={"serialized": serialize(unsigned)}, headers=headers)
    signature = payload.get("signature") if isinstance(payload, dict) else None
    if status != 200 or not signature:
        raise PortalError("signer refused", status, payload)
    return {**unsigned, "signatures": [{"signatureType": "I", "value": signature}]}


# ---- the API ----

def _urls():
    return ENVIRONMENTS[settings()["environment"]]


def token(force=False):
    values = settings()
    key = f"{values['environment']}:{values['client_id']}"
    if not force and _TOKEN["value"] and _TOKEN["key"] == key and _TOKEN["expires"] > time.time() + 60:
        return _TOKEN["value"]
    status, payload = _http("POST", _urls()["identity"] + "/connect/token", form={
        "grant_type": "client_credentials", "client_id": values["client_id"], "client_secret": values["client_secret"], "scope": "InvoicingAPI",
    })
    if status != 200 or not payload.get("access_token"):
        raise PortalError("login refused", status, payload)
    _TOKEN.update(value=payload["access_token"], expires=time.time() + int(payload.get("expires_in", 3600)), key=key)
    return _TOKEN["value"]


def _api(method, path, body=None):
    status, payload = _http(method, _urls()["api"] + path, body=body, headers={"Authorization": f"Bearer {token()}"})
    if status == 401:  # the token expired early: once more with a fresh one
        status, payload = _http(method, _urls()["api"] + path, body=body, headers={"Authorization": f"Bearer {token(force=True)}"})
    return status, payload


def submit(documents):
    status, payload = _api("POST", "/api/v1/documentsubmissions", {"documents": documents})
    if status not in (200, 202):
        raise PortalError("submission refused", status, payload)
    return payload


def details(uuid):
    status, payload = _api("GET", f"/api/v1/documents/{urllib.parse.quote(uuid)}/details")
    if status != 200:
        raise PortalError("details refused", status, payload)
    return payload


def cancel(uuid, reason):
    status, payload = _api("PUT", f"/api/v1/documents/state/{urllib.parse.quote(uuid)}/state", {"status": "cancelled", "reason": reason})
    if status != 200:
        raise PortalError("cancel refused", status, payload)
    return payload


def check_connection():
    """(ok, problems) for the settings screen: settings present, login works, signer answers."""

    problems = [f"missing:{name}" for name in missing()]
    if problems:
        return False, problems
    try:
        token(force=True)
    except PortalError as exc:
        problems.append(f"login:{exc.status or exc.message}")
    try:
        sign({"ping": "hesba"})
    except PortalError as exc:
        problems.append(f"signer:{exc.status or exc.message}")
    return not problems, problems


def error_text(payload):
    """The authority's own reason, flattened to one line for the screen."""

    if not isinstance(payload, dict):
        return ""
    error = payload.get("error") if isinstance(payload.get("error"), dict) else payload
    parts = []
    if isinstance(error, dict):
        if error.get("message"):
            parts.append(str(error["message"]))
        for detail in error.get("details") or []:
            if isinstance(detail, dict):
                parts.append(" ".join(str(detail.get(k, "")) for k in ("propertyPath", "message") if detail.get(k)))
    if not parts and payload.get("error_description"):
        parts.append(str(payload["error_description"]))
    return " · ".join(part for part in parts if part)[:1000]
