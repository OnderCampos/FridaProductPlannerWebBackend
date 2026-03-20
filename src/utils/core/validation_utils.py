import re

"""
Validation utility functions for checking data structures and formats.
These functions can be used across different modules to ensure consistent validation.
"""

def has_expected_epic_structure(json_obj, expected_keys) -> bool:
    """
    This function checks if the JSON object has the expected keys and that the values for these keys are not empty.

    Args:
        json_obj (dict): The JSON object to check.
        expected_keys (list): The list of expected keys.

    Returns:
        bool: True if the JSON object has the expected keys and non-empty values, False otherwise.
    """
    # Check if the input data is a dictionary
    if not isinstance(json_obj, dict):
        return False

    # Convert the list of required keys to a set
    required_keys_set = set(expected_keys)

    # Convert the keys of the dictionary to a set
    data_keys_set = set(json_obj.keys())

    # Check if both sets are equal (no missing or extra keys)
    if required_keys_set != data_keys_set:
        return False

    # Check if all values for the required keys are not empty
    for key in required_keys_set:
        if not json_obj[key]:
            return False

    return True

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
