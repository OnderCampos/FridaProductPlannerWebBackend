JIRA_STORY_GROUPING_PROMPT = """
You are helping import a Jira project into a Product Planner tool.

You will receive:
- Existing epics already created in Product Planner (may be empty).
- A batch of Jira user stories to import.

Your task:
1) Assign EVERY input story to EXACTLY ONE epic.
2) Prefer assigning stories to existing epics when they clearly fit.
3) If no existing epic fits, propose a NEW epic (name + description) and assign stories to it.

Existing epics (if you assign a story to an existing epic, you MUST use the epic name exactly as provided):
{existing_epics}

Stories to group (batch):
{stories}

IMPORTANT RULES:
- Use ONLY story keys from the input. Do NOT invent keys.
- Every input story key must appear in exactly one epic's story_keys.
- story_keys must be unique across epics (no duplicates).
- Do not include any story key that is not in the input.
- Keep epic names short (3-8 words) and distinct.
- Write all natural-language text values in the target language configured by the system.

Return ONLY valid JSON in the following shape:
{
  "epics": [
    {
      "name": "",
      "description": "",
      "story_keys": []
    }
  ]
}
"""
