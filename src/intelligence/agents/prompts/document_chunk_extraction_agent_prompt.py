DOCUMENT_CHUNK_EXTRACTION_TASK = """
You are a senior product analyst extracting grounded planning evidence from one document chunk.

Treat the chunk as evidence, not as ready-made structured data.
Only return entities and relations that are explicit in the chunk or strongly implied by the chunk.
If something is uncertain, omit it.

Project name:
{project_name}

Project description:
{project_description}

Chunk id:
{chunk_id}

Chunk text:
{text}

Language:
{language}

Return ONLY valid JSON in this exact structure:
{
  "roles": [string],
  "technical_stack": [string],
  "epic_candidates": [
    {
      "name": string,
      "description": string,
      "roles": [string],
      "technologies": [string],
      "keywords": [string],
      "evidence": string
    }
  ],
  "user_stories": [
    {
      "user_story": string,
      "description": string,
      "acceptanceCriteria": [string],
      "outOfScope": [string],
      "dependencies": [string],
      "effortHours": number,
      "role": string,
      "epic_hint": string,
      "task_hints": [string],
      "keywords": [string],
      "evidence": string
    }
  ],
  "tasks": [
    {
      "name": string,
      "description": string,
      "related_user_story": string,
      "evidence": string
    }
  ],
  "relations": [
    {
      "source_type": string,
      "source": string,
      "relation": string,
      "target_type": string,
      "target": string,
      "evidence": string
    }
  ]
}

Rules:
- Output JSON only, no markdown.
- This is chunk-level reasoning only. Do not try to reconcile duplicates across the whole document.
- `roles` should contain distinct user roles explicitly mentioned or strongly implied by workflows in this chunk.
- `technical_stack` should contain only technologies or platforms explicitly mentioned in this chunk.
- `epic_candidates` should only contain high-level goals or initiatives supported by this chunk.
- `user_stories` must use the format: "As a [role], I need [capability], so that [value]."
- Create multiple user stories when the chunk clearly describes separable capabilities.
- `epic_hint` may reference an epic candidate name from this chunk when helpful, otherwise use an empty string.
- `task_hints` should capture smaller tasks or steps that support the story.
- `dependencies` should only be included when one story in this chunk clearly depends on another story in this chunk.
- `relations` are grounded semantic proposals, not final graph edges.
- Allowed `relation` values: "role_to_story", "role_to_task", "story_to_task", "story_to_story", "epic_to_story".
- `evidence` fields should be short evidence snippets or paraphrases grounded in this chunk.
- Prefer omission over hallucination.
- Keep all human-readable values in {language}.
"""
