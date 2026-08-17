"""
Reads Gmail. Uses Gmail API (OAuth) if configured, otherwise falls back to existing Chrome via AppleScript.
"""
import json
import subprocess
import time
from pathlib import Path
import sys


def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
GMAIL_INBOX     = "https://mail.google.com/mail/u/0/#inbox"


def _chrome_read(url: str, wait: int = 4) -> str:
    """Navigate to URL in existing Chrome, wait for load, read via clipboard."""
    # Navigate
    subprocess.run([
        "osascript",
        "-e", 'tell application "Google Chrome"',
        "-e", "activate",
        "-e", f'open location "{url}"',
        "-e", "end tell",
    ], capture_output=True)
    time.sleep(wait)

    # Select all + copy → read clipboard (no JS permission needed)
    r = subprocess.run([
        "osascript",
        "-e", 'tell application "Google Chrome" to activate',
        "-e", 'delay 0.3',
        "-e", 'tell application "System Events" to keystroke "a" using {command down}',
        "-e", 'delay 0.3',
        "-e", 'tell application "System Events" to keystroke "c" using {command down}',
        "-e", 'delay 0.3',
        "-e", 'do shell script "pbpaste"',
    ], capture_output=True, text=True, timeout=20)
    return r.stdout.strip()


def _ai_format(raw: str, prompt: str) -> str:
    import google.generativeai as genai
    try:
        with open(API_CONFIG_PATH) as f:
            api_key = json.load(f).get("gemini_api_key", "")
    except Exception:
        api_key = ""
    if not api_key:
        return raw[:1000]
    genai.configure(api_key=api_key)
    return genai.GenerativeModel("gemini-2.5-flash-lite").generate_content(
        f"{prompt}\n\n{raw[:5000]}"
    ).text.strip()


def email_reader(parameters: dict, player=None, speak=None) -> str:
    action = parameters.get("action", "check")
    count  = int(parameters.get("count", 10))

    # Try Gmail API first
    try:
        from integrations.gmail import is_ready, get_unread_emails
        if is_ready():
            if player:
                player.write_log("SYS: Reading Gmail via API…")
            emails = get_unread_emails(max_results=count)
            if not emails:
                return "No unread emails in your inbox, sir."

            lines = []
            for i, e in enumerate(emails, 1):
                sender = e["from"].split("<")[0].strip().strip('"')
                lines.append(f"{i}. From {sender}: \"{e['subject']}\" — {e['snippet'][:80]}")

            if action == "summary":
                return _ai_format(
                    "\n".join(lines),
                    f"Summarize these {count} unread emails. Highlight urgent items. Use bullet points. Address the user as 'sir'."
                )
            return f"You have {len(emails)} unread emails, sir.\n" + "\n".join(lines)
    except Exception as e:
        print(f"[EmailReader] API error: {e}, falling back to Chrome")

    # Fallback: Chrome scraping
    if player:
        player.write_log("SYS: Reading Gmail in existing Chrome…")

    raw = _chrome_read(GMAIL_INBOX, wait=4)
    if not raw:
        return "Could not read Gmail. Make sure Chrome is open and you are logged into Gmail."
    if "Sign in" in raw:
        return "Please log into Gmail in Chrome first."

    if action == "check":
        return _ai_format(raw, f"List {count} most recent unread emails. Sender, subject, 1-line snippet each. Address as 'sir'.")
    return _ai_format(raw, f"Summarize {count} most recent emails. Highlight urgent items. Bullet points. Address as 'sir'.")
