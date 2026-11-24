"""Gmail SMTP sender (App Password). The password is never logged; errors are translated into plain advice."""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from config.settings import Settings

log = logging.getLogger("email")


def build_message(settings: Settings, subject: str, html: str, text: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.gmail_address
    msg["To"] = settings.email_recipient
    msg.set_content(text)
    msg.add_alternative(html, subtype="html")
    return msg


def send_email(settings: Settings, subject: str, html: str, text: str) -> tuple[bool, str]:
    """Return (sent, message). Never raises: a failed e-mail must not fail the whole run."""
    problems = settings.validate(need_sheets=False, need_email=True)
    problems = [p for p in problems if "needed to send the email" in p]
    if problems:
        return False, "Email not sent: " + " ".join(problems)
    msg = build_message(settings, subject, html, text)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=45) as smtp:
            smtp.login(settings.gmail_address, settings.gmail_app_password)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        return False, ("Gmail rejected the login. Use a 16-character App Password (Google Account > Security > 2-Step "
                       "Verification > App passwords), not your normal password.")
    except (smtplib.SMTPException, OSError) as exc:
        return False, f"Email not sent ({type(exc).__name__}): {str(exc)[:200]}"
    log.info("Digest e-mail sent to %s", settings.email_recipient)
    return True, "sent"
