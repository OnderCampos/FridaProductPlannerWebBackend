GENERATE_EPIC_PROMPT = """
You are given a project description, user roles, a specific functionality, and the epic context.
Your task is to create comprehensive and ATOMIC user stories ONLY for the specific functionality provided.

IMPORTANT - FOCUS ON THE SPECIFIC FUNCTIONALITY:
1. The functionality to focus on is: {functionality}
2. Use the epic ONLY as context, not as a source for additional user stories
3. Generate user stories ONLY for this functionality, not for the entire epic

IMPORTANT - USER ROLES ANALYSIS:
Use these specific user roles for your user stories:
{users}

Epic context (for reference only):
{epic}

All functionalities in this epic (for reference to avoid overlap):
{functionalities}

USER STORY CREATION PROCESS & STRICT LIMITS:
1. CRITICAL RULE: You MUST generate a MAXIMUM of 3 to 5 user stories for the provided functionality.
2. CONSOLIDATE ACTIONS: Do NOT create separate stories for every single CRUD operation or minor action. Group related actions (e.g., Create, Read, Update, Delete) into a single, cohesive user story if they serve the same user goal.
3. ERROR HANDLING: Include error handling and edge cases as "Acceptance Criteria" within the main user stories. Do NOT create separate user stories solely for error handling.
4. ROLE FOCUS: Focus only on the primary user role that derives the most value from this functionality. Do not create duplicate stories just to cover every minor role.

For each user story, provide the following information: User Story, Description, Order, Dependencies, Story Points, Effort Hours, {template_field_keys}

STORY CREATION GUIDELINES:
- Begin user stories with the equivalent of "As a [specific role]" in the target language (avoid generic "user" unless appropriate)
- Make each story ATOMIC - focused on a SINGLE user action or capability with this specific functionality
- Follow the format: "As a [role], I need/want [specific feature/UI element], so that [clear business value]" translated to the target language
- Ensure each story provides clear business value with an explicit "so that..." statement translated to the target language
- Be SPECIFIC about UI components and actions (e.g., "I need a search bar with filters", "I need a delete button with confirmation dialog")
- Specify concrete interface elements like buttons, forms, modals, search bars, etc.
- Include detailed acceptance criteria that are testable and verifiable
- When present in the output keys, fill `acceptance_criteria` (markdown bullet list string) and `out_of_scope` (markdown bullet list string; use "N/A" if truly none)
- Consider mobile/responsive adaptations where relevant
- Assign an order number (1, 2, 3, etc.) based on logical implementation sequence
- Identify dependencies - which user story IDs must be completed before this one
- Assign Story Points to estimate the effort and complexity of the user story. You MUST use the Fibonacci sequence (1, 2, 3, 5, 8, 13, 21). Use 1-3 for simple tasks, 5-8 for medium complexity, and 13-21 for highly complex tasks.
- Also estimate effort hours (``effortHours``) as a realistic number of hours required to implement the story. Use decimals if needed (e.g., 2.5). Must be greater than 0.

CONCRETE EXAMPLES OF GOOD ATOMIC USER STORIES:
- "As an Administrator, I need a search bar with filters for users by name, role and date created, so that I can quickly find specific users in large datasets"
- "As a Content Manager, I need a delete button with confirmation dialog when removing articles, so that I can prevent accidental deletions"
- "As an Editor, I need form validation that highlights invalid fields in real-time, so that I can correct errors before submission"
- "As a Reviewer, I need a comment section with formatting options attached to each document, so that I can provide detailed feedback"

Consider the following when creating user stories for user_story_id field:
    "user_story_id": "",// A ID generated for each user story with a short reference of the user story eg. "login_feature" in English

IMPORTANT - RESPONSE FORMAT:
Respond with a list of objects in the following format, fill each object completely:
{{
    "user_stories": [
        {{
            "epic": "",
            "user_story": "",
            "description": "",
            "user_story_id": "",// A ID generated for each user story with a short reference of the user story eg. "login_feature"
            "order": 1,// Sequential number indicating implementation order (1, 2, 3, etc.)
            "story_points": 3, // Integer using Fibonacci sequence (1, 2, 3, 5, 8, 13, 21) indicating estimated effort
            "effortHours": 4, // Number of hours required to implement this story (decimals allowed)
            "dependencies": [],// Array of user_story_id values that must be completed first (empty array if no dependencies)
            {template_fields_json}
        }},
        ...
    ]
}}

DEPENDENCY RULES:
- Foundation stories (authentication, data models, etc.) should have no dependencies (empty array)
- Stories that require existing features should list those feature's user_story_id values in dependencies
- A story can depend on multiple other stories
- Ensure dependencies create a logical flow (e.g., create before edit, edit before delete)
- Consider technical dependencies (backend before frontend, API before UI)

QUALITY CRITERIA FOR USER STORIES:
- Single Responsibility: Each story focuses on ONE clear user action or capability
- Atomic: Each story represents the smallest possible functional increment
- Independent: Can be developed and tested separately from other stories
- Complete: Has well-defined acceptance criteria and test cases
- Valuable: Delivers meaningful outcome to users
- Specific: Includes concrete UI components and interaction methods
- Role-specific: Uses the exact roles identified in the analysis
- Testable: Contains clear conditions for verification
- Every user role that interacts with this functionality must have at least one user story
- Include at least one error handling story for the functionality

DO NOT OMIT any field.
IMPORTANT: Respond ONLY with the list in JSON format.
The epic should match the data point provided.

Fields from the template:
{fields_description}

The value of all fields MUST be a string, even if the field description indicates otherwise.
For list-formatted fields, provide the list as a string in markdown format.
Write all natural-language text values in the target language configured by the system.
Keep the object keys as-is, and keep `user_story_id` as a short reference in English.
"""
