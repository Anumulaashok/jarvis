"""
WatcherManager: starts and manages all background watcher agents.
Routes notifications through the active persona's speak function.
"""
import threading
from typing import Callable, Optional

from agents.watchers.gmail_watcher import GmailWatcher
from agents.watchers.slack_watcher import SlackWatcher


class WatcherManager:

    def __init__(self):
        self._speak_fn: Optional[Callable[[str], None]] = None
        self._lock     = threading.Lock()
        self._watchers = [
            GmailWatcher(interval_seconds=300),   # check email every 5 min
            SlackWatcher(interval_seconds=120),   # check Slack every 2 min
        ]

    def set_speak(self, fn: Callable[[str], None]) -> None:
        """Wire the speak callback — called whenever a watcher has a notification."""
        with self._lock:
            self._speak_fn = fn
        for w in self._watchers:
            w.set_callback(self._dispatch)
        # Wire Mick so his speak calls also route through the orchestrator
        try:
            from agents.mick.mick_agent import get_mick
            get_mick().set_speak(fn)
        except Exception as e:
            print(f"[WatcherManager] Mick wire error: {e}")

    def _dispatch(self, message: str) -> None:
        with self._lock:
            fn = self._speak_fn
        if fn:
            try:
                fn(message)
            except Exception as e:
                print(f"[WatcherManager] ⚠ Dispatch error: {e}")

    def start_all(self) -> None:
        for w in self._watchers:
            w.set_callback(self._dispatch)
            w.start()
        print(f"[WatcherManager] ✅ {len(self._watchers)} watchers running")

    def stop_all(self) -> None:
        for w in self._watchers:
            w.stop()

    def status(self) -> list[dict]:
        return [
            {
                "name":    w.name,
                "running": w._thread is not None and w._thread.is_alive(),
            }
            for w in self._watchers
        ]


# Singleton
_manager: Optional[WatcherManager] = None
_manager_lock = threading.Lock()


def get_watcher_manager() -> WatcherManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = WatcherManager()
    return _manager
