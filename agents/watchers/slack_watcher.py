"""
SlackWatcher: polls Slack every 2 minutes for new messages across all joined channels.
Fires a summary notification when new activity arrives.
"""
import time
from typing import Optional

from agents.watchers.base_watcher import BaseWatcher


class SlackWatcher(BaseWatcher):

    def __init__(self, interval_seconds: int = 120):
        super().__init__(interval_seconds)
        self._last_check: float = 0.0
        self._initialized = False

    @property
    def name(self) -> str:
        return "SlackWatcher"

    def poll(self) -> Optional[str]:
        from integrations.slack_api import is_ready, get_unread_summary
        if not is_ready():
            return None

        if not self._initialized:
            self._last_check  = time.time()
            self._initialized = True
            print(f"[SlackWatcher] Baseline set at {self._last_check:.0f}")
            return None

        since_ts   = self._last_check
        self._last_check = time.time()

        since_minutes = int((time.time() - since_ts) / 60) + 1
        updates = get_unread_summary(since_minutes=since_minutes)

        if not updates:
            return None

        total = sum(u["count"] for u in updates)
        channels = ", ".join(f"#{u['channel']} ({u['count']})" for u in updates[:4])

        if total == 1:
            u = updates[0]
            return f"New Slack message in #{u['channel']}: {u['latest'][:100]}"
        else:
            return (
                f"{total} new Slack messages across {len(updates)} channel(s), sir. "
                f"{channels}."
            )
