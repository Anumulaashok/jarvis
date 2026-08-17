"""
Visual problem solver — takes a screenshot, sends to Gemini Vision,
returns a structured action plan.
"""
import base64
import json
import subprocess
import tempfile
from pathlib import Path


def _api_key() -> str:
    try:
        cfg = Path(__file__).resolve().parent.parent.parent / "config" / "api_keys.json"
        return json.loads(cfg.read_text(encoding="utf-8")).get("gemini_api_key", "")
    except Exception:
        return ""


def take_screenshot(path: str = None) -> str:
    """Take a screenshot of the current screen. Returns the file path."""
    import time as _time
    if path:
        save = path
    else:
        # Use ~/Desktop so we know the dir exists; clean up after use
        save = str(Path.home() / "Desktop" / f"jarvis_shot_{int(_time.time())}.png")
    Path(save).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["screencapture", "-x", save], timeout=10, capture_output=True)
    return save


def analyze_screenshot(screenshot_path: str, goal: str = "", context: str = "") -> dict:
    """
    Send screenshot to Gemini Vision and get structured action plan.
    Returns dict with keys: what_i_see, problem, state, actions, ask_user
    """
    api_key = _api_key()
    if not api_key:
        return {"problem": "No API key", "state": "unknown", "actions": [], "ask_user": None}

    try:
        with open(screenshot_path, "rb") as f:
            image_bytes = f.read()
    except Exception as e:
        return {"problem": f"Screenshot read error: {e}", "state": "unknown", "actions": [], "ask_user": None}

    prompt = f"""You are a browser automation assistant analyzing a screenshot to solve a problem.

Goal: {goal or "Complete the current browser task"}
Context: {context or "The browser agent needs help proceeding"}

Look at the screenshot carefully and respond in JSON only (no markdown):
{{
  "what_i_see": "brief description of what is visible on screen",
  "problem": "what is blocking progress or what went wrong",
  "state": "blocked|in_progress|done|needs_input|error",
  "actions": [
    {{"type": "click", "target": "button/link text or description"}},
    {{"type": "type", "target": "field description", "value": "text to type"}},
    {{"type": "navigate", "value": "URL to go to"}},
    {{"type": "wait", "value": "seconds"}},
    {{"type": "scroll", "value": "down or up"}},
    {{"type": "key", "value": "Enter or Tab or Escape"}}
  ],
  "ask_user": "question to ask user if their input is needed, or null",
  "url_pattern": "the domain or URL pattern this problem is associated with",
  "confidence": 0.0
}}

Only include the most relevant 1-3 actions. If the user must provide sensitive info (passwords, card numbers, OTPs), use ask_user instead of trying to fill them."""

    try:
        from google import genai
        from google.genai import types as genai_types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                genai_types.Part.from_text(text=prompt),
            ],
        )
        text = response.text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        return json.loads(text)

    except json.JSONDecodeError as e:
        return {"problem": f"Could not parse Gemini response: {e}", "state": "unknown", "actions": [], "ask_user": None}
    except Exception as e:
        print(f"[VisualSolver] Gemini error: {e}")
        return {"problem": str(e), "state": "unknown", "actions": [], "ask_user": None}


def save_debug_screenshot(label: str = "debug") -> str:
    """Take and save a labeled screenshot to Desktop for manual review."""
    import time
    path = str(Path.home() / "Desktop" / f"jarvis_{label}_{int(time.time())}.png")
    return take_screenshot(path)
