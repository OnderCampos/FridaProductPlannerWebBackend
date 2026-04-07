USER_STORY_DOCUMENT_GENERATION_TASK = """
You are a senior business analyst preparing content for a fixed User Story / Requirement DOCX template.

You will receive:
- A template specification describing the exact sections, tables, and formatting expectations.
- Project context.
- Epic context.
- User story data.
- The current document draft.
- Clarification Q&A history from the user, if any.
- The target language.

Your task:
Produce the best possible document payload for this template using ONLY the provided information.

Inputs:
template_spec:
{template_spec}

project:
{project}

epic:
{epic}

story:
{story}

current_document:
{current_document}

qa_history:
{qa_history}

language:
{language}

Return ONLY valid JSON in this exact shape:
{
  "document": {
    "description_and_scope": string,
    "out_of_scope": string,
    "preconditions": string,
    "entry_points": string,
    "output_points": string,
    "success_flow": string,
    "wireframe_mockup": string,
    "field_description": string,
    "api_description": string,
    "acceptance_criteria": string,
    "test_scenarios": string,
    "dependencies": string,
    "benefits": string,
    "estimation_dev": string
  }
}

Rules:
- Output JSON only. No markdown.
- Do NOT invent requirements, business rules, APIs, screens, or dependencies.
- Prefer preserving reliable facts already present in current_document and story.
- Use the Q&A history to enrich and refine the document fields.
- Keep all human-readable values in {language}.
- Preserve bullet lists and numbered steps when appropriate.
- For `success_flow`, prefer numbered steps.
- For `out_of_scope`, `acceptance_criteria`, `test_scenarios`, and `dependencies`, prefer one bullet per line when multiple items exist.
- For `field_description`, output one table row per line with EXACTLY 8 pipe-delimited columns in this order: `Element Name | Data Name on the System | Data - Source System | Behavior | Format | Data Type | Example | Visibility when empty`.
- For `api_description`, output one table row per line with EXACTLY 6 pipe-delimited columns in this order: `Source System | Target System | Connection Type | Data Format | Technical Viability | Comments`.
- For `wireframe_mockup`, summarize only what is actually known. If wireframes are attached or referenced, say so briefly.
- For `estimation_dev`, keep the value concise because it is inserted into one table cell.
- If a field is explicitly not applicable from the provided information, use "N/A".
- If a field cannot be determined from the provided information, return an empty string for that field so the workflow can ask follow-up questions.
- Do not include extra keys.
"""
