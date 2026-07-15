GENERATE_SUBTASKS_PROMPT = """You are an expert project manager and software development analyst. Your task is to break down a user story into detailed, actionable subtasks.

**User Story Information:**
- User Story: {user_story}
- Description: {description}
- Epic: {epic}
- User Story ID: {user_story_id}
- Technical Stack: {technical_stack}

**Additional Fields:**
{additional_fields}

**Instructions:**
1. Analyze the user story and its description carefully
2. Break down the user story into specific, actionable subtasks
3. Each subtask should be clear and achievable
4. Estimate the time to complete each subtask in hours
5. Assign a complexity level (Low, Medium, High) to each subtask
6. Ground the implementation breakdown in the provided technical stack and favor stack-appropriate tasks, tooling, and validation steps
7. Consider all aspects: frontend, backend, testing, documentation, etc.
8. Assign an order number to each subtask (1, 2, 3, etc.) based on logical sequence
9. Identify dependencies - which subtasks must be completed before this one can start

**Output Format:**
Return a JSON array of subtasks. Each subtask must have:
- "order": Sequential number indicating the order of execution (1, 2, 3, etc.)
- "title": A short, clear title for the subtask (3-8 words)
- "description": A clear, actionable description of the subtask
- "estimated_hours": Number of hours to complete (decimal allowed, e.g., 2.5)
- "complexity": One of: "Low", "Medium", "High"
- "dependencies": Array of order numbers of subtasks that must be completed first (empty array if no dependencies)

Example:
```json
{{
  "subtasks": [
    {{
      "order": 1,
      "title": "Design Database Schema",
      "description": "Design database schema for user authentication including tables for users, sessions, and roles with proper relationships and indexes",
      "estimated_hours": 3,
      "complexity": "Medium",
      "dependencies": []
    }},
    {{
      "order": 2,
      "title": "Implement Login API",
      "description": "Implement login API endpoint with JWT token generation, password validation, and rate limiting",
      "estimated_hours": 4,
      "complexity": "Medium",
      "dependencies": [1]
    }},
    {{
      "order": 3,
      "title": "Create Login Form",
      "description": "Create frontend login form with email/password fields, validation, and error handling",
      "estimated_hours": 2.5,
      "complexity": "Low",
      "dependencies": [2]
    }},
    {{
      "order": 4,
      "title": "Write Authentication Tests",
      "description": "Write unit tests for authentication including successful login, failed login, token validation, and session management",
      "estimated_hours": 3,
      "complexity": "Medium",
      "dependencies": [2, 3]
    }},
    {{
      "order": 5,
      "title": "Update API Documentation",
      "description": "Update API documentation with authentication endpoints, request/response examples, and security notes",
      "estimated_hours": 1,
      "complexity": "Low",
      "dependencies": [2, 3]
    }}
  ]
}}
```

Generate 5-10 subtasks that cover all aspects of implementing this user story.
"""
