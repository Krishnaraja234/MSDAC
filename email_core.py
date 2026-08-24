"""
Email sending for signup verification codes.
CONFIRMED: uses a Gmail or Outlook account with an app password (NOT the
regular account password - Gmail/Outlook require a separate "app
password" for SMTP access when 2FA is enabled, which it should be).
Configuration is via environment variables, so the actual credentials
never need to be hardcoded into this file or committed anywhere:
    MSDAC_SMTP_EMAIL     - the sending email address (required)
    MSDAC_SMTP_PASSWORD  - the app password for that account (required)
    MSDAC_SMTP_HOST      - defaults to Gmail's smtp.gmail.com if unset
    MSDAC_SMTP_PORT      - defaults to 587 (STARTTLS) if unset
For Gmail: Google Account -> Security -> 2-Step Verification -> App
passwords -> generate one for "Mail". Use that 16-character password
here, not your normal Gmail password.
For Outlook: similar - Microsoft Account -> Security -> Advanced
security options -> App passwords. Host is smtp.office365.com.

NOTE: Render's free tier has no outbound IPv6 route, but smtp.gmail.com
resolves to an IPv6 address by default. That mismatch causes
"[Errno 101] Network is unreachable" even with correct credentials.
_get_ipv4_smtp() below resolves the host to an IPv4 address explicitly
and connects to that, sidestepping the issue without touching global
socket behavior (so it won't affect any other part of the app).
"""
import os
import socket
import smtplib
from email.mime.text import MIMEText

SMTP_EMAIL = os.environ.get("MSDAC_SMTP_EMAIL")
SMTP_PASSWORD = os.environ.get("MSDAC_SMTP_PASSWORD")
SMTP_HOST = os.environ.get("MSDAC_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("MSDAC_SMTP_PORT", "587"))


class EmailNotConfigured(Exception):
    pass


def _get_ipv4_smtp(host: str, port: int, timeout: int = 15) -> smtplib.SMTP:
    """
    Connects to an SMTP server over IPv4 explicitly.

    Render's free-tier network has no outbound IPv6 route, and
    smtp.gmail.com (and some other providers) resolve to an IPv6
    address by default, which produces "[Errno 101] Network is
    unreachable" even when credentials are correct. Resolving to an
    IPv4 address first and connecting to that avoids the issue.

    EHLO is sent with the original hostname (not the raw IP), since
    some mail servers care about the HELO/EHLO name matching a real
    hostname rather than an IP literal.
    """
    addr_info = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    ipv4_addr = addr_info[0][4][0]
    server = smtplib.SMTP(timeout=timeout)
    server.connect(ipv4_addr, port)
    server.ehlo(host)
    return server


def send_verification_code(to_email: str, code: str, username: str = ""):
    """
    Sends the 6-digit verification code to the given email address.
    Raises EmailNotConfigured if MSDAC_SMTP_EMAIL/MSDAC_SMTP_PASSWORD
    aren't set, or smtplib.SMTPException (or similar) on any actual
    send failure - both are meant to be caught by the caller and shown
    as a clear error rather than crashing the signup request.
    """
    if not SMTP_EMAIL or not SMTP_PASSWORD:
        raise EmailNotConfigured(
            "Email sending isn't configured yet - set the MSDAC_SMTP_EMAIL "
            "and MSDAC_SMTP_PASSWORD environment variables (see email_core.py for setup instructions)."
        )

    subject = "MSDAC Tool - Your Verification Code"
    body = (
        f"Hello{' ' + username if username else ''},\n\n"
        f"Your MSDAC tool signup verification code is: {code}\n\n"
        "Enter this code on the verification page to complete your signup.\n"
        "If you didn't request this, you can ignore this email.\n"
    )
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_email

    with _get_ipv4_smtp(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        server.sendmail(SMTP_EMAIL, [to_email], msg.as_string())
