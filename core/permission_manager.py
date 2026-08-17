"""Session-level permission manager.

Sensitive tools require one-time user consent per session.
Once granted, the permission is cached for the lifetime of the session.
Once denied, the tool is blocked for the session unless reset.
"""

import threading
from typing import Callable

# tool_name → (display_name, description shown to user)
PERMISSION_REQUIRED: dict[str, tuple[str, str]] = {
    "system_control":   ("System Control",   "Shutdown, restart, sleep, or lock your computer"),
    "send_message":     ("Send Message",     "Send messages on your behalf via WhatsApp, Telegram, or Discord"),
    "computer_control": ("Computer Control", "Control your mouse and keyboard"),
    "file_controller":  ("File System",      "Create, read, modify, move, or delete files on your computer"),
    "dev_agent":        ("Dev Agent",        "Write and execute code projects on your computer"),
    "generated_code":   ("Code Execution",   "Generate and run custom Python code on your computer"),
    "email_reader":     ("Email Access",     "Read your Gmail inbox"),
    "slack_reader":     ("Slack Access",     "Read your Slack messages"),
    "browser_control":  ("Browser Control",  "Automate and control your web browser"),
    "claude_dev":       ("Claude Dev Agent", "Run Claude Code CLI in your repos — write code, git push, create PRs, send Slack updates"),
}


class PermissionManager:
    """Tracks granted/denied permissions for the current session."""

    def __init__(self):
        self._granted: set[str] = set()
        self._denied:  set[str] = set()
        self._lock = threading.Lock()
        self._request_fn: Callable[[str, str, str], bool] | None = None

    def set_request_fn(self, fn: Callable[[str, str, str], bool]) -> None:
        """Register the UI callback (tool, display_name, description) → bool."""
        self._request_fn = fn

    def needs_permission(self, tool: str) -> bool:
        return tool in PERMISSION_REQUIRED

    def is_granted(self, tool: str) -> bool:
        with self._lock:
            return tool in self._granted

    def is_denied(self, tool: str) -> bool:
        with self._lock:
            return tool in self._denied

    def request(self, tool: str) -> bool:
        """
        Request permission for a tool.
        - Returns True immediately if already granted or not in the sensitive list.
        - Returns False immediately if already denied.
        - Otherwise blocks the calling thread and shows a UI prompt.
        """
        if tool not in PERMISSION_REQUIRED:
            return True

        with self._lock:
            if tool in self._granted:
                return True
            if tool in self._denied:
                return False

        if self._request_fn is None:
            return True

        display_name, description = PERMISSION_REQUIRED[tool]
        allowed = self._request_fn(tool, display_name, description)

        with self._lock:
            if allowed:
                self._granted.add(tool)
            else:
                self._denied.add(tool)

        return allowed

    def reset(self) -> None:
        """Clear all cached decisions (call on session restart)."""
        with self._lock:
            self._granted.clear()
            self._denied.clear()


_instance: PermissionManager | None = None


def get_permission_manager() -> PermissionManager:
    global _instance
    if _instance is None:
        _instance = PermissionManager()
    return _instance
