from typing import List, Dict, Any, Optional, Tuple
from src.services.setup.firebase_setup import FIRESTORE_CLIENT
from src.schemas.response import ResponseModel
import logging

def _get_project_for_user(project_id: str, user_id: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    Validate project access for a user and return project data.
    """
    project_ref = FIRESTORE_CLIENT.collection("projects").document(project_id)
    project_doc = project_ref.get()

    if not project_doc.exists:
        return None, "Project not found"

    project_data = project_doc.to_dict()
    if project_data.get("user_id") == user_id:
        return project_data, None

    member_query = (
        FIRESTORE_CLIENT.collection("team_members")
        .where("project_id", "==", project_id)
        .where("user_id", "==", user_id)
        .get()
    )
    if member_query:
        return project_data, None

    return None, "Unauthorized: You don't have access to this project"


def get_all_templates_by_project(project_id: str, user_id: str) -> ResponseModel:
    """
    Retrieves all templates for a specific project.

    Args:
        project_id (str): The project ID
        user_id (str): The user ID

    Returns:
        ResponseModel: Response containing all templates for the project
    """
    try:
        _, access_error = _get_project_for_user(project_id, user_id)
        if access_error:
            return ResponseModel(
                success=False,
                message=access_error,
                data=None
            )

        templates_ref = FIRESTORE_CLIENT.collection("templates").where("project_id", "==", project_id)
        templates_docs = templates_ref.get()

        templates = []
        for doc in templates_docs:
            template_data = doc.to_dict()
            template_data["id"] = doc.id
            templates.append(template_data)

        return ResponseModel(
            success=True,
            message=f"Retrieved {len(templates)} template(s) successfully",
            data=templates
        )
    except Exception as e:
        logging.error(f"Error retrieving templates for project {project_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving templates: {str(e)}",
            data=None
        )

def get_selected_template_by_project(project_id: str, user_id: str) -> ResponseModel:
    """
    Retrieves the selected template for a specific project.

    Args:
        project_id (str): The project ID
        user_id (str): The user ID

    Returns:
        ResponseModel: Response containing the selected template or an error message
    """
    try:
        project_data, access_error = _get_project_for_user(project_id, user_id)
        if access_error:
            return ResponseModel(
                success=False,
                message=access_error,
                data=None
            )

        selected_template_id = project_data.get("selected_template_id")
        if not selected_template_id:
            return ResponseModel(
                success=False,
                message="No selected template found for the project",
                data=None
            )

        template_ref = FIRESTORE_CLIENT.collection("templates").document(selected_template_id)
        template_doc = template_ref.get()
        if not template_doc.exists:
            return ResponseModel(
                success=False,
                message="Selected template not found",
                data=None
            )

        selected_template = template_doc.to_dict()
        if selected_template.get("project_id") != project_id:
            return ResponseModel(
                success=False,
                message="Selected template does not belong to this project",
                data=None
            )
        selected_template["id"] = template_doc.id

        return ResponseModel(
            success=True,
            message="Selected template retrieved successfully",
            data=selected_template
        )
    except Exception as e:
        logging.error(f"Error retrieving selected template for project {project_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error retrieving selected template: {str(e)}",
            data=None
        )

def create_template(project_id: str, user_id: str, name: str, language: str, fields: List[Dict[str, str]]) -> ResponseModel:
    """
    Creates a new template for a project.

    Args:
        project_id (str): The project ID
        user_id (str): The user ID
        language (str): The language of the template
        fields (List[Dict[str, str]]): List of fields with name and description

    Returns:
        ResponseModel: Response containing the created template
    """
    try:
        _, access_error = _get_project_for_user(project_id, user_id)
        if access_error:
            return ResponseModel(
                success=False,
                message=access_error,
                data=None
            )

        template_data = {
            "project_id": project_id,
            "name": name,
            "language": language,
            "fields": fields
        }

        template_ref = FIRESTORE_CLIENT.collection("templates").add(template_data)
        template_id = template_ref[1].id
        template_data["id"] = template_id

        return ResponseModel(
            success=True,
            message="Template created successfully",
            data=template_data
        )
    except Exception as e:
        logging.error(f"Error creating template: {e}")
        return ResponseModel(
            success=False,
            message=f"Error creating template: {str(e)}",
            data=None
        )

def update_template(
    template_id: str,
    project_id: str,
    user_id: str,
    name: Optional[str] = None,
    language: Optional[str] = None,
    fields: Optional[List[Dict[str, str]]] = None
) -> ResponseModel:
    """
    Updates an existing template.

    Args:
        template_id (str): The template ID
        project_id (str): The project ID
        user_id (str): The user ID
        name (str, optional): The new name of the template
        language (str, optional): The new language of the template
        fields (List[Dict[str, str]], optional): The new list of fields

    Returns:
        ResponseModel: Response containing the updated template
    """
    try:
        _, access_error = _get_project_for_user(project_id, user_id)
        if access_error:
            return ResponseModel(
                success=False,
                message=access_error,
                data=None
            )

        template_ref = FIRESTORE_CLIENT.collection("templates").document(template_id)
        template_doc = template_ref.get()

        if not template_doc.exists:
            return ResponseModel(
                success=False,
                message="Template not found",
                data=None
            )

        template_data = template_doc.to_dict()

        if template_data.get("project_id") != project_id:
            return ResponseModel(
                success=False,
                message="Template not found in this project",
                data=None
            )

        update_data = {}
        if name is not None:
            update_data["name"] = name
        if language is not None:
            update_data["language"] = language
        if fields is not None:
            update_data["fields"] = fields

        if not update_data:
            return ResponseModel(
                success=False,
                message="No fields to update",
                data=None
            )

        template_ref.update(update_data)

        updated_doc = template_ref.get()
        updated_data = updated_doc.to_dict()
        updated_data["id"] = template_id

        return ResponseModel(
            success=True,
            message="Template updated successfully",
            data=updated_data
        )
    except Exception as e:
        logging.error(f"Error updating template {template_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error updating template: {str(e)}",
            data=None
        )

def delete_template(template_id: str, project_id: str, user_id: str) -> ResponseModel:
    """
    Deletes a specific template.

    Args:
        template_id (str): The template ID
        project_id (str): The project ID
        user_id (str): The user ID

    Returns:
        ResponseModel: Response confirming deletion
    """
    try:
        project_data, access_error = _get_project_for_user(project_id, user_id)
        if access_error:
            return ResponseModel(
                success=False,
                message=access_error,
                data=None
            )

        template_ref = FIRESTORE_CLIENT.collection("templates").document(template_id)
        template_doc = template_ref.get()

        if not template_doc.exists:
            return ResponseModel(
                success=False,
                message="Template not found",
                data=None
            )

        template_data = template_doc.to_dict()

        if template_data.get("project_id") != project_id:
            return ResponseModel(
                success=False,
                message="Template not found in this project",
                data=None
            )

        template_ref.delete()

        selected_template_id = project_data.get("selected_template_id")
        if selected_template_id == template_id:
            FIRESTORE_CLIENT.collection("projects").document(project_id).update(
                {"selected_template_id": None}
            )

        return ResponseModel(
            success=True,
            message="Template deleted successfully",
            data=None
        )
    except Exception as e:
        logging.error(f"Error deleting template {template_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error deleting template: {str(e)}",
            data=None
        )

def set_selected_template(project_id: str, user_id: str, template_id: str) -> ResponseModel:
    """
    Sets a specific template as the selected template for a project.

    Args:
        project_id (str): The project ID
        user_id (str): The user ID
        template_id (str): The template ID

    Returns:
        ResponseModel: Response confirming the selection with template data
    """
    try:
        _, access_error = _get_project_for_user(project_id, user_id)
        if access_error:
            return ResponseModel(
                success=False,
                message=access_error,
                data=None
            )

        template_ref = FIRESTORE_CLIENT.collection("templates").document(template_id)
        template_doc = template_ref.get()
        if not template_doc.exists:
            return ResponseModel(
                success=False,
                message="Template not found",
                data=None
            )
        template_data = template_doc.to_dict()
        if template_data.get("project_id") != project_id:
            return ResponseModel(
                success=False,
                message="Template not found in this project",
                data=None
            )

        FIRESTORE_CLIENT.collection("projects").document(project_id).update(
            {"selected_template_id": template_id}
        )

        # Get the updated template data
        template_data["id"] = template_id

        return ResponseModel(
            success=True,
            message="Template selected successfully",
            data=template_data
        )
    except Exception as e:
        logging.error(f"Error setting selected template {template_id} for project {project_id}: {e}")
        return ResponseModel(
            success=False,
            message=f"Error setting selected template: {str(e)}",
            data=None
        )
    

def generate_template_formating(template: dict) -> tuple:
    """
    Extracts and processes template fields to generate keys, JSON template, and description.

    Args:
        template (dict): Template containing fields and their descriptions

    Returns:
        tuple: (template_field_keys, template_fields_json, fields_description)
    """
    # Prepare the fields from the template
    fields = [
        {
            "name": field["name"],
            "description": field["description"],
            "key": field["name"].lower().replace(" ", "_"),
        }
        for field in template.get("fields", [])
    ]

    # Generate template field keys
    template_field_keys = [field["key"] for field in fields]

    # Generate JSON template structure
    template_fields_json = "".join(
        [f'            "{key}": "",\n' for key in template_field_keys]
    )

    # Generate fields description
    fields_description = "\n".join(
        [
            f"- {field['name']} ({field['key']}): {field['description']}"
            for field in fields
        ]
    )

    return template_field_keys, template_fields_json, fields_description
