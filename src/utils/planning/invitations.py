"""
Utility functions for managing member invitations
"""
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from google.cloud import firestore
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
import logging
import secrets
import hashlib

logger = logging.getLogger(__name__)

# Invitation expires after 7 days
INVITATION_EXPIRY_DAYS = 7


def generate_invitation_token() -> tuple[str, str]:
    """
    Generate a secure invitation token and its hash.
    
    Returns:
        Tuple of (plain_token, hashed_token)
    """
    # Generate a secure random token
    plain_token = secrets.token_urlsafe(32)
    
    # Hash the token for storage
    hashed_token = hashlib.sha256(plain_token.encode()).hexdigest()
    
    return plain_token, hashed_token


def create_invitation(
    project_id: str,
    invited_by: str,
    name: str,
    email: str,
    role: str,
    seniority: str,
    invited_by_name: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create a pending invitation in Firestore.
    
    Args:
        project_id: Project ID for the invitation
        invited_by: User ID of the inviter
        name: Invitee display name
        email: Invitee email
        role: Role to assign when accepted
        seniority: Seniority to assign when accepted
        invited_by_name: Optional inviter display name
        
    Returns:
        Dictionary with the created invitation data.
        Includes `invitation_token` for delivery workflows.
    """
    try:
        db = FIRESTORE_CLIENT
        invitations_ref = db.collection("member_invitations")
        invitation_ref = invitations_ref.document()
        invitation_id = invitation_ref.id

        plain_token, hashed_token = generate_invitation_token()
        invited_date = datetime.utcnow()
        expires_date = invited_date + timedelta(days=INVITATION_EXPIRY_DAYS)

        invitation_data = {
            "id": invitation_id,
            "project_id": project_id,
            "email": (email or "").strip().lower(),
            "name": name,
            "role": role,
            "seniority": seniority,
            "status": "Pending",
            "invited_by": invited_by,
            "invited_by_name": invited_by_name,
            "invited_date": invited_date.isoformat() + "Z",
            "expires_date": expires_date.isoformat() + "Z",
            "response_date": None,
            "token_hash": hashed_token,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP,
        }

        invitation_ref.set(invitation_data)

        response_payload = dict(invitation_data)
        response_payload.pop("token_hash", None)
        response_payload.pop("created_at", None)
        response_payload.pop("updated_at", None)
        response_payload["invitation_token"] = plain_token

        logger.info(f"Created invitation {invitation_id} for {response_payload['email']} in project {project_id}")
        return response_payload
    except Exception as e:
        logger.error(f"Error creating invitation: {str(e)}")
        raise


def get_project_invitations(
    project_id: str,
    status: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Get all invitations for a project with optional status filter.
    
    Args:
        project_id: Project ID
        status: Optional status filter (Pending/Accepted/Rejected/Expired)
        
    Returns:
        List of invitation dictionaries
    """
    try:
        db = FIRESTORE_CLIENT
        query = db.collection("member_invitations").where("project_id", "==", project_id)
        
        # Apply status filter
        if status:
            query = query.where("status", "==", status)
        
        # Execute query
        invitations = []
        for doc in query.stream():
            invitation_data = doc.to_dict()
            
            # Remove sensitive fields
            invitation_data.pop("token_hash", None)
            invitation_data.pop("created_at", None)
            invitation_data.pop("updated_at", None)
            
            # Get invited_by user name
            if invitation_data.get("invited_by"):
                invited_by_ref = db.collection("users").document(invitation_data["invited_by"])
                invited_by_doc = invited_by_ref.get()
                if invited_by_doc.exists:
                    invited_by_data = invited_by_doc.to_dict()
                    invitation_data["invited_by_name"] = invited_by_data.get("name", "Unknown")
                else:
                    invitation_data["invited_by_name"] = None
            
            invitations.append(invitation_data)
        
        logger.info(f"Retrieved {len(invitations)} invitations for project {project_id}")
        return invitations
        
    except Exception as e:
        logger.error(f"Error getting project invitations: {str(e)}")
        raise


def get_invitation_by_id(invitation_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a specific invitation by ID.
    
    Args:
        invitation_id: Invitation ID
        
    Returns:
        Invitation dictionary or None if not found
    """
    try:
        db = FIRESTORE_CLIENT
        invitation_ref = db.collection("member_invitations").document(invitation_id)
        invitation_doc = invitation_ref.get()
        
        if not invitation_doc.exists:
            return None
        
        invitation_data = invitation_doc.to_dict()
        invitation_data.pop("created_at", None)
        invitation_data.pop("updated_at", None)
        
        return invitation_data
        
    except Exception as e:
        logger.error(f"Error getting invitation by ID: {str(e)}")
        raise


def get_invitation_by_token(token: str) -> Optional[Dict[str, Any]]:
    """
    Get an invitation by its token.
    
    Args:
        token: Plain invitation token
        
    Returns:
        Invitation dictionary or None if not found
    """
    try:
        # Hash the token to match stored hash
        hashed_token = hashlib.sha256(token.encode()).hexdigest()
        
        db = FIRESTORE_CLIENT
        query = db.collection("member_invitations").where("token_hash", "==", hashed_token)
        
        invitations = list(query.stream())
        if not invitations:
            return None
        
        invitation_data = invitations[0].to_dict()
        invitation_data.pop("created_at", None)
        invitation_data.pop("updated_at", None)
        
        return invitation_data
        
    except Exception as e:
        logger.error(f"Error getting invitation by token: {str(e)}")
        raise


def check_pending_invitation(project_id: str, email: str) -> bool:
    """
    Check if there's a pending invitation for the email in the project.
    
    Args:
        project_id: Project ID
        email: Email address
        
    Returns:
        True if pending invitation exists, False otherwise
    """
    try:
        db = FIRESTORE_CLIENT
        query = db.collection("member_invitations")\
            .where("project_id", "==", project_id)\
            .where("email", "==", email)\
            .where("status", "==", "Pending")
        
        invitations = list(query.stream())
        return len(invitations) > 0
        
    except Exception as e:
        logger.error(f"Error checking pending invitation: {str(e)}")
        raise


def cancel_invitation(invitation_id: str) -> bool:
    """
    Cancel a pending invitation.
    
    Args:
        invitation_id: Invitation ID
        
    Returns:
        True if successful, False if not found or not pending
    """
    try:
        db = FIRESTORE_CLIENT
        invitation_ref = db.collection("member_invitations").document(invitation_id)
        invitation_doc = invitation_ref.get()
        
        if not invitation_doc.exists:
            return False
        
        invitation_data = invitation_doc.to_dict()
        
        # Can only cancel pending invitations
        if invitation_data.get("status") != "Pending":
            return False
        
        # Delete the invitation
        invitation_ref.delete()
        
        logger.info(f"Cancelled invitation {invitation_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error cancelling invitation: {str(e)}")
        raise


def resend_invitation(invitation_id: str) -> Optional[tuple[Dict[str, Any], str]]:
    """
    Resend a pending invitation and extend the expiration date.
    
    Args:
        invitation_id: Invitation ID
        
    Returns:
        Tuple of (updated_invitation_data, new_plain_token) or None if not found/not pending
    """
    try:
        db = FIRESTORE_CLIENT
        invitation_ref = db.collection("member_invitations").document(invitation_id)
        invitation_doc = invitation_ref.get()
        
        if not invitation_doc.exists:
            return None
        
        invitation_data = invitation_doc.to_dict()
        
        # Can only resend pending invitations
        if invitation_data.get("status") != "Pending":
            return None
        
        # Generate new token
        plain_token, hashed_token = generate_invitation_token()
        
        # Extend expiration date
        invited_date = datetime.utcnow()
        expires_date = invited_date + timedelta(days=INVITATION_EXPIRY_DAYS)
        
        # Update invitation
        update_data = {
            "invited_date": invited_date.isoformat() + "Z",
            "expires_date": expires_date.isoformat() + "Z",
            "token_hash": hashed_token,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        
        invitation_ref.update(update_data)
        
        # Get updated data
        updated_doc = invitation_ref.get()
        updated_data = updated_doc.to_dict()
        updated_data.pop("token_hash", None)
        updated_data.pop("created_at", None)
        updated_data.pop("updated_at", None)
        
        logger.info(f"Resent invitation {invitation_id}")
        return updated_data, plain_token
        
    except Exception as e:
        logger.error(f"Error resending invitation: {str(e)}")
        raise


def accept_invitation(token: str, user_id: str) -> Optional[Dict[str, Any]]:
    """
    Accept an invitation and create a team member.
    
    Args:
        token: Plain invitation token
        user_id: User ID of the person accepting
        
    Returns:
        Dictionary with project and member info, or None if invalid
    """
    try:
        # Get invitation by token
        invitation_data = get_invitation_by_token(token)
        if not invitation_data:
            return None
        
        # Check if invitation is still pending
        if invitation_data.get("status") != "Pending":
            return None
        
        # Check if invitation has expired
        expires_date = datetime.fromisoformat(invitation_data["expires_date"].replace("Z", ""))
        if datetime.utcnow() > expires_date:
            # Update status to expired
            db = FIRESTORE_CLIENT
            invitation_ref = db.collection("member_invitations").document(invitation_data["id"])
            invitation_ref.update({
                "status": "Expired",
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            return None
        
        db = FIRESTORE_CLIENT
        
        # Create team member
        from src.utils.planning.members import create_team_member
        member_data = create_team_member(
            project_id=invitation_data["project_id"],
            user_id=user_id,
            name=invitation_data["name"],
            email=invitation_data["email"],
            role=invitation_data["role"],
            seniority=invitation_data["seniority"]
        )
        
        # Update invitation status
        invitation_ref = db.collection("member_invitations").document(invitation_data["id"])
        invitation_ref.update({
            "status": "Accepted",
            "response_date": datetime.utcnow().isoformat() + "Z",
            "updated_at": firestore.SERVER_TIMESTAMP
        })
        
        # Get project name
        project_ref = db.collection("projects").document(invitation_data["project_id"])
        project_doc = project_ref.get()
        project_name = "Unknown"
        if project_doc.exists:
            project_data = project_doc.to_dict()
            project_name = project_data.get("name", "Unknown")
        
        logger.info(f"Accepted invitation {invitation_data['id']} for user {user_id}")
        
        return {
            "project_id": invitation_data["project_id"],
            "project_name": project_name,
            "member_id": member_data["id"],
            "role": invitation_data["role"],
            "seniority": invitation_data["seniority"]
        }
        
    except Exception as e:
        logger.error(f"Error accepting invitation: {str(e)}")
        raise


def reject_invitation(token: str) -> bool:
    """
    Reject an invitation.
    
    Args:
        token: Plain invitation token
        
    Returns:
        True if successful, False if invalid
    """
    try:
        # Get invitation by token
        invitation_data = get_invitation_by_token(token)
        if not invitation_data:
            return False
        
        # Check if invitation is still pending
        if invitation_data.get("status") != "Pending":
            return False
        
        # Check if invitation has expired
        expires_date = datetime.fromisoformat(invitation_data["expires_date"].replace("Z", ""))
        if datetime.utcnow() > expires_date:
            # Update status to expired
            db = FIRESTORE_CLIENT
            invitation_ref = db.collection("member_invitations").document(invitation_data["id"])
            invitation_ref.update({
                "status": "Expired",
                "updated_at": firestore.SERVER_TIMESTAMP
            })
            return False
        
        # Update invitation status
        db = FIRESTORE_CLIENT
        invitation_ref = db.collection("member_invitations").document(invitation_data["id"])
        invitation_ref.update({
            "status": "Rejected",
            "response_date": datetime.utcnow().isoformat() + "Z",
            "updated_at": firestore.SERVER_TIMESTAMP
        })
        
        logger.info(f"Rejected invitation {invitation_data['id']}")
        return True
        
    except Exception as e:
        logger.error(f"Error rejecting invitation: {str(e)}")
        raise


def check_and_expire_invitations(project_id: str):
    """
    Check and expire any pending invitations that have passed their expiration date.
    
    Args:
        project_id: Project ID to check invitations for
    """
    try:
        db = FIRESTORE_CLIENT
        query = db.collection("member_invitations")\
            .where("project_id", "==", project_id)\
            .where("status", "==", "Pending")
        
        now = datetime.utcnow()
        expired_count = 0
        
        for doc in query.stream():
            invitation_data = doc.to_dict()
            expires_date = datetime.fromisoformat(invitation_data["expires_date"].replace("Z", ""))
            
            if now > expires_date:
                doc.reference.update({
                    "status": "Expired",
                    "updated_at": firestore.SERVER_TIMESTAMP
                })
                expired_count += 1
        
        if expired_count > 0:
            logger.info(f"Expired {expired_count} invitations for project {project_id}")
        
    except Exception as e:
        logger.error(f"Error checking and expiring invitations: {str(e)}")
        raise
