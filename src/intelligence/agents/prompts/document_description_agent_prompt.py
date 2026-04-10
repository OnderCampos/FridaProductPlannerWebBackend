DOCUMENT_DESCRIPTION_TASK = """
You are a product analyst. Synthesize a concise but complete project description
from the generated epic names and epic descriptions provided.

Epics:
{epics}

Language:
{language}

Return ONLY valid JSON in this exact structure:
{
  "project_description": string
}

Rules:
- Output JSON only, no markdown.
- "project_description" should explain the product, its main workflows, and the value implied by the epic set.
- Base the summary on the provided epics only. Do not invent capabilities that are not supported by the epic names or descriptions.
- Use the epic descriptions as the main evidence. Use epic names as supporting context.
- Merge overlapping epics into one coherent product-level description instead of listing epics one by one.
- Do not include role lists, technical stack lists, or extra top-level keys.
- Keep all human-readable values in {language}.
"""
