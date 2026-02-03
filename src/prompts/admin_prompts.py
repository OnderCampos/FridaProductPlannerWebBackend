from .security_instructions import append_security_instruction

# Original prompt definitions
_TRANSFORM_USER_DATA_PROMPT = """
Transform the following user data by renaming 'uid' to 'user_id' and removing sensitive fields like 'passwordHash' and 'salt'. 
Return the result as a JSON object.

User Data: {user_data}
"""

# Applying security instructions to all prompts
TRANSFORM_USER_DATA_PROMPT = append_security_instruction(_TRANSFORM_USER_DATA_PROMPT)

TRANSFORM_TEXT_TO_USER_JSON_PROMPT = """
You are given a team_id and a text that represents user information.
Transform it into a JSON array of users with the following structure:

{{
  "users": [
    {{
      "email": "user.email@example.com",
      "password": "user.email",
      "team_id": "{team_id}",
      "name": "User Full Name",
      "role": "User Role",
      "seniority": "User Seniority Level"
    }}
  ]
}}
The password must be the email before the @ symbol plus  (e.g., for email "user.email@example.com", the password would be "user.email").
Extract as much information as you can from the text. If any field is missing, make a reasonable guess.
All users must have the same team_id provided: {team_id}

Here's the text to transform:
{text_data}

Provide ONLY the JSON response without any additional text or explanation.
"""

# Epic generation prompts - to be filled with specific content later
MERGE_PROMPT = """
# TODO: Add merge prompt for combining text chunks
# Format: {current}, {next}, {language}
"""

GET_INFORMATION_FROM_TEXT_PROMPT = """
# TODO: Add prompt for extracting information from text
# Format: {text}, {language}
"""

EPICS_INFORMATION_QUESTIONS_PROMPT = """
# TODO: Add prompt for generating questions about epics
# Format: {text}
"""

GENERATE_EPICS_PROMPT = """
# TODO: Add prompt for generating epics from project information
# Format: {project_name}, {project_description}, {language}
"""
