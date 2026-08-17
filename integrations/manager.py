"""
Integration Manager — stores credentials, tracks auth state, provides service registry.
All credentials live in config/integrations.json (never committed).
OAuth tokens live in config/tokens/{service}.json.
"""
import json
import sys
from pathlib import Path
from typing import Optional


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR    = _base_dir()
CREDS_PATH  = BASE_DIR / "config" / "integrations.json"
TOKENS_DIR  = BASE_DIR / "config" / "tokens"

INTEGRATIONS = {
    "gmail": {
        "label":       "Gmail",
        "description": "Read and monitor your Gmail inbox",
        "required":    ["google_client_id", "google_client_secret"],
        "optional":    [],
        "scopes":      ["https://www.googleapis.com/auth/gmail.readonly"],
    },
    "slack": {
        "label":       "Slack",
        "description": "Read messages and notifications from Slack",
        "required":    ["slack_bot_token"],
        "optional":    ["slack_default_channel"],
    },
    "google_calendar": {
        "label":       "Google Calendar",
        "description": "Read your upcoming events and reminders",
        "required":    ["google_client_id", "google_client_secret"],
        "optional":    [],
        "scopes":      ["https://www.googleapis.com/auth/calendar.readonly"],
    },
    "github": {
        "label":       "GitHub",
        "description": "Monitor PRs, issues and notifications",
        "required":    ["github_token"],
        "optional":    ["github_username"],
    },
    "notion": {
        "label":       "Notion",
        "description": "Read pages and databases from Notion",
        "required":    ["notion_token"],
        "optional":    [],
    },
    "linear": {
        "label":       "Linear",
        "description": "Track issues and project updates",
        "required":    ["linear_api_key"],
        "optional":    [],
    },
    "jira": {
        "label":       "Jira",
        "description": "Track issues and sprints",
        "required":    ["jira_url", "jira_email", "jira_api_token"],
        "optional":    [],
    },
}


def _load() -> dict:
    try:
        return json.loads(CREDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def set_credential(service: str, key: str, value: str) -> None:
    data = _load()
    if service not in data:
        data[service] = {}
    data[service][key] = value
    _save(data)


def get_credential(service: str, key: str) -> Optional[str]:
    return _load().get(service, {}).get(key)


def get_service_creds(service: str) -> dict:
    return _load().get(service, {})


def is_configured(service: str) -> bool:
    """Returns True if all required credentials are present."""
    info  = INTEGRATIONS.get(service, {})
    creds = _load().get(service, {})
    return all(creds.get(k) for k in info.get("required", []))


def is_authed(service: str) -> bool:
    """Returns True if the service has valid OAuth tokens (where applicable)."""
    token_file = TOKENS_DIR / f"{service}.json"
    return token_file.exists() and token_file.stat().st_size > 10


def get_status() -> dict:
    """Returns status of all integrations."""
    result = {}
    for svc, info in INTEGRATIONS.items():
        configured = is_configured(svc)
        authed     = is_authed(svc) if configured else False
        result[svc] = {
            "label":      info["label"],
            "configured": configured,
            "authed":     authed,
            "ready":      configured and (authed or "token" not in str(info.get("required", []))),
        }
    return result


def save_token(service: str, token_data: dict) -> None:
    TOKENS_DIR.mkdir(parents=True, exist_ok=True)
    (TOKENS_DIR / f"{service}.json").write_text(
        json.dumps(token_data, indent=2), encoding="utf-8"
    )


def load_token(service: str) -> Optional[dict]:
    path = TOKENS_DIR / f"{service}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def remove_token(service: str) -> None:
    path = TOKENS_DIR / f"{service}.json"
    try:
        path.unlink()
    except Exception:
        pass
