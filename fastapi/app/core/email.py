"""SMTP email delivery for ListingIQ.

Configuration comes from the shared `settings` object (config.yml overlaid with
environment variables), so switching from a local development SMTP server to
Amazon SES only requires changing SMTP_* environment variables — no business
logic changes.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from html import escape

from ..logger import get_logger
from .config import settings

logger = get_logger(__name__)

INVITATION_SUBJECT = "You've been invited to join your brokerage on ListingIQ"
PASSWORD_RESET_SUBJECT = "Reset your ListingIQ password"

# Kept as module attributes for backwards compatibility with existing imports.
SMTP_HOST = settings['smtp']['host']
SMTP_PORT = settings['smtp']['port']
SMTP_USER = settings['smtp']['user']
SMTP_PASSWORD = settings['smtp']['password']
SMTP_FROM = settings['smtp']['from_address']
FRONTEND_URL = settings['frontend_url']


class EmailDeliveryError(RuntimeError):
    """Raised when an email could not be handed to the SMTP server."""


def get_frontend_url() -> str:
    """Read the frontend base URL at call time so tests can override settings."""
    return (settings['frontend_url'] or FRONTEND_URL or '').rstrip('/')


def build_invitation_url(token: str) -> str:
    return f"{get_frontend_url()}/join?token={token}"


def build_password_reset_url(token: str) -> str:
    return f"{get_frontend_url()}/reset-password?token={token}"


def dashboard_url(path: str) -> str:
    """Return an absolute frontend URL for links in notification emails."""
    return f"{get_frontend_url()}/{path.lstrip('/')}"


def _notification_email(title: str, message: str, action_label: str, action_url: str) -> tuple[str, str]:
    safe_title = escape(title)
    safe_message = escape(message)
    safe_label = escape(action_label)
    safe_url = escape(action_url, quote=True)
    html_body = f"""
<html><body style="font-family:Arial,Helvetica,sans-serif;color:#0c1424;background:#f4f6f8;padding:40px 16px;">
  <div style="max-width:560px;margin:auto;background:#fff;border:1px solid #dfe3e8;border-radius:12px;padding:36px;">
    <div style="font-size:20px;font-weight:700;color:#1f5c4d;">ListingIQ</div>
    <h1 style="font-size:25px;margin:26px 0 12px;">{safe_title}</h1>
    <p style="color:#4a5568;line-height:1.6;">{safe_message}</p>
    <a href="{safe_url}" style="display:inline-block;margin:10px 0 22px;padding:13px 22px;background:#1f5c4d;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;">{safe_label}</a>
    <p style="font-size:13px;color:#657083;word-break:break-all;">If the button does not work, open:<br><a href="{safe_url}" style="color:#1f5c4d;">{safe_url}</a></p>
  </div>
</body></html>
"""
    text_body = f"{title}\n\n{message}\n\n{action_label}:\n{action_url}\n"
    return html_body, text_body


def send_reply_notification_email(
    recipient_email: str,
    sender_name: str,
    campaign_name: str,
    property_address: str,
    message_text: str,
    conversation_id: int,
    is_first_reply: bool,
) -> None:
    subject = f"{sender_name} replied for {property_address}"
    activity = "sent the first reply" if is_first_reply else "replied"
    reply_text = message_text.strip()
    html_body, text_body = _notification_email(
        subject,
        (
            f"{sender_name} from {campaign_name} {activity} about "
            f"{property_address}: “{reply_text}”"
        ),
        "Open reply",
        dashboard_url(f"/dashboard?view=followups&conversation_id={conversation_id}"),
    )
    send_email(recipient_email, subject, html_body, text_body)


def send_lead_assigned_email(
    recipient_email: str,
    broker_name: str,
    lead_id: int,
    lead_name: str | None,
    property_address: str | None,
    reassigned: bool = False,
) -> None:
    action = "reassigned" if reassigned else "assigned"
    lead_label = lead_name or f"Lead #{lead_id}"
    property_label = property_address or "the property"
    subject = f"{lead_label} {action} to you for {property_label}"
    message = f"{broker_name} {action} {lead_label} to you for {property_label}."
    html_body, text_body = _notification_email(
        subject,
        message,
        "Open lead",
        dashboard_url(f"/dashboard?view=leads&lead_id={lead_id}"),
    )
    send_email(recipient_email, subject, html_body, text_body)


def send_booking_created_email(
    recipient_email: str,
    attendee_name: str,
    meeting_title: str,
    starts_at: str,
    booking_id: int,
    location_address: str | None = None,
    location_url: str | None = None,
) -> None:
    subject = f"Meeting created: {meeting_title}"
    if location_address:
        destination = f" Location: {location_address}."
        if location_url:
            destination += f" Directions: {location_url}"
        action_label = "Open map"
        action_url = location_url
    else:
        destination = ""
        action_label = "View meeting"
        action_url = None
    html_body, text_body = _notification_email(
        subject,
        f"Your meeting with {attendee_name} is scheduled for {starts_at}.{destination}",
        action_label,
        action_url or dashboard_url(f"/dashboard?view=calendar&booking_id={booking_id}"),
    )
    send_email(recipient_email, subject, html_body, text_body)


def send_booking_reminder_email(
    recipient_email: str,
    attendee_name: str,
    meeting_title: str,
    starts_at: str,
    booking_id: int,
    minutes_until_start: int | None,
    location_address: str | None = None,
    location_url: str | None = None,
) -> None:
    timing = (
        f"in {minutes_until_start} minutes"
        if minutes_until_start is not None
        else "soon"
    )
    subject = f"Meeting {timing}: {meeting_title}"
    if location_address:
        destination = f" Location: {location_address}."
        if location_url:
            destination += f" Directions: {location_url}"
        action_label = "Open map"
        action_url = location_url
    else:
        destination = ""
        action_label = "Open meeting"
        action_url = None
    html_body, text_body = _notification_email(
        subject,
        f"Get prepared—your meeting with {attendee_name} starts at {starts_at}.{destination}",
        action_label,
        action_url or dashboard_url(f"/dashboard?view=calendar&booking_id={booking_id}"),
    )
    send_email(recipient_email, subject, html_body, text_body)


def send_new_brokerage_leads_email(
    recipient_email: str,
    brokerage_name: str,
    lead_count: int,
) -> None:
    noun = "lead has" if lead_count == 1 else "leads have"
    subject = f"{lead_count} new {'lead' if lead_count == 1 else 'leads'} added to {brokerage_name}"
    html_body, text_body = _notification_email(
        subject,
        f"{lead_count} new {noun} been added to your brokerage's Lead Pool.",
        "Open Lead Pool",
        dashboard_url("/dashboard?view=leads"),
    )
    send_email(recipient_email, subject, html_body, text_body)


def send_password_reset_email(recipient_email: str, reset_url: str) -> None:
    safe_url = escape(reset_url, quote=True)
    html_body = f"""
<html><body style="font-family:Arial,Helvetica,sans-serif;color:#0c1424;background:#f4f6f8;padding:40px 16px;">
  <div style="max-width:520px;margin:auto;background:#fff;border:1px solid #dfe3e8;border-radius:12px;padding:36px;">
    <div style="font-size:20px;font-weight:700;color:#1f5c4d;">ListingIQ</div>
    <h1 style="font-size:25px;margin:26px 0 12px;">Reset your password</h1>
    <p style="color:#4a5568;line-height:1.6;">Use the button below to choose a new password. This link expires in one hour and stops working after your password is changed.</p>
    <a href="{safe_url}" style="display:inline-block;margin:10px 0 22px;padding:13px 22px;background:#1f5c4d;color:#fff;text-decoration:none;border-radius:8px;font-weight:700;">Choose a new password</a>
    <p style="font-size:13px;color:#657083;word-break:break-all;">If the button does not work, open:<br><a href="{safe_url}" style="color:#1f5c4d;">{safe_url}</a></p>
    <p style="font-size:12px;color:#8a94a3;margin-top:24px;">If you did not request this reset, you can safely ignore this email.</p>
  </div>
</body></html>
"""
    text_body = (
        "Reset your ListingIQ password\n\n"
        "Open this link to choose a new password. It expires in one hour:\n"
        f"{reset_url}\n\n"
        "If you did not request this reset, you can safely ignore this email.\n"
    )
    send_email(recipient_email, PASSWORD_RESET_SUBJECT, html_body, text_body)


def build_invitation_email_html(
    recipient_email: str,
    brokerage_name: str,
    role: str,
    invitation_url: str,
    expires_at: str,
) -> str:
    safe_brokerage = escape(brokerage_name or 'Your brokerage')
    safe_role = escape(role)
    safe_email = escape(recipient_email)
    safe_url = escape(invitation_url, quote=True)
    return f"""
<html>
  <body style="font-family: Arial, Helvetica, sans-serif; color:#0c1424; margin:0; padding:0; background:#f4f6f8;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td align="center" style="padding:40px 16px;">
          <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="max-width:100%; background:#ffffff; border:1px solid #dfe3e8; border-radius:12px;">
            <tr>
              <td style="padding:36px;">
                <div style="font-size:20px; font-weight:700; color:#1768e5;">ListingIQ</div>
                <h1 style="font-size:26px; margin:28px 0 12px; letter-spacing:-0.02em;">You've been invited</h1>
                <p style="font-size:16px; color:#4a5568; line-height:1.6; margin:0 0 18px;">
                  <strong>{safe_brokerage}</strong> has invited you to join their brokerage on ListingIQ as
                  <strong>{safe_role}</strong>.
                </p>
                <p style="font-size:16px; color:#4a5568; line-height:1.6; margin:0 0 26px;">
                  Click the button below to accept the invitation and complete your registration
                  for <strong>{safe_email}</strong>.
                </p>
                <a href="{safe_url}" style="display:inline-block; padding:13px 22px; background:#1768e5; color:#ffffff; text-decoration:none; border-radius:8px; font-weight:700; font-size:16px;">
                  Accept Invitation
                </a>
                <p style="font-size:14px; color:#657083; line-height:1.6; margin:28px 0 8px;">
                  This invitation expires on {escape(expires_at)} (48 hours after it was sent).
                </p>
                <p style="font-size:14px; color:#657083; line-height:1.6; margin:0;">
                  If the button does not work, copy and paste this link into your browser:
                </p>
                <p style="font-size:13px; word-break:break-all; margin:8px 0 0;">
                  <a href="{safe_url}" style="color:#1768e5;">{safe_url}</a>
                </p>
                <hr style="border:none; border-top:1px solid #e2e5ea; margin:28px 0 18px;" />
                <p style="font-size:12px; color:#8a94a3; line-height:1.5; margin:0;">
                  If you did not expect this invitation, you can safely ignore this email.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def build_invitation_email_text(
    recipient_email: str,
    brokerage_name: str,
    role: str,
    invitation_url: str,
    expires_at: str,
) -> str:
    return (
        "You've been invited\n\n"
        f"{brokerage_name} has invited you to join their brokerage on ListingIQ as {role}.\n\n"
        f"Accept the invitation and complete your registration for {recipient_email}:\n"
        f"{invitation_url}\n\n"
        f"This invitation expires on {expires_at}.\n\n"
        "If you did not expect this invitation, you can safely ignore this email.\n"
    )


def send_email(recipient_email: str, subject: str, html_body: str, text_body: str) -> None:
    """Send a multipart email through the configured SMTP server."""
    smtp_cfg = settings['smtp']
    host = smtp_cfg['host']
    sender = smtp_cfg['from_address']

    if not host or not sender:
        raise EmailDeliveryError('SMTP is not configured. Set SMTP_HOST and SMTP_FROM.')

    message = MIMEMultipart('alternative')
    message['Subject'] = subject
    message['From'] = sender if '<' in str(sender) else formataddr(('ListingIQ', sender))
    message['To'] = recipient_email
    message.attach(MIMEText(text_body, 'plain', 'utf-8'))
    message.attach(MIMEText(html_body, 'html', 'utf-8'))

    port = smtp_cfg['port']
    user = smtp_cfg['user']
    password = smtp_cfg['password']

    try:
        smtp_class = smtplib.SMTP_SSL if smtp_cfg.get('use_ssl') else smtplib.SMTP
        with smtp_class(host=host, port=port, timeout=15) as smtp:
            smtp.ehlo()
            if not smtp_cfg.get('use_ssl') and smtp.has_extn('starttls'):
                smtp.starttls()
                smtp.ehlo()
            if user and password:
                smtp.login(user, password)
            smtp.sendmail(sender, [recipient_email], message.as_string())
    except EmailDeliveryError:
        raise
    except Exception as exc:  # smtplib/socket errors
        logger.error('smtp_send_failed', extra={'error': str(exc)})
        raise EmailDeliveryError('Unable to deliver the email.') from exc


def send_invitation_email(
    recipient_email: str,
    brokerage_name: str,
    role: str,
    invitation_url: str,
    expires_at: str,
) -> None:
    send_email(
        recipient_email=recipient_email,
        subject=INVITATION_SUBJECT,
        html_body=build_invitation_email_html(
            recipient_email, brokerage_name, role, invitation_url, expires_at
        ),
        text_body=build_invitation_email_text(
            recipient_email, brokerage_name, role, invitation_url, expires_at
        ),
    )
