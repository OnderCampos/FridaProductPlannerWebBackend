DOCUMENT_USER_STORY_EXTRACTION_TASK = """
You are a product owner. Extract user stories from the document content using the provided epics.

Epics:
{epics}

Document text:
{text}

Language:
{language}

Return ONLY valid JSON in this exact structure:
{
  "user_stories": [
    {
      "epic": string,
      "user_story": string,
      "description": string,
      "user_story_id": string,
      "order": number,
      "dependencies": [string],
      "effortHours": number
    }
  ]
}

Rules:
- Output JSON only, no markdown.
- "epic" must match one of the provided epic names exactly.
- "user_story" must follow the format: "As a [role], I need [capability], so that [value]."
- "description" should add concrete details about the story.
- "user_story_id" should be a short, stable identifier (snake_case).
- "order" should be an integer sequence starting at 1 within each epic.
- "dependencies" should list user_story_id values; use an empty array if none.
- "effortHours" should be a numeric estimate; use 0 if unknown.
- Do not include extra top-level keys or extra fields in stories.
- Keep all human-readable values in {language}.
"""
