"""API access and spoiler-safe bout data preparation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import local
from typing import Any, TypeVar
from urllib.parse import quote

import requests


DIVISION_ORDER = ("Jonokuchi", "Jonidan", "Sandanme", "Makushita", "Juryo", "Makuuchi")
LookupValue = TypeVar("LookupValue")


class SumoApiError(RuntimeError):
    """Raised when the sumo-api.com response cannot safely be used."""


def _basho_day_key(match: dict[str, Any], label: str) -> tuple[str, int]:
    basho_id = match.get("bashoId")
    if not isinstance(basho_id, str) or len(basho_id) != 6 or not basho_id.isdigit():
        raise SumoApiError(f"sumo-api.com returned an invalid basho ID in {label}")
    day = _required_int(match.get("day"), f"{label} day")
    if not 1 <= day <= 16:
        raise SumoApiError(f"sumo-api.com returned an invalid {label} day")
    return basho_id, day


def _match_order_key(match: dict[str, Any], label: str) -> tuple[str, int, int, int]:
    basho_id, day = _basho_day_key(match, label)
    division = match.get("division")
    if division not in DIVISION_ORDER:
        raise SumoApiError(f"sumo-api.com returned an invalid division in {label}")
    return (
        basho_id,
        day,
        DIVISION_ORDER.index(division),
        _required_int(match.get("matchNo"), f"{label} match number"),
    )


@dataclass(frozen=True)
class BoutRecord:
    wins: int
    losses: int

    def to_dict(self) -> dict[str, int]:
        return {"wins": self.wins, "losses": self.losses}


@dataclass(frozen=True)
class BoutSide:
    rikishi_id: int
    name: str
    rank: str | None
    record: BoutRecord | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rikishiId": self.rikishi_id,
            "name": self.name,
            "rank": self.rank,
            "record": self.record.to_dict() if self.record else None,
        }


@dataclass(frozen=True)
class Bout:
    match_no: int
    east: BoutSide
    west: BoutSide
    division: str = "Makuuchi"

    def to_payload(self, index: int, total: int) -> dict[str, Any]:
        return {
            "index": index,
            "total": total,
            "matchNo": self.match_no,
            "division": self.division,
            "east": self.east.to_dict(),
            "west": self.west.to_dict(),
        }


def _required_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise SumoApiError(f"sumo-api.com returned an invalid {label}")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise SumoApiError(f"sumo-api.com returned an invalid {label}") from error


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SumoApiError(f"sumo-api.com returned a missing or invalid {label}")
    return value.strip()


def _parse_matches(payload: dict[str, Any], day: int) -> list[dict[str, Any]]:
    matches = payload.get("torikumi")
    if not isinstance(matches, list):
        raise SumoApiError(f"sumo-api.com returned no torikumi list for day {day}")

    parsed: list[dict[str, Any]] = []
    for match in matches:
        if not isinstance(match, dict):
            raise SumoApiError(f"sumo-api.com returned an invalid bout for day {day}")
        parsed.append(match)
    return sorted(parsed, key=lambda match: _required_int(match.get("matchNo"), "match number"))


def _participant_ids(matches: list[dict[str, Any]]) -> set[int]:
    result: set[int] = set()
    for match in matches:
        result.add(_required_int(match.get("eastId"), "east rikishi ID"))
        result.add(_required_int(match.get("westId"), "west rikishi ID"))
    return result


def build_bouts(
    prior_days: list[list[dict[str, Any]]],
    current_day: list[dict[str, Any]],
) -> list[Bout]:
    """Build per-bout records using only outcomes from earlier bouts."""
    wins: dict[int, int] = {}
    losses: dict[int, int] = {}
    known: dict[int, bool] = {}

    def apply_result(match: dict[str, Any]) -> None:
        east_id = _required_int(match.get("eastId"), "east rikishi ID")
        west_id = _required_int(match.get("westId"), "west rikishi ID")
        known.setdefault(east_id, True)
        known.setdefault(west_id, True)

        if match.get("winnerId") is None:
            known[east_id] = False
            known[west_id] = False
            return

        winner_id = _required_int(match.get("winnerId"), "winner ID")
        if winner_id == east_id:
            wins[east_id] = wins.get(east_id, 0) + 1
            losses[west_id] = losses.get(west_id, 0) + 1
        elif winner_id == west_id:
            wins[west_id] = wins.get(west_id, 0) + 1
            losses[east_id] = losses.get(east_id, 0) + 1
        else:
            raise SumoApiError("sumo-api.com winner ID does not match either bout participant")

    for day_matches in prior_days:
        for match in day_matches:
            apply_result(match)

    bouts: list[Bout] = []
    for match in current_day:
        east_id = _required_int(match.get("eastId"), "east rikishi ID")
        west_id = _required_int(match.get("westId"), "west rikishi ID")
        known.setdefault(east_id, True)
        known.setdefault(west_id, True)

        def record_for(rikishi_id: int) -> BoutRecord | None:
            if not known.get(rikishi_id, True):
                return None
            return BoutRecord(wins.get(rikishi_id, 0), losses.get(rikishi_id, 0))

        bouts.append(
            Bout(
                match_no=_required_int(match.get("matchNo"), "match number"),
                east=BoutSide(
                    rikishi_id=east_id,
                    name=_required_text(match.get("eastShikona"), "east English shikona"),
                    rank=match.get("eastRank") if isinstance(match.get("eastRank"), str) else None,
                    record=record_for(east_id),
                ),
                west=BoutSide(
                    rikishi_id=west_id,
                    name=_required_text(match.get("westShikona"), "west English shikona"),
                    rank=match.get("westRank") if isinstance(match.get("westRank"), str) else None,
                    record=record_for(west_id),
                ),
                division=match.get("division", "Makuuchi"),
            )
        )
        apply_result(match)
    return bouts


def _build_kanji_lookup(
    entries: list[tuple[str, LookupValue]],
) -> dict[str, LookupValue]:
    lookup: dict[str, LookupValue] = {}
    ambiguous: set[str] = set()

    for japanese, value in entries:
        ring_name = japanese.split("(", 1)[0].split("（", 1)[0].strip()
        variants = {
            japanese,
            "".join(japanese.split()),
            ring_name,
            "".join(ring_name.split()),
        }
        first_name = ring_name.split()[0] if ring_name.split() else ring_name
        variants.add(first_name)
        variants.add("".join(first_name.split()))

        for variant in variants:
            if not variant:
                continue
            previous = lookup.get(variant)
            if previous is not None and previous != value:
                ambiguous.add(variant)
            else:
                lookup[variant] = value

    for variant in ambiguous:
        lookup.pop(variant, None)
    return lookup


def build_kanji_lookup(profiles: list[dict[str, Any]]) -> dict[str, str]:
    """Map API Japanese shikona variants directly to API English shikona."""
    entries = [
        (
            _required_text(profile.get("shikonaJp"), "Japanese shikona"),
            _required_text(profile.get("shikonaEn"), "English shikona"),
        )
        for profile in profiles
    ]
    return _build_kanji_lookup(entries)


class SumoApiClient:
    def __init__(
        self,
        base_url: str = "https://sumo-api.com/api",
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session
        self._thread_sessions = local()

    def _get_json(self, url: str) -> dict[str, Any]:
        try:
            session = self.session
            if session is None:
                session = getattr(self._thread_sessions, "session", None)
                if session is None:
                    session = requests.Session()
                    self._thread_sessions.session = session
            response = session.get(url, timeout=self.timeout)
            response.raise_for_status()
        except requests.RequestException as error:
            raise SumoApiError(f"Unable to fetch sumo-api.com data: {error}") from error

        try:
            payload = response.json()
        except ValueError as error:
            raise SumoApiError("sumo-api.com returned invalid JSON") from error
        if not isinstance(payload, dict):
            raise SumoApiError("sumo-api.com returned an invalid response")
        return payload

    def fetch_torikumi(self, basho_id: str, division: str, day: int) -> list[dict[str, Any]]:
        division_path = quote(division, safe="")
        payload = self._get_json(
            f"{self.base_url}/basho/{basho_id}/torikumi/{division_path}/{day}"
        )
        matches = _parse_matches(payload, day)
        for match in matches:
            match["division"] = division
        return matches

    def fetch_rikishi(self, rikishi_id: int) -> dict[str, Any]:
        return self._get_json(f"{self.base_url}/rikishi/{rikishi_id}")

    def fetch_rikishi_matches(self, rikishi_id: int) -> list[dict[str, Any]]:
        payload = self._get_json(f"{self.base_url}/rikishi/{rikishi_id}/matches")
        matches = payload.get("records")
        if not isinstance(matches, list) or any(
            not isinstance(match, dict) for match in matches
        ):
            raise SumoApiError("sumo-api.com returned an invalid rikishi match list")
        total = payload.get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise SumoApiError("sumo-api.com returned an invalid rikishi match count")
        if total > len(matches):
            raise SumoApiError(
                "sumo-api.com returned only part of the rikishi match history; "
                "cannot calculate a complete career record"
            )
        return matches

    def fetch_head_to_head(self, rikishi_id: int, opponent_id: int) -> list[dict[str, Any]]:
        payload = self._get_json(
            f"{self.base_url}/rikishi/{rikishi_id}/matches/{opponent_id}"
        )
        matches = payload.get("matches")
        if not isinstance(matches, list) or any(not isinstance(match, dict) for match in matches):
            raise SumoApiError("sumo-api.com returned an invalid head-to-head match list")
        total = payload.get("total")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise SumoApiError("sumo-api.com returned an invalid head-to-head match count")
        if total > len(matches):
            raise SumoApiError(
                "sumo-api.com returned only part of the head-to-head history; "
                "cannot calculate a complete pre-match record"
            )
        return matches


class BashoSchedule:
    """Cached basho history with a refreshable current day for live manual control."""

    def __init__(
        self,
        client: SumoApiClient,
        basho_id: str,
        division: str | None,
        day: int,
    ) -> None:
        self.client = client
        self.basho_id = basho_id
        self.division = division
        self.day = day
        self._divisions = DIVISION_ORDER if division is None else (division,)
        self._h2h_cache: dict[tuple[int, int], list[dict[str, Any]]] = {}
        self._rikishi_cache: dict[int, dict[str, Any]] = {}
        self._career_matches_cache: dict[int, list[dict[str, Any]]] = {}
        self._prior_days = [
            self._fetch_day(current_day)
            for current_day in range(1, day)
        ]
        self._current_day = self._fetch_day(day)
        self._bouts = build_bouts(self._prior_days, self._current_day)
        if not self._bouts:
            raise SumoApiError(f"No bouts were returned for {division}, day {day}")
        self._profile_ids = sorted(_participant_ids(self._current_day))
        self._kanji_lookup: dict[str, str] | None = None
        self._kanji_id_lookup: dict[str, int] | None = None

    def _fetch_day(self, day: int) -> list[dict[str, Any]]:
        if len(self._divisions) == 1:
            matches = self.client.fetch_torikumi(self.basho_id, self._divisions[0], day)
        else:
            with ThreadPoolExecutor(max_workers=len(self._divisions)) as executor:
                responses = executor.map(
                    lambda division: self.client.fetch_torikumi(
                        self.basho_id, division, day
                    ),
                    self._divisions,
                )
                matches = [match for response in responses for match in response]
        order = {division: index for index, division in enumerate(DIVISION_ORDER)}
        return sorted(
            matches,
            key=lambda match: (
                order.get(match.get("division"), len(order)),
                _required_int(match.get("matchNo"), "match number"),
            ),
        )

    @property
    def kanji_lookup(self) -> dict[str, str]:
        self._load_kanji_lookups()
        return self._kanji_lookup or {}

    def _load_kanji_lookups(self) -> None:
        profile_ids = sorted(_participant_ids(self._current_day))
        if profile_ids != self._profile_ids:
            self._profile_ids = profile_ids
            self._kanji_lookup = None
            self._kanji_id_lookup = None
        if self._kanji_lookup is None or self._kanji_id_lookup is None:
            with ThreadPoolExecutor(
                max_workers=min(8, len(self._profile_ids))
            ) as executor:
                profiles = list(executor.map(self._fetch_rikishi, self._profile_ids))
            self._kanji_lookup = build_kanji_lookup(profiles)
            self._kanji_id_lookup = _build_kanji_lookup(
                [
                    (
                        _required_text(profile.get("shikonaJp"), "Japanese shikona"),
                        rikishi_id,
                    )
                    for rikishi_id, profile in zip(self._profile_ids, profiles, strict=True)
                ]
            )

    @property
    def bouts(self) -> list[Bout]:
        return self._bouts

    def refresh(self) -> list[Bout]:
        current_day = self._fetch_day(self.day)
        bouts = build_bouts(self._prior_days, current_day)
        if not bouts:
            raise SumoApiError(f"No bouts were returned for {self.division}, day {self.day}")
        self._current_day = current_day
        self._bouts = bouts
        return self._bouts

    def _fetch_rikishi(self, rikishi_id: int) -> dict[str, Any]:
        if rikishi_id not in self._rikishi_cache:
            self._rikishi_cache[rikishi_id] = self.client.fetch_rikishi(rikishi_id)
        return self._rikishi_cache[rikishi_id]

    def wrestler_profiles(self, bout: Bout) -> dict[str, dict[str, Any]]:
        """Return broadcast-safe profile details for both participants."""
        profiles: dict[str, dict[str, Any]] = {}
        for side, participant in (("east", bout.east), ("west", bout.west)):
            profile = self._fetch_rikishi(participant.rikishi_id)

            def text_field(name: str) -> str | None:
                value = profile.get(name)
                return value.strip() if isinstance(value, str) and value.strip() else None

            def measurement_field(name: str) -> int | float | None:
                value = profile.get(name)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value <= 0
                ):
                    return None
                return value

            profiles[side] = {
                "birthDate": text_field("birthDate"),
                "heya": text_field("heya"),
                "height": measurement_field("height"),
                "weight": measurement_field("weight"),
                "shusshin": text_field("shusshin"),
                "debut": text_field("debut"),
                "lifetime": self._lifetime_record(participant.rikishi_id, bout),
            }
        return profiles

    def _lifetime_record(self, rikishi_id: int, bout: Bout) -> dict[str, int]:
        if rikishi_id not in self._career_matches_cache:
            self._career_matches_cache[rikishi_id] = self.client.fetch_rikishi_matches(
                rikishi_id
            )

        target = (self.basho_id, self.day)
        wins = 0
        losses = 0
        for match in self._career_matches_cache[rikishi_id]:
            match_key = _basho_day_key(match, "rikishi match history")
            if match_key >= target:
                continue
            winner_id = match.get("winnerId")
            if winner_id is None:
                continue
            winner_id = _required_int(winner_id, "rikishi match winner ID")
            east_id = _required_int(match.get("eastId"), "rikishi match east ID")
            west_id = _required_int(match.get("westId"), "rikishi match west ID")
            if rikishi_id not in (east_id, west_id) or winner_id not in (east_id, west_id):
                raise SumoApiError(
                    "sumo-api.com rikishi match does not match the requested wrestler"
                )
            if winner_id == rikishi_id:
                wins += 1
            else:
                losses += 1
        return {"wins": wins, "losses": losses, "matches": wins + losses}

    def head_to_head(self, bout: Bout) -> dict[str, Any]:
        pair = (
            min(bout.east.rikishi_id, bout.west.rikishi_id),
            max(bout.east.rikishi_id, bout.west.rikishi_id),
        )
        if pair not in self._h2h_cache:
            self._h2h_cache[pair] = self.client.fetch_head_to_head(*pair)

        target = (
            self.basho_id,
            self.day,
            DIVISION_ORDER.index(bout.division) if bout.division in DIVISION_ORDER else len(DIVISION_ORDER),
            bout.match_no,
        )
        east_wins = 0
        west_wins = 0
        history: list[str] = []
        prior_matches: list[tuple[tuple[str, int, int, int], int | None]] = []
        for match in self._h2h_cache[pair]:
            match_key = _match_order_key(match, "head-to-head history")
            if match_key >= target:
                continue
            winner_id = match.get("winnerId")
            if winner_id is not None:
                winner_id = _required_int(winner_id, "head-to-head winner ID")
                if winner_id not in (bout.east.rikishi_id, bout.west.rikishi_id):
                    raise SumoApiError(
                        "sumo-api.com head-to-head winner is not one of the bout participants"
                    )
            prior_matches.append((match_key, winner_id))

        for _, winner_id in sorted(prior_matches, key=lambda item: item[0]):
            if winner_id == bout.east.rikishi_id:
                east_wins += 1
                history.append("east")
            elif winner_id == bout.west.rikishi_id:
                west_wins += 1
                history.append("west")

        return {
            "eastWins": east_wins,
            "westWins": west_wins,
            "total": east_wins + west_wins,
            "history": history,
        }

    def find_bout_index_by_kanji(self, left_name: str, right_name: str) -> int | None:
        """Resolve one or both OCR-read Japanese names to one scheduled bout."""
        self._load_kanji_lookups()
        lookup = self._kanji_id_lookup or {}
        detected_ids: set[int] = set()
        for name in (left_name, right_name):
            if name:
                rikishi_id = lookup.get(name) or lookup.get("".join(name.split()))
                if rikishi_id is not None:
                    detected_ids.add(rikishi_id)
        if not detected_ids:
            return None

        matches = [
            index
            for index, bout in enumerate(self._bouts)
            if detected_ids & {bout.east.rikishi_id, bout.west.rikishi_id}
        ]
        return matches[0] if len(matches) == 1 else None
