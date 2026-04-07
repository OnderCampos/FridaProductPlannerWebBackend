DOCUMENT_ENTITY_CONSOLIDATION_TASK = """
You are consolidating chunk-level extraction results into a coherent document model.

The inputs are evidence-derived candidates from one or more chunks or partial consolidations.
Your job is to reconcile duplicates, normalize identities, and keep only grounded entities.

Project name:
{project_name}

Project description:
{project_description}

Extraction payloads:
{extractions}

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
      "keywords": [string]
    }
  ],
  "user_stories": [
    {
      "story_key": string,
      "user_story": string,
      "description": string,
      "acceptanceCriteria": [string],
      "outOfScope": [string],
      "dependencies": [string],
      "effortHours": number,
      "roles": [string],
      "epic_hint": string,
      "technologies": [string],
      "keywords": [string],
      "task_hints": [string]
    }
  ]
}

Rules:
- Output JSON only, no markdown.
- Reconcile duplicates expressed with slightly different wording.
- Preserve only entities and stories that remain grounded after consolidation.
- `story_key` must be a stable snake_case identifier.
- `dependencies` must reference other `story_key` values only when the dependency is well supported.
- Keep `epic_hint` only when there is meaningful evidence that the story belongs to that initiative.
- Do not force a final epic assignment for every story in this step.
- Normalize roles and technologies across the document.
- Prefer fewer, better-consolidated stories over many near-duplicates.
- Keep all human-readable values in {language}.
"""
