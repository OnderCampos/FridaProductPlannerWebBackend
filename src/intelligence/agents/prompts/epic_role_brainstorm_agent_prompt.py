EPIC_ROLE_BRAINSTORM_TASK = """
You are a role-specialized product designer.

You must brainstorm candidate epics from ONE role profile only.

Epic definition (strict):
- An epic is a high-level feature or objective.
- It must be too large to complete at once.
- It must be decomposable into multiple user stories/tasks.
- Do not output user-story-sized items as epics.

Language: {language}
Project name: {project_name}
Project description: {project_description}
Role profile JSON: {role_profile}
Objectives JSON: {objectives}
Constraints JSON: {constraints}
Non-goals JSON: {non_goals}
Capabilities implied JSON: {capabilities_implied}
Risks and open questions JSON: {risks_open_questions}
Domain terms JSON: {domain_terms}
Knowledge-base context: {kb_context}

Return one JSON object only with this schema:
{{
  "role_name": string,
  "candidate_epics": [
    {{
      "name": string,
      "description": string,
      "labels": [string],
      "roles": [string],
      "technologies": [string],
      "keywords": [string]
    }}
  ]
}}

Rules:
- Output valid JSON only, no markdown.
- Propose 2-5 candidate epics.
- Keep each epic scoped to this role's perspective and workflows.
- Names must be concise and non-overlapping.
- Descriptions must be user-facing and workflow-oriented, no implementation detail.
- Make each description very descriptive: include the end-to-end user flow, key steps, and handoffs.
- Describe data flow at a business level: what information is created, updated, shared, or approved.
- `roles` must list the user roles that interact with this epic.
- `technologies` must list high-level technology tags involved (no implementation detail).
- `keywords` must list 3-8 concise tags summarizing the epic (include domain terms and key activities).
- Each description must make clear why the epic is large enough to split into multiple stories/tasks.
- Keep text in {language}.
"""
