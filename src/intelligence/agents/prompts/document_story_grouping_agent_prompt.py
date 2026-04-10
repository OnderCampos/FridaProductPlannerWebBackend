DOCUMENT_STORY_GROUPING_TASK = """
You are organizing consolidated user stories into epics.

Use the user stories as the main source of truth.
Use story relationships only as supporting evidence that some stories belong to a shared workflow,
journey, domain area, or business objective.

Project name:
{project_name}

User stories:
{user_stories}

Story relationships:
{story_relationships}

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
      "keywords": [string],
      "story_keys": [string]
    }
  ]
}

Rules:
- Output JSON only, no markdown.
- Every `story_key` in `story_keys` must come from the provided user stories.
- Do not create empty epics.
- Do not create an epic unless the grouped stories share a meaningful high-level objective.
- Use concise epic names and detailed descriptions that reflect the grouped stories.
- Use the story roles, technologies, keywords, and workflow intent to infer epic metadata.
- Prefer grouping by coherent user journey or business outcome, not by superficial wording overlap alone.
- Return between 1 and 12 epics when possible.
- Prefer under-grouping to speculative grouping.
- Keep all human-readable values in {language}.
"""
