SUMMARIZE_PROJECT_DESCRIPTION_PROMPT = """
You are given two texts: CURRENT TEXT and NEXT TEXT. Your task is to merge them into a single summary
of roughly the same size as each input (~5000 characters), preserving the most important information
from both WITHOUT duplication.

Rules:
- Do NOT invent facts. Only include information present in the inputs.
- Remove duplicates (exact and conceptual). If the same concept appears in both texts, keep it once.
- If user roles are present, list them ONCE under "User Roles:" as a unique bullet list.
  Normalize common variants:
  - developer(s) -> Developer
  - tech lead(s) / technical lead -> Tech Lead
  - QA / QA engineer / quality assurance -> QA
  - modernization team(s) / modernisation team(s) -> Modernization Team
  - architect(s) -> Architect
- If technical stack items are present, list them ONCE under "Technical Stack:" as a unique bullet list.
- Preserve key workflows/user interactions and epics/features; consolidate duplicates.
- If a project name is present, keep it.

Output format (plain text):
- Project Name: <if available>
- Project Summary: <1-5 concise paragraphs>
- User Roles:
  - <role 1>
  - <role 2>
- Technical Stack:
  - <item 1>
  - <item 2>
- Key Workflows / Interactions:
  - <item 1>
  - <item 2>
- Epics / Features:
  - <item 1>
  - <item 2>

Omit any section that has no information.

CURRENT TEXT:
```
{current}
```

NEXT TEXT:
```
{next}
```

Return the merged text in {language}.
"""


GENERATE_KEYWORDS_FOR_KBS_PROMPT = """
You are given a project description text. Your task is to extract and generate a list of relevant
keywords and key phrases that can be used to create or enhance a knowledge base for the project.
The keywords should capture the main concepts, entities, user roles, workflows, and technical terms mentioned
in the project description.
The format of the response should be a JSON object as follows:

{{
    [
        "keyword1",
        "keyword2",
        ...
    ]
}}
"""
