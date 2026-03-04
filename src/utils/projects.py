from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
import logging

from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.schemas.response import ResponseModel
from src.utils.epic_generation import generate_epics
from src.utils.epics import get_epics_for_project, delete_epics_for_project
from src.utils.members import get_project_members as get_team_members, format_team_members_response
from src.utils.project_memberships import (
    delete_memberships_for_project,
    get_memberships_for_user,
    upsert_project_membership,
)
from src.utils.users import upsert_user_profile
from src.utils.user_stories import get_user_stories_by_epic
from src.utils.permissions import get_project_access
from src.schemas.user_data import UserData


def _current_timestamp_iso() -> str:
    """Generate current timestamp in ISO format"""
    return datetime.now(timezone.utc).isoformat()


def get_project_for_user(project_id: str, user_id: str) -> ResponseModel:
    """
    Retrieves a project and verifies ownership without loading related data.
    """
    try:
        project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()

        if not project_doc.exists:
            return ResponseModel(
                success=False,
                message="Project not found",
                data=None
            )

        project_data = project_doc.to_dict()

        if project_data.get("user_id") != user_id:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this project",
                data=None
            )

        project_data["id"] = project_doc.id

        return ResponseModel(
            success=True,
            message="Project retrieved successfully",
            data=project_data
        )
    except Exception as e:
        logging.error(f"Error retrieving project {project_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving project: {str(e)}",
            data=None
        )


def get_all_projects_for_user(user_id: str, include_member_projects: bool = False, user_email: Optional[str] = None) -> ResponseModel:
    """
    Retrieves all projects for a specific user.
    
    Args:
        user_id (str): The user ID
        
    Returns:
        ResponseModel: Response containing the user's projects
    """
    try:
        print(f"Fetching projects for user with {user_id}")
        project_ids = set()
        all_projects = []

        # Owned projects
        projects_ref = FIRESTORE_CLIENT.collection("projects").where("user_id", "==", user_id)
        projects_docs = projects_ref.get()
        for doc in projects_docs:
            project_data = doc.to_dict()
            project_data["id"] = doc.id  # Add document ID as project ID
            project_ids.add(doc.id)

            team_members = get_team_members(doc.id)
            project_data["teamMembers"] = team_members

            all_projects.append(project_data)

        # Member projects
        if include_member_projects:
            membership_project_ids = set()
            memberships = get_memberships_for_user(user_id, user_email)
            for membership in memberships:
                project_id = membership.get("project_id")
                if not project_id or project_id in project_ids:
                    continue
                membership_project_ids.add(project_id)
                project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
                if not project_doc.exists:
                    continue
                project_data = project_doc.to_dict()
                project_data["id"] = project_doc.id
                project_ids.add(project_id)

                team_members = get_team_members(project_id)
                project_data["teamMembers"] = team_members

                all_projects.append(project_data)

            # Backward compatibility: include projects from team_members not yet in memberships.
            member_docs = []
            members_ref = FIRESTORE_CLIENT.collection("team_members")
            try:
                member_docs = members_ref.where("user_id", "==", user_id).get()
            except Exception:
                member_docs = []

            if user_email:
                try:
                    member_docs += members_ref.where("email", "==", user_email).get()
                except Exception:
                    pass

            for doc in member_docs or []:
                member_data = doc.to_dict()
                project_id = member_data.get("project_id")
                if not project_id or project_id in project_ids:
                    continue
                project_doc = FIRESTORE_CLIENT.collection("projects").document(project_id).get()
                if not project_doc.exists:
                    continue
                project_data = project_doc.to_dict()
                project_data["id"] = project_doc.id
                project_ids.add(project_id)

                team_members = get_team_members(project_id)
                project_data["teamMembers"] = team_members

                all_projects.append(project_data)

        #print(f"PROJECTS: {all_projects}")

        return ResponseModel(
            success=True, 
            message="Projects retrieved successfully.", 
            data=all_projects
        )
    except Exception as e:
        logging.error(f"Error retrieving projects for user {user_id}: {e}")
        return ResponseModel(
            success=False, 
            message="Error getting projects.", 
            data={}
        )


def get_project_by_id(
    project_id: str,
    user_id: str,
    allow_member: bool = False,
    user_email: Optional[str] = None,
) -> ResponseModel:
    """
    Retrieves a specific project by ID, ensuring the user owns it.
    
    Args:
        project_id (str): The project ID
        user_id (str): The user ID
        
    Returns:
        ResponseModel: Response containing the project data
    """
    try:
        if allow_member:
            project_response = get_project_access(project_id, user_id, user_email)
        else:
            project_response = get_project_for_user(project_id, user_id)
        if not project_response.success:
            return project_response

        project_data = project_response.data
        if allow_member and isinstance(project_data, dict) and "project" in project_data:
            project_data = project_data.get("project")

        # Get epics for the project using utility function
        project_epics = get_epics_for_project(project_id, user_id)
        for epic in project_epics:
            stories_response = get_user_stories_by_epic(epic["id"], user_id, allow_member=allow_member)
            stories = stories_response.data or []
            for story in stories:
                if "assigneeId" not in story and "assigned_to" in story:
                    story["assigneeId"] = story.get("assigned_to")
            epic["userStories"] = stories
        project_data["epics"] = project_epics

        # Include team members
        members = get_team_members(project_id)
        project_data["teamMembers"] = format_team_members_response(members)
        
        return ResponseModel(
            success=True,
            message="Project and epics retrieved successfully",
            data=project_data
        )
    except Exception as e:
        logging.error(f"Error retrieving project {project_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving project: {str(e)}",
            data=None
        )


async def create_project(user_data: UserData, name: str, description: str, project_key: str) -> ResponseModel:
    """
    Creates a new project using a user-provided project_key.
    
    Args:
        name (str): Project name
        description (str): Project description
        project_key (str): User-provided project key
        user_id (str): Owner user ID
        
    Returns:
        ResponseModel: Response containing the created project
    """
    print(f"[DEBUG] Creating project for user: {user_data.get_user_id()}")
    try:
        if not project_key:
            return ResponseModel(
                success=False, 
                message="project_key is required.", 
                data={}
            )

        # Check if project_key already exists for this user
        existing_projects = FIRESTORE_CLIENT.collection("projects").where(
            "user_id", "==", user_data.get_user_id()
        ).where(
            "project_key", "==", project_key
        ).get()
        
        if existing_projects:
            return ResponseModel(
                success=False, 
                message="Project with this key already exists for this user.", 
                data={}
            )

        # Generate epics using AI
        epics_result = await generate_epics(user_data=user_data, project_name=name, project_description=description)
        
        now = _current_timestamp_iso()
        owner_email = user_data.get_email()
        project_data = {
            "user_id": user_data.get_user_id(),
            "name": name,
            "description": description,
            "project_key": project_key,
            "projectLead": owner_email,
            "created_at": now,
            "updated_at": now,
        }
        
        # Add additional data from epic generation if successful
        if epics_result.is_success():
            epic_data = epics_result.get_data()
            if epic_data:
                # Add project description, technical stack, and roles to project
                project_data["ai_project_description"] = epic_data.get("project_description", "")
                project_data["technical_stack"] = epic_data.get("technical_stack", [])
                project_data["roles"] = epic_data.get("roles", [])
        
        # Add document to collection (auto-generates document ID)
        doc_ref = FIRESTORE_CLIENT.collection("projects").add(project_data)
        
        # Get the generated document ID
        project_id = doc_ref[1].id
        project_data["id"] = project_id

        # Ensure owner profile + membership records exist
        upsert_user_profile(
            user_id=user_data.get_user_id(),
            email=owner_email,
            name=owner_email,
            role="leader",
        )
        upsert_project_membership(
            project_id=project_id,
            user_id=user_data.get_user_id(),
            email=owner_email,
            role="leader",
            project_role="owner",
        )
        
        # Save epics in separate collection if epic generation was successful
        created_epics = []
        if epics_result.is_success():
            epic_data = epics_result.get_data()
            if epic_data and "epics" in epic_data:
                epics_list = epic_data["epics"]
                for epic in epics_list:
                    epic_doc_data = {
                        "project_id": project_id,
                        "user_id": user_data.get_user_id(),
                        "name": epic.get("name", ""),
                        "description": epic.get("description", ""),
                        "labels": epic.get("labels", []),
                        "created_at": now,
                        "updated_at": now,
                    }
                    
                    # Add epic to epics collection
                    epic_doc_ref = FIRESTORE_CLIENT.collection("epics").add(epic_doc_data)
                    epic_id = epic_doc_ref[1].id
                    epic_doc_data["id"] = epic_id
                    created_epics.append(epic_doc_data)
        
        # Include created epics in response for reference
        response_data = project_data.copy()
        response_data["epics"] = created_epics
        
        return ResponseModel(
            success=True, 
            message="Project and epics created successfully.", 
            data=response_data
        )
    except Exception as e:
        logging.error(f"Error creating project: {e}")
        return ResponseModel(
            success=False, 
            message=f"Error creating project: {e}", 
            data={}
        )


def update_project(project_id: str, user_id: str, name: Optional[str] = None, 
    description: Optional[str] = None, project_key: Optional[str] = None, tech_stack: List[str] = None) -> ResponseModel:
    """
    Updates an existing project.
    
    Args:
        project_id (str): The project ID
        user_id (str): The user ID
        name (str, optional): New project name
        description (str, optional): New project description
        project_key (str, optional): New project key
        
    Returns:
        ResponseModel: Response containing the updated project
    """
    try:
        project_ref = FIRESTORE_CLIENT.collection("projects").document(project_id)
        project_doc = project_ref.get()
        
        if not project_doc.exists:
            return ResponseModel(
                success=False,
                message="Project not found",
                data=None
            )
        
        project_data = project_doc.to_dict()
        
        # Check if user owns the project
        if project_data.get("user_id") != user_id:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this project",
                data=None
            )
        
        # Check if new project_key conflicts with existing projects (if provided)
        if project_key and project_key != project_data.get("project_key"):
            existing_projects = FIRESTORE_CLIENT.collection("projects").where(
                "user_id", "==", user_id
            ).where(
                "project_key", "==", project_key
            ).get()
            
            if existing_projects:
                return ResponseModel(
                    success=False,
                    message="Project with this key already exists for this user",
                    data=None
                )
        
        # Build update data
        update_data = {"updated_at": _current_timestamp_iso()}
        if name is not None:
            update_data["name"] = name
        if description is not None:
            update_data["description"] = description
        if project_key is not None:
            update_data["project_key"] = project_key
        if tech_stack is not None:
            update_data["technical_stack"] = tech_stack
        
        if len(update_data) == 1:  # Only updated_at
            return ResponseModel(
                success=False,
                message="No fields to update",
                data=None
            )
        
        # Update the document
        project_ref.update(update_data)
        
        # Get updated project
        updated_doc = project_ref.get()
        updated_data = updated_doc.to_dict()
        updated_data["id"] = project_id
        
        return ResponseModel(
            success=True,
            message="Project updated successfully",
            data=updated_data
        )
    except Exception as e:
        logging.error(f"Error updating project {project_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error updating project: {str(e)}",
            data=None
        )


def delete_project(project_id: str, user_id: str) -> ResponseModel:
    """
    Deletes a project by project_id. Only owner (user_id) can delete.
    
    Args:
        project_id (str): The project ID
        user_id (str): The user ID
        
    Returns:
        ResponseModel: Response confirming deletion
    """
    try:
        project_ref = FIRESTORE_CLIENT.collection("projects").document(project_id)
        project_doc = project_ref.get()
        
        if not project_doc.exists:
            return ResponseModel(
                success=False,
                message="Project not found",
                data=None
            )
        
        project_data = project_doc.to_dict()
        
        # Check if user owns the project
        if project_data.get("user_id") != user_id:
            return ResponseModel(
                success=False,
                message="Unauthorized: You don't own this project",
                data=None
            )
        
        # Delete associated epics first
        delete_epics_result = delete_epics_for_project(project_id, user_id)
        if not delete_epics_result.success:
            logging.warning(f"Failed to delete epics for project {project_id}: {delete_epics_result.message}")
        
        # Delete the project
        project_ref.delete()

        # Cleanup memberships for this project
        delete_memberships_for_project(project_id)
        
        return ResponseModel(
            success=True,
            message="Project and associated epics deleted successfully",
            data=delete_epics_result.data if delete_epics_result.success else None
        )
    except Exception as e:
        logging.error(f"Error deleting project {project_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error deleting project: {str(e)}",
            data=None
        )

def get_project_members_by_id(project_id: str) -> List[Dict[str, Any]]:
    """
    Retrieves all members associated with a specific project.
    
    Args:
        project_id (str): The project ID
        
    Returns:
        List[Dict[str, Any]]: List of members associated with the project
    """
    try:
        members_ref = FIRESTORE_CLIENT.collection("member_project").where(
            "project_id", "==", project_id
        )
        members_docs = members_ref.get()
        
        members_list = []
        for doc in members_docs:
            member_data = doc.to_dict()
            member_data["id"] = doc.id  # Add document ID as member ID
            members_list.append(member_data)
        
        return members_list
    except Exception as e:
        logging.error(f"Error retrieving members for project {project_id}: {e}")
        return []

