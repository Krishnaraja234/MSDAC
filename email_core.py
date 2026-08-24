"""
Email sending for signup verification codes.
Uses Brevo (https://brevo.com) over its HTTPS API instead of SMTP.
Render's free tier blocks outbound traffic on SMTP ports 25/465/587,
so raw smtplib (even to Gmail/Outlook) will hang and time out there.
Brevo sends over normal HTTPS (port 443), which isn't blocked.

Unlike Resend, Brevo lets you verify a single sender EMAIL ADDRESS
(no domain ownership required) - so an existing Gmail address works
fine as the sender.

Configuration is via environment variables, so the actual API key
never needs to be hardcoded into this file or committed anywhere:
    BREVO_API_KEY     - your Brevo API key (required)
    BREVO_FROM_EMAIL  - the verified sender address (required)
                         must be verified in Brevo dashboard first
                         (Settings -> Senders, Domains & Dedicated IPs
                         -> Senders -> Add a Sender -> confirm the
                         link Brevo emails to that address)
    BREVO_FROM_NAME   - display name shown to recipients (optional,
                         defaults to "MSDAC Tool")

Setup:
    1. Sign up at https://brevo.com (free tier: 300 emails/day)
    2. Settings -> Senders, Domains & Dedicated IPs -> Senders tab
       -> Add a Sender -> enter your email address + display name
    3. Click the confirmation link Brevo emails to that address
    4. Settings -> SMTP & API -> API Keys tab -> Generate a New API Key
    5. Set BREVO_API_KEY and BREVO_FROM_EMAIL as env vars on Render
"""
import os
import requests

BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
BREVO_FROM_EMAIL = os.environ.get("BREVO_FROM_EMAIL")
BREVO_FROM_NAME = os.environ.get("BREVO_FROM_NAME", "MSDAC Tool")
BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


class EmailNotConfigured(Exception):
    pass


class EmailSendError(Exception):
    """Raised when Brevo's API rejects or fails to send the email."""
    pass


def send_verification_code(to_email: str, code: str, username: str = ""):
    """
    Sends the 6-digit verification code to the given email address.

    Raises EmailNotConfigured if BREVO_API_KEY/BREVO_FROM_EMAIL
    aren't set, or EmailSendError on any actual send failure (bad
    API key, unverified sender, rate limit, etc) - both are meant to
    be caught by the caller and shown as a clear error rather than
    crashing the signup request.
    """
    if not BREVO_API_KEY or not BREVO_FROM_EMAIL:
        raise EmailNotConfigured(
            "Email sending isn't configured yet - set the BREVO_API_KEY "
            "and BREVO_FROM_EMAIL environment variables (see email_core.py for setup instructions)."
        )

    subject = "MSDAC Tool - Your Verification Code"
    text_body = (
        f"Hello{' ' + username if username else ''},\n\n"
        f"Your MSDAC tool signup verification code is: {code}\n\n"
        "Enter this code on the verification page to complete your signup.\n"
        "If you didn't request this, you can ignore this email.\n"
    )

    try:
        response = requests.post(
            BREVO_API_URL,
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={
                "sender": {"name": BREVO_FROM_NAME, "email": BREVO_FROM_EMAIL},
                "to": [{"email": to_email}],
                "subject": subject,
                "textContent": text_body,
            },
            timeout=15,
        )
    except requests.RequestException as e:
        raise EmailSendError(f"Could not reach Brevo API: {e}") from e

    if response.status_code >= 400:
        raise EmailSendError(
            f"Brevo API returned {response.status_code}: {response.text}"
        )
