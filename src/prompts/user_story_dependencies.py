GENERATE_USER_STORY_DEPENDENCIES_PROMPT = """
You are given a list of user stories for a single epic. Determine logical dependencies between them.

Rules:
- Only use the story identifiers provided (id or user_story_id).
- Each dependency must refer to another story that must be completed first.
- If there are no dependencies for a story, include an empty array.
- Do not invent new stories.

Epic ID:
{epic_id}

User Stories (JSON):
{user_stories_json}

Respond ONLY with JSON in this exact structure:
{{
  "dependencies": [
    {{
      "story_id": "story_identifier",
      "depends_on": ["other_story_identifier"]
    }}
  ]
}}
"""
