IDENTIFY_EPIC_USERS_AND_FUNCTIONALITY_PROMPT = """
You are tasked with analyzing an epic in detail and identifying the key elements needed for successful implementation.
The epic is described below, along with the overall project context.
{epic}

The project description is as follows:
{project_description}

STEP 1 - USER IDENTIFICATION (HIGHEST PRIORITY):
First and foremost, you MUST identify ALL types of users that will interact with the functionality in this epic.

For each user type:
- Provide a clear name/role (e.g., Administrator, Customer, Content Creator)
- Describe their specific permissions and access levels
- Explain how they interact with the functionality in this epic
- Identify their goals, needs, and pain points related to this epic

STEP 2 - EPIC FUNCTIONALITY ANALYSIS:
After identifying users, break down the epic into EXACTLY 3 INDEPENDENT core functionalities:

- Each functionality must be self-contained and represent a complete set of related actions
- Each functionality must be independent from the others (no overlap in actions)
- Each functionality should contain multiple concrete action statements (e.g., "Create new records", "Filter search results", "Generate reports")
- For each functionality, list at least 3 specific action statements that users can perform
- Functionalities should cover the complete scope of the epic
- Name each functionality based on its primary purpose (e.g., "User Authentication", "Content Management", "Reporting System")

RESPONSE FORMAT:
Return your analysis in the following structured JSON format:

{{
  "epic_analysis": {{
    "users": [
      {{
        "role": "Role name",
        "permissions": "What permissions this role needs",
        "interactions": "How this role interacts with the system",
        "needs": "Specific needs and pain points of this role"
      }}
    ],
    "functionalities": [
      {{
        "name": "Functionality name",
        "description": "Detailed description of the functionality",
        "user_benefits": "How this benefits users",
        "action_statements": [
          "Action statement 1 (e.g., 'Create new records')",
          "Action statement 2 (e.g., 'Filter search results')",
          "Action statement 3 (e.g., 'Delete outdated information')",
          "Action statement 4 (e.g., 'Export data in multiple formats')"
        ],
        "primary_roles": ["Role1", "Role2"] 
      }}
    ]
  }}
}}

IMPORTANT GUIDELINES:
1. Action statements MUST:
   - Be specific, concrete, and actionable
   - Start with a verb (Create, View, Edit, Delete, Filter, Export, etc.)
   - Describe a single discrete action a user can take
   - Be different across functionalities (no duplicate actions in different functionalities)

2. Each functionality MUST:
   - Be completely independent from other functionalities
   - Have a clear, distinct purpose
   - Include all related actions grouped together
   - Be focused enough to be individually implementable

3. The set of functionalities MUST:
   - Cover the entire scope of the epic
   - Represent logical groupings of related actions
   - Be balanced in scope and complexity

IMPORTANT:
- User identification is THE MOST CRITICAL first step - you MUST begin with comprehensive user analysis
- Always respond with the format inside a code block
- Focus solely on user roles and application-level functionality. Avoid any mention of technical roles (e.g., Developer) or implementation details.
- Be specific and detailed in all descriptions
- Think step by step through all aspects of the epic
- Remember each functionality will be processed SEPARATELY to generate user stories
"""


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

ATOMIC USER STORY CREATION PROCESS:
1. For the functionality provided, create a SEPARATE atomic user story for EACH user role that would interact with it
2. For EACH specific action a user can take with this functionality, create a SEPARATE user story
3. Do NOT combine multiple roles into a single user story
4. For CRUD operations, create a SEPARATE user story for EACH operation (Create, Read, Update, Delete)
5. Include user stories for error handling and edge cases related to this functionality

For each user story, provide the following information: User Story, Description, Order, Dependencies, Story Points, {template_field_keys}

STORY CREATION GUIDELINES:
- Begin user stories with "As a [specific role]" (not generic "user" unless appropriate)
- Make each story ATOMIC - focused on a SINGLE user action or capability with this specific functionality
- Follow the format: "As a [role], I need [specific feature/UI element], so that [clear business value]"
- Ensure each story provides clear business value with "so that..." statement
- Be SPECIFIC about UI components and actions (e.g., "I need a search bar with filters", "I need a delete button with confirmation dialog")
- Specify concrete interface elements like buttons, forms, modals, search bars, etc.
- Include detailed acceptance criteria that are testable and verifiable
- Consider mobile/responsive adaptations where relevant
- Assign an order number (1, 2, 3, etc.) based on logical implementation sequence
- Identify dependencies - which user story IDs must be completed before this one
- Assign Story Points to estimate the effort and complexity of the user story. You MUST use the Fibonacci sequence (1, 2, 3, 5, 8, 13, 21). Use 1-3 for simple tasks, 5-8 for medium complexity, and 13-21 for highly complex tasks.

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
The response should be in English language, but respect the object keys.
"""