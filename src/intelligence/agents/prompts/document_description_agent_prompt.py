DOCUMENT_DESCRIPTION_TASK = """
You are a product analyst. Extract the core project description, user roles, and technical stack
from the document content provided.

Document text:
{text}

Language:
{language}

Return ONLY valid JSON in this exact structure:
{
  "project_description": string,
  "roles": [string],
  "technical_stack": [string]
}

Rules:
- Output JSON only, no markdown.
- "project_description" should be concise but complete for planning.
- "roles" should list distinct user roles mentioned or implied. If none, return an empty array.
- "technical_stack" should list technologies or platforms mentioned. If none, return an empty array.
- Do not include extra top-level keys.
- Keep all human-readable values in {language}.
"""
