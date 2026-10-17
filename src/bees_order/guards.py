"""Abuse limits for the public demo (spec R16, Phase 3 clarifications).

- Per session: only messages that call the LLM count (clarify answers and buttons are free).
- Global: actual OpenRouter API calls per UTC day, counted by the parser's on_call hook.
The US$10 hard cap on the key is the last line behind both.
"""

import os
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime


class DailyCallCap:
    def __init__(self, cap: int | None = None, today: Callable[[], date] | None = None):
        self.cap = cap if cap is not None else int(os.getenv("DAILY_LLM_CALL_CAP", "2000"))
        self._today = today or (lambda: datetime.now(UTC).date())
        self._lock = threading.Lock()
        self._day = self._today()
        self._count = 0

    def _roll(self) -> None:
        if self._today() != self._day:
            self._day, self._count = self._today(), 0

    def record(self) -> None:
        with self._lock:
            self._roll()
            self._count += 1

    def exhausted(self) -> bool:
        with self._lock:
            self._roll()
            return self._count >= self.cap

    @property
    def count(self) -> int:
        with self._lock:
            self._roll()
            return self._count


class Limits:
    def __init__(self, session_limit: int | None = None, daily: DailyCallCap | None = None):
        self.session_limit = (session_limit if session_limit is not None
                              else int(os.getenv("SESSION_MESSAGE_LIMIT", "40")))
        self.daily = daily or DailyCallCap()
