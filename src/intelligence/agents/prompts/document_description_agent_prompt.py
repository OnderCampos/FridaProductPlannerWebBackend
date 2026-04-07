DOCUMENT_DESCRIPTION_TASK = """
You are a product analyst. Extract the core project description, user roles, and technical stack
from the document content provided.

Document text:
{text}

Language:
{language}

Return ONLY valid JSON in this exact structure:
{
  "project_description": string,
  "roles": [string],
  "technical_stack": [string]
}

Rules:
- Output JSON only, no markdown.
- "project_description" should be concise but complete for planning.
- "roles" should list distinct normalized user roles mentioned or implied. If none, return an empty array.
- Normalize market segments or audience labels into application personas focused on permissions and workflows.
- Prefer short role names such as "End User", "Administrator", "Manager", "Seller", "Buyer", "Operator", "Support Agent", "Instructor", or "Student" when appropriate.
- Example: "eCommerce sellers (e.g., Etsy, Shopify)" should become "Seller".
- Example: "Hobbyist 3D printing enthusiasts" should become "End User" unless the document clearly describes a separate permission model.
- Do not return company-size labels, demographic labels, or brand/channel labels as roles unless they represent distinct system permissions/workflows.
- Keep only the most important roles that directly use the product and are relevant for writing user stories.
- Prioritize end users and core operational personas.
- Prefer 2-5 roles total unless the document clearly requires more.
- "technical_stack" should list technologies or platforms mentioned. If none, return an empty array.
- Do not include extra top-level keys.
- Keep all human-readable values in {language}.
"""
