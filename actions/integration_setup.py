"""
integration_setup action: set credentials, trigger OAuth, check status.
Called by the persona when user says things like "set up Gmail" or "show integration status".
"""
import json
import sys
from pathlib import Path

def get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def integration_setup(parameters: dict, player=None, speak=None) -> str:
    action  = parameters.get("action", "status")
    service = parameters.get("service", "").lower().strip()
    key     = parameters.get("key", "").strip()
    value   = parameters.get("value", "").strip()

    from integrations.manager import (
        set_credential, get_status, is_configured, INTEGRATIONS
    )

    if action == "status":
        statuses = get_status()
        lines = []
        for svc, info in statuses.items():
            icon = "✓" if info["ready"] else ("~" if info["configured"] else "✗")
            lines.append(f"{icon} {info['label']}: {'ready' if info['ready'] else 'not configured'}")
        return "Integration status, sir:\n" + "\n".join(lines)

    elif action == "set":
        if not service or not key or not value:
            return "Please provide service, key, and value to set a credential."
        set_credential(service, key, value)
        if player:
            player.write_log(f"SYS: {service}/{key} saved.")
        return f"{service} credential '{key}' saved, sir."

    elif action == "auth":
        if service == "gmail":
            if not is_configured("gmail"):
                return "Gmail not configured yet. Please set google_client_id and google_client_secret first."
            if speak:
                speak("Opening browser for Gmail authorization, sir. Please complete the sign-in.")
            from integrations.gmail import auth
            return auth()
        elif service == "slack":
            from integrations.slack_api import test_connection
            result = test_connection()
            return result
        else:
            return f"No auth flow defined for '{service}', sir."

    elif action == "list":
        lines = []
        for svc, info in INTEGRATIONS.items():
            lines.append(f"• {info['label']}: {info['description']}")
        return "Available integrations, sir:\n" + "\n".join(lines)

    else:
        return f"Unknown integration action '{action}'. Use: status, set, auth, list."
