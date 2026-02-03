
GENERATE_EPICS_PROMPT = """
You are provided with the following text:
    {text}
your task is to generate a project description and a list of Epics derived from the text provided. For each epic return the name and a description.

The project description must summarize the overall purpose and goals, focusing on how different users interact with the system, the main workflows, and the relationships between different entities in the project.
The project description MUST identify all user roles involved in the project.

EPIC CREATION GUIDELINES:
- Create well-defined epics focused on specific user interactions or workflows
- Each epic must center on a distinct set of user actions or business processes
- Epics should NOT overlap in user actions or business processes
- Be very descriptive about user roles, views, and interactions in each epic
- Describe all workflows in natural language that anyone can understand
- Include user workflows and data flows between users in each epic
- The project description most contain the user roles involved in the project.
- The epics must consider in the description the data flows involved in the project.
- Consider different user perspectives and views in the system
- Include both functional user interactions and non-functional user experiences as separate epics
- Use natural, conversational language that can be understood by non-technical stakeholders.
- Avoid technical jargon or implementation details in the project description and epic descriptions.

The format of the response should be in the following JSON format:
{{
    "project_description": "",//The project description focusing on users, roles, and interactions and workflows be very detailed
    "technical_stack": ["azure", "python", "react", "mongodb"], // A list of technology tags as strings (e.g., azure, python, react, nodejs, mongodb, docker, etc.)
    "roles": ["Admin", "User", "Manager"], // A list of user role names as strings
    "epics": [
        {{
            "name": "",//String
            "description": "" // String with detailed explanation in natural language about user interactions, roles, views, and workflows
        }},
        ...
    ]

}}
- Always respond in valid JSON format as specified above.
- Do NOT omit any fields; ensure every field is completed accurately.
- technical_stack must be an array of strings containing technology tags (e.g., "azure", "python", "react", "nodejs", "mongodb", "docker", "kubernetes", "sql", etc.)
- roles must be an array of strings containing user role names (e.g., "Admin", "User", "Manager", "Customer", etc.)
- Be very explicit and detailed in your responses, focusing only on user interactions, roles, views, and workflows.
- Describe the workflows as a narrative of user actions and system responses.
- DO NOT include any technical implementation details in the project description or epic descriptions.
- ONLY the "technical_stack" field should contain technology-related tags and identifiers.
- IMPORTANT Respond ONLY with the object in {language} language, but respect the object keys
"""

SUMMARIZE_PROJECT_DESCRIPTION_PROMPT = """
You are given two texts: CURRENT TEXT and NEXT TEXT. Your task is to create a new text, about the same size as each input(5000 characters),
 that contains the key points and most important information from both. Be concise and do not lose any critical details.
 The key points must be preserved in the merged text.
    - Project Description
    - User Roles
    - User Interactions
    - Workflows
    - Epics and their details
    - Technical Stack (if any)

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


