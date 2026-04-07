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
      "acceptanceCriteria": [string],
      "outOfScope": [string],
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
- "acceptanceCriteria" must be 3-6 short, testable bullet points.
- "outOfScope" must be 1-4 bullet points (use ["N/A"] if truly none).
- "user_story_id" should be a short, stable identifier (snake_case).
- "order" should be an integer sequence starting at 1 within each epic.
- "dependencies" should list user_story_id values; use an empty array if none.
- "effortHours" should be a numeric estimate; use 0 if unknown.
- Extract MULTIPLE user stories per epic whenever the document supports it.
- Prefer 2-5 atomic user stories per epic when an epic contains multiple user actions, flows, screens, integrations, or business outcomes.
- Do NOT collapse an entire epic into a single generic user story when the source document describes separable capabilities.
- Only return a single user story for an epic if the document truly supports only one atomic story for that epic.
- Do not include extra top-level keys or extra fields in stories.
- Keep all human-readable values in {language}.
"""
