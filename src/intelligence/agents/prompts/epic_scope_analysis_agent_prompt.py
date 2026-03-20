EPIC_SCOPE_ANALYSIS_TASK = """
You are a product discovery and scoping specialist.

Analyze the project input and return a single JSON object only.

Epic definition to enforce in this workflow:
- An epic is a high-level feature or objective.
- It is too large to complete as a single task.
- It must be decomposable into multiple user stories or tasks.
- It should represent a coherent business outcome, not a tiny action.

Input text:
{text}

Language:
{language}

Required output JSON schema:
{{
  "objectives": [string],
  "target_users_roles": [
    {{
      "role_name": string,
      "domain_focus": string,
      "responsibilities": [string],
      "permissions": [string],
      "workflows": [string],
      "pain_points": [string],
      "personality_guidance": string
    }}
  ],
  "constraints": {{
    "security": [string],
    "compliance": [string],
    "platform": [string],
    "timeline": [string],
    "integrations": [string]
  }},
  "non_goals": [string],
  "capabilities_implied": [string],
  "risks_open_questions": [string],
  "domain_terms": [string]
}}

Rules:
- Output valid JSON only, no markdown.
- Be specific and concrete for target_users_roles because role-specific agents will be created from this.
- Keep items short and implementation-relevant.
- Capture role and workflow details that help define epics at the correct level (not story-level).
- Do not include extra top-level keys.
- Keep all human-readable values in {language}.
"""

