"""Account mailer for VESQOR account emails (verification, password reset).

Sends through Microsoft Graph when MS_GRAPH_* is configured (the tenant has SMTP
AUTH disabled), falling back to SMTP otherwise.
"""

from __future__ import annotations

import html
import json
import logging
import smtplib
import urllib.request
import urllib.parse
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from open_webui.env import (
    MS_GRAPH_CLIENT_ID,
    MS_GRAPH_CLIENT_SECRET,
    MS_GRAPH_SENDER,
    MS_GRAPH_TENANT_ID,
    SMTP_FROM,
    SMTP_FROM_NAME,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USER,
    SMTP_USE_TLS,
)

log = logging.getLogger(__name__)

_graph_token: dict = {'value': '', 'exp': 0.0}


def _graph_access_token() -> str:
    """Cached client-credentials token for Graph."""
    import time

    if _graph_token['value'] and _graph_token['exp'] > time.time() + 60:
        return _graph_token['value']
    body = urllib.parse.urlencode({
        'client_id': MS_GRAPH_CLIENT_ID,
        'client_secret': MS_GRAPH_CLIENT_SECRET,
        'scope': 'https://graph.microsoft.com/.default',
        'grant_type': 'client_credentials',
    }).encode()
    req = urllib.request.Request(
        f'https://login.microsoftonline.com/{MS_GRAPH_TENANT_ID}/oauth2/v2.0/token',
        data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.load(resp)
    _graph_token['value'] = data['access_token']
    _graph_token['exp'] = time.time() + int(data.get('expires_in', 3600))
    return _graph_token['value']


def _send_via_graph(to_email: str, subject: str, text_body: str, html_body: str) -> None:
    """POST /users/<sender>/sendMail. Raises on failure so the caller can fall back."""
    token = _graph_access_token()
    payload = {
        'message': {
            'subject': subject,
            'body': {'contentType': 'HTML', 'content': html_body},
            'toRecipients': [{'emailAddress': {'address': to_email}}],
        },
        'saveToSentItems': False,
    }
    req = urllib.request.Request(
        f'https://graph.microsoft.com/v1.0/users/{urllib.parse.quote(MS_GRAPH_SENDER)}/sendMail',
        method='POST', data=json.dumps(payload).encode(),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=25) as resp:
        if resp.status not in (200, 202):
            raise RuntimeError(f'graph sendMail status {resp.status}')


def _send(to_email: str, subject: str, text_body: str, html_body: str, kind: str) -> bool:
    """Deliver one message. Graph first (tenant SMTP AUTH is disabled), then SMTP."""
    if MS_GRAPH_TENANT_ID and MS_GRAPH_CLIENT_ID and MS_GRAPH_CLIENT_SECRET and MS_GRAPH_SENDER:
        try:
            _send_via_graph(to_email, subject, text_body, html_body)
            log.info('%s email sent to %s via Graph (%s)', kind.capitalize(), to_email, MS_GRAPH_SENDER)
            return True
        except Exception as e:
            log.exception('Graph send failed for %s email to %s: %s', kind, to_email, e)

    if not SMTP_HOST:
        log.warning('SMTP not configured — %s email for %s not sent', kind, to_email)
        return False

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f'{SMTP_FROM_NAME} <{SMTP_FROM}>'
    msg['To'] = to_email
    msg.attach(MIMEText(text_body, 'plain', 'utf-8'))
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.ehlo()
            if SMTP_USE_TLS:
                server.starttls()
                server.ehlo()
            if SMTP_USER:
                server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(SMTP_FROM, [to_email], msg.as_string())
        log.info('%s email sent to %s', kind.capitalize(), to_email)
        return True
    except Exception as e:
        log.exception('Failed to send %s email to %s: %s', kind, to_email, e)
        return False


def _shell(heading: str, intro: str, cta_html: str, footnote: str) -> str:
    """VESQOR-branded HTML wrapper shared by the account emails."""
    return (
        '<div style="font-family:Helvetica,Arial,sans-serif;max-width:560px;margin:0 auto;'
        'background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;overflow:hidden">'
        '<div style="background:#0a0a12;padding:24px;text-align:center">'
        '<span style="color:#ffffff;font-weight:600;letter-spacing:0.15em;font-size:14px">'
        'VESQOR MEGA AI</span></div>'
        '<div style="padding:32px">'
        f'<h2 style="margin:0 0 16px;font-size:20px;color:#111827">{heading}</h2>'
        f'<p style="margin:0 0 24px;font-size:14px;line-height:1.6;color:#4b5563">{intro}</p>'
        f'{cta_html}'
        f'<p style="margin:24px 0 0;font-size:12px;color:#9ca3af">{footnote}</p>'
        '</div></div>'
    )


def _button(url: str, label: str) -> str:
    return (
        f'<a href="{url}" style="display:inline-block;background:#111827;color:#ffffff;'
        'padding:12px 28px;border-radius:8px;text-decoration:none;font-size:14px;font-weight:600">'
        f'{label}</a>'
    )


def send_password_reset_email(to_email: str, reset_url: str) -> bool:
    """Send a one-time password-reset link.

    Returns True if the message was accepted by the SMTP server.
    """
    text_body = (
        f'Hello,\n\n'
        f'We received a request to reset your VESQOR MEGA AI password. '
        f'Choose a new one here:\n\n'
        f'{reset_url}\n\n'
        f'This link is valid for 24 hours and can be used once.\n\n'
        f'If you did not request a password reset, you can safely ignore this email — '
        f'your password stays unchanged.\n\n'
        f'— VESQOR MEGA AI'
    )
    html_body = _shell(
        'Reset your password',
        'We received a request to reset your VESQOR MEGA AI password. '
        'Click below to choose a new one:',
        _button(reset_url, 'Reset password'),
        'This link is valid for 24 hours and can be used once. If you did not request a '
        'password reset, you can safely ignore this email — your password stays unchanged.',
    )
    return _send(
        to_email,
        'Reset your VESQOR MEGA AI password',
        text_body,
        html_body,
        'password reset',
    )


def send_username_reminder_email(to_email: str, name: str | None = None) -> bool:
    """Remind the account holder which email they sign in with.

    Returns True if the message was accepted by the SMTP server.
    """
    greeting = f'Hello {name},' if name else 'Hello,'
    text_body = (
        f'{greeting}\n\n'
        f'Your VESQOR MEGA AI login email is: {to_email}\n\n'
        f'Sign in with this email address and your password.\n\n'
        f'If you did not request this reminder, you can safely ignore this email.\n\n'
        f'— VESQOR MEGA AI'
    )
    # The name comes from user input — escape both interpolations.
    html_body = _shell(
        'Your login email',
        f'{html.escape(greeting)} your VESQOR MEGA AI login email is:',
        (
            '<div style="display:inline-block;background:#f3f4f6;border-radius:8px;'
            'padding:12px 20px;font-size:15px;font-weight:600;color:#111827">'
            f'{html.escape(to_email)}</div>'
        ),
        'Sign in with this email address and your password. If you did not request this '
        'reminder, you can safely ignore this email.',
    )
    return _send(
        to_email,
        'Your VESQOR MEGA AI login email',
        text_body,
        html_body,
        'username reminder',
    )


def send_verification_email(to_email: str, verify_url: str) -> bool:
    """Send a one-time email-verification link.

    Returns True if the message was accepted by the SMTP server.
    """
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

    # Graph first (tenant SMTP AUTH is disabled); SMTP only as a fallback.
    if MS_GRAPH_TENANT_ID and MS_GRAPH_CLIENT_ID and MS_GRAPH_CLIENT_SECRET and MS_GRAPH_SENDER:
        try:
            _send_via_graph(to_email, subject, body, html)
            log.info('Verification email sent to %s via Graph (%s)', to_email, MS_GRAPH_SENDER)
            return True
        except Exception as e:
            log.exception('Graph send failed for verification email to %s: %s', to_email, e)

    if not SMTP_HOST:
        log.warning('SMTP not configured and Graph unavailable — verification email for %s not sent', to_email)
        return False

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
        log.info('Verification email sent to %s via SMTP', to_email)
        return True
    except Exception as e:
        log.exception('Failed to send verification email to %s: %s', to_email, e)
        return False
