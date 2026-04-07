IDENTIFY_EPIC_USERS_AND_FUNCTIONALITY_PROMPT = """
You are tasked with analyzing an epic in detail and identifying the key elements needed for successful implementation.
The epic is described below, along with the overall project context.
{epic}

The project description is as follows:
{project_description}

STEP 1 - USER IDENTIFICATION (HIGHEST PRIORITY):
First and foremost, you MUST identify only the MOST IMPORTANT user types that directly interact with the functionality in this epic.

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
- Keep only the roles that are most relevant for user stories and end users of the system.
- Prioritize end users and core admin/operational roles who perform actions in the product.
- Exclude broad audience segments, market categories, company-size labels, and indirect stakeholders unless they have distinct system workflows.
- Prefer 2-4 important roles for the epic unless the epic clearly requires more.
- Always respond with the format inside a code block
- Focus solely on user roles and application-level functionality. Avoid any mention of technical roles (e.g., Developer) or implementation details.
- Be specific and detailed in all descriptions
- Think step by step through all aspects of the epic
- Remember each functionality will be processed SEPARATELY to generate user stories
"""

