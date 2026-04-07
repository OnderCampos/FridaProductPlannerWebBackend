DOCUMENT_STORY_GROUPING_TASK = """
You are organizing consolidated user stories into epics.

This is not duplicate removal. This is higher-level grouping.
Group stories that share a business goal, user journey, workflow, or domain objective strongly enough to form an epic.
Only create epics that are meaningfully supported by the stories and the document evidence.

Project name:
{project_name}

Project description:
{project_description}

Roles:
{roles}

Technical stack:
{technical_stack}

Epic candidates:
{epic_candidates}

User stories:
{user_stories}

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
- Prefer explicit epic candidates when they match the story clusters, but you may create emergent epics from related stories.
- Every `story_key` in `story_keys` must come from the provided user stories.
- Do not create empty epics.
- Do not create an epic unless the grouped stories share a meaningful high-level objective.
- Use concise epic names and detailed descriptions that reflect the grouped stories.
- Return between 1 and 12 epics when possible.
- Prefer under-grouping to speculative grouping.
- Keep all human-readable values in {language}.
"""
