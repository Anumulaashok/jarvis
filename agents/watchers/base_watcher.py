"""
BaseWatcher: abstract background thread that polls a service and fires
on_update(update: str) when something new arrives.
"""
import threading
import time
from abc import ABC, abstractmethod
from typing import Callable, Optional


class BaseWatcher(ABC):

    def __init__(self, interval_seconds: int = 300):
        self._interval   = interval_seconds
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._on_update: Optional[Callable[[str], None]] = None

    @property
    def name(self) -> str:
        return self.__class__.__name__

    def set_callback(self, callback: Callable[[str], None]) -> None:
        self._on_update = callback

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name=self.name
        )
        self._thread.start()
        print(f"[Watcher] ▶ {self.name} started (every {self._interval}s)")

    def stop(self) -> None:
        self._stop_event.set()
        print(f"[Watcher] ■ {self.name} stopped")

    def _run(self) -> None:
        # First run after a short delay so app can finish starting up
        time.sleep(10)
        while not self._stop_event.is_set():
            try:
                update = self.poll()
                if update and self._on_update:
                    self._on_update(update)
            except Exception as e:
                print(f"[Watcher] ⚠ {self.name} error: {e}")
            self._stop_event.wait(timeout=self._interval)

    @abstractmethod
    def poll(self) -> Optional[str]:
        """Check the service. Return a notification string or None if nothing new."""
        ...
