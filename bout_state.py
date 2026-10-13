"""Thread-safe bout navigation state shared by HTTP and Socket.IO clients."""

from __future__ import annotations

from threading import RLock
from typing import Any

from sumo_data import Bout, SumoApiError


class BoutController:
    def __init__(self, schedule: Any) -> None:
        self._schedule = schedule
        self._bouts: list[Bout] = schedule.bouts
        if not self._bouts:
            raise SumoApiError("The selected basho day has no bouts")
        self._index = 0
        self._lock = RLock()

    def state(self) -> dict[str, Any]:
        with self._lock:
            return self._bouts[self._index].to_payload(self._index, len(self._bouts))

    def move(self, offset: int) -> dict[str, Any]:
        with self._lock:
            target = min(max(self._index + offset, 0), len(self._bouts) - 1)
            if target == self._index:
                return self._bouts[self._index].to_payload(self._index, len(self._bouts))

            refreshed_bouts = self._schedule.refresh()
            if not refreshed_bouts:
                raise SumoApiError("The selected basho day has no bouts")
            self._bouts = refreshed_bouts
            self._index = min(target, len(self._bouts) - 1)
            return self._bouts[self._index].to_payload(self._index, len(self._bouts))
