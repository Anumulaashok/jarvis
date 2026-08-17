import re
import time
import json
from functools import wraps
from typing import Any, Dict, List, Optional, Tuple, Type, Callable, Union

def clean_text(text: str) -> str:
    """
    Cleans a given string by removing extra whitespace, leading/trailing whitespace,
    and standardizing line breaks.

    Args:
        text (str): The input string to clean.

    Returns:
        str: The cleaned string.
    """
    if not isinstance(text, str):
        return ""
    text = text.replace('\r\n', '\n').replace('\r', '\n') # Standardize line breaks
    text = re.sub(r'[ \t]+', ' ', text) # Replace multiple spaces/tabs with single space
    text = re.sub(r'\n{2,}', '\n\n', text) # Reduce multiple newlines to at most two
    text = text.strip() # Remove leading/trailing whitespace
    return text

def chunk_text(text: str, max_tokens: int, overlap: int = 0) -> List[str]:
    """
    Splits a large text into smaller chunks based on a maximum token count,
    with an optional overlap between chunks.
    This is a character-based chunking for simplicity, assuming tokens are roughly
    proportional to characters or words, and actual tokenization would happen
    before LLM calls.

    Args:
        text (str): The input text to chunk.
        max_tokens (int): The maximum number of characters/approximate tokens per chunk.
        overlap (int): The number of characters to overlap between consecutive chunks.

    Returns:
        List[str]: A list of text chunks.
    """
    if not text:
        return []
    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than 0")
    if overlap < 0:
        raise ValueError("overlap must be non-negative")
    if overlap >= max_tokens:
        raise ValueError("overlap must be less than max_tokens")

    chunks = []
    current_pos = 0
    while current_pos < len(text):
        end_pos = min(current_pos + max_tokens, len(text))
        chunk = text[current_pos:end_pos]
        chunks.append(chunk)

        if end_pos == len(text):
            break
        current_pos += (max_tokens - overlap)
        if current_pos >= len(text): # Ensure no empty chunks are added if overlap makes next chunk start past end
            break
    return chunks

def safe_get(data: Union[Dict[str, Any], List[Any]], path: str, default: Any = None) -> Any:
    """
    Safely retrieves a value from a nested dictionary or list using a dot-separated path.

    Example:
        data = {"a": {"b": [{"c": 1}]}}
        safe_get(data, "a.b.0.c") == 1
        safe_get(data, "a.b.1.c", "not_found") == "not_found"

    Args:
        data (Union[Dict[str, Any], List[Any]]): The dictionary or list to traverse.
        path (str): The dot-separated path to the desired value.
        default (Any, optional): The default value to return if the path is not found.
                                 Defaults to None.

    Returns:
        Any: The value at the specified path, or the default value if not found.
    """
    keys = path.split('.')
    current = data
    try:
        for key in keys:
            if isinstance(current, dict):
                current = current[key]
            elif isinstance(current, list):
                current = current[int(key)]
            else:
                return default
        return current
    except (KeyError, IndexError, TypeError, ValueError):
        return default

def serialize_to_json(data: Any, indent: Optional[int] = None) -> str:
    """
    Serializes a Python object to a JSON string.

    Args:
        data (Any): The Python object to serialize.
        indent (Optional[int]): If not None, JSON array elements and object members
                                will be pretty-printed with that indent level.

    Returns:
        str: The JSON string representation of the object.

    Raises:
        TypeError: If the object cannot be serialized to JSON.
    """
    return json.dumps(data, indent=indent, ensure_ascii=False)

def deserialize_from_json(json_string: str) -> Any:
    """
    Deserializes a JSON string to a Python object.

    Args:
        json_string (str): The JSON string to deserialize.

    Returns:
        Any: The Python object represented by the JSON string.

    Raises:
        json.JSONDecodeError: If the input string is not a valid JSON document.
    """
    return json.loads(json_string)

def retry_on_exception(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: Union[Tuple[Type[Exception], ...], Type[Exception]] = (Exception,),
    logger: Optional[Callable[[str], None]] = None
) -> Callable:
    """
    A decorator that retries a function call if specific exceptions occur.

    Args:
        max_retries (int): The maximum number of times to retry the function. Defaults to 3.
        delay (float): The initial delay in seconds before the first retry. Defaults to 1.0.
        backoff_factor (float): Factor by which the delay will be multiplied after each retry.
                                Defaults to 2.0.
        exceptions (Union[Tuple[Type[Exception], ...], Type[Exception]]):
            The exception or tuple of exceptions to catch and retry on. Defaults to (Exception,).
        logger (Optional[Callable[[str], None]]): A callable (e.g., a logger function)
                                                 to log retry attempts.

    Returns:
        Callable: The decorated function.
    """
    if isinstance(exceptions, type):
        exceptions = (exceptions,)

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            current_delay = delay
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt < max_retries:
                        msg = (f"Attempt {attempt}/{max_retries} failed for {func.__name__} "
                               f"with exception: {type(e).__name__}. Retrying in {current_delay:.2f}s...")
                        if logger:
                            logger(msg)
                        else:
                            print(msg) # Fallback to print if no logger
                        time.sleep(current_delay)
                        current_delay *= backoff_factor
                    else:
                        msg = (f"Attempt {attempt}/{max_retries} failed for {func.__name__} "
                               f"with exception: {type(e).__name__}. No more retries.")
                        if logger:
                            logger(msg)
                        else:
                            print(msg) # Fallback to print if no logger
                        raise # Re-raise the last exception after max_retries
        return wrapper
    return decorator

if __name__ == "__main__":
    # --- Test clean_text ---
    print("--- Testing clean_text ---")
    text1 = "  Hello   World!\n\nThis is a test. \r\n"
    cleaned1 = clean_text(text1)
    print(f"Original: '{text1}'")
    print(f"Cleaned:  '{cleaned1}'")
    assert cleaned1 == "Hello World!\n\nThis is a test."

    text2 = "No leading or trailing.  Just   extra   spaces. "
    cleaned2 = clean_text(text2)
    print(f"Original: '{text2}'")
    print(f"Cleaned:  '{cleaned2}'")
    assert cleaned2 == "No leading or trailing. Just extra spaces."

    print("\n--- Testing chunk_text ---")
    long_text = "This is a very long string that needs to be chunked into smaller pieces. Each piece should have a maximum length. We can also test with some overlap to ensure continuity between chunks."
    chunks_no_overlap = chunk_text(long_text, max_tokens=30)
    print(f"Text: '{long_text}'")
    print(f"Chunks (max 30 chars, no overlap): {len(chunks_no_overlap)} chunks")
    for i, chunk in enumerate(chunks_no_overlap):
        print(f"  Chunk {i+1}: '{chunk}' ({len(chunk)} chars)")
    assert len(chunks_no_overlap) == 6
    assert chunks_no_overlap[0] == "This is a very long string th"

    chunks_with_overlap = chunk_text(long_text, max_tokens=30, overlap=10)
    print(f"Chunks (max 30 chars, 10 overlap): {len(chunks_with_overlap)} chunks")
    for i, chunk in enumerate(chunks_with_overlap):
        print(f"  Chunk {i+1}: '{chunk}' ({len(chunk)} chars)")
    assert len(chunks_with_overlap) == 9
    assert chunks_with_overlap[1] == "long string that needs to be"

    print("\n--- Testing safe_get ---")
    data_test = {
        "user": {
            "profile": {
                "name": "Alice",
                "email": "alice@example.com",
                "addresses": [
                    {"type": "home", "street": "123 Main St"},
                    {"type": "work", "street": "456 Oak Ave"}
                ]
            }
        },
        "items": ["apple", "banana"]
    }

    print(f"Data: {json.dumps(data_test, indent=2)}")
    print(f"safe_get('user.profile.name'): {safe_get(data_test, 'user.profile.name')}")
    assert safe_get(data_test, 'user.profile.name') == "Alice"
    print(f"safe_get('user.profile.addresses.0.street'): {safe_get(data_test, 'user.profile.addresses.0.street')}")
    assert safe_get(data_test, 'user.profile.addresses.0.street') == "123 Main St"
    print(f"safe_get('user.profile.addresses.1.type'): {safe_get(data_test, 'user.profile.addresses.1.type')}")
    assert safe_get(data_test, 'user.profile.addresses.1.type') == "work"
    print(f"safe_get('user.profile.addresses.2.street', 'N/A'): {safe_get(data_test, 'user.profile.addresses.2.street', 'N/A')}")
    assert safe_get(data_test, 'user.profile.addresses.2.street', 'N/A') == "N/A"
    print(f"safe_get('items.0'): {safe_get(data_test, 'items.0')}")
    assert safe_get(data_test, 'items.0') == "apple"
    print(f"safe_get('non_existent.path', 'default_val'): {safe_get(data_test, 'non_existent.path', 'default_val')}")
    assert safe_get(data_test, 'non_existent.path', 'default_val') == "default_val"
    print(f"safe_get('user.age'): {safe_get(data_test, 'user.age')}")
    assert safe_get(data_test, 'user.age') is None

    print("\n--- Testing serialize_to_json and deserialize_from_json ---")
    obj_to_serialize = {"name": "Test Item", "value": 123, "active": True, "details": None, "list": [1,2,{"key": "val"}]}
    json_str = serialize_to_json(obj_to_serialize, indent=2)
    print(f"Serialized JSON:\n{json_str}")
    deserialized_obj = deserialize_from_json(json_str)
    print(f"Deserialized object: {deserialized_obj}")
    assert obj_to_serialize == deserialized_obj

    print("\n--- Testing retry_on_exception ---")
    call_count = 0
    class CustomError(Exception):
        pass

    @retry_on_exception(max_retries=3, delay=0.1, exceptions=(CustomError, ValueError))
    def flaky_function_custom_error(should_fail_times: int):
        global call_count
        call_count += 1
        print(f"  Flaky function called (attempt {call_count}), should_fail_times={should_fail_times}")
        if call_count <= should_fail_times:
            raise CustomError("Simulated failure")
        return "Success!"

    print("Scenario 1: Succeeds after 2 retries (3 calls total)")
    call_count = 0
    try:
        result = flaky_function_custom_error(2)
        print(f"Result: {result}")
        assert result == "Success!"
        assert call_count == 3
    except Exception as e:
        print(f"Unexpected exception: {e}")
        assert False

    print("\nScenario 2: Fails after max_retries")
    call_count = 0
    try:
        flaky_function_custom_error(3) # Will fail 3 times, exceeding 3 retries (4 calls total)
        assert False, "Expected CustomError, but function succeeded"
    except CustomError as e:
        print(f"Caught expected exception: {e}")
        assert call_count == 4 # 1 initial call + 3 retries
    except Exception as e:
        print(f"Caught unexpected exception type: {type(e).__name__}")
        assert False

    print("\nScenario 3: No retries needed")
    call_count = 0
    try:
        result = flaky_function_custom_error(0)
        print(f"Result: {result}")
        assert result == "Success!"
        assert call_count == 1
    except Exception as e:
        print(f"Unexpected exception: {e}")
        assert False

    print("\nAll tests passed!")