GENERATE_EPIC_PROMPT = """
You are given the full project and epic context for a product-planning system.
Your task is to analyze the ENTIRE epic internally and generate the COMPLETE list of atomic user stories needed to cover that epic.

Project name:
{project_name}

Project description:
{project_description}

Epic context:
{epic}

IMPORTANT SCOPE RULES:
1. Analyze the full epic internally. Do NOT split the work into pre-defined functionalities.
2. Generate user stories for the WHOLE epic, not just one part of it.
3. Generate as many user stories as needed to cover the epic completely. Do NOT stop at 3 to 5.
4. Prefer completeness over arbitrary limits, but keep stories atomic and non-duplicated.
5. Cover all important personas, workflows, states, permissions, validations, and business outcomes that are clearly implied by the epic.
6. Do NOT create separate stories only for error handling; include error and validation behavior inside acceptance criteria unless it clearly represents a distinct user capability.

INTERNAL ANALYSIS YOU MUST DO BEFORE WRITING:
1. Identify the relevant end-user and operational roles implied by the epic.
2. Break the epic into discrete user capabilities, flows, and outcomes.
3. Convert those capabilities into atomic, implementation-ready user stories.
4. Deduplicate overlapping stories and keep only the strongest version.
5. Order the stories in a practical implementation sequence.

STORY CREATION GUIDELINES:
- Begin each story with the equivalent of "As a [specific role]" in the target language.
- Follow the format: "As a [role], I need/want [specific capability], so that [clear business value]".
- Each story must represent one clear capability or outcome, not an entire subsystem.
- Be specific about user-facing behavior, controls, screens, forms, filters, approvals, states, reports, notifications, or integrations when relevant.
- Split distinct permissions, lifecycle states, approval paths, reporting views, or multi-step workflows into separate stories when they deliver different user value.
- Group minor actions together only when they clearly belong to the same user goal.
- Consider responsive/mobile behavior only when it is relevant to the epic.

ESTIMATION AND DEPENDENCY RULES:
- `story_points` must use Fibonacci values: 1, 2, 3, 5, 8, 13, 21.
- `effortHours` must be a positive number greater than 0.
- `dependencies` must be an array of `user_story_id` values that should be completed first.
- Foundation stories may use an empty dependency array.
- Dependencies must reflect a logical build order and should not create cycles.

QUALITY RULES:
- Cover the epic as fully as the source context supports.
- Keep stories atomic, testable, and user-valued.
- Avoid generic filler stories that do not add distinct value.
- Avoid implementation-only stories about code structure, database design, endpoints, or frameworks unless the epic explicitly requires technical-user workflows.
- Use concise but concrete business language.

For each user story, provide the following information:
- User Story
- Description
- User Story ID
- Order
- Story Points
- Effort Hours
- Dependencies
- Acceptance Criteria
- Out of Scope
- Document
- {template_field_keys}

The `user_story_id` must be a short English reference such as `login_feature`, `approve_request`, or `export_report`.

IMPORTANT RESPONSE FORMAT:
Respond ONLY with valid JSON in this exact top-level shape:
{{
  "user_stories": [
    {{
      "epic": "",
      "user_story": "",
      "description": "",
      "user_story_id": "",
      "order": 1,
      "story_points": 3,
      "effortHours": 4,
      "dependencies": [],
      "acceptanceCriteria": ["..."],
      "outOfScope": ["..."],
      "document": {{
        "description_and_scope": "",
        "out_of_scope": "",
        "preconditions": "",
        "entry_points": "",
        "output_points": "",
        "success_flow": "",
        "wireframe_mockup": "",
        "field_description": "",
        "api_description": "",
        "acceptance_criteria": "",
        "test_scenarios": "",
        "benefits": "",
        "estimation_dev": ""
      }},
      {template_fields_json}
    }}
  ]
}}

FIELD RULES:
- `acceptanceCriteria` must contain 3 to 6 short, testable bullet items.
- `outOfScope` must contain 1 to 4 items. Use ["N/A"] only if truly none.
- `document` must always be an object with all listed keys populated as strings.
- Template-driven fields must always be included.
- The value of all template-driven fields MUST be a string, even if the field description suggests another format.
- For list-like template fields, provide a markdown-style string.

Fields from the template:
{fields_description}

Write all natural-language values in the target language configured by the system.
Keep JSON keys as-is.
Keep `user_story_id` values in English.
Do not add commentary outside the JSON.
"""
