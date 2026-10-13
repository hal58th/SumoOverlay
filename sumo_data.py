"""API access and spoiler-safe bout data preparation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests


class SumoApiError(RuntimeError):
    """Raised when the sumo-api.com response cannot safely be used."""


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

    def to_payload(self, index: int, total: int) -> dict[str, Any]:
        return {
            "index": index,
            "total": total,
            "matchNo": self.match_no,
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
            )
        )
        apply_result(match)
    return bouts


def build_kanji_lookup(profiles: list[dict[str, Any]]) -> dict[str, str]:
    """Map API Japanese shikona variants directly to API English shikona."""
    lookup: dict[str, str] = {}
    ambiguous: set[str] = set()

    for profile in profiles:
        japanese = _required_text(profile.get("shikonaJp"), "Japanese shikona")
        english = _required_text(profile.get("shikonaEn"), "English shikona")
        variants = {japanese, "".join(japanese.split())}
        first_name = japanese.split()[0] if japanese.split() else japanese
        variants.add(first_name)
        variants.add("".join(first_name.split()))

        for variant in variants:
            if not variant:
                continue
            previous = lookup.get(variant)
            if previous is not None and previous != english:
                ambiguous.add(variant)
            else:
                lookup[variant] = english

    for variant in ambiguous:
        lookup.pop(variant, None)
    return lookup


class SumoApiClient:
    def __init__(
        self,
        base_url: str = "https://sumo-api.com/api",
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def _get_json(self, url: str) -> dict[str, Any]:
        try:
            response = self.session.get(url, timeout=self.timeout)
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
        return _parse_matches(payload, day)

    def fetch_rikishi(self, rikishi_id: int) -> dict[str, Any]:
        return self._get_json(f"{self.base_url}/rikishi/{rikishi_id}")


class BashoSchedule:
    """Cached basho history with a refreshable current day for live manual control."""

    def __init__(
        self,
        client: SumoApiClient,
        basho_id: str,
        division: str,
        day: int,
    ) -> None:
        self.client = client
        self.basho_id = basho_id
        self.division = division
        self.day = day
        self._prior_days = [
            client.fetch_torikumi(basho_id, division, current_day)
            for current_day in range(1, day)
        ]
        self._current_day = client.fetch_torikumi(basho_id, division, day)
        self._bouts = build_bouts(self._prior_days, self._current_day)
        if not self._bouts:
            raise SumoApiError(f"No bouts were returned for {division}, day {day}")
        self._profile_ids = sorted(_participant_ids(self._current_day))
        self._kanji_lookup: dict[str, str] | None = None

    @property
    def kanji_lookup(self) -> dict[str, str]:
        profile_ids = sorted(_participant_ids(self._current_day))
        if profile_ids != self._profile_ids:
            self._profile_ids = profile_ids
            self._kanji_lookup = None
        if self._kanji_lookup is None:
            profiles = [self.client.fetch_rikishi(rikishi_id) for rikishi_id in self._profile_ids]
            self._kanji_lookup = build_kanji_lookup(profiles)
        return self._kanji_lookup

    @property
    def bouts(self) -> list[Bout]:
        return self._bouts

    def refresh(self) -> list[Bout]:
        current_day = self.client.fetch_torikumi(self.basho_id, self.division, self.day)
        bouts = build_bouts(self._prior_days, current_day)
        if not bouts:
            raise SumoApiError(f"No bouts were returned for {self.division}, day {self.day}")
        self._current_day = current_day
        self._bouts = bouts
        return self._bouts
