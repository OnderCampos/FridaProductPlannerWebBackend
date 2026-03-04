"""
Utility functions for managing team members
"""
from typing import Dict, Any, Optional, List
from datetime import datetime
from google.cloud import firestore
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.utils.project_memberships import derive_membership_role, upsert_project_membership, delete_project_membership
from src.utils.users import upsert_user_profile
import logging

logger = logging.getLogger(__name__)


def create_team_member(
    project_id: str,
    user_id: Optional[str],
    name: str,
    email: str,
    role: str,
    seniority: str,
    avatar: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a new team member in Firestore.
    
    Args:
        project_id: Project ID the member belongs to
        user_id: User ID of the member
        name: Full name of the member
        email: Email address of the member
        role: Role of the member
        seniority: Seniority level of the member
        avatar: Optional avatar URL
        
    Returns:
        Dict containing the created member data
    """
    try:
        db = FIRESTORE_CLIENT
        members_ref = db.collection("team_members")
        
        # Generate a new member ID
        new_member_ref = members_ref.document()
        member_id = new_member_ref.id
        
        # Create member data
        member_data = {
            "id": member_id,
            "project_id": project_id,
            "user_id": user_id or email,
            "name": name,
            "email": email,
            "role": role,
            "seniority": seniority,
            "status": "Active",
            "joined_date": datetime.utcnow().isoformat() + "Z",
            "avatar": avatar,
            "created_at": firestore.SERVER_TIMESTAMP,
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        
        # Save to Firestore
        new_member_ref.set(member_data)

        # Ensure user + membership records exist
        upsert_user_profile(
            user_id=user_id,
            email=email,
            name=name,
            member_id=member_id,
        )
        membership_role = derive_membership_role(role, seniority)
        membership_user_id = user_id
        if membership_user_id and email and membership_user_id.lower() == email.lower():
            membership_user_id = None
        upsert_project_membership(
            project_id=project_id,
            user_id=membership_user_id,
            email=email,
            role=membership_role,
            project_role=role,
            member_id=member_id,
        )
        
        logger.info(f"Created team member {member_id} for project {project_id}")
        return member_data
        
    except Exception as e:
        logger.error(f"Error creating team member: {str(e)}")
        raise


def get_project_members(
    project_id: str,
    status: Optional[str] = None,
    role: Optional[str] = None,
    seniority: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Get all team members for a project with optional filters.
    
    Args:
        project_id: Project ID to get members for
        status: Optional status filter (Active/Inactive)
        role: Optional role filter
        seniority: Optional seniority filter
        
    Returns:
        List of member dictionaries
    """
    try:
        db = FIRESTORE_CLIENT
        query = db.collection("team_members").where("project_id", "==", project_id)
        
        # Apply filters
        if status:
            query = query.where("status", "==", status)
        if role:
            query = query.where("role", "==", role)
        if seniority:
            query = query.where("seniority", "==", seniority)
        
        # Execute query
        members = []
        for doc in query.stream():
            member_data = doc.to_dict()
            # Remove internal fields
            member_data.pop("created_at", None)
            member_data.pop("updated_at", None)
            members.append(member_data)
        
        logger.info(f"Retrieved {len(members)} members for project {project_id}")
        return members
        
    except Exception as e:
        logger.error(f"Error getting project members: {str(e)}")
        raise


def get_member_by_id(project_id: str, member_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a specific team member by ID.
    
    Args:
        project_id: Project ID
        member_id: Member ID
        
    Returns:
        Member dictionary or None if not found
    """
    try:
        db = FIRESTORE_CLIENT
        member_ref = db.collection("team_members").document(member_id)
        member_doc = member_ref.get()
        
        if not member_doc.exists:
            return None
        
        member_data = member_doc.to_dict()
        
        # Verify the member belongs to the project
        if member_data.get("project_id") != project_id:
            return None
        
        # Remove internal fields
        member_data.pop("created_at", None)
        member_data.pop("updated_at", None)
        
        return member_data
        
    except Exception as e:
        logger.error(f"Error getting member by ID: {str(e)}")
        raise


def get_member_details(member_id: str) -> Optional[Dict[str, Any]]:
    """
    Get detailed information about a team member including statistics.
    
    Args:
        member_id: Member ID
        
    Returns:
        Detailed member dictionary or None if not found
    """
    try:
        db = FIRESTORE_CLIENT
        
        # Get basic member info
        member_ref = db.collection("members").document(member_id)
        member_doc = member_ref.get()
        
        if not member_doc.exists:
            return None
        
        member_data = member_doc.to_dict()
        
        # Remove internal fields
        member_data.pop("created_at", None)
        member_data.pop("updated_at", None)
        
        # Get project_id from member data
        project_id = member_data.get("project_id")
        
        # Get projects the member is part of
        projects = []
        project_members = db.collection("team_members").where("user_id", "==", member_data["user_id"]).stream()
        for pm_doc in project_members:
            pm_data = pm_doc.to_dict()
            # Get project name
            project_ref = db.collection("projects").document(pm_data["project_id"])
            project_doc = project_ref.get()
            if project_doc.exists:
                project_info = project_doc.to_dict()
                projects.append({
                    "projectId": pm_data["project_id"],
                    "projectName": project_info.get("name", "Unknown"),
                    "role": pm_data["role"],
                    "joinedDate": pm_data["joined_date"]
                })
        
        # Get assigned epics count
        epics_query = db.collection("epics").where("project_id", "==", project_id).where("assigned_to", "==", member_id)
        assigned_epics = len(list(epics_query.stream()))
        
        # Get assigned user stories count
        stories_query = db.collection("user_stories").where("epic_id", "in", 
            [epic.id for epic in db.collection("epics").where("project_id", "==", project_id).stream()])
        assigned_user_stories = 0
        completed_user_stories = 0
        
        for story_doc in stories_query.stream():
            story_data = story_doc.to_dict()
            if story_data.get("assigned_to") == member_id:
                assigned_user_stories += 1
                if story_data.get("status") == "Done":
                    completed_user_stories += 1
        
        # Add statistics to member data
        member_data["projects"] = projects
        member_data["assigned_epics"] = assigned_epics
        member_data["assigned_user_stories"] = assigned_user_stories
        member_data["completed_user_stories"] = completed_user_stories
        
        return member_data
        
    except Exception as e:
        logger.error(f"Error getting member details: {str(e)}")
        raise


def update_team_member(
    project_id: str,
    member_id: str,
    role: Optional[str] = None,
    seniority: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Update a team member's role or seniority.
    
    Args:
        project_id: Project ID
        member_id: Member ID
        role: Optional new role
        seniority: Optional new seniority
        
    Returns:
        Updated member dictionary or None if not found
    """
    try:
        db = FIRESTORE_CLIENT
        member_ref = db.collection("team_members").document(member_id)
        member_doc = member_ref.get()
        
        if not member_doc.exists:
            return None
        
        member_data = member_doc.to_dict()
        
        # Verify the member belongs to the project
        if member_data.get("project_id") != project_id:
            return None
        
        # Prepare update data
        update_data = {
            "updated_at": firestore.SERVER_TIMESTAMP
        }
        
        if role is not None:
            update_data["role"] = role
        if seniority is not None:
            update_data["seniority"] = seniority
        
        # Update in Firestore
        member_ref.update(update_data)
        
        # Get updated data
        updated_doc = member_ref.get()
        updated_data = updated_doc.to_dict()
        updated_data.pop("created_at", None)
        updated_data.pop("updated_at", None)

        # Sync membership role
        membership_role = derive_membership_role(
            updated_data.get("role"),
            updated_data.get("seniority"),
        )
        membership_user_id = updated_data.get("user_id")
        membership_email = updated_data.get("email")
        if membership_user_id and membership_email and membership_user_id.lower() == membership_email.lower():
            membership_user_id = None

        upsert_project_membership(
            project_id=project_id,
            user_id=membership_user_id,
            email=membership_email,
            role=membership_role,
            project_role=updated_data.get("role"),
            member_id=updated_data.get("id") or member_id,
        )
        
        logger.info(f"Updated team member {member_id}")
        return updated_data
        
    except Exception as e:
        logger.error(f"Error updating team member: {str(e)}")
        raise


def remove_team_member(project_id: str, member_id: str) -> bool:
    """
    Remove a team member from a project.
    
    Args:
        project_id: Project ID
        member_id: Member ID
        
    Returns:
        True if successful, False if member not found
    """
    try:
        db = FIRESTORE_CLIENT
        member_ref = db.collection("team_members").document(member_id)
        member_doc = member_ref.get()
        
        if not member_doc.exists:
            return False
        
        member_data = member_doc.to_dict()
        
        # Verify the member belongs to the project
        if member_data.get("project_id") != project_id:
            return False
        
        # Delete the member
        member_ref.delete()

        # Remove membership record
        delete_project_membership(
            project_id=project_id,
            user_id=member_data.get("user_id"),
            email=member_data.get("email"),
        )
        
        logger.info(f"Removed team member {member_id} from project {project_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error removing team member: {str(e)}")
        raise


def check_member_exists(project_id: str, email: str) -> bool:
    """
    Check if a member with the given email already exists in the project.
    
    Args:
        project_id: Project ID
        email: Email address to check
        
    Returns:
        True if member exists, False otherwise
    """
    try:
        db = FIRESTORE_CLIENT
        query = db.collection("team_members").where("project_id", "==", project_id).where("email", "==", email)
        
        members = list(query.stream())
        return len(members) > 0
        
    except Exception as e:
        logger.error(f"Error checking member existence: {str(e)}")
        raise


def get_member_by_email(project_id: str, email: str) -> Optional[Dict[str, Any]]:
    """
    Get a team member by email address.
    
    Args:
        project_id: Project ID
        email: Email address
        
    Returns:
        Member dictionary or None if not found
    """
    try:
        db = FIRESTORE_CLIENT
        query = db.collection("team_members").where("project_id", "==", project_id).where("email", "==", email)
        
        members = list(query.stream())
        if not members:
            return None
        
        member_data = members[0].to_dict()
        member_data.pop("created_at", None)
        member_data.pop("updated_at", None)
        
        return member_data
        
    except Exception as e:
        logger.error(f"Error getting member by email: {str(e)}")
        raise


def format_team_member_response(member_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize a team member record for API responses.
    """
    return {
        "id": member_data.get("id"),
        "name": member_data.get("name"),
        "email": member_data.get("email"),
        "role": member_data.get("role"),
        "seniority": member_data.get("seniority"),
        "status": member_data.get("status"),
        "joinedDate": member_data.get("joined_date") or member_data.get("joinedDate"),
        "avatar": member_data.get("avatar")
    }


def format_team_members_response(members: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Normalize a list of team member records for API responses.
    """
    return [format_team_member_response(member) for member in members]
