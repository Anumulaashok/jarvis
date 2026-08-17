"""
Reads Slack. Uses Slack API (bot token) if configured, otherwise falls back to Chrome via AppleScript.
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
SLACK_URL       = "https://app.slack.com"


def _chrome_read(url: str, wait: int = 5) -> str:
    """Navigate to URL in existing Chrome, wait for load, read via clipboard."""
    subprocess.run([
        "osascript",
        "-e", 'tell application "Google Chrome"',
        "-e", "activate",
        "-e", f'open location "{url}"',
        "-e", "end tell",
    ], capture_output=True)
    time.sleep(wait)

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


def slack_reader(parameters: dict, player=None, speak=None) -> str:
    action  = parameters.get("action", "summary")
    minutes = int(parameters.get("minutes", 30))

    # Try Slack API first
    try:
        from integrations.slack_api import is_ready, get_unread_summary, get_channels, get_recent_messages
        if is_ready():
            if player:
                player.write_log("SYS: Reading Slack via API…")

            updates = get_unread_summary(since_minutes=minutes)
            if not updates:
                return f"No new Slack messages in the last {minutes} minutes, sir."

            total = sum(u["count"] for u in updates)
            lines = [f"#{u['channel']}: {u['count']} new — {u['latest'][:80]}" for u in updates]
            summary = f"{total} new Slack messages across {len(updates)} channel(s), sir.\n" + "\n".join(lines)

            if action == "summary":
                return _ai_format(summary, "Give a brief digest of these Slack updates. Highlight anything that needs attention. Address as 'sir'.")
            return summary
    except Exception as e:
        print(f"[SlackReader] API error: {e}, falling back to Chrome")

    # Fallback: Chrome
    if player:
        player.write_log("SYS: Reading Slack in existing Chrome…")
    raw = _chrome_read(SLACK_URL, wait=5)
    if not raw:
        return "Could not read Slack. Make sure Chrome is open and you are logged into Slack."
    if "sign in" in raw.lower():
        return "Please log into Slack in Chrome first."
    return _ai_format(raw, "Give a brief summary of recent Slack messages. Highlight important items. Bullet points. Address as 'sir'.")
