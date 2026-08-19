"""Email delivery adapters (:class:`EmailDeliveryPort`).

Two implementations behind one port (docs/16 §8):

* :class:`LoggingEmailDelivery` — the default dev "mailbox": it does not touch the network; the email
  is durably recorded by the outbox and marked ``recorded`` so links are clickable from the admin
  Outbox page while working fully offline.
* :class:`SmtpEmailDelivery` — a real SMTP send (stdlib ``smtplib``), used automatically when
  ``NORTHSTAR_SMTP_HOST`` is configured. Marks the message ``sent`` (or ``failed`` with the error).

``email_delivery_from_env`` picks SMTP when configured, otherwise the dev mailbox.
"""

from __future__ import annotations

import logging
import os
import smtplib
import uuid
from email.message import EmailMessage as MimeEmailMessage

from ..application.transactional import DeliveryOutcome, EmailDeliveryPort, EmailStatus

_log = logging.getLogger("northstar.messaging.email")


class LoggingEmailDelivery(EmailDeliveryPort):
    """Dev mailbox: record-only delivery (no network). Links are read from the admin Outbox."""

    def deliver(
        self, *, to_email: str, subject: str, html_body: str, text_body: str
    ) -> DeliveryOutcome:
        _log.info("dev-mailbox email to=%s subject=%s", to_email, subject)
        return DeliveryOutcome(status=EmailStatus.RECORDED, provider_message_id=None)


class SmtpEmailDelivery(EmailDeliveryPort):
    """Real SMTP delivery via stdlib ``smtplib`` (config from the environment)."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        sender: str,
        use_tls: bool,
        timeout_s: float = 15.0,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._sender = sender
        self._use_tls = use_tls
        self._timeout = timeout_s

    def deliver(
        self, *, to_email: str, subject: str, html_body: str, text_body: str
    ) -> DeliveryOutcome:
        message = MimeEmailMessage()
        message["From"] = self._sender
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(text_body)
        message.add_alternative(html_body, subtype="html")
        try:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as smtp:
                if self._use_tls:
                    smtp.starttls()
                if self._username and self._password:
                    smtp.login(self._username, self._password)
                smtp.send_message(message)
        except Exception as exc:  # noqa: BLE001 - surfaced as a FAILED outbox record
            return DeliveryOutcome(status=EmailStatus.FAILED, error=str(exc)[:500])
        return DeliveryOutcome(
            status=EmailStatus.SENT, provider_message_id=f"smtp-{uuid.uuid4().hex}"
        )


def email_delivery_from_env() -> EmailDeliveryPort:
    """Return the SMTP delivery when ``NORTHSTAR_SMTP_HOST`` is set, else the dev mailbox."""
    host = os.environ.get("NORTHSTAR_SMTP_HOST")
    if not host:
        return LoggingEmailDelivery()
    return SmtpEmailDelivery(
        host=host,
        port=int(os.environ.get("NORTHSTAR_SMTP_PORT", "587")),
        username=os.environ.get("NORTHSTAR_SMTP_USER"),
        password=os.environ.get("NORTHSTAR_SMTP_PASSWORD"),
        sender=os.environ.get("NORTHSTAR_SMTP_FROM", "no-reply@bestinfopages.local"),
        use_tls=os.environ.get("NORTHSTAR_SMTP_TLS", "1") != "0",
    )
