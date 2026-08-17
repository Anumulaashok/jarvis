import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional, TypedDict

# Try to import yaml, but make it optional to avoid hard dependency if not strictly needed
# or to provide a clear error if missing in runtime.
try:
    import yaml
except ImportError:
    yaml = None # Set to None if not available

def parse_json(data_string: str) -> Optional[Dict[str, Any]]:
    """
    Safely parses a JSON string into a Python dictionary.

    Args:
        data_string: The string containing JSON data.

    Returns:
        A dictionary if parsing is successful, None otherwise.
    """
    try:
        return json.loads(data_string)
    except (json.JSONDecodeError, TypeError) as e:
        # In a production environment, a proper logging framework would be used here.
        # For this example, we print a warning.
        print(f"Warning: Failed to parse JSON string: {e}")
        return None

def parse_yaml(data_string: str) -> Optional[Dict[str, Any]]:
    """
    Safely parses a YAML string into a Python dictionary.
    Requires the 'PyYAML' library to be installed.

    Args:
        data_string: The string containing YAML data.

    Returns:
        A dictionary if parsing is successful, None otherwise.
    """
    if yaml is None:
        print("Error: PyYAML library is not installed. Cannot parse YAML.")
        return None
    try:
        # Using safe_load for security reasons to prevent arbitrary code execution
        return yaml.safe_load(data_string)
    except (yaml.YAMLError, TypeError) as e:
        print(f"Warning: Failed to parse YAML string: {e}")
        return None

def slugify(text: str) -> str:
    """
    Converts a string into a URL-friendly slug.
    Removes non-alphanumeric characters, converts to lowercase,
    and replaces spaces and multiple hyphens with a single hyphen.

    Args:
        text: The input string to slugify.

    Returns:
        The slugified string.
    """
    text = str(text) # Ensure text is a string
    text = text.lower()
    # Replace non-alphanumeric characters (except hyphens and spaces) with empty string
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    # Replace multiple spaces/hyphens with a single hyphen
    text = re.sub(r'[\s-]+', '-', text).strip('-')
    return text

def clean_text(text: str) -> str:
    """
    Performs basic text cleaning:
    - Removes leading/trailing whitespace.
    - Replaces multiple internal spaces, tabs, and newlines with a single space.

    Args:
        text: The input string to clean.

    Returns:
        The cleaned string.
    """
    text = str(text) # Ensure text is a string
    text = text.strip()
    text = re.sub(r'\s+', ' ', text)
    return text

def truncate_string(text: str, max_length: int, suffix: str = "...") -> str:
    """
    Truncates a string to a specified maximum length.
    If the string exceeds `max_length`, it appends a suffix.

    Args:
        text: The input string to truncate.
        max_length: The maximum desired length of the string.
        suffix: The string to append if truncation occurs (e.g., "...").

    Returns:
        The truncated string.
    """
    text = str(text) # Ensure text is a string
    if not isinstance(max_length, int) or max_length < 0:
        raise ValueError("max_length must be a non-negative integer.")

    if len(text) <= max_length:
        return text

    if len(suffix) >= max_length:
        # If suffix is too long, truncate suffix itself
        return suffix[:max_length]

    return text[:max_length - len(suffix)] + suffix

def generate_uuid() -> str:
    """
    Generates a unique identifier (UUID) as a string.

    Returns:
        A UUID string.
    """
    return str(uuid.uuid4())

def get_current_timestamp_utc(format_string: str = "%Y-%m-%dT%H:%M:%S.%fZ") -> str:
    """
    Retrieves the current UTC timestamp formatted as a string.
    Default format is ISO 8601 with milliseconds and 'Z' for Zulu time (UTC).

    Args:
        format_string: The desired format string for the timestamp
                       (e.g., "%Y-%m-%d %H:%M:%S").

    Returns:
        A string representation of the current UTC timestamp.
    """
    return datetime.now(timezone.utc).strftime(format_string)

class GenericSettings(TypedDict, total=False):
    """
    A common TypedDict for generic application settings or structured data.
    Keys are optional (total=False) to allow for flexible usage.
    This provides type hinting for common data structures used across the project.
    """
    name: str
    version: str
    debug_mode: bool
    log_level: str
    api_key: Optional[str]
    # Add other common settings/data fields as needed

# Example usage and self-test
if __name__ == "__main__":
    print("--- utils/common.py Self-Test ---")

    # Test parse_json
    print("\n[Testing JSON Parsing]")
    json_data_ok = '{"name": "AgentX", "version": 1.0, "active": true}'
    json_data_bad = '{"name": "AgentX", "version": 1.0, "active": true,' # Malformed
    parsed_ok = parse_json(json_data_ok)
    parsed_bad = parse_json(json_data_bad)
    print(f"Parsed OK JSON: {parsed_ok}")
    print(f"Parsed BAD JSON: {parsed_bad}")

    # Test parse_yaml
    print("\n[Testing YAML Parsing]")
    yaml_data_ok = """
agent:
  name: AgentY
  config:
    enabled: true
    threshold: 0.8
    """
    yaml_data_bad = """
agent:
  name: AgentY
    config: # Indentation error
      enabled: true
    """
    parsed_yaml_ok = parse_yaml(yaml_data_ok)
    parsed_yaml_bad = parse_yaml(yaml_data_bad)
    print(f"Parsed OK YAML: {parsed_yaml_ok}")
    print(f"Parsed BAD YAML: {parsed_yaml_bad}")

    # Test slugify
    print("\n[Testing Slugify]")
    print(f"Slugify 'Hello World! How are you?': '{slugify('Hello World! How are you?')}'")
    print(f"Slugify 'SaaS App - Problem Statement #1': '{slugify('SaaS App - Problem Statement #1')}'")
    print(f"Slugify '  _ Test String With_  Special Chars  _ ': '{slugify('  _ Test String With_  Special Chars  _ ')}'")
    print(f"Slugify 12345: '{slugify(12345)}'") # Test with non-string input

    # Test clean_text
    print("\n[Testing Clean Text]")
    dirty_text = "  This is a   \n  dirty string with   too many    spaces.  "
    print(f"Original: '{dirty_text}'")
    print(f"Cleaned: '{clean_text(dirty_text)}'")
    print(f"Cleaned number: '{clean_text(12345)}'") # Test with non-string input

    # Test truncate_string
    print("\n[Testing Truncate String]")
    long_string = "This is a very long string that needs to be truncated for display purposes."
    print(f"Original: '{long_string}' ({len(long_string)} chars)")
    print(f"Truncated (20): '{truncate_string(long_string, 20)}'")
    print(f"Truncated (50, '...Read More'): '{truncate_string(long_string, 50, '...Read More')}'")
    print(f"Truncated (100): '{truncate_string(long_string, 100)}'") # No truncation needed
    print(f"Truncated (5, '>>'): '{truncate_string('short', 5, '>>')}'")
    print(f"Truncated (2, '>>'): '{truncate_string('short', 2, '>>')}'") # Suffix itself is truncated
    try:
        truncate_string(long_string, -1)
    except ValueError as e:
        print(f"Truncate (negative length) error: {e}")

    # Test generate_uuid
    print("\n[Testing UUID Generation]")
    print(f"Generated UUID 1: {generate_uuid()}")
    print(f"Generated UUID 2: {generate_uuid()}")

    # Test get_current_timestamp_utc
    print("\n[Testing Timestamp]")
    print(f"Current UTC Timestamp (default): {get_current_timestamp_utc()}")
    print(f"Current UTC Timestamp (date only): {get_current_timestamp_utc('%Y-%m-%d')}")

    # Test GenericSettings TypedDict
    print("\n[Testing GenericSettings TypedDict]")
    my_settings: GenericSettings = {
        "name": "Problem Solver Agent",
        "version": "1.0.0",
        "debug_mode": True,
        "log_level": "INFO"
    }
    print(f"Generic Settings: {my_settings}")
    # Accessing an optional key
    print(f"API Key (optional): {my_settings.get('api_key')}")
    # Update an optional key
    my_settings['api_key'] = generate_uuid()
    print(f"API Key updated: {my_settings['api_key']}")