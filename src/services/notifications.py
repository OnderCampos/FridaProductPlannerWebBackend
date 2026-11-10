from __future__ import annotations

from dataclasses import dataclass, field
from html import escape
import logging
from typing import Any, Dict, Iterable, Optional, Sequence

import requests

from src.services.setup.variables_setup import (
    DISABLE_EMAIL_NOTIFICATIONS,
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
        """
        Converts the attachment to a dictionary format suitable for sending to the notification provider API.
        The content is converted to a list of integers representing the byte values, as expected by the
        notification provider. The name is included as-is.
        """
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
        """
        Converts the notification message to a dictionary format suitable for sending to the notification provider API.
        The sender_mail and sender_name are determined by the message's own values if provided, or fall back to the defaults passed as arguments. The recipients (to, cc, bcc) are converted to lists, and the attachments are converted using their to_payload method. The subject, body, html_body, and is_html fields are included as-is.
        """
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
        self.notifications_disabled = bool(DISABLE_EMAIL_NOTIFICATIONS)
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
        """
        Sends a notification message using the configured notification provider API.
        Args:
            message: An instance of NotificationMessage containing the details of the email to be sent.
        Returns:
            The response from the notification provider, which could be a dictionary (if JSON is returned), a string (if plain text is returned), or None if there was no content.
        Raises:
            NotificationServiceError: If the notification provider returns an error status code or if the API URL is not configured.
        """
        if self.notifications_disabled:
            logger.info(
                "Email notifications are disabled via DISABLE_EMAIL_NOTIFICATIONS; skipping subject '%s'",
                message.subject,
            )
            return {
                "sent": False,
                "status": "disabled",
                "reason": "notifications_disabled",
                "message": "Email notifications are disabled by environment configuration.",
            }

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

    def build_email_template(self, main_content: str) -> str:
        """
        Builds a complete HTML email template with the given main content. The template includes consistent styling and structure for all notification emails sent by Product Planner.
        Args:
            main_content: The main HTML content to be included in the body of the email. This should be a well-formed HTML string containing the specific message for the notification.
        Returns:
            A complete HTML string representing the email template, ready to be sent as the html_body of a notification message.
        """
        bg_outer = "#060a1a"      
        bg_card = "#0b1a32"       
        text_primary = "#eaf2ff"  
        text_muted = "#8fb5ff"    
        accent_cyan = "#16e0d0"   
        border_color = "rgba(255, 255, 255, 0.12)"

        html = f"""
        <div style="background-color: {bg_outer}; padding: 40px 20px; font-family: 'Segoe UI', Arial, sans-serif; -webkit-font-smoothing: antialiased;">
            <div style="max-width: 600px; margin: 0 auto; background-color: {bg_card}; border: 1px solid {border_color}; border-radius: 12px; overflow: hidden; box-shadow: 0 22px 70px rgba(0, 0, 0, 0.55);">
                
                <div style="text-align: center; padding: 35px 20px 25px; border-bottom: 1px solid rgba(255, 255, 255, 0.05);">
                    <h1 style="margin: 0; font-size: 24px; color: #ffffff; font-weight: 600; letter-spacing: 1px;">
                        PRODUCT <span style="color: {accent_cyan};">PLANNER</span>
                    </h1>
                </div>
                
                <div style="padding: 35px 40px; color: {text_primary}; font-size: 15px; line-height: 1.6;">
                    {main_content}
                </div>
                
                <div style="padding: 25px; text-align: center; border-top: 1px solid {border_color}; background-color: rgba(0, 0, 0, 0.15);">
                    <p style="margin: 0; font-size: 12px; color: {text_muted};">
                        This message was automatically generated by Product Planner. If you have any questions, please contact your administrator.
                    </p>
                </div>
                
            </div>
        </div>
        """
        return html

    def _notification_result(self, sent: bool, status: str, reason: str, message: str) -> Dict[str, Any]:
        return {
            "sent": bool(sent),
            "status": str(status),
            "reason": str(reason),
            "message": str(message),
        }

    """
    ---------------------------------------------------------------------------------------------------------------------------------------
    ---------------------------------------------------------------------------------------------------------------------------------------
    ---------------------------------------------------------------------------------------------------------------------------------------
    PROJECTS NOTIFICATIONS
    ----------------------------------------------------------------------------------------------------------------------------------------
    -----------------------------------------------------------------------------------------------------------------------------------------
    -----------------------------------------------------------------------------------------------------------------------------------------
    """
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
        """
        Sends an email notification to a user when they are invited to join a project.
        Args:
            invitee_name: The name of the person being invited.
            invitee_email: The email address of the person being invited.
            project_name: The name of the project they are being invited to.
            inviter_name: The name of the person who sent the invitation.
            role: The role assigned to the invitee in the project.
            seniority: The seniority level assigned to the invitee in the project.
            expires_at: Optional expiration date for the invitation (e.g., "2024-12-31").
        Returns:
            The response from the notification service, or None if the email could not be sent.
        """
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

        final_html = self.build_email_template("".join(html_parts))

        message = NotificationMessage(
            to=[invitee_email],
            subject=subject,
            body="\n".join(body_lines),
            #html_body="".join(html_parts),
            html_body=final_html,
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
        """
        Sends an email notification to a user when they are added to a project they already have access to.
        Args:
            member_name: The name of the person being added.
            member_email: The email address of the person being added.
            project_name: The name of the project they are being added to.
            added_by_name: The name of the person who added them to the project.
            role: The role assigned to the member in the project.
            seniority: The seniority level assigned to the member in the project.
        Returns:
            The response from the notification service, or None if the email could not be sent.
        """
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

        final_html = self.build_email_template("".join(html_parts))

        message = NotificationMessage(
            to=[member_email],
            subject=subject,
            body="\n".join(body_lines),
            #html_body="".join(html_parts),
            html_body=final_html,
            is_html=True,
        )
        return self.send_mail(message)

    def send_account_created(
        self,
        *,
        account_name: str,
        account_email: str,
        password: str,
    ) -> dict | str | None:
        """
        Sends an email notifying a newly provisioned user that their account was created.
        """
        clean_account_name = account_name.strip() or account_email
        login_url = f"{self.frontend_base_url}/login" if self.frontend_base_url else ""

        subject = "Your Product Planner account was created"
        body_lines = [
            f"Hello {clean_account_name},",
            "",
            "A Product Planner account was created for you.",
            f"Email: {account_email}",
            f"Password: {password}",
            "",
            "Please sign in and change your password as soon as possible.",
        ]
        if login_url:
            body_lines.extend(["", f"Sign in to Product Planner: {login_url}"])

        html_parts = [
            f"<p>Hello {escape(clean_account_name)},</p>",
            "<p>A Product Planner account was created for you.</p>",
            "<div style='background-color: rgba(0,0,0,0.2); padding: 16px; border-radius: 8px; border-left: 3px solid #16e0d0; margin-top: 16px;'>",
            f"<p style='margin: 0 0 8px; color: #eaf2ff;'><strong>Email:</strong> {escape(account_email)}</p>",
            f"<p style='margin: 0; color: #eaf2ff;'><strong>Password:</strong> {escape(password)}</p>",
            "</div>",
            "<p>Please sign in and change your password as soon as possible.</p>",
        ]
        if login_url:
            html_parts.append(
                "<div style='text-align: center; margin: 35px 0 20px;'>"
                f"<a href=\"{escape(login_url)}\" style=\"display: inline-block; padding: 14px 28px; "
                "background-color: #16e0d0; color: #051327; text-decoration: none; "
                "border-radius: 6px; font-weight: 600; font-size: 14px;\">"
                "Open Product Planner"
                "</a></div>"
            )

        final_html = self.build_email_template("".join(html_parts))

        message = NotificationMessage(
            to=[account_email],
            subject=subject,
            body="\n".join(body_lines),
            html_body=final_html,
            is_html=True,
        )
        return self.send_mail(message)

    def try_send_project_invitation(self, **kwargs) -> bool:
        """
        Tries to send a project invitation notification, returning True if successful or False if an error occurs.
        The kwargs should match the parameters of the send_project_invitation method.
        """
        try:
            self.send_project_invitation(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send project invitation notification")
            return False

    def try_send_account_created(self, **kwargs) -> bool:
        """
        Tries to send an account created notification, returning True if successful or False if an error occurs.
        """
        try:
            self.send_account_created(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send account created notification")
            return False

    def try_send_project_member_added(self, **kwargs) -> bool:
        """
        Tries to send a project member added notification, returning True if successful or False if an error occurs.
        The kwargs should match the parameters of the send_project_member_added method.
        """
        try:
            self.send_project_member_added(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send project membership notification")
            return False

    """
    ---------------------------------------------------------------------------------------------------------------------------------------
    ---------------------------------------------------------------------------------------------------------------------------------------
    ---------------------------------------------------------------------------------------------------------------------------------------
    USER STORY NOTIFICATIONS
    ----------------------------------------------------------------------------------------------------------------------------------------
    -----------------------------------------------------------------------------------------------------------------------------------------
    -----------------------------------------------------------------------------------------------------------------------------------------
    """
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
        """
        Sends an email notification to a user when they are assigned a user story.
        Args:
            assignee_name: The name of the person being assigned.
            assignee_email: The email address of the person being assigned.
            project_name: The name of the project the user story belongs to.
            epic_name: The name of the epic the user story belongs to.
            story_title: The title of the user story they are being assigned to.
            story_reference: A reference or link related to the user story (e.g., a ticket number or URL).
            assigned_by_name: The name of the person who assigned them the user story.
        Returns:
            The response from the notification service, or None if the email could not be sent.
        """
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
                "<div style='text-align: center; margin: 35px 0 20px;'>"
                f"<a href=\"{escape(project_url)}\" style=\"display: inline-block; padding: 14px 28px; "
                "background-color: #16e0d0; color: #051327; text-decoration: none; "
                "border-radius: 6px; font-weight: 600; font-size: 14px;\">"
                "Open Product Planner"
                "</a></div>"
            )

        final_html = self.build_email_template("".join(html_parts))

        message = NotificationMessage(
            to=[assignee_email],
            subject=subject,
            body="\n".join(body_lines),
            #html_body="".join(html_parts),
            html_body=final_html,
            is_html=True,
        )
        return self.send_mail(message)

    def send_user_story_updated(
        self,
        *,
        leader_email: str,
        leader_name: str,
        changer_name: str,
        project_name: str,
        epic_name: str,
        story_title: str,
        changes: dict
    ) -> dict | str | None:
        clean_leader_name = leader_name.strip() or leader_email
        clean_changer_name = changer_name.strip() or "A user"
        clean_project_name = project_name.strip() or "a project"
        clean_epic_name = epic_name.strip() or "Am epic"
        clean_story_title = story_title.strip() or "a user story"
        project_url = f"{self.frontend_base_url}/projects" if self.frontend_base_url else ""

        subject = f"Update in: {clean_story_title}"

        body_lines = [
            f"Hello {clean_leader_name},",
            "",
            f"{clean_changer_name} updated a user story in {clean_project_name}.",
            f"Epic: {clean_epic_name}",
            f"Story: {clean_story_title}",
            f"Changes: ",
        ]
        for field, delta in changes.items():
            body_lines.append(f"- {field}: {delta['old']} -> {delta['new']}")

        # Build changes list in HTML
        changes_html = ""
        for field, delta in changes.items():
            changes_html += (
                f"<li style='margin-bottom: 8px;'><strong style='color: #eaf2ff;'>{field}:</strong> "
                f"<span style='opacity: 0.7;'>{escape(delta['old'])}</span> "
                f"➔ <span style='color: #16e0d0; font-weight: 500;'>{escape(delta['new'])}</span></li>"
            )

        html_parts = [
            f"<p>Hello <strong>{escape(clean_leader_name)}</strong>,</p>",
            (
                "<p>"
                f"<strong>{escape(clean_changer_name)}</strong> updated a user story in "
                f"<strong>{escape(clean_project_name)}</strong>."
                "</p>"
            ),
            "<div style='background-color: rgba(0,0,0,0.2); padding: 15px; border-radius: 8px; border-left: 3px solid #16e0d0; margin-top: 15px;'>",
            f"<p style='margin-top: 0; color: #eaf2ff;'><strong>Epic:</strong> {escape(clean_epic_name)}<br/>",
            f"<strong>Story:</strong> {escape(clean_story_title)}</p>",
            "<ul style='color: #8fb5ff; padding-left: 20px; margin-bottom: 0;'>",
            changes_html,
            "</ul>",
            "</div>",
        ]

        if project_url:
            html_parts.append(
                "<div style='text-align: center; margin: 35px 0 20px;'>"
                f"<a href=\"{escape(project_url)}\" style=\"display: inline-block; padding: 14px 28px; "
                "background-color: #16e0d0; color: #051327; text-decoration: none; "
                "border-radius: 6px; font-weight: 600; font-size: 14px;\">"
                "Review in Product Planner"
                "</a></div>"
            )

        final_html = self.build_email_template("".join(html_parts))

        message = NotificationMessage(
            to=[leader_email],
            subject=subject,
            body="\n".join(body_lines),
            html_body=final_html,
            is_html=True
        )

        return self.send_mail(message)

    def try_send_user_story_assignment(self, **kwargs) -> bool:
        """
        Tries to send a user story assignment notification, returning True if successful or False if an
        error occurs. The kwargs should match the parameters of the send_user_story_assignment method.
        """
        try:
            self.send_user_story_assignment(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send user story assignment notification")
            return False

    def try_send_user_story_updated(self, **kwargs) -> bool:
        try:
            self.send_user_story_updated(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send user story updated notification")
            return False

    """
    ---------------------------------------------------------------------------------------------------------------------------------------
    ---------------------------------------------------------------------------------------------------------------------------------------
    ---------------------------------------------------------------------------------------------------------------------------------------
    SPRINTS NOTIFICATIONS
    ----------------------------------------------------------------------------------------------------------------------------------------
    -----------------------------------------------------------------------------------------------------------------------------------------
    -----------------------------------------------------------------------------------------------------------------------------------------
    """
    def send_sprint_assignment(
        self,
        *,
        leader_email: str,
        leader_name: str,
        changer_name: str,
        project_name: str,
        epic_name: Optional[str],
        item_title: str,
        old_sprint_name: str,
        new_sprint_name: str,
    ) -> dict | str | None:
        clean_leader_name = leader_name.strip() or leader_email
        clean_changer_name = changer_name.strip() or "A user"
        clean_project_name = project_name.strip() or "a project"
        clean_item_title = item_title.strip() or "an item"
        project_url = f"{self.frontend_base_url}/projects" if self.frontend_base_url else ""

        subject = f"Sprint Update: {clean_item_title}"

        body_lines = [
            f"Hello {clean_leader_name},",
            "",
            f"{clean_changer_name} updated a sprint assignment in {clean_project_name}.",
        ]

        clean_epic_name = ""
        if epic_name and epic_name != "N/A":
            clean_epic_name = epic_name.strip()
            body_lines.append(f"Epic: {clean_epic_name}")
            
        body_lines.extend([
            f"Item: {clean_item_title}",
            f"Movement: {old_sprint_name} -> {new_sprint_name}",
        ])

        html_parts = [
            f"<p>Hello <strong>{escape(clean_leader_name)}</strong>,</p>",
            (
                "<p>"
                f"<strong>{escape(clean_changer_name)}</strong> updated a sprint assignment in "
                f"<strong>{escape(clean_project_name)}</strong>."
                "</p>"
            ),
            "<div style='background-color: rgba(0,0,0,0.2); padding: 15px; border-radius: 8px; border-left: 3px solid #16e0d0; margin-top: 15px;'>",
        ]

        if clean_epic_name:
            html_parts.append(f"<p style='margin-top: 0; margin-bottom: 5px; color: #eaf2ff;'><strong>Epic:</strong> {escape(clean_epic_name)}</p>")

        html_parts.extend([
            f"<p style='margin-top: 0; margin-bottom: 10px; color: #eaf2ff;'><strong>Item:</strong> {escape(clean_item_title)}</p>",
            "<ul style='color: #8fb5ff; padding-left: 20px; margin-bottom: 0;'>",
            f"<li style='margin-bottom: 8px;'><strong style='color: #eaf2ff;'>Sprint:</strong> "
            f"<span style='opacity: 0.7;'>{escape(old_sprint_name)}</span> "
            f"➔ <span style='color: #16e0d0; font-weight: 500;'>{escape(new_sprint_name)}</span></li>",
            "</ul>",
            "</div>",
        ])

        if project_url:
            html_parts.append(
                "<div style='text-align: center; margin: 35px 0 20px;'>"
                f"<a href=\"{escape(project_url)}\" style=\"display: inline-block; padding: 14px 28px; "
                "background-color: #16e0d0; color: #051327; text-decoration: none; "
                "border-radius: 6px; font-weight: 600; font-size: 14px;\">"
                "Review in Product Planner"
                "</a></div>"
            )

        final_html = self.build_email_template("".join(html_parts))

        message = NotificationMessage(
            to=[leader_email],
            subject=subject,
            body="\n".join(body_lines),
            html_body=final_html,
            is_html=True
        )

        return self.send_mail(message)

    def try_send_sprint_assignment(self, **kwargs) -> bool:
        try:
            self.send_sprint_assignment(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send sprint assignment notification")
            return False

    """
    ---------------------------------------------------------------------------------------------------------------------------------------
    ---------------------------------------------------------------------------------------------------------------------------------------
    ---------------------------------------------------------------------------------------------------------------------------------------
    SUBTASKS NOTIFICATIONS
    ----------------------------------------------------------------------------------------------------------------------------------------
    -----------------------------------------------------------------------------------------------------------------------------------------
    -----------------------------------------------------------------------------------------------------------------------------------------
    """
    def send_subtask_updated(
        self,
        *,
        leader_email: str,
        leader_name: str,
        changer_name: str,
        project_name: str,
        epic_name: str,
        parent_story_title: str,
        subtask_title: str,
        changes: dict
    ) -> dict | str | None:
        clean_leader_name = leader_name.strip() or leader_email
        clean_changer_name = changer_name.strip() or "A user"
        clean_project_name = project_name.strip() or "a project"
        clean_epic_name = epic_name.strip() or "an epic"
        clean_parent_story = parent_story_title.strip() or "a user story"
        clean_subtask_title = subtask_title.strip() or "a subtask"
        project_url = f"{self.frontend_base_url}/projects" if self.frontend_base_url else ""

        subject = f"Subtask Update: {clean_subtask_title}"
        
        # Versión en texto plano
        body_lines = [
            f"Hello {clean_leader_name},",
            "",
            f"{clean_changer_name} updated a subtask in {clean_project_name}.",
            f"Epic: {clean_epic_name}",
            f"Parent Story: {clean_parent_story}",
            f"Subtask: {clean_subtask_title}",
            f"Changes: ",
        ]
        for field, delta in changes.items():
            body_lines.append(f"- {field}: {delta['old']} -> {delta['new']}")

        # Build changes list in HTML
        changes_html = ""
        for field, delta in changes.items():
            changes_html += (
                f"<li style='margin-bottom: 8px;'><strong style='color: #eaf2ff;'>{field}:</strong> "
                f"<span style='opacity: 0.7; text-decoration: line-through;'>{escape(delta['old'])}</span> "
                f"➔ <span style='color: #16e0d0; font-weight: 500;'>{escape(delta['new'])}</span></li>"
            )

        html_parts = [
            f"<p>Hello <strong>{escape(clean_leader_name)}</strong>,</p>",
            (
                "<p>"
                f"<strong>{escape(clean_changer_name)}</strong> updated a subtask in "
                f"<strong>{escape(clean_project_name)}</strong>."
                "</p>"
            ),
            "<div style='background-color: rgba(0,0,0,0.2); padding: 15px; border-radius: 8px; border-left: 3px solid #16e0d0; margin-top: 15px;'>",
            f"<p style='margin-top: 0; color: #eaf2ff; margin-bottom: 5px;'><strong>Epic:</strong> {escape(clean_epic_name)}</p>",
            f"<p style='margin-top: 0; color: #eaf2ff; margin-bottom: 5px;'><strong>Story:</strong> {escape(clean_parent_story)}</p>",
            f"<p style='margin-top: 0; color: #eaf2ff;'><strong>Subtask:</strong> {escape(clean_subtask_title)}</p>",
            "<ul style='color: #8fb5ff; padding-left: 20px; margin-bottom: 0;'>",
            changes_html,
            "</ul>",
            "</div>",
        ]

        if project_url:
            html_parts.append(
                "<div style='text-align: center; margin: 35px 0 20px;'>"
                f"<a href=\"{escape(project_url)}\" style=\"display: inline-block; padding: 14px 28px; "
                "background-color: #16e0d0; color: #051327; text-decoration: none; "
                "border-radius: 6px; font-weight: 600; font-size: 14px;\">"
                "Review in Product Planner"
                "</a></div>"
            )

        final_html = self.build_email_template("".join(html_parts))

        message = NotificationMessage(
            to=[leader_email],
            subject=subject,
            body="\n".join(body_lines),
            html_body=final_html,
            is_html=True
        )

        return self.send_mail(message)

    def send_subtask_assignment(
        self,
        *,
        assignee_name: str,
        assignee_email: str,
        project_name: str,
        epic_name: str,
        parent_story_title: str,
        subtask_title: str,
        assigned_by_name: str,
    ) -> dict | str | None:
        clean_assignee_name = assignee_name.strip() or assignee_email
        clean_project_name = project_name.strip() or "your project"
        clean_epic_name = epic_name.strip() or "N/A"
        clean_parent_story = parent_story_title.strip() or "Independent Task"
        clean_subtask_title = subtask_title.strip() or "a subtask"
        clean_assigned_by_name = assigned_by_name.strip() or "A Product Planner administrator"
        project_url = f"{self.frontend_base_url}/projects" if self.frontend_base_url else ""

        subject = f"Subtask assigned: {clean_subtask_title}"
        body_lines = [
            f"Hello {clean_assignee_name},",
            "",
            f"{clean_assigned_by_name} assigned you a subtask in Product Planner.",
            f"Project: {clean_project_name}",
        ]

        # Solo mostramos el Epic y la Historia si existen (para ocultarlos en las independientes)
        if clean_epic_name != "N/A":
            body_lines.append(f"Epic: {clean_epic_name}")
        if clean_parent_story != "Independent Task":
            body_lines.append(f"Parent Story: {clean_parent_story}")

        body_lines.append(f"Subtask: {clean_subtask_title}")

        if project_url:
            body_lines.extend(["", f"Open Product Planner: {project_url}"])

        html_parts = [
            f"<p>Hello <strong>{escape(clean_assignee_name)}</strong>,</p>",
            (
                "<p>"
                f"<strong>{escape(clean_assigned_by_name)}</strong> assigned you a subtask in Product Planner."
                "</p>"
            ),
            "<div style='background-color: rgba(0,0,0,0.2); padding: 15px; border-radius: 8px; border-left: 3px solid #16e0d0; margin-top: 15px;'>",
            f"<p style='margin-top: 0; color: #eaf2ff; margin-bottom: 5px;'><strong>Project:</strong> {escape(clean_project_name)}</p>"
        ]

        # Condicionales para HTML (ocultar campos si es independiente)
        if clean_epic_name != "N/A":
            html_parts.append(f"<p style='margin-top: 0; color: #eaf2ff; margin-bottom: 5px;'><strong>Epic:</strong> {escape(clean_epic_name)}</p>")
        if clean_parent_story != "Independent Task":
            html_parts.append(f"<p style='margin-top: 0; color: #eaf2ff; margin-bottom: 5px;'><strong>Story:</strong> {escape(clean_parent_story)}</p>")

        html_parts.extend([
            f"<p style='margin-top: 0; color: #eaf2ff; margin-bottom: 0;'><strong>Subtask:</strong> {escape(clean_subtask_title)}</p>",
            "</div>"
        ])

        if project_url:
            html_parts.append(
                "<div style='text-align: center; margin: 35px 0 20px;'>"
                f"<a href=\"{escape(project_url)}\" style=\"display: inline-block; padding: 14px 28px; "
                "background-color: #16e0d0; color: #051327; text-decoration: none; "
                "border-radius: 6px; font-weight: 600; font-size: 14px;\">"
                "Open Product Planner"
                "</a></div>"
            )

        final_html = self.build_email_template("".join(html_parts))

        message = NotificationMessage(
            to=[assignee_email],
            subject=subject,
            body="\n".join(body_lines),
            html_body=final_html,
            is_html=True,
        )

        return self.send_mail(message)

    def try_send_subtask_updated(self, **kwargs) -> bool:
        try:
            self.send_subtask_updated(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send subtask updated notification")
            return False

    def try_send_subtask_assignment(self, **kwargs) -> bool:
        try:
            self.send_subtask_assignment(**kwargs)
            return True
        except Exception:
            logger.exception("Failed to send subtask assignment notification")
            return False
