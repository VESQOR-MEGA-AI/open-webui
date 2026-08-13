"""SMTP mailer for VESQOR email verification."""

from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from open_webui.env import (
    SMTP_FROM,
    SMTP_FROM_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
    SMTP_USE_TLS,
)

log = logging.getLogger(__name__)


def send_verification_email(to_email: str, verify_url: str) -> bool:
    """Send a one-time email-verification link.

    Returns True if the message was accepted by the SMTP server.
    """
    if not SMTP_HOST:
        log.warning('SMTP not configured — verification email for %s not sent', to_email)
        return False

    subject = 'Confirm your VESQOR MEGA AI account'
    body = (
        f'Hello,\n\n'
        f'Welcome to VESQOR MEGA AI. Please confirm your email address by clicking the link below:\n\n'
        f'{verify_url}\n\n'
        f'This link is valid for 24 hours.\n\n'
        f'If you did not create this account, you can safely ignore this email.\n\n'
        f'— VESQOR MEGA AI'
    )

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f'{SMTP_FROM_NAME} <{SMTP_FROM}>'
    msg['To'] = to_email
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    html = (
        '<div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:0 auto;'
        'background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden">'
        '<div style="background:#0a0a12;padding:24px;text-align:center">'
        '<span style="color:#ffffff;font-weight:600;letter-spacing:0.15em;font-size:14px">'
        'VESQOR MEGA AI</span></div>'
        '<div style="padding:32px">'
        '<h2 style="margin:0 0 16px;font-size:20px;color:#111827">Confirm your email</h2>'
        '<p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#4b5563">'
        'Welcome to VESQOR MEGA AI. Please confirm your email address to activate your account:</p>'
        f'<a href="{verify_url}" style="display:inline-block;background:#111827;color:#ffffff;'
        'padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">'
        'Confirm email</a>'
        '<p style="margin:24px 0 0;font-size:12px;color:#9ca3af">'
        'This link is valid for 24 hours. If you did not create this account, you can safely ignore this email.</p>'
        '</div></div>'
    )
    msg.attach(MIMEText(html, 'html', 'utf-8'))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            if SMTP_USE_TLS:
                server.starttls()
                server.ehlo()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        log.info('Verification email sent to %s', to_email)
        return True
    except Exception as e:
        log.exception('Failed to send verification email to %s: %s', to_email, e)
        return False
