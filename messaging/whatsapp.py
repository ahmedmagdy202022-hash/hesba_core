"""T9 (live part): the outbox's messages sent by the shop's own WhatsApp Business account (WhatsApp Cloud API).

* **Configuration** comes from the deployment's environment, never the database: ``WHATSAPP_TOKEN`` (a system
  user's access token), ``WHATSAPP_PHONE_NUMBER_ID`` (the sending number's id in Meta's WhatsApp Manager),
  ``WHATSAPP_API_VERSION`` (default ``v21.0``) and ``WHATSAPP_LANGUAGE`` (the template's language, default ``ar``).
* **Templates.** A business may start a chat only with a template Meta approved. Each kind of message names its
  template on the outbox screen. The template's body takes two parameters: ``{{1}}``, the company, and ``{{2}}``, the
  message as the shop wrote it. Suggested wording to submit to Meta: ``رسالة من {{1}}: {{2}}``. A kind with no
  template stays a one-click send from the outbox.
* **Sending:** ``POST https://graph.facebook.com/{version}/{phone_number_id}/messages``, a template message. The
  answer's ``messages[0].id`` is kept on the message.

Network calls go through ``_http`` only, which is what the tests replace.
"""

import json
import re
import urllib.error
import urllib.request

from decouple import config

TIMEOUT = 20


def settings():
    return {
        "token": config("WHATSAPP_TOKEN", default="").strip(),
        "phone_number_id": config("WHATSAPP_PHONE_NUMBER_ID", default="").strip(),
        "version": config("WHATSAPP_API_VERSION", default="v21.0").strip() or "v21.0",
        "language": config("WHATSAPP_LANGUAGE", default="ar").strip() or "ar",
    }


def missing():
    values = settings()
    return [name for name, key in (("WHATSAPP_TOKEN", "token"), ("WHATSAPP_PHONE_NUMBER_ID", "phone_number_id")) if not values[key]]


def _http(url, body, token):
    """(status, parsed JSON or {}) for one POST. A network failure is status 0."""

    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
                                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310 - the fixed Graph API host
            raw, status = response.read(), response.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return 0, {"error": {"message": f"unreachable: {exc}"}}
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, ValueError):
        return status, {}


def parameter(text):
    """A template parameter as WhatsApp takes it: no new lines or tabs, and no run of more than three spaces."""

    text = re.sub(r"[\r\n\t]+", " ", str(text or ""))
    return re.sub(r" {4,}", "   ", text).strip()[:1000]


def send(phone, template, company, text):
    """(message id, "") when WhatsApp took it, else ("", the reason)."""

    values = settings()
    body = {
        "messaging_product": "whatsapp", "recipient_type": "individual", "to": phone, "type": "template",
        "template": {"name": template, "language": {"code": values["language"]},
                     "components": [{"type": "body", "parameters": [{"type": "text", "text": parameter(company)},
                                                                     {"type": "text", "text": parameter(text)}]}]},
    }
    url = f"https://graph.facebook.com/{values['version']}/{values['phone_number_id']}/messages"
    status, payload = _http(url, body, values["token"])
    messages = payload.get("messages") if isinstance(payload, dict) else None
    if status == 200 and messages and messages[0].get("id"):
        return str(messages[0]["id"])[:120], ""
    error = payload.get("error") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else {}
    reason = " · ".join(str(part) for part in (error.get("code"), error.get("message"), (error.get("error_data") or {}).get("details")) if part)
    return "", (reason or f"refused:{status}")[:500]
