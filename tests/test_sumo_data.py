from threading import Barrier

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


def test_kanji_lookup_strips_parenthetical_reading_for_ocr_matching():
    lookup = build_kanji_lookup(
        [
            {"shikonaJp": "藤天晴(ふじてんせい)", "shikonaEn": "Fujitensei"},
            {"shikonaJp": "夢道鵬(むどうほう)", "shikonaEn": "Mudoho"},
        ]
    )

    assert lookup["藤天晴"] == "Fujitensei"
    assert lookup["夢道鵬"] == "Mudoho"


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


def test_schedule_matches_ocr_names_to_the_unique_bout_in_either_orientation():
    class FakeClient:
        def fetch_torikumi(self, basho_id, division, day):
            return [match(1, 1, 2)]

        def fetch_rikishi(self, rikishi_id):
            profiles = {
                1: {"shikonaJp": "大喜翔", "shikonaEn": "East 1"},
                2: {"shikonaJp": "栃幸大", "shikonaEn": "West 2"},
            }
            return profiles[rikishi_id]

    schedule = BashoSchedule(FakeClient(), "202505", "Makuuchi", 1)

    assert schedule.find_bout_index_by_kanji("大喜翔", "栃幸大") == 0
    assert schedule.find_bout_index_by_kanji("栃幸大", "大喜翔") == 0
    assert schedule.find_bout_index_by_kanji("大喜翔", "") == 0
    assert schedule.find_bout_index_by_kanji("", "栃幸大") == 0
    assert schedule.find_bout_index_by_kanji("知らない", "栃幸大") == 0
    assert schedule.find_bout_index_by_kanji("知らない", "") is None


def test_schedule_matches_lower_division_ocr_names_with_parenthetical_readings():
    class FakeClient:
        def fetch_torikumi(self, basho_id, division, day):
            return [
                {
                    **match(1, 9050, 91),
                    "division": "Makushita",
                }
            ]

        def fetch_rikishi(self, rikishi_id):
            profiles = {
                9050: {
                    "shikonaJp": "藤天晴(ふじてんせい)",
                    "shikonaEn": "Fujitensei",
                },
                91: {
                    "shikonaJp": "夢道鵬(むどうほう)",
                    "shikonaEn": "Mudoho",
                },
            }
            return profiles[rikishi_id]

    schedule = BashoSchedule(FakeClient(), "202505", "Makushita", 1)

    assert schedule.find_bout_index_by_kanji("藤天晴", "夢道鵬") == 0


def test_schedule_matches_ocr_by_rikishi_id_when_api_english_spellings_differ():
    class FakeClient:
        def fetch_torikumi(self, basho_id, division, day):
            return [match(1, 1, 2)]

        def fetch_rikishi(self, rikishi_id):
            profiles = {
                1: {"shikonaJp": "大喜翔", "shikonaEn": "Profile spelling"},
                2: {"shikonaJp": "栃幸大", "shikonaEn": "Another spelling"},
            }
            return profiles[rikishi_id]

    schedule = BashoSchedule(FakeClient(), "202505", "Makuuchi", 1)

    assert schedule.find_bout_index_by_kanji("大喜翔", "栃幸大") == 0


def test_schedule_does_not_choose_between_ambiguous_ocr_matches():
    class FakeClient:
        def fetch_torikumi(self, basho_id, division, day):
            return [match(1, 1, 2), match(2, 1, 2)]

        def fetch_rikishi(self, rikishi_id):
            names = {1: "大喜翔", 2: "栃幸大"}
            english = {1: "East 1", 2: "West 2"}
            return {"shikonaJp": names[rikishi_id], "shikonaEn": english[rikishi_id]}

    schedule = BashoSchedule(FakeClient(), "202505", "Makuuchi", 1)

    assert schedule.find_bout_index_by_kanji("大喜翔", "栃幸大") is None


def test_schedule_does_not_choose_between_bouts_from_one_recognized_wrestler():
    class FakeClient:
        def fetch_torikumi(self, basho_id, division, day):
            return [match(1, 1, 2), match(2, 1, 3)]

        def fetch_rikishi(self, rikishi_id):
            return {
                "shikonaJp": {1: "大喜翔", 2: "栃幸大", 3: "藤天晴"}[rikishi_id],
                "shikonaEn": {1: "Rikishi1", 2: "Rikishi2", 3: "Rikishi3"}[rikishi_id],
            }

    schedule = BashoSchedule(FakeClient(), "202505", "Makuuchi", 1)

    assert schedule.find_bout_index_by_kanji("大喜翔", "") is None


def test_schedule_fetches_ocr_profiles_concurrently():
    class FakeClient:
        def __init__(self):
            self.profile_barrier = Barrier(2)

        def fetch_torikumi(self, basho_id, division, day):
            return [match(1, 1, 2), match(2, 3, 4)]

        def fetch_rikishi(self, rikishi_id):
            self.profile_barrier.wait(timeout=2)
            return {
                "shikonaJp": f"力士{rikishi_id}",
                "shikonaEn": f"Rikishi{rikishi_id}",
            }

    schedule = BashoSchedule(FakeClient(), "202505", "Makuuchi", 1)

    assert schedule.kanji_lookup["力士1"] == "Rikishi1"


def test_schedule_returns_cached_broadcast_profile_details():
    class FakeClient:
        def __init__(self):
            self.profile_calls = []
            self.career_calls = []

        def fetch_torikumi(self, basho_id, division, day):
            return [match(1, 1, 2)]

        def fetch_rikishi(self, rikishi_id):
            self.profile_calls.append(rikishi_id)
            return {
                "birthDate": "2000-06-07T00:00:00Z",
                "heya": "Nishonoseki",
                "height": 190,
                "weight": 188,
                "shusshin": "Ishikawa",
                "debut": "202305",
                "unrelated": "not exposed",
            }

        def fetch_rikishi_matches(self, rikishi_id):
            self.career_calls.append(rikishi_id)
            return []

    client = FakeClient()
    schedule = BashoSchedule(client, "202505", "Makuuchi", 1)
    bout = schedule.bouts[0]

    profiles = schedule.wrestler_profiles(bout)
    assert profiles == {
        "east": {
            "birthDate": "2000-06-07T00:00:00Z",
            "heya": "Nishonoseki",
            "height": 190,
            "weight": 188,
            "shusshin": "Ishikawa",
            "debut": "202305",
            "lifetime": {"wins": 0, "losses": 0, "matches": 0},
        },
        "west": {
            "birthDate": "2000-06-07T00:00:00Z",
            "heya": "Nishonoseki",
            "height": 190,
            "weight": 188,
            "shusshin": "Ishikawa",
            "debut": "202305",
            "lifetime": {"wins": 0, "losses": 0, "matches": 0},
        },
    }
    schedule.wrestler_profiles(bout)
    assert client.profile_calls == [1, 2]
    assert client.career_calls == [1, 2]


def test_lifetime_records_include_only_completed_matches_before_selected_bout():
    def career_match(basho_id, day, match_no, east_id, west_id, winner_id):
        return {
            "bashoId": basho_id,
            "division": "Mae-zumo" if basho_id == "202401" else "Makuuchi",
            "day": day,
            "matchNo": match_no,
            "eastId": east_id,
            "westId": west_id,
            "winnerId": winner_id,
        }

    class FakeClient:
        def fetch_torikumi(self, basho_id, division, day):
            return [match(3, 1, 2)]

        def fetch_rikishi(self, rikishi_id):
            return {"shikonaJp": f"力士{rikishi_id}", "shikonaEn": f"Rikishi{rikishi_id}"}

        def fetch_rikishi_matches(self, rikishi_id):
            if rikishi_id == 1:
                return [
                    career_match("202401", 1, 1, 1, 8, 1),
                    career_match("202403", 16, 1, 1, 9, 9),
                    career_match("202505", 1, 4, 1, 2, 1),
                    career_match("202505", 1, 5, 1, 3, 3),
                ]
            return [
                career_match("202401", 1, 2, 7, 2, 7),
                career_match("202403", 2, 2, 6, 2, 6),
            ]

    schedule = BashoSchedule(FakeClient(), "202505", "Makuuchi", 1)
    profiles = schedule.wrestler_profiles(schedule.bouts[0])

    assert profiles["east"]["lifetime"] == {"wins": 1, "losses": 1, "matches": 2}
    assert profiles["west"]["lifetime"] == {"wins": 0, "losses": 2, "matches": 2}


def test_cached_sumo_api_snapshot_supports_all_divisions_and_safe_h2h(sumo_api_client):
    schedule = BashoSchedule(sumo_api_client, "202505", None, 1)
    onosato_vs_wakamotoharu = next(
        bout
        for bout in schedule.bouts
        if {bout.east.rikishi_id, bout.west.rikishi_id} == {8850, 13}
    )

    assert len(schedule.bouts) == 163
    assert schedule.bouts[0].division == "Jonokuchi"
    assert schedule.bouts[-1].division == "Makuuchi"
    assert onosato_vs_wakamotoharu.match_no == 20

    stats = schedule.head_to_head(onosato_vs_wakamotoharu)

    if onosato_vs_wakamotoharu.east.rikishi_id == 8850:
        assert stats["eastWins"] == 4
        assert stats["westWins"] == 2
    else:
        assert stats["eastWins"] == 2
        assert stats["westWins"] == 4
    assert stats["total"] == 6
    assert len(stats["history"]) == 6
    assert stats["history"].count("east") == stats["eastWins"]
    assert stats["history"].count("west") == stats["westWins"]


def test_head_to_head_history_is_oldest_first_and_excludes_future_matches():
    class FakeClient:
        def fetch_torikumi(self, basho_id, division, day):
            return [match(1, 1, 2)]

        def fetch_head_to_head(self, rikishi_id, opponent_id):
            return [
                {
                    "bashoId": "202403",
                    "day": 16,
                    "division": "Makuuchi",
                    "matchNo": 4,
                    "winnerId": 1,
                },
                {
                    "bashoId": "202401",
                    "day": 2,
                    "division": "Makuuchi",
                    "matchNo": 3,
                    "winnerId": 1,
                },
                {
                    "bashoId": "202402",
                    "day": 1,
                    "division": "Makuuchi",
                    "matchNo": 2,
                    "winnerId": 2,
                },
                {
                    "bashoId": "202505",
                    "day": 1,
                    "division": "Makuuchi",
                    "matchNo": 2,
                    "winnerId": 1,
                },
            ]

    schedule = BashoSchedule(FakeClient(), "202505", "Makuuchi", 1)
    stats = schedule.head_to_head(schedule.bouts[0])

    assert stats == {
        "eastWins": 2,
        "westWins": 1,
        "total": 3,
        "history": ["east", "west", "east"],
    }


def test_live_api_client_targets_documented_h2h_route():
    class CapturedResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"matches": [], "total": 0}

    class CapturingSession:
        url = None

        def get(self, url, timeout):
            self.url = url
            return CapturedResponse()

    session = CapturingSession()
    client = SumoApiClient(session=session)

    assert client.fetch_head_to_head(19, 8850) == []
    assert session.url.endswith("/rikishi/19/matches/8850")


def test_live_api_client_fetches_complete_rikishi_match_history():
    class CapturedResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"records": [{"winnerId": 19}], "total": 1}

    class CapturingSession:
        url = None

        def get(self, url, timeout):
            self.url = url
            return CapturedResponse()

    session = CapturingSession()
    client = SumoApiClient(session=session)

    assert client.fetch_rikishi_matches(19) == [{"winnerId": 19}]
    assert session.url.endswith("/rikishi/19/matches")


def test_api_client_rejects_partial_rikishi_match_history():
    class PartialResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"records": [], "total": 1}

    class PartialSession:
        def get(self, url, timeout):
            return PartialResponse()

    with pytest.raises(SumoApiError, match="only part"):
        SumoApiClient(session=PartialSession()).fetch_rikishi_matches(19)


def test_api_client_rejects_partial_h2h_history():
    class PartialResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"matches": [], "total": 2}

    class PartialSession:
        def get(self, url, timeout):
            return PartialResponse()

    client = SumoApiClient(session=PartialSession())

    with pytest.raises(SumoApiError, match="only part"):
        client.fetch_head_to_head(19, 8850)
