"""
Security instructions module for use in prompts.

This module contains security guidelines that should be appended to all prompts
to ensure sensitive information is properly handled.
"""

SECURITY_INSTRUCTION = """
IMPORTANT SECURITY GUIDELINES consider the following for the response:
- DO NOT include or reference any personal identifiable information (PII) such as real names, user IDs, emails, or addresses
- DO NOT include any authentication data such as passwords, tokens, or security credentials
- DO NOT reference or include any private or confidential business data
- If you need to refer to users, use generic terms like "User", "Admin", or "Manager" instead of real names
- Use placeholder values (e.g., "USER_ID_HERE") instead of real IDs or credentials
- All generated examples must use fictitious data only
"""

def append_security_instruction(prompt: str) -> str:
    """
    Appends the security instruction to the given prompt.
    
    Args:
        prompt (str): The original prompt text
        
    Returns:
        str: The prompt with security instructions appended
    """
    return f"{prompt}\n\n{SECURITY_INSTRUCTION}"
