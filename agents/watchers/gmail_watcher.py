"""
GmailWatcher: polls Gmail every 3 minutes, hands new emails to Mick for processing.
"""
import time
from typing import Optional

from agents.watchers.base_watcher import BaseWatcher


class GmailWatcher(BaseWatcher):

    def __init__(self, interval_seconds: int = 180):
        super().__init__(interval_seconds)
        self._last_ids: set[str] = set()
        self._initialized = False

    @property
    def name(self) -> str:
        return "GmailWatcher"

    def _persist(self, ids: set) -> None:
        try:
            from integrations.pg_store import set_watcher_state, is_ready as pg_ready
            if pg_ready():
                set_watcher_state("gmail", time.time(), list(ids))
        except Exception:
            pass

    def poll(self) -> Optional[str]:
        from integrations.gmail import is_ready, get_unread_emails
        if not is_ready():
            return None

        if not self._initialized:
            try:
                from integrations.pg_store import get_watcher_state, is_ready as pg_ready
                if pg_ready():
                    state = get_watcher_state("gmail")
                    self._last_ids = set(state.get("last_ids", []))
            except Exception:
                pass
            self._initialized = True

        emails = get_unread_emails(max_results=20)
        current_ids = {e["id"] for e in emails}

        if not self._last_ids:
            # First run — baseline, no notifications
            self._last_ids = current_ids
            self._persist(current_ids)
            print(f"[GmailWatcher] Baseline: {len(current_ids)} unread emails")
            return None

        new_ids = current_ids - self._last_ids
        self._last_ids = current_ids
        self._persist(current_ids)

        if not new_ids:
            return None

        new_emails = [e for e in emails if e["id"] in new_ids]
        print(f"[GmailWatcher] {len(new_emails)} new email(s) → handing to Mick")

        # Hand off to Mick — he routes through the orchestrator
        try:
            from agents.mick.mick_agent import get_mick
            get_mick().process_new_emails(new_emails)
        except Exception as e:
            print(f"[GmailWatcher] Mick error: {e}")
            # Fallback: return raw notification
            if len(new_emails) == 1:
                e0 = new_emails[0]
                sender = e0["from"].split("<")[0].strip().strip('"')
                return f"Mick has an update. New email from {sender}: \"{e0['subject']}\"."
            return f"Mick has an update. {len(new_emails)} new emails have arrived, sir."

        # Mick handles its own speak() calls — return None so watcher doesn't double-announce
        return None
