"""
Mick's learned email preferences — persisted in memory/mick_preferences.json.
"""
import json
import sys
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent.parent


PREFS_PATH = _base_dir() / "memory" / "mick_preferences.json"

_DEFAULT = {
    "ignored_senders":      [],   # substrings matched against From header
    "ignored_keywords":     [],   # subject/snippet keywords → auto-archive
    "important_senders":    [],   # always report immediately
    "auto_reply_patterns":  [],   # {"keyword": "...", "reply": "..."}
    "learned_labels":       {},   # sender_domain → "ignore"|"report"|"reply"
}


def load() -> dict:
    try:
        data = json.loads(PREFS_PATH.read_text(encoding="utf-8"))
        # merge with defaults so new keys always exist
        for k, v in _DEFAULT.items():
            data.setdefault(k, v)
        return data
    except Exception:
        return _DEFAULT.copy()


def save(prefs: dict) -> None:
    PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREFS_PATH.write_text(json.dumps(prefs, indent=2, ensure_ascii=False), encoding="utf-8")


def add_ignored(sender_or_domain: str) -> None:
    p = load()
    s = sender_or_domain.lower().strip()
    if s not in p["ignored_senders"]:
        p["ignored_senders"].append(s)
    d = _domain(sender_or_domain)
    if d:
        p["learned_labels"][d] = "ignore"
    save(p)


def add_important(sender_or_domain: str) -> None:
    p = load()
    s = sender_or_domain.lower().strip()
    if s not in p["important_senders"]:
        p["important_senders"].append(s)
    d = _domain(sender_or_domain)
    if d:
        p["learned_labels"][d] = "report"
    save(p)


def add_keyword_ignore(keyword: str) -> None:
    p = load()
    kw = keyword.lower().strip()
    if kw not in p["ignored_keywords"]:
        p["ignored_keywords"].append(kw)
    save(p)


def learn_label(sender_or_domain: str, label: str) -> None:
    """label: 'ignore' | 'report' | 'reply'"""
    p = load()
    d = _domain(sender_or_domain) or sender_or_domain.lower().strip()
    p["learned_labels"][d] = label
    save(p)


def should_ignore(email: dict) -> bool:
    p = load()
    from_str = email.get("from", "").lower()
    subject  = email.get("subject", "").lower()
    snippet  = email.get("snippet", "").lower()

    for s in p["ignored_senders"]:
        if s in from_str:
            return True

    d = _domain(from_str)
    if d and p["learned_labels"].get(d) == "ignore":
        return True

    for kw in p["ignored_keywords"]:
        if kw in subject or kw in snippet:
            return True

    return False


def is_important(email: dict) -> bool:
    p = load()
    from_str = email.get("from", "").lower()
    for s in p["important_senders"]:
        if s in from_str:
            return True
    d = _domain(from_str)
    return bool(d and p["learned_labels"].get(d) == "report")


def _domain(text: str) -> str:
    """Extract domain from email address or raw string."""
    import re
    m = re.search(r"@([\w.\-]+)", text)
    return m.group(1).lower() if m else ""
