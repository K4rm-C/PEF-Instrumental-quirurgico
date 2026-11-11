"""MS-06: optional e-mail channel of the notifications (in addition to the in-app channel).

Enabled only when NOTIFICATIONS_SMTP_HOST is set: no provider or credential is ever assumed. Configuration:
  NOTIFICATIONS_SMTP_HOST / _PORT (587) / _USER / _PASSWORD / _FROM (required with a host)
  NOTIFICATIONS_SMTP_SECURITY   starttls (default) | ssl | none (none only for a local relay / test server)
  NOTIFICATIONS_SMTP_TIMEOUT    seconds (10)
  NOTIFICATIONS_EMAIL_TYPES     notification types also sent by e-mail (default: discrepancy_pending,
                                review_pending, session_unresolved, station_alert)
  NOTIFICATIONS_PORTAL_URL      link to the portal's notification page (optional)

Content is minimal and non-clinical, like the in-app notification: the type, the session label or station
name and the time. Never patient data, free text, tokens or links with secrets.
The in-app notification is committed before any delivery exists; a failing mail server only marks the
delivery `failed` (attempts / last_error), retried up to max_attempts.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage

logger = logging.getLogger("notifications.email")

DEFAULT_TYPES = ("discrepancy_pending", "review_pending", "session_unresolved", "station_alert", "final_count_escalated",
                 "service_alert")
SUBJECTS = {
    "session_scheduled": "Session assigned to you", "discrepancy_pending": "Discrepancies pending reconciliation",
    "correction_required": "Your session needs corrections", "discrepancy_resolved": "Discrepancy resolved",
    "session_closed": "Session closed", "review_pending": "Session pending review",
    "session_unresolved": "Session closed as unresolved", "station_alert": "Capture station alert",
    "final_count_escalated": "Possible physical difference reported", "service_alert": "Platform component alert",
}
STATION_KINDS = {"heartbeat_lost": "the station stopped reporting", "camera_error": "camera error"}


@dataclass(frozen=True)
class EmailSettings:
    host: str = ""
    port: int = 587
    user: str = ""
    password: str = field(default="", repr=False)
    sender: str = ""
    security: str = "starttls"
    timeout: float = 10.0
    types: tuple[str, ...] = DEFAULT_TYPES
    portal_url: str = ""

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.sender)

    @classmethod
    def from_env(cls) -> "EmailSettings":
        types = tuple(t.strip() for t in os.getenv("NOTIFICATIONS_EMAIL_TYPES", ",".join(DEFAULT_TYPES)).split(",")
                      if t.strip())
        security = os.getenv("NOTIFICATIONS_SMTP_SECURITY", "starttls").strip().lower()
        return cls(host=os.getenv("NOTIFICATIONS_SMTP_HOST", "").strip(),
                   port=int(os.getenv("NOTIFICATIONS_SMTP_PORT", "587") or 587),
                   user=os.getenv("NOTIFICATIONS_SMTP_USER", "").strip(),
                   password=os.getenv("NOTIFICATIONS_SMTP_PASSWORD", ""),
                   sender=os.getenv("NOTIFICATIONS_SMTP_FROM", "").strip(),
                   security=security if security in ("starttls", "ssl", "none") else "starttls",
                   timeout=float(os.getenv("NOTIFICATIONS_SMTP_TIMEOUT", "10") or 10),
                   types=types, portal_url=os.getenv("NOTIFICATIONS_PORTAL_URL", "").strip().rstrip("/"))


def build_message(settings: EmailSettings, notification: dict, recipient: str) -> EmailMessage:
    data = notification.get("data") or {}
    kind = notification["type"]
    message = EmailMessage()
    message["Subject"] = f"[PEF] {SUBJECTS.get(kind, kind)}"
    message["From"] = settings.sender
    message["To"] = recipient
    lines = [SUBJECTS.get(kind, kind) + "."]
    if data.get("session_label"):
        lines.append(f"Session: {data['session_label']}")
    if kind == "station_alert":
        lines.append(f"Station: {data.get('station_name') or '-'} ({STATION_KINDS.get(data.get('kind'), data.get('kind'))})")
        if data.get("camera_name"):
            lines.append(f"Camera: {data['camera_name']}")
    if kind == "service_alert":
        lines.append(f"Component: {data.get('component') or '-'} ({data.get('kind') or '-'})")
    if isinstance(data.get("open_discrepancies"), int):
        lines.append(f"Open discrepancies: {data['open_discrepancies']}")
    lines.append(f"Time (UTC): {notification['created_at'].strftime('%Y-%m-%d %H:%M')}")
    if settings.portal_url:
        lines.append(f"Open the portal: {settings.portal_url}/notifications")
    lines.append("")
    lines.append("This is an automatic message of the PEF system. Details are only available in the portal.")
    message.set_content("\n".join(lines))
    return message


def send(settings: EmailSettings, message: EmailMessage) -> None:
    """One message over SMTP; raises on any failure (the caller records it on the delivery)."""
    if settings.security == "ssl":
        client = smtplib.SMTP_SSL(settings.host, settings.port, timeout=settings.timeout,
                                  context=ssl.create_default_context())
    else:
        client = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)
    try:
        if settings.security == "starttls":
            client.starttls(context=ssl.create_default_context())
        if settings.user:
            client.login(settings.user, settings.password)
        client.send_message(message)
    finally:
        try:
            client.quit()
        except Exception:  # noqa: BLE001 - the connection may already be gone
            pass
