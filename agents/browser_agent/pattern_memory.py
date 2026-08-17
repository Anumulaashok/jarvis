"""
Browser pattern memory — stores problems Jarvis has encountered and how it solved them.
Used by the self-healer to apply known fixes instantly without re-asking Gemini.
"""
import json
import time
from pathlib import Path
import sys


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


PATTERNS_PATH = _base_dir() / "memory" / "browser_patterns.json"


def _load() -> list:
    try:
        return json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save(patterns: list) -> None:
    PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PATTERNS_PATH.write_text(json.dumps(patterns, indent=2, ensure_ascii=False), encoding="utf-8")


def find_match(problem_desc: str, url: str = "") -> dict | None:
    """Find a previously learned pattern that matches this problem."""
    patterns = _load()
    problem_lower = problem_desc.lower()
    url_lower = url.lower()

    for p in patterns:
        # Match by URL pattern
        if p.get("url_pattern") and p["url_pattern"] in url_lower:
            return p
        # Match by trigger keywords (majority must match)
        keywords = p.get("trigger_keywords", [])
        if keywords:
            hits = sum(1 for kw in keywords if kw.lower() in problem_lower)
            if hits >= max(1, len(keywords) // 2):
                return p

    return None


def record_success(problem_desc: str, url: str, solution_steps: list, url_pattern: str = "") -> None:
    """Record a successful fix so it can be reused."""
    patterns = _load()

    # Check if pattern already exists → increment count
    for p in patterns:
        if p.get("url_pattern") and p["url_pattern"] and p["url_pattern"] in url.lower():
            p["success_count"] = p.get("success_count", 0) + 1
            p["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            _save(patterns)
            return

    # Extract keywords from problem description
    stop_words = {"a", "an", "the", "is", "was", "to", "in", "on", "at", "it", "i", "and", "or"}
    keywords = [w for w in problem_desc.lower().split() if len(w) > 3 and w not in stop_words][:6]

    patterns.append({
        "id":               f"pattern_{int(time.time())}",
        "problem":          problem_desc[:200],
        "url_pattern":      url_pattern or _extract_domain(url),
        "trigger_keywords": keywords,
        "solution":         solution_steps,
        "success_count":    1,
        "last_seen":        time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    _save(patterns)
    print(f"[BrowserAgent] Learned new pattern: {problem_desc[:60]}")


def list_patterns() -> list:
    return _load()


def _extract_domain(url: str) -> str:
    import re
    m = re.search(r"(?:https?://)?([^/]+)", url)
    return m.group(1) if m else ""
