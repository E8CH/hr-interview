"""SMTP 이메일 어댑터

USE_MOCK=true (PoC 기본) → 실제 발송 대신 .eml 형태로 ./storage/outbox/smtp 에 기록.
USE_MOCK=false           → smtplib 로 실제 SMTP 서버에 발송.
"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

import structlog

from app.config import settings
from app.services.channels.base import (
    ChannelSendError,
    OutboundMessage,
    SendResult,
    write_outbox,
)

log = structlog.get_logger(__name__)


class EmailSmtpAdapter:
    channel_id = "gmail_smtp"
    channel_type = "email"
    provider = "smtp"

    def build_message(self, message: OutboundMessage) -> EmailMessage:
        mail = EmailMessage()
        mail["From"] = message.config.get("mail_from", settings.mail_from)
        mail["To"] = message.recipient
        if message.cc:
            mail["Cc"] = ", ".join(message.cc)
        mail["Subject"] = message.subject or "(제목 없음)"
        mail["X-Notification-Id"] = message.notification_id
        mail.set_content(message.body, subtype="html")
        return mail

    def send(self, message: OutboundMessage) -> SendResult:
        mail = self.build_message(message)
        if settings.use_mock:
            path = write_outbox(
                settings.outbox_dir, self.provider, message, mail.as_string()
            )
            log.info(
                "email_mock_sent", recipient=message.recipient, artifact=path
            )
            return SendResult(True, self.provider, "mock smtp", artifact=path)

        host = message.config.get("host", settings.smtp_host)
        port = int(message.config.get("port", settings.smtp_port))
        user = message.config.get("user", settings.smtp_user)
        password = message.config.get("password", settings.smtp_password)
        try:
            with smtplib.SMTP(host, port, timeout=10) as server:
                if message.config.get("use_tls", False):
                    server.starttls()
                if user and password:
                    server.login(user, password)
                server.send_message(mail)
        except Exception as exc:
            raise ChannelSendError(f"SMTP 발송 실패: {exc}") from exc
        return SendResult(True, self.provider, f"smtp {host}:{port}")
