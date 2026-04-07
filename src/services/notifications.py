from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
import logging
from typing import Iterable, Optional, Sequence

import requests

from src.services.setup.variables_setup import (
    FRONTEND_BASE_URL,
    NOTIFICATION_API_URL,
    NOTIFICATION_SENDER_EMAIL,
    NOTIFICATION_SENDER_NAME,
)

logger = logging.getLogger(__name__)


class NotificationServiceError(RuntimeError):
    """Raised when the notification provider returns an error."""


@dataclass
class NotificationAttachment:
    name: str
    content: bytes | bytearray | Sequence[int]

    def to_payload(self) -> dict:
        if isinstance(self.content, (bytes, bytearray)):
            attachment_bytes = list(self.content)
        else:
            attachment_bytes = [int(value) for value in self.content]
        return {
            "Attachment": attachment_bytes,
            "Name": self.name,
        }


@dataclass
class NotificationMessage:
    to: Sequence[str]
    subject: str
    body: str
    html_body: Optional[str] = None
    is_html: bool = False
    cc: Sequence[str] = field(default_factory=list)
    bcc: Sequence[str] = field(default_factory=list)
    attachments: Sequence[NotificationAttachment] = field(default_factory=list)
    sender_mail: Optional[str] = None
    sender_name: Optional[str] = None

    def to_payload(self, default_sender_mail: str, default_sender_name: str) -> dict:
        sender_mail = (self.sender_mail or default_sender_mail or "").strip()
        sender_name = (self.sender_name or default_sender_name or "").strip()
        return {
            "To": list(self.to),
            "Cc": list(self.cc),
            "Bcc": list(self.bcc),
            "Subject": self.subject,
            "Body": self.body,
            "HTMLBody": self.html_body or "",
            "isHTML": bool(self.is_html),
            "SenderMail": sender_mail,
            "SenderName": sender_name,
            "AttachmentList": [attachment.to_payload() for attachment in self.attachments],
        }


class NotificationService:
    def __init__(
        self,
        *,
        api_url: Optional[str] = None,
        sender_mail: Optional[str] = None,
        sender_name: Optional[str] = None,
        frontend_base_url: Optional[str] = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.api_url = (api_url or NOTIFICATION_API_URL or "").strip()
        self.sender_mail = (sender_mail or NOTIFICATION_SENDER_EMAIL or "").strip()
        self.sender_name = (sender_name or NOTIFICATION_SENDER_NAME or "").strip()
        self.frontend_base_url = (frontend_base_url or FRONTEND_BASE_URL or "").rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._validate_sender(self.sender_mail)

    @staticmethod
    def _validate_sender(sender_mail: str) -> None:
        normalized = str(sender_mail or "").strip().lower()
        if not normalized.endswith("@fridaplatform.online"):
            raise ValueError("SenderMail must use the @fridaplatform.online domain")

    @staticmethod
    def _normalize_recipients(values: Optional[Iterable[str]]) -> list[str]:
        recipients: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            clean = str(value or "").strip().lower()
            if not clean or clean in seen:
                continue
            seen.add(clean)
            recipients.append(clean)
        return recipients

    def send_mail(self, message: NotificationMessage) -> dict | str | None:
        if not self.api_url:
            raise NotificationServiceError("NOTIFICATION_API_URL is not configured")

        payload = message.to_payload(self.sender_mail, self.sender_name)
        payload["To"] = self._normalize_recipients(payload.get("To"))
        payload["Cc"] = self._normalize_recipients(payload.get("Cc"))
        payload["Bcc"] = self._normalize_recipients(payload.get("Bcc"))

        if not payload["To"]:
            raise NotificationServiceError("At least one recipient is required")

        response = requests.post(
            self.api_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=self.timeout_seconds,
        )

        if not response.ok:
            raise NotificationServiceError(
                f"Notification provider returned {response.status_code}: {response.text}"
            )

        try:
            return response.json()
        except ValueError:
            return response.text

    def send_project_invitation(
        self,
        *,
        invitee_name: str,
        invitee_email: str,
        project_name: str,
        inviter_name: str,
        role: str,
        seniority: str,
        expires_at: Optional[str] = None,
    ) -> dict | str | None:
        clean_invitee_name = invitee_name.strip() or invitee_email
        clean_project_name = project_name.strip() or "your project"
        clean_inviter_name = inviter_name.strip() or "A Product Planner administrator"
        login_url = f"{self.frontend_base_url}/login" if self.frontend_base_url else ""

        subject = f"You've been invited to join {clean_project_name}"
        body_lines = [
            f"Hello {clean_invitee_name},",
            "",
            f"{clean_inviter_name} invited you to join the project '{clean_project_name}' in Product Planner.",
            f"Assigned role: {role}",
            f"Assigned seniority: {seniority}",
        ]
        if expires_at:
            body_lines.append(f"Invitation expires: {expires_at}")
        if login_url:
            body_lines.extend(["", f"Sign in to Product Planner: {login_url}"])
        body_lines.extend(["", "If you were not expecting this invitation, you can ignore this email."])

        html_parts = [
            f"<p>Hello {escape(clean_invitee_name)},</p>",
            (
                "<p>"
                f"<strong>{escape(clean_inviter_name)}</strong> invited you to join "
                f"<strong>{escape(clean_project_name)}</strong> in Product Planner."
                "</p>"
            ),
            "<ul>",
            f"<li><strong>Role:</strong> {escape(role)}</li>",
            f"<li><strong>Seniority:</strong> {escape(seniority)}</li>",
        ]
        if expires_at:
            html_parts.append(f"<li><strong>Invitation expires:</strong> {escape(expires_at)}</li>")
        html_parts.append("</ul>")
        if login_url:
            html_parts.append(
                "<p>"
                f"<a href=\"{escape(login_url)}\">Open Product Planner</a>"
                "</p>"
            )
        html_parts.append("<p>If you were not expecting this invitation, you can ignore this email.</p>")

        message = NotificationMessage(
            to=[invitee_email],
            subject=subject,
            body="\n".join(body_lines),
            html_body="".join(html_parts),
            is_html=True,
        )
        return self.send_mail(message)

    def send_project_member_added(
        self,
        *,
        member_name: str,
        member_email: str,
        project_name: str,
        added_by_name: str,
        role: str,
        seniority: str,
    ) -> dict | str | None:
        clean_member_name = member_name.strip() or member_email
        clean_project_name = project_name.strip() or "your project"
        clean_added_by_name = added_by_name.strip() or "A Product Planner administrator"
        login_url = f"{self.frontend_base_url}/login" if self.frontend_base_url else ""

        subject = f"You were added to {clean_project_name}"
        body_lines = [
            f"Hello {clean_member_name},",
            "",
            f"{clean_added_by_name} added you to the project '{clean_project_name}' in Product Planner.",
            f"Assigned role: {role}",
            f"Assigned seniority: {seniority}",
        ]
        if login_url:
            body_lines.extend(["", f"Sign in to Product Planner: {login_url}"])
        body_lines.extend(["", "You already have access to this project."])

        html_parts = [
            f"<p>Hello {escape(clean_member_name)},</p>",
            (
                "<p>"
                f"<strong>{escape(clean_added_by_name)}</strong> added you to "
                f"<strong>{escape(clean_project_name)}</strong> in Product Planner."
                "</p>"
            ),
            "<ul>",
            f"<li><strong>Role:</strong> {escape(role)}</li>",
            f"<li><strong>Seniority:</strong> {escape(seniority)}</li>",
            "</ul>",
        ]
        if login_url:
            html_parts.append(
                "<p>"
                f"<a href=\"{escape(login_url)}\">Open Product Planner</a>"
                "</p>"
            )
        html_parts.append("<p>You already have access to this project.</p>")

        message = NotificationMessage(
            to=[member_email],
            subject=subject,
            body="\n".join(body_lines),
            html_body="".join(html_parts),
            is_html=True,
        )
        return self.send_mail(message)

    def send_user_story_assignment(
        self,
        *,
        assignee_name: str,
        assignee_email: str,
        project_name: str,
        epic_name: str,
        story_title: str,
        story_reference: str,
        assigned_by_name: str,
    ) -> dict | str | None:
        clean_assignee_name = assignee_name.strip() or assignee_email
        clean_project_name = project_name.strip() or "your project"
        clean_epic_name = epic_name.strip() or "an epic"
        clean_story_title = story_title.strip() or "a user story"
        clean_story_reference = story_reference.strip()
        clean_assigned_by_name = assigned_by_name.strip() or "A Product Planner administrator"
        project_url = f"{self.frontend_base_url}/projects" if self.frontend_base_url else ""

        subject = f"User story assigned: {clean_story_title}"
        body_lines = [
            f"Hello {clean_assignee_name},",
            "",
            f"{clean_assigned_by_name} assigned you a user story in Product Planner.",
            f"Project: {clean_project_name}",
            f"Epic: {clean_epic_name}",
            f"User story: {clean_story_title}",
        ]
        if clean_story_reference:
            body_lines.append(f"Reference: {clean_story_reference}")
        if project_url:
            body_lines.extend(["", f"Open Product Planner: {project_url}"])

        html_parts = [
            f"<p>Hello {escape(clean_assignee_name)},</p>",
            (
                "<p>"
                f"<strong>{escape(clean_assigned_by_name)}</strong> assigned you a user story in Product Planner."
                "</p>"
            ),
            "<ul>",
            f"<li><strong>Project:</strong> {escape(clean_project_name)}</li>",
            f"<li><strong>Epic:</strong> {escape(clean_epic_name)}</li>",
            f"<li><strong>User story:</strong> {escape(clean_story_title)}</li>",
        ]
        if clean_story_reference:
            html_parts.append(f"<li><strong>Reference:</strong> {escape(clean_story_reference)}</li>")
        html_parts.append("</ul>")
        if project_url:
            html_parts.append(
                "<p>"
                f"<a href=\"{escape(project_url)}\">Open Product Planner</a>"
                "</p>"
            )

        message = NotificationMessage(
            to=[assignee_email],
            subject=subject,
            body="\n".join(body_lines),
            html_body="".join(html_parts),
            is_html=True,
        )
        return self.send_mail(message)

    def try_send_project_invitation(self, **kwargs) -> bool:
        try:
            self.send_project_invitation(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send project invitation notification")
            return False

    def try_send_project_member_added(self, **kwargs) -> bool:
        try:
            self.send_project_member_added(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send project membership notification")
            return False

    def try_send_user_story_assignment(self, **kwargs) -> bool:
        try:
            self.send_user_story_assignment(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send user story assignment notification")
            return False
