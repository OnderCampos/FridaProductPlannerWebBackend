import re

def get_code_block(text: str) -> str:
    """
    This function extracts the code block from a text.
    Args: text (str): The text to extract the code block from.
    Returns: str: The code block extracted from the text.
    """
    try:
        code_pattern = re.compile(r"```(\w+)*\n(.*)```", re.DOTALL)
        matches = code_pattern.findall(text)
        if len(matches) == 0:
            return None
        return matches[0][1]
    except Exception as e:
        return None
