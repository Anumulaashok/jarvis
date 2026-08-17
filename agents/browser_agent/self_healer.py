"""
Self-healing browser agent.

When a browser task gets stuck or fails:
  1. Take a screenshot
  2. Check pattern memory for known fix
  3. If known: apply fix immediately
  4. If unknown: ask Gemini Vision to analyse and suggest actions
  5. Execute suggested actions
  6. If successful: record the pattern for next time
  7. If needs user input: surface question via speak() callback

The healer is used by MacOSChromeSession to wrap any action that may get stuck.
"""
import time
from typing import Callable, Optional


MAX_RETRIES = 4


def heal(
    goal: str,
    session,                        # MacOSChromeSession instance
    speak: Optional[Callable] = None,
    context: str = "",
) -> str:
    """
    Self-healing loop:
      1. Read current page state (text-based, fast)
      2. If recognisable situation → apply fix immediately
      3. Otherwise → take screenshot + ask Gemini Vision
      4. Execute actions, re-check, repeat
      5. On success → record pattern; on failure → ask user
    """
    from agents.browser_agent.visual_solver import take_screenshot, analyze_screenshot
    from agents.browser_agent.pattern_memory import find_match, record_success
    from actions.browser_control import _summarise_page_state

    url = session.get_url()
    print(f"[SelfHealer] Starting heal. Goal: {goal[:60]} | URL: {url}")

    for attempt in range(1, MAX_RETRIES + 1):
        print(f"[SelfHealer] Attempt {attempt}/{MAX_RETRIES}")

        # ── Step 1: understand current state from page text ───────────────────
        page_text  = session.get_text()
        page_state = _summarise_page_state(url, page_text)
        print(f"[SelfHealer] Page state: {page_state[:120]}")

        # ── Step 2: check known patterns ──────────────────────────────────────
        known = find_match(page_state, url)
        if known:
            print(f"[SelfHealer] Known pattern matched: {known['id']}")
            result = _execute_actions(known["solution"], session)
            time.sleep(2)
            new_url = session.get_url()
            if new_url != url:
                url = new_url
                continue
            known["success_count"] = known.get("success_count", 0) + 1
            return f"Applied known fix. {page_state}"

        # ── Step 3: handle recognisable states without screenshot ─────────────
        if "LOGIN PAGE" in page_state:
            if speak:
                speak("Sir, I've hit a login page. Could you sign in and let me know when done?")
            return f"NEEDS_INPUT: Please sign in. {page_state}"

        if "2FA" in page_state or "OTP" in page_state:
            if speak:
                speak("Sir, a verification code is required. Please enter it.")
            return f"NEEDS_INPUT: Enter verification code. {page_state}"

        if "CAPTCHA" in page_state:
            if speak:
                speak("Sir, there's a captcha. Please solve it and I'll continue.")
            return f"NEEDS_INPUT: Solve captcha. {page_state}"

        if "PAYMENT" in page_state or "BILLING FORM" in page_state:
            if speak:
                speak("Sir, I can see the billing form. Please give me your card details and I'll fill them in.")
            return f"NEEDS_INPUT: {page_state}"

        # ── Step 4: no text-match → use Gemini Vision (screenshot) ───────────
        screenshot = take_screenshot()
        analysis   = analyze_screenshot(
            screenshot,
            goal=goal,
            context=f"Current URL: {url}\nPage state: {page_state}\nContext: {context}",
        )

        what_i_see = analysis.get("what_i_see", "")
        problem    = analysis.get("problem", "")
        state      = analysis.get("state", "unknown")
        actions    = analysis.get("actions", [])
        ask_user   = analysis.get("ask_user")
        url_pat    = analysis.get("url_pattern", "")

        print(f"[SelfHealer] Vision State={state} | Problem: {problem[:80]}")

        # ── Already done ──────────────────────────────────────────────────────
        if state == "done":
            record_success(problem or "task completed", url, actions, url_pat)
            return f"Task completed. {what_i_see}"

        # ── Needs user input ──────────────────────────────────────────────────
        if ask_user or state == "needs_input":
            question = ask_user or "I need your input to proceed, sir."
            if speak:
                speak(f"Sir, I'm looking at the screen. {question}")
            return f"NEEDS_INPUT: {question}"

        # ── Execute Gemini-suggested actions ──────────────────────────────────
        if actions:
            result = _execute_actions(actions, session)
            time.sleep(2)
            new_url = session.get_url()

            # Check if we made progress
            if new_url != url:
                record_success(problem, url, actions, url_pat)
                print(f"[SelfHealer] Progress made — URL changed to {new_url[:60]}")
                url = new_url
                continue  # Re-analyze new state

            if "error" not in result.lower():
                record_success(problem, url, actions, url_pat)
                return f"Resolved: {problem[:60]}"

        # ── No progress ───────────────────────────────────────────────────────
        if attempt == MAX_RETRIES:
            if speak:
                speak(f"Sir, I've tried {MAX_RETRIES} times but I'm stuck. {problem}. Could you help?")
            return f"STUCK: {problem}"

        time.sleep(2)

    return "Healing loop exhausted without resolution."


def _execute_actions(actions: list, session) -> str:
    """Execute a list of action dicts on the browser session."""
    results = []
    for act in actions:
        t = act.get("type", "")
        try:
            if t == "click":
                r = session.smart_click(act.get("target", ""))
            elif t == "type":
                r = session.smart_type(act.get("target", ""), act.get("value", ""))
            elif t == "navigate":
                r = session.go_to(act.get("value", ""))
            elif t == "wait":
                secs = float(act.get("value", 2))
                time.sleep(min(secs, 10))
                r = f"Waited {secs}s"
            elif t == "scroll":
                r = session.scroll(act.get("value", "down"))
            elif t == "key":
                r = _press_key(act.get("value", ""))
            else:
                r = f"Unknown action type: {t}"
        except Exception as e:
            r = f"Error executing {t}: {e}"
        print(f"[SelfHealer] {t} → {r}")
        results.append(r)
        time.sleep(0.5)

    return " | ".join(results)


def _press_key(key: str) -> str:
    import subprocess
    key_map = {
        "enter": "return", "tab": "tab", "escape": "escape",
        "space": "space", "arrowdown": "down arrow", "arrowup": "up arrow",
    }
    k = key_map.get(key.lower(), key.lower())
    subprocess.run([
        "osascript",
        "-e", 'tell application "System Events"',
        "-e", f'keystroke "{k}"',
        "-e", "end tell",
    ], capture_output=True)
    return f"Pressed {key}"
