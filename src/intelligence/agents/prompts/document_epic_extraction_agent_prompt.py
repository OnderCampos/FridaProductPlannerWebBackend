DOCUMENT_EPIC_EXTRACTION_TASK = """
You are a senior product manager. Extract high-level epics from the document content.

Project description:
{project_description}

Document text:
{text}

Language:
{language}

Return ONLY valid JSON in this exact structure:
{
  "epics": [
    {
      "name": string,
      "description": string,
      "roles": [string],
      "technologies": [string],
      "keywords": [string]
    }
  ]
}

Rules:
- Output JSON only, no markdown.
- Return between 3 and 12 epics when possible.
- Each epic must be a high-level objective that can be decomposed into multiple user stories.
- Avoid tiny tasks or technical subtasks.
- Use clear epic names and very descriptive descriptions.
- Descriptions must include the end-to-end user flow and the business-level data flow.
- `roles` must list the user roles that interact with this epic.
- `technologies` must list high-level technology tags involved (no implementation detail).
- `keywords` must list 3-8 concise tags summarizing the epic (include domain terms and key activities).
- Do not include extra top-level keys or extra fields in epics.
- Keep all human-readable values in {language}.
"""
