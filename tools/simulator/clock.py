"""Time, injectable so the state machine can be tested without sleeping."""
from __future__ import annotations

import time


class Clock:
    """Monotonic time for durations, wall time for the epoch fields."""

    def now(self) -> float:
        return time.monotonic()

    def wall(self) -> float:
        return time.time()


class ManualClock(Clock):
    """A clock that only moves when told to."""

    def __init__(self, start: float = 1000.0, wall: float = 1_790_000_000.0):
        self._now = start
        self._wall = wall

    def now(self) -> float:
        return self._now

    def wall(self) -> float:
        return self._wall

    def advance(self, seconds: float) -> None:
        self._now += seconds
        self._wall += seconds
