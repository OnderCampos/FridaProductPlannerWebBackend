DOCUMENT_CHUNK_EXTRACTION_TASK = """
You are a senior product analyst extracting grounded user stories from one document chunk.

Treat the chunk as evidence, not as ready-made structured data.
Infer user stories from the chunk when the user need, goal, and value are supported by the text.
Do not expect the source text to already be written as a user story.
If something is uncertain, omit it.

Project name:
{project_name}

Chunk id:
{chunk_id}

Chunk text:
{text}

Language:
{language}

Return ONLY valid JSON in this exact structure:
{
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
      "technologies": [string],
      "keywords": [string],
      "evidence": string
    }
  ]
}

Rules:
- Output JSON only, no markdown.
- This is chunk-level reasoning only. Do not try to reconcile duplicates across the whole document.
- A user story describes what a user needs and why they need it, not how to build it.
- Do not include implementation guidance, coding instructions, architecture decisions, API design, database design, class names, frameworks, libraries, endpoints, queries, or technical solution proposals inside `user_story`, `description`, `acceptanceCriteria`, or `outOfScope`.
- Rewrite implementation-heavy source text into functional behavior. Keep only what the product must do for the user or business.
- Be creatively inferential but still grounded: when the chunk implies a meaningful user need, produce the user story even if the text states it indirectly.
- The document does not need to state a user story explicitly in that exact format for it to be extractable.
- You must detect and infer valid user stories from any relevant text in the chunk when the user need, action, and value are clearly supported.
- Every meaningful feature, workflow, user action, or business capability described in the chunk should be represented by at least one user story when the chunk provides enough evidence.
- Do not stop after extracting only the most obvious stories if the chunk clearly describes additional meaningful capabilities.
- Do not expect the source text to literally say "As a ..., I want ..., so that ..."; derive that structure from requirements, workflows, feature descriptions, and process text.
- Rewrite raw requirements, process descriptions, feature descriptions, or workflow explanations into proper user stories when the evidence supports it.
- Expand one feature description into multiple user stories when it clearly implies multiple user goals, steps, permissions, states, or business outcomes.
- Prefer extracting slightly more valid user stories over collapsing several distinct user needs into one generic story.
- Split stories by distinct actor, intent, workflow step, or value outcome when the chunk supports that separation.
- Capture implied user value explicitly in the `so that` part, even when the source text only hints at the benefit.
- Think in this form: "As a [type of user], I want [goal or action], so that [benefit or value]."
- Keep each user story simple, clear, and focused on user value.
- `user_stories` must use the format: "As a [role], I need [capability], so that [value]."
- `description` must describe scope, behavior, and business rules only. Do not mention how developers should implement it.
- Every user story must contain an explicit user role/persona in both the `user_story` sentence and the `role` field.
- The `role` field is required and must be a concrete actor such as "End User", "Administrator", "Manager", "Operator", "Support Agent", "Student", or "Instructor" when appropriate.
- Do not leave the role empty, and do not use vague placeholders like "User" unless the source genuinely supports only that generic persona.
- Create multiple user stories when the chunk clearly describes separable capabilities.
- `epic_hint` may reference an epic area or workflow name from this chunk when helpful, otherwise use an empty string.
- `task_hints` should capture smaller tasks or steps that support the story.
- `technologies` should contain only technologies or platforms explicitly mentioned for that story in this chunk.
- `dependencies` should only be included when one story in this chunk clearly depends on another story in this chunk.
- `evidence` fields should be short evidence snippets or paraphrases grounded in this chunk.
- Do not invent product areas, personas, or value claims that are not supported by the chunk.
- Prefer omission over hallucination.
- Keep all human-readable values in {language}.
"""
