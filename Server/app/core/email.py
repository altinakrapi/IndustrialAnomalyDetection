from html import escape
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import settings


class EmailConfigurationError(RuntimeError):
    pass


def email_is_configured() -> bool:
    return all(
        (
            settings.SMTP_HOST,
            settings.SMTP_USERNAME,
            settings.SMTP_PASSWORD,
            settings.SMTP_FROM_EMAIL,
        )
    )


def send_password_reset_email(recipient: str, reset_url: str) -> None:
    if not email_is_configured():
        raise EmailConfigurationError("SMTP email settings are incomplete")

    message = EmailMessage()
    message["Subject"] = "Reset your Industrial Anomaly Detection password"
    message["From"] = settings.SMTP_FROM_EMAIL
    message["To"] = recipient
    safe_reset_url = escape(reset_url, quote=True)
    message.set_content(
        "We received a request to reset your password.\n\n"
        f"Use this one-time link within 30 minutes:\n{reset_url}\n\n"
        "If you did not request a password reset, you can ignore this email."
    )
    message.add_alternative(
        "<p>We received a request to reset your password.</p>"
        f'<p><a href="{safe_reset_url}">Reset your password</a></p>'
        "<p>This one-time link expires in 30 minutes. "
        "If you did not request a password reset, you can ignore this email.</p>",
        subtype="html",
    )

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.send_message(message)
