"""Small standard-library Gmail API sender."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from fitlit import config, gmail_auth


class GmailSendError(RuntimeError):
    """Raised when Gmail rejects or cannot complete a send."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def validate_recipient(value: str) -> str:
    _, address = parseaddr(value.strip())
    if not address or address != value.strip() or "@" not in address:
        raise GmailSendError("FITLIT_GMAIL_TO must contain one plain email address")
    return address


def _raw_message(subject: str, text: str, html: str) -> str:
    recipient = validate_recipient(config.GMAIL_TO)
    message = EmailMessage()
    message["From"] = formataddr((config.GMAIL_FROM_NAME, recipient))
    message["To"] = recipient
    message["Subject"] = subject
    message["X-FitLit-Notification"] = "health-insight"
    message.set_content(text)
    message.add_alternative(html, subtype="html")
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii").rstrip("=")


def send(subject: str, text: str, html: str) -> str:
    """Send one message and return Gmail's immutable message id."""
    raw = _raw_message(subject, text, html)
    body = json.dumps({"raw": raw}).encode("utf-8")
    url = f"{config.GMAIL_API_BASE}/users/me/messages/send"
    token = gmail_auth.get_access_token()

    for attempt in range(2):
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=config.REQUEST_TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            message_id = payload.get("id")
            if not message_id:
                raise GmailSendError("Gmail send response contained no message id")
            return message_id
        except urllib.error.HTTPError as exc:
            if exc.code == 401 and attempt == 0:
                token = gmail_auth.get_access_token(force_refresh=True)
                continue
            detail = exc.read().decode("utf-8", "replace")
            retryable = exc.code == 429 or (
                exc.code == 403
                and ("SERVICE_DISABLED" in detail or "accessNotConfigured" in detail)
            )
            raise GmailSendError(
                f"Gmail API {exc.code} {exc.reason}: {detail}",
                retryable=retryable,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise GmailSendError(f"Gmail API unreachable: {exc}") from exc
    raise GmailSendError("Gmail authorization retry failed")
