"""Thread-safe bout navigation state shared by HTTP and Socket.IO clients."""

from __future__ import annotations

from threading import RLock
from typing import Any

from sumo_data import DIVISION_ORDER, Bout, SumoApiError


SANYAKU_RANKS = ("Yokozuna", "Ozeki", "Sekiwake", "Komusubi")


def _is_sanyaku_bout(bout: Bout) -> bool:
    return any(
        side.rank and side.rank.startswith(SANYAKU_RANKS)
        for side in (bout.east, bout.west)
    )


class BoutController:
    def __init__(self, schedule: Any) -> None:
        self._schedule = schedule
        self._bouts: list[Bout] = schedule.bouts
        if not self._bouts:
            raise SumoApiError("The selected basho day has no bouts")
        self._index = 0
        self._lock = RLock()

    def state(self, include_head_to_head: bool = False) -> dict[str, Any]:
        with self._lock:
            bout = self._bouts[self._index]
            payload = bout.to_payload(self._index, len(self._bouts))
            division_bouts = [
                (index, scheduled_bout)
                for index, scheduled_bout in enumerate(self._bouts)
                if scheduled_bout.division == bout.division
            ]
            division_position = next(
                position
                for position, (index, _) in enumerate(division_bouts, start=1)
                if index == self._index
            )
            division_number = len(DIVISION_ORDER) - DIVISION_ORDER.index(bout.division)
            payload["divisionNumber"] = division_number
            payload["divisionBoutNumber"] = division_position
            payload["divisionTotal"] = len(division_bouts)
            if division_number == 1:
                first_sanyaku = next(
                    (
                        position
                        for position, (_, scheduled_bout) in enumerate(division_bouts, start=1)
                        if _is_sanyaku_bout(scheduled_bout)
                    ),
                    None,
                )
                payload["boutsUntilNextStage"] = (
                    first_sanyaku - division_position
                    if first_sanyaku is not None and division_position < first_sanyaku
                    else None
                )
            else:
                payload["boutsUntilNextStage"] = len(division_bouts) - division_position + 1
            head_to_head = getattr(self._schedule, "head_to_head", None)
            payload["headToHead"] = (
                head_to_head(bout) if include_head_to_head and callable(head_to_head) else None
            )
            wrestler_profiles = getattr(self._schedule, "wrestler_profiles", None)
            if include_head_to_head and callable(wrestler_profiles):
                payload["wrestlerProfiles"] = wrestler_profiles(bout)
            return payload

    @property
    def schedule(self) -> Any:
        with self._lock:
            return self._schedule

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

    def jump_to_division(self, division_number: int) -> dict[str, Any]:
        if division_number not in range(1, len(DIVISION_ORDER) + 1):
            raise ValueError("Division number must be from 1 through 6")
        division = DIVISION_ORDER[len(DIVISION_ORDER) - division_number]
        with self._lock:
            target = next(
                (
                    index
                    for index, bout in enumerate(self._bouts)
                    if bout.division == division
                ),
                None,
            )
            if target is None:
                raise ValueError(f"Division {division_number} is not in the loaded schedule")
            self._index = target
            return self._bouts[self._index].to_payload(self._index, len(self._bouts))

    def select_bout_by_kanji(self, left_name: str, right_name: str) -> dict[str, Any] | None:
        with self._lock:
            find_bout = getattr(self._schedule, "find_bout_index_by_kanji", None)
            if not callable(find_bout):
                return None
            target = find_bout(left_name, right_name)
            if target is None:
                return None
            if not isinstance(target, int) or not 0 <= target < len(self._bouts):
                raise SumoApiError("OCR schedule lookup returned an invalid bout index")
            self._index = target
            return self._bouts[self._index].to_payload(self._index, len(self._bouts))

    def replace_schedule(self, schedule: Any, reset_index: bool = False) -> None:
        with self._lock:
            bouts = schedule.bouts
            if not bouts:
                raise SumoApiError("The selected basho day has no bouts")
            self._schedule = schedule
            self._bouts = bouts
            self._index = 0 if reset_index else min(self._index, len(bouts) - 1)
