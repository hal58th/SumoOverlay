import pytest
import requests

from sumo_data import (
    BashoSchedule,
    BoutRecord,
    SumoApiClient,
    SumoApiError,
    build_bouts,
    build_kanji_lookup,
)


def match(match_no, east_id, west_id, winner_id=None):
    result = {
        "matchNo": match_no,
        "eastId": east_id,
        "eastShikona": f"East {east_id}",
        "eastRank": "Maegashira 1 East",
        "westId": west_id,
        "westShikona": f"West {west_id}",
        "westRank": "Maegashira 1 West",
    }
    if winner_id is not None:
        result["winnerId"] = winner_id
    return result


def test_records_use_only_outcomes_before_each_bout():
    historical = [
        match(1, 1, 2, winner_id=1),
        match(2, 3, 5, winner_id=3),
        match(3, 4, 6, winner_id=4),
    ]
    today = [
        match(1, 1, 3, winner_id=3),
        match(2, 1, 4),
    ]

    bouts = build_bouts([historical], today)

    assert bouts[0].east.record == BoutRecord(wins=1, losses=0)
    assert bouts[0].west.record == BoutRecord(wins=1, losses=0)
    # The winner of bout 1 appears in the later record, but never in bout 1's record.
    assert bouts[1].east.record == BoutRecord(wins=1, losses=1)
    assert bouts[1].west.record == BoutRecord(wins=1, losses=0)
    assert "winnerId" not in bouts[0].to_payload(0, 2)


def test_unresolved_prior_result_hides_only_the_affected_records():
    historical = [match(1, 1, 2)]
    today = [match(1, 1, 3)]

    bout = build_bouts([historical], today)[0]

    assert bout.east.record is None
    assert bout.west.record == BoutRecord(wins=0, losses=0)


def test_winner_must_be_a_bout_participant():
    with pytest.raises(SumoApiError, match="does not match"):
        build_bouts([], [match(1, 1, 2, winner_id=3)])


def test_kanji_lookup_maps_api_names_and_drops_ambiguous_keys():
    lookup = build_kanji_lookup(
        [
            {"shikonaJp": "栃大海　雄", "shikonaEn": "Tochitaikai"},
            {"shikonaJp": "栃大海　別", "shikonaEn": "Other"},
            {"shikonaJp": "大の里　泰輝", "shikonaEn": "Onosato"},
        ]
    )

    assert lookup["大の里"] == "Onosato"
    assert lookup["大の里　泰輝"] == "Onosato"
    assert "栃大海" not in lookup


def test_api_client_surfaces_http_failures():
    class FailedResponse:
        def raise_for_status(self):
            raise requests.HTTPError("503")

    class FailedSession:
        def get(self, url, timeout):
            return FailedResponse()

    client = SumoApiClient(session=FailedSession())

    with pytest.raises(SumoApiError, match="Unable to fetch"):
        client.fetch_torikumi("202505", "Makuuchi", 1)


def test_schedule_caches_history_and_refreshes_only_selected_day():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def fetch_torikumi(self, basho_id, division, day):
            self.calls.append(day)
            if day == 1:
                return [match(1, 1, 2, winner_id=1)]
            return [match(1, 1, 2, winner_id=2)]

        def fetch_rikishi(self, rikishi_id):
            return {
                "shikonaJp": f"力士{rikishi_id}",
                "shikonaEn": f"Rikishi{rikishi_id}",
            }

    client = FakeClient()
    schedule = BashoSchedule(client, "202505", "Makuuchi", 2)

    assert schedule.bouts[0].east.record == BoutRecord(wins=1, losses=0)
    assert client.calls == [1, 2]
    assert schedule.kanji_lookup["力士1"] == "Rikishi1"
    schedule.refresh()
    assert client.calls == [1, 2, 2]
