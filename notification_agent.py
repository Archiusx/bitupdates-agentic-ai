"""
BIT Updates v2 - Notification Agent
Multi-channel notification dispatch: Email, SMS, Slack, WhatsApp.
Handles escalation alerts and status change notifications.
"""

import asyncio
from datetime import datetime, timezone
from typing import Optional
import httpx
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from backend.agents.base_agent import BaseAgent
from backend.utils.config import settings
from backend.utils.database import mongo


NOTIFICATION_PROMPT = """
You are a Notification Agent for campus support system.
Generate appropriate notification messages for different channels.

Complaint Title: {title}
Status Change: {old_status} → {new_status}
Admin Message: {admin_reply}
Student Name: {student_name}
Department: {department}
Priority: {priority}

Generate notification messages for each channel. Return JSON:
{{
  "email_subject": "<professional email subject>",
  "email_body": "<HTML email body with greeting, update, and next steps. Use basic HTML tags>",
  "sms_text": "<SMS message under 160 chars>",
  "whatsapp_text": "<WhatsApp message with emoji, under 300 chars>",
  "slack_text": "<Slack message with markdown formatting>",
  "push_notification_title": "<short push title under 50 chars>",
  "push_notification_body": "<push body under 100 chars>",
  "should_notify_admin": <true|false>,
  "admin_alert_text": "<alert text for admin dashboard if needed>"
}}

Return ONLY the JSON object.
"""


class NotificationAgent(BaseAgent):
    """
    Notification Agent: Alerts & Escalations
    Dispatches multi-channel notifications based on complaint events.
    """

    def __init__(self):
        super().__init__(
            name="NotificationAgent",
            description="Multi-channel notification dispatch for complaint lifecycle events",
        )

    async def process(self, input_data: dict) -> dict:
        """Generate and dispatch notifications."""
        # Generate notification content with Gemini
        prompt = NOTIFICATION_PROMPT.format(
            title=input_data.get("title", "Your complaint"),
            old_status=input_data.get("old_status", "open"),
            new_status=input_data.get("new_status", "in_progress"),
            admin_reply=input_data.get("admin_reply", "Your complaint is being reviewed."),
            student_name=input_data.get("student_name", "Student"),
            department=input_data.get("department", "Admin"),
            priority=input_data.get("priority", "medium"),
        )

        raw = await self.gemini_generate(prompt)
        messages = self.parse_json_response(raw)

        if not messages:
            messages = self._default_messages(input_data)

        # Dispatch notifications based on user preferences
        dispatch_results = {}
        prefs = input_data.get("notification_preferences", {"email": True})

        if prefs.get("email") and input_data.get("user_email"):
            dispatch_results["email"] = await self._send_email(
                to=input_data["user_email"],
                subject=messages.get("email_subject", "Your complaint update"),
                body=messages.get("email_body", ""),
            )

        if prefs.get("slack") and settings.SLACK_WEBHOOK_URL:
            dispatch_results["slack"] = await self._send_slack(
                messages.get("slack_text", "")
            )

        # Log notification to MongoDB
        await self._log_notification(input_data, messages, dispatch_results)

        return {
            "messages": messages,
            "dispatch_results": dispatch_results,
            "channels_notified": list(dispatch_results.keys()),
        }

    async def send_escalation_alert(self, complaint: dict, reason: str) -> dict:
        """Send escalation alert to admins and department heads."""
        admin_message = f"""
🚨 ESCALATION ALERT

Complaint: {complaint.get('title', 'N/A')}
Department: {complaint.get('department', 'N/A')}
Priority: {complaint.get('ai_priority', 'N/A')}
Reason: {reason}
Student: {complaint.get('user_name', 'N/A')}
Created: {complaint.get('created_at', 'N/A')}

Action Required: Please review and respond within 2 hours.
"""
        results = {}

        # Email to admin
        if settings.SMTP_USER:
            results["email"] = await self._send_email(
                to=settings.SMTP_USER,
                subject=f"🚨 ESCALATION: {complaint.get('title', 'Complaint')}",
                body=admin_message,
            )

        # Slack alert
        if settings.SLACK_WEBHOOK_URL:
            results["slack"] = await self._send_slack(admin_message)

        return results

    async def _send_email(self, to: str, subject: str, body: str) -> dict:
        """Send email via SMTP."""
        if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
            return {"status": "skipped", "reason": "SMTP not configured"}

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = settings.EMAIL_FROM
            msg["To"] = to

            # Plain text fallback
            text_body = body.replace("<br>", "\n").replace("</p>", "\n")
            msg.attach(MIMEText(text_body, "plain"))
            msg.attach(MIMEText(body, "html"))

            def _send():
                with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
                    server.starttls()
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                    server.send_message(msg)

            await asyncio.to_thread(_send)
            return {"status": "sent", "to": to}

        except Exception as e:
            self.log.error("Email send failed", error=str(e))
            return {"status": "failed", "error": str(e)}

    async def _send_slack(self, text: str) -> dict:
        """Send Slack webhook message."""
        if not settings.SLACK_WEBHOOK_URL:
            return {"status": "skipped", "reason": "Slack not configured"}

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    settings.SLACK_WEBHOOK_URL,
                    json={"text": text},
                    timeout=10,
                )
                return {"status": "sent" if resp.status_code == 200 else "failed"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}

    async def _log_notification(self, input_data: dict, messages: dict, results: dict):
        """Log notification dispatch to MongoDB."""
        try:
            col = mongo.get_collection("notifications")
            await col.insert_one({
                "complaint_id": input_data.get("complaint_id"),
                "user_id": input_data.get("user_id"),
                "user_email": input_data.get("user_email"),
                "event_type": input_data.get("event_type", "status_change"),
                "old_status": input_data.get("old_status"),
                "new_status": input_data.get("new_status"),
                "channels_attempted": list(results.keys()),
                "channels_succeeded": [k for k, v in results.items() if v.get("status") == "sent"],
                "messages": {k: v[:200] if isinstance(v, str) else v for k, v in messages.items()},
                "timestamp": datetime.now(timezone.utc),
            })
        except Exception:
            pass

    def _default_messages(self, input_data: dict) -> dict:
        name = input_data.get("student_name", "Student")
        status = input_data.get("new_status", "updated")
        title = input_data.get("title", "your complaint")

        return {
            "email_subject": f"Complaint Update: {title}",
            "email_body": f"<p>Dear {name},</p><p>Your complaint <b>{title}</b> has been {status}.</p>",
            "sms_text": f"BIT Updates: Your complaint '{title[:50]}' is {status}.",
            "whatsapp_text": f"🎫 *BIT Updates*\nYour complaint has been {status}!\n\n_{title}_",
            "slack_text": f"📋 *Complaint Update*: `{title}` → *{status}*",
            "push_notification_title": "Complaint Update",
            "push_notification_body": f"Your complaint is now {status}",
            "should_notify_admin": False,
            "admin_alert_text": None,
        }
