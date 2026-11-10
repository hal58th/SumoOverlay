import logging
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import yaml

import app as app_module
from app import create_app
from config import (
    SettingsError,
    default_settings,
    initialize_config_file,
    load_settings,
    validate_settings,
)
from sumo_data import DIVISION_ORDER, Bout, BoutRecord, BoutSide, SumoApiError


@pytest.fixture(autouse=True)
def isolate_default_config(monkeypatch, tmp_path):
    config_path = tmp_path / "app-default-config.yaml"
    monkeypatch.setattr(app_module, "DEFAULT_CONFIG_PATH", config_path)
    initialize_config_file(config_path, logging.getLogger("tests"))


def test_frozen_application_uses_executable_directory(monkeypatch, tmp_path):
    executable = tmp_path / "SumoOverlay.exe"
    monkeypatch.setattr(app_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_module.sys, "executable", str(executable))

    assert app_module._application_directory() == tmp_path


def make_bout(match_no, east, west):
    return Bout(
        match_no,
        BoutSide(east, f"East {east}", "Maegashira 1 East", BoutRecord(1, 0)),
        BoutSide(west, f"West {west}", "Maegashira 1 West", BoutRecord(0, 1)),
    )


@dataclass
class FakeSchedule:
    bouts: list[Bout]
    fail_refresh: bool = False
    refresh_count: int = 0
    kanji_lookup: dict[str, str] | None = None
    h2h: dict[str, int] | None = None

    def refresh(self):
        self.refresh_count += 1
        if self.fail_refresh:
            raise SumoApiError("API unavailable")
        return self.bouts

    def head_to_head(self, bout):
        return self.h2h or {"eastWins": 0, "westWins": 0, "total": 0, "history": []}


def test_manual_controls_update_state_and_emit_websocket_event():
    schedule = FakeSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)])
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    client = app.test_client()
    socket_client = app.extensions["socketio"].test_client(app)
    socket_client.get_received()

    response = client.post("/api/control/next")

    assert response.status_code == 200
    assert response.json["matchNo"] == 2
    assert response.json["index"] == 1
    assert schedule.refresh_count == 1
    events = socket_client.get_received()
    assert any(
        event["name"] == "bout_update"
        and event["args"][0]["headToHead"] is not None
        for event in events
    )
    assert any(
        event["name"] == "pre_match_update"
        and event["args"][0]["headToHead"] is not None
        for event in events
    )
    socket_client.disconnect()


def test_controller_prefetches_current_and_next_two_bouts_on_startup_and_navigation():
    schedule = FakeSchedule(
        [
            make_bout(1, 10, 11),
            make_bout(2, 12, 13),
            make_bout(3, 14, 15),
            make_bout(4, 16, 17),
        ]
    )
    schedule.prefetch_batches = []
    schedule.prefetch_head_to_head = lambda bouts: schedule.prefetch_batches.append(
        [bout.match_no for bout in bouts]
    )
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    controller = app.extensions["bout_controller"]

    assert schedule.prefetch_batches == [[1, 2, 3]]

    controller.move(2)

    assert schedule.prefetch_batches[-1] == [3, 4]


def test_upcoming_matches_state_tracks_the_configured_number_of_bouts():
    schedule = FakeSchedule(
        [make_bout(index, index * 2, index * 2 + 1) for index in range(1, 10)]
    )
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    client = app.test_client()

    initial_state = client.get("/api/state").json
    assert initial_state["isLastMatchOfDay"] is False
    upcoming = initial_state["upcomingMatches"]
    assert len(upcoming) == 6
    assert upcoming[0] == {"east": "East 4", "west": "West 5"}
    assert upcoming[-1] == {"east": "East 14", "west": "West 15"}

    response = client.post("/api/control/next")
    assert response.status_code == 200
    assert len(response.json["upcomingMatches"]) == 6
    assert response.json["upcomingMatches"][0] == {
        "east": "East 6",
        "west": "West 7",
    }

    for _ in range(7):
        response = client.post("/api/control/next")
    assert response.json["upcomingMatches"] == []
    assert response.json["isLastMatchOfDay"] is True


def test_upcoming_match_count_is_configurable_in_settings(tmp_path):
    settings = default_settings()
    settings["overlay"]["upcomingmatches"]["match_count"] = 3
    settings["overlay"]["slimhalf"]["show_upcoming_matches"] = True
    settings["overlay"]["slimhalf"]["upcoming_matches_count"] = 8
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, settings)
    schedule = FakeSchedule(
        [make_bout(index, index * 2, index * 2 + 1) for index in range(1, 12)]
    )
    app = create_app(
        schedule=schedule,
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )

    state = app.test_client().get("/api/state").json
    assert len(state["upcomingMatches"]) == 8
    assert state["settings"]["overlay"]["upcomingmatches"]["match_count"] == 3
    assert state["settings"]["overlay"]["slimhalf"]["upcoming_matches_count"] == 8


def test_upcoming_matches_overlay_uses_configured_template(tmp_path):
    settings = default_settings()
    settings["templates"]["upcomingmatches"] = "custom_upcoming.html"
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, settings)
    custom_template_dir = tmp_path / "custom_templates"
    custom_template_dir.mkdir()
    (custom_template_dir / "custom_upcoming.html").write_text(
        "<main>Custom upcoming matches</main>", encoding="utf-8"
    )
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )

    assert b"Custom upcoming matches" in app.test_client().get(
        "/upcomingmatches"
    ).data


def test_failed_head_to_head_refresh_does_not_publish_unavailable_pre_match_state():
    class FailingH2HSchedule(FakeSchedule):
        def head_to_head(self, bout):
            raise SumoApiError("H2H API unavailable")

    schedule = FailingH2HSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)])
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    client = app.test_client()
    socket_client = app.extensions["socketio"].test_client(app)
    socket_client.get_received()

    response = client.post("/api/control/next")

    assert response.status_code == 200
    assert response.json["matchNo"] == 2
    events = socket_client.get_received()
    assert any(event["name"] == "pre_match_error" for event in events)
    assert not any(event["name"] == "pre_match_update" for event in events)
    assert any(
        event["name"] == "bout_update"
        and event["args"][0]["headToHead"] is None
        for event in events
    )
    socket_client.disconnect()


def test_next_bout_response_contains_the_new_head_to_head_score():
    class ChangingH2HSchedule(FakeSchedule):
        def head_to_head(self, bout):
            return {
                "eastWins": bout.match_no,
                "westWins": 3 - bout.match_no,
                "total": 3,
                "history": [],
            }

    schedule = ChangingH2HSchedule(
        [make_bout(1, 10, 11), make_bout(2, 12, 13)]
    )
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)

    response = app.test_client().post("/api/control/next")

    assert response.status_code == 200
    assert response.json["matchNo"] == 2
    assert response.json["headToHead"] == {
        "eastWins": 2,
        "westWins": 1,
        "total": 3,
        "history": [],
    }


def test_failed_refresh_keeps_state_and_returns_explicit_error():
    schedule = FakeSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)], fail_refresh=True)
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    client = app.test_client()

    response = client.post("/api/control/next")

    assert response.status_code == 502
    assert response.json == {"error": "API unavailable"}
    assert client.get("/api/state").json["index"] == 0


def test_socket_control_surfaces_refresh_error():
    schedule = FakeSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)], fail_refresh=True)
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    socket_client = app.extensions["socketio"].test_client(app)
    socket_client.get_received()

    socket_client.emit("control", {"action": "next"})

    events = socket_client.get_received()
    assert any(event["name"] == "control_error" for event in events)
    assert app.extensions["bout_controller"].state()["index"] == 0
    socket_client.disconnect()


def test_announcing_winner_updates_overlay_scores_without_refreshing_h2h():
    class CountingH2HSchedule(FakeSchedule):
        h2h_calls = 0

        def head_to_head(self, bout):
            self.h2h_calls += 1
            return super().head_to_head(bout)

    schedule = CountingH2HSchedule([make_bout(1, 10, 11)])
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    socket_client = app.extensions["socketio"].test_client(app)
    socket_client.get_received()
    h2h_calls_before_result = schedule.h2h_calls

    socket_client.emit("announce_winner", {"winner": "east"})

    events = socket_client.get_received()
    result = next(event["args"][0] for event in events if event["name"] == "bout_result")
    assert result["east"]["record"] == {"wins": 2, "losses": 0}
    assert result["west"]["record"] == {"wins": 0, "losses": 2}
    assert result["announcedWinner"] == "east"
    assert "recordEmphasis" not in result
    assert schedule.h2h_calls == h2h_calls_before_result
    socket_client.disconnect()


def test_http_winner_action_works_without_socket_and_makes_no_api_requests():
    class CountingSchedule(FakeSchedule):
        h2h_calls = 0

        def head_to_head(self, bout):
            self.h2h_calls += 1
            return super().head_to_head(bout)

    schedule = CountingSchedule([make_bout(1, 10, 11)])
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)

    response = app.test_client().post(
        "/api/control/winner", json={"winner": "west"}
    )

    assert response.status_code == 200
    assert response.json["west"]["record"] == {"wins": 1, "losses": 1}
    assert response.json["east"]["record"] == {"wins": 1, "losses": 1}
    assert response.json["announcedWinner"] == "west"
    assert "recordEmphasis" not in response.json
    assert schedule.h2h_calls == 0

    repeated_response = app.test_client().post(
        "/api/control/winner", json={"winner": "west"}
    )
    assert repeated_response.status_code == 200
    assert repeated_response.json["east"]["record"] == {"wins": 1, "losses": 0}
    assert repeated_response.json["west"]["record"] == {"wins": 0, "losses": 1}
    assert "announcedWinner" not in repeated_response.json


def test_announced_winner_is_cleared_when_manually_moving_between_bouts():
    schedule = FakeSchedule(
        [make_bout(1, 10, 11), make_bout(2, 12, 13), make_bout(3, 14, 15)]
    )
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    client = app.test_client()
    socket_client = app.extensions["socketio"].test_client(app)
    socket_client.get_received()

    socket_client.emit("announce_winner", {"winner": "east"})
    assert any(
        event["name"] == "bout_result"
        for event in socket_client.get_received()
    )
    next_response = client.post("/api/control/next")
    previous_response = client.post("/api/control/prev")

    assert next_response.json["east"]["record"] == {"wins": 1, "losses": 0}
    assert previous_response.json["matchNo"] == 1
    assert previous_response.json["east"]["record"] == {"wins": 1, "losses": 0}
    assert previous_response.json["west"]["record"] == {"wins": 0, "losses": 1}
    socket_client.disconnect()


def test_announced_winner_rejects_invalid_side():
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        test_config={"TESTING": True},
        watch_config=False,
    )
    socket_client = app.extensions["socketio"].test_client(app)
    socket_client.get_received()

    socket_client.emit("announce_winner", {"winner": "north"})

    assert any(
        event["name"] == "control_error"
        for event in socket_client.get_received()
    )
    socket_client.disconnect()


def test_pages_and_kanji_lookup_are_available():
    schedule = FakeSchedule([make_bout(1, 10, 11)], kanji_lookup={"栃大海": "Tochitaikai"})
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    client = app.test_client()

    assert client.get("/prematch").status_code == 200
    assert client.get("/pre-match-overlay").status_code == 404
    assert client.get("/overlay").status_code == 404
    scoreboard_overlay = client.get("/scoreboard")
    assert scoreboard_overlay.status_code == 200
    slimhalf_overlay = client.get("/scoreboard/slimhalf")
    assert slimhalf_overlay.status_code == 200
    slimfull_overlay = client.get("/scoreboard/slimfull")
    assert slimfull_overlay.status_code == 200
    upcoming_overlay = client.get("/upcomingmatches")
    assert upcoming_overlay.status_code == 200
    assert client.get("/main-overlay").status_code == 404
    assert client.get("/scoreboard-overlay").status_code == 404
    assert 'Makuuchi: 1' in scoreboard_overlay.text
    assert "return number ? `Div ${number}` : \"Division\";" in scoreboard_overlay.text
    assert "fitNameToWidth" not in scoreboard_overlay.text
    assert "state.settings.overlay.scoreboard.width}px" in scoreboard_overlay.text
    assert "state.settings.overlay.scoreboard.height}px" in scoreboard_overlay.text
    assert 'style.font_sizes.text}px' in scoreboard_overlay.text
    assert '"--scoreboard-division-font-size"' in scoreboard_overlay.text
    assert '"--overlay-current-score-font-size"' in scoreboard_overlay.text
    assert '"--overlay-current-score-color"' in scoreboard_overlay.text
    assert 'wins.classList.toggle("announced-result", announcedWinner === side);' in scoreboard_overlay.text
    assert 'state.announcedWinner' in scoreboard_overlay.text
    assert '"--overlay-border-width"' in scoreboard_overlay.text
    assert '"--overlay-border-color"' in scoreboard_overlay.text
    assert 'mainOverlay.style.setProperty("--scoreboard-opacity", String(style.opacity));' in scoreboard_overlay.text
    assert 'mainOverlay.style.setProperty("--scoreboard-text-opacity", String(style.text_opacity));' in scoreboard_overlay.text
    assert "eastRecord.hidden = !settings.show_current_score;" in scoreboard_overlay.text
    assert "settings.show_stable_name" in scoreboard_overlay.text
    assert "state.stableNames" in scoreboard_overlay.text
    assert "style.colors.stable_name" in scoreboard_overlay.text
    assert "style.font_sizes.stable_name" in scoreboard_overlay.text
    main_styles = client.get("/static/overlay.css").text.split(
        ".pre-match-card {", maxsplit=1
    )[0]
    assert "width: 1582px;" not in main_styles
    assert "height: 174px;" not in main_styles
    assert "font-size: var(--overlay-text-font-size);" in main_styles
    assert "line-height: 1.25;" in main_styles
    assert "font-size: var(--overlay-muted-font-size);" in main_styles
    assert "font-size: var(--scoreboard-versus-font-size);" in main_styles
    assert 'style.font_sizes.text}px' in client.get("/prematch").text
    assert 'style.font_sizes.current_score}px' in client.get("/prematch").text
    main_strip_styles = main_styles.split(".main-strip {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    assert "background: #10141c;" in main_strip_styles
    assert "var(--scoreboard-opacity, 1)" in main_strip_styles
    assert "box-shadow" not in main_strip_styles
    assert "opacity: var(--scoreboard-text-opacity, 1);" in main_styles
    assert 'class="configured-box slim-strip slimhalf"' in slimhalf_overlay.text
    assert 'state.settings.overlay["slimhalf"]' in slimhalf_overlay.text
    assert 'id="east-record"' in slimhalf_overlay.text
    assert 'id="west-record"' in slimhalf_overlay.text
    assert "eastRecord.textContent = formatRecord(state.east.record);" in slimhalf_overlay.text
    assert "eastRecord.hidden = !settings.show_current_score;" in slimhalf_overlay.text
    assert 'id="upcoming-matches"' in slimhalf_overlay.text
    assert "settings.show_upcoming_matches" in slimhalf_overlay.text
    assert "settings.upcoming_matches_count" in slimhalf_overlay.text
    assert "settings.show_stable_name" in slimhalf_overlay.text
    assert "state.stableNames" in slimhalf_overlay.text
    assert "style.colors.stable_name" in slimhalf_overlay.text
    assert "style.font_sizes.stable_name" in slimhalf_overlay.text
    assert "`On Deck: ${" in slimhalf_overlay.text
    assert 'upcoming.join(" | ")' in slimhalf_overlay.text
    assert "style.colors.upcoming_matches" in slimhalf_overlay.text
    assert "style.font_sizes.upcoming_matches" in slimhalf_overlay.text
    assert "slimOverlay.hidden = false;" in slimhalf_overlay.text
    assert 'class="configured-box slim-strip slimfull"' in slimfull_overlay.text
    assert 'state.settings.overlay["slimfull"]' in slimfull_overlay.text
    assert 'id="east-record"' in slimfull_overlay.text
    assert "Bout ${state.divisionBoutNumber} out of ${state.divisionTotal}" in slimfull_overlay.text
    assert '`${state.division} (${shortDivision})`' in slimfull_overlay.text
    assert 'id="head-to-head"' in slimfull_overlay.text
    assert '"/api/pre-match-state"' in slimfull_overlay.text
    assert 'Head to Head: First Meeting' in slimfull_overlay.text
    assert 'Head to Head: ${headToHead.eastWins} - ${headToHead.westWins}' in slimfull_overlay.text
    assert slimfull_overlay.text.index('id="head-to-head"') < slimfull_overlay.text.index('id="division"')
    bout_details_index = slimfull_overlay.text.index('id="bout-details"')
    assert bout_details_index < slimfull_overlay.text.index("</div>", bout_details_index)
    all_styles = client.get("/static/overlay.css").text
    slimhalf_line_styles = all_styles.split(".slim-main-line {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    slimfull_line_styles = all_styles.split(
        ".slimfull .slim-main-line {", maxsplit=1
    )[1].split("}", maxsplit=1)[0]
    assert "gap: 10px;" in slimhalf_line_styles
    assert "gap: 10px;" in slimfull_line_styles
    slim_name_styles = all_styles.split(".slim-name {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    assert "flex-shrink: 0;" in slim_name_styles
    assert "var(--slim-opacity, 1)" in all_styles
    assert "var(--slim-text-opacity, 1)" in all_styles
    assert "var(--slim-upcoming-matches-color" in all_styles
    assert "var(--slim-upcoming-matches-font-size" in all_styles
    assert "var(--slim-stable-name-color" in all_styles
    assert "var(--slim-stable-name-font-size" in all_styles
    assert "var(--scoreboard-stable-name-color" in all_styles
    assert "var(--scoreboard-stable-name-font-size" in all_styles
    main_stable_name_styles = all_styles.split(".main-stable-name {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    assert "justify-self: center;" in main_stable_name_styles
    assert "text-align: center;" in main_stable_name_styles
    assert 'class="configured-box slim-strip upcoming-strip"' in upcoming_overlay.text
    assert "state.upcomingMatches.slice(0, style.match_count).map(" in upcoming_overlay.text
    assert "matchups.join(\" - \")" in upcoming_overlay.text
    assert "state.upcomingMatches.slice(0, style.match_count)" in upcoming_overlay.text
    assert '"Musubi-no-ichiban (last match)"' in upcoming_overlay.text
    assert 'state.settings.overlay.upcomingmatches' in upcoming_overlay.text
    assert 'class="upcoming-label">Upcoming:</span>' in upcoming_overlay.text
    assert ".upcoming-label" in all_styles
    assert "color: var(--overlay-accent-color);" in all_styles
    assert "clamp(" not in all_styles
    assert "vw" not in all_styles
    assert "vh" not in all_styles
    pre_match_html = client.get("/prematch").text
    pre_match_card_styles = all_styles.split(".pre-match-card {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    assert "justify-content: flex-start;" in pre_match_card_styles
    assert "box-shadow" not in pre_match_card_styles
    assert (
        "const { width, height, opacity } = state.settings.overlay.prematch;"
        in pre_match_html
    )
    assert 'id="bouts-until-next-stage"' not in pre_match_html
    assert "Bout ${state.divisionBoutNumber} out of ${state.divisionTotal}" in pre_match_html
    assert "state.boutsUntilNextStage" not in pre_match_html
    assert '"Highest rank"' in pre_match_html
    assert "highestRankKey === rankComparisonKey(currentRank)" in pre_match_html
    assert '? "Head to Head: First Meeting"' in pre_match_html
    assert "rankComparisonKey(rank)" in pre_match_html
    assert "state.divisionNumber <= 2" in pre_match_html
    assert "state.settings.preMatchSalariedSeconds" in pre_match_html
    assert "state.settings.preMatchUnsalariedSeconds" in pre_match_html
    assert 'preMatchOverlay.style.width = `${width}px`;' in pre_match_html
    assert "preMatchOverlay.style.setProperty(\"--pre-match-opacity\", String(opacity));" in pre_match_html
    assert 'String(style.text_opacity)' in pre_match_html
    assert "fitNameToWidth" not in pre_match_html
    assert '"--prematch-border-width"' in pre_match_html
    assert '"--prematch-border-color"' in pre_match_html
    assert "--prematch-border-width: 1px;" in all_styles
    assert "max-height: 14em;" in all_styles
    west_rank_record_styles = all_styles.split(".west .rank-record {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    west_profile_styles = all_styles.split(".west .wrestler-profile {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    assert "justify-content: flex-start;" in west_rank_record_styles
    assert "text-align: left;" in west_profile_styles
    west_name_styles = all_styles.split(".west .wrestler-name {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    assert "text-align: left;" in west_name_styles
    assert "-webkit-line-clamp: 2;" in all_styles
    assert "--prematch-border-style: solid;" in all_styles
    assert "--overlay-border-width: 0px;" in all_styles
    assert "--overlay-border-style: none;" in all_styles
    assert "rgb(20 25 34 / var(--pre-match-opacity, 0.5))" in all_styles
    assert "rgb(37 42 51 / var(--pre-match-opacity, 0.5))" in all_styles
    assert "opacity: var(--prematch-text-opacity, 1);" in all_styles
    control = client.get("/control")
    assert control.status_code == 200
    assert "style.font_sizes.current_score" in control.text
    assert "--overlay-current-score-font-size" in control.text
    assert (
        ".control-panel .record { font-size: var(--overlay-current-score-font-size); }"
        in control.text
    )
    assert "style.font_sizes.text}px" in control.text
    assert "style.font_sizes.muted}px" in control.text
    assert "style.font_sizes.accent}px" in control.text
    assert "style.font_sizes.result}px" in control.text
    assert "font-size: var(--overlay-accent-font-size);" in control.text
    assert "font-size: var(--overlay-result-font-size);" in control.text
    assert "<span>SUMO</span>" not in control.text
    assert 'id="bouts-until-next-stage"' not in control.text
    assert 'class="rank-record"' in control.text
    assert "Bout ${state.index + 1}/${state.total}" in control.text
    assert "until next division" in control.text
    assert client.get("/api/lookup").json == {"栃大海": "Tochitaikai"}
    assert client.get("/api/state").json["headToHead"] is None


def test_bold_pre_match_records_do_not_change_score_row_height():
    stylesheet_path = Path(__file__).resolve().parents[1] / "static" / "overlay.css"
    all_styles = stylesheet_path.read_text(encoding="utf-8")
    record_styles = all_styles.split(".record {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    emphasized_record_styles = all_styles.split(".record strong {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]

    assert "line-height: 1;" in record_styles
    assert "font-size: inherit;" in emphasized_record_styles
    assert "line-height: inherit;" in emphasized_record_styles
    assert "font-size: 1.12em;" not in emphasized_record_styles


def test_announced_winner_record_counts_are_red_in_all_overlay_templates():
    root = Path(__file__).resolve().parents[1]
    templates = [
        root / "templates" / "scoreboard_overlay.html",
        root / "templates" / "prematch_overlay.html",
        root / "templates" / "control.html",
    ]
    for template_path in templates:
        template = template_path.read_text(encoding="utf-8")
        assert 'wins.classList.toggle("announced-result", announcedWinner === side);' in template
        assert "announcedWinner !== side" in template

    styles = (root / "static" / "overlay.css").read_text(encoding="utf-8")
    assert ".main-record .announced-result" in styles
    assert ".record .announced-result" in styles
    assert "color: #ff4b4b;" in styles


def test_pre_match_wrestler_names_align_independently_of_column_content_height():
    stylesheet_path = Path(__file__).resolve().parents[1] / "static" / "overlay.css"
    all_styles = stylesheet_path.read_text(encoding="utf-8")
    matchup_styles = all_styles.split(".matchup {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    wrestler_styles = all_styles.split(".wrestler {", maxsplit=1)[1].split("}", maxsplit=1)[0]
    versus_styles = all_styles.split(".versus {", maxsplit=1)[1].split("}", maxsplit=1)[0]

    assert "align-items: start;" in matchup_styles
    assert "align-self: start;" in wrestler_styles
    assert "align-self: center;" in versus_styles


def test_overlay_routes_load_configured_custom_template_files(tmp_path):
    settings = default_settings()
    settings["templates"]["prematch"] = "custom_prematch.html"
    settings["templates"]["slimhalf"] = "custom_slimhalf.html"
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, settings)
    custom_template_dir = tmp_path / "custom_templates"
    custom_template_dir.mkdir()
    (custom_template_dir / "custom_prematch.html").write_text(
        "<main>Custom pre-match overlay</main>", encoding="utf-8"
    )
    (custom_template_dir / "custom_slimhalf.html").write_text(
        "<main>Custom slimhalf overlay</main>", encoding="utf-8"
    )
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )
    client = app.test_client()

    assert b"Custom pre-match overlay" in client.get("/prematch").data
    assert b"Custom slimhalf overlay" in client.get("/scoreboard/slimhalf").data
    assert b"Sumo Slim Scoreboard" in client.get("/scoreboard/slimfull").data
    assert b"Makuuchi: 1" in client.get("/scoreboard").data
    assert client.get("/overlay").status_code == 404
    assert client.get("/scoreboard-overlay").status_code == 404
    assert b"Sumo Overlay Controls" in client.get("/control").data


def test_partial_override_config_merges_nested_values(tmp_path):
    settings_path = tmp_path / "config.yaml"
    override_path = tmp_path / "config.firefox.yaml"
    settings = default_settings()
    settings["overlay"]["scoreboard"]["height"] = 200
    write_config(settings_path, settings)
    write_config(
        override_path,
        {
            "ocr": {
                "monitor": 2,
                "capture_regions": {
                    "scoresheet": {"x": 120},
                },
            },
            "overlay": {"scoreboard": {"width": 1400}},
        },
    )

    effective = load_settings(settings_path, override_path)

    assert effective["ocr"]["monitor"] == 2
    assert effective["ocr"]["interval_seconds"] == settings["ocr"]["interval_seconds"]
    assert effective["ocr"]["capture_regions"]["scoresheet"] == {
        **settings["ocr"]["capture_regions"]["scoresheet"],
        "x": 120,
    }
    assert effective["overlay"]["scoreboard"] == {
        **settings["overlay"]["scoreboard"],
        "width": 1400,
    }


def test_control_updates_persist_to_the_layer_that_defines_the_setting(tmp_path):
    settings_path = tmp_path / "config.yaml"
    override_path = tmp_path / "config.firefox.yaml"
    write_config(settings_path, default_settings())
    write_config(override_path, {"control": {"pre_match_salaried_seconds": 30}})
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        override_config_path=override_path,
        test_config={"TESTING": True},
        watch_config=True,
    )

    try:
        response = app.test_client().post(
            "/api/control/pre-match", json={"salaried_seconds": 45}
        )

        assert response.status_code == 200
        assert response.json["settings"]["preMatchSalariedSeconds"] == 45
        assert response.json["settings"]["preMatchUnsalariedSeconds"] == 20
        assert yaml.safe_load(override_path.read_text(encoding="utf-8")) == {
            "control": {"pre_match_salaried_seconds": 45}
        }
        assert yaml.safe_load(settings_path.read_text(encoding="utf-8"))["control"][
            "pre_match_salaried_seconds"
        ] == 30
    finally:
        app.extensions["settings_watcher"].stop()


def test_override_config_changes_are_hot_reloaded(tmp_path):
    settings_path = tmp_path / "config.yaml"
    override_path = tmp_path / "config.firefox.yaml"
    settings = default_settings()
    settings["server"]["watch_interval_seconds"] = 0.05
    write_config(settings_path, settings)
    write_config(override_path, {"ocr": {"monitor": 2}})
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        override_config_path=override_path,
        test_config={"TESTING": True},
        watch_config=True,
    )
    client = app.test_client()

    try:
        write_config(override_path, {"ocr": {"monitor": 3}})
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = client.get("/api/settings").json
            if response["settings"]["ocr"]["monitor"] == 3:
                break
            time.sleep(0.05)
        assert response["settings"]["ocr"]["monitor"] == 3
    finally:
        app.extensions["settings_watcher"].stop()


def test_template_settings_accept_only_plain_html_filenames(tmp_path):
    settings = default_settings()
    settings["templates"]["prematch"] = "../outside.html"

    with pytest.raises(SettingsError, match="HTML filename without a path"):
        validate_settings(settings)

    settings = default_settings()
    settings["templates"]["prematch"] = "missing_overlay.html"
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, settings)
    with pytest.raises(SettingsError, match="was not found"):
        create_app(
            schedule=FakeSchedule([make_bout(1, 10, 11)]),
            config_path=settings_path,
            watch_config=False,
        )


def test_head_to_head_is_served_by_the_pre_match_state_route():
    schedule = FakeSchedule(
        [make_bout(1, 10, 11)],
        h2h={"eastWins": 3, "westWins": 2, "total": 5, "history": []},
    )
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    client = app.test_client()

    assert client.get("/api/state").json["headToHead"] is None
    assert client.get("/api/pre-match-state").json["headToHead"] == {
        "eastWins": 3,
        "westWins": 2,
        "total": 5,
        "history": [],
    }


def test_pre_match_state_includes_wrestler_profiles():
    class ProfileSchedule(FakeSchedule):
        def wrestler_profiles(self, bout):
            return {
                "east": {"heya": "East Stable", "height": 190, "weight": 188},
                "west": {"heya": "West Stable", "height": 180, "weight": 150},
            }

    schedule = ProfileSchedule([make_bout(1, 10, 11)])
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    client = app.test_client()

    state = client.get("/api/pre-match-state").json

    assert state["wrestlerProfiles"]["east"]["heya"] == "East Stable"
    assert state["wrestlerProfiles"]["west"]["weight"] == 150
    assert "wrestlerProfiles" not in client.get("/api/state").json


def test_scoreboard_state_includes_stable_names_when_enabled(tmp_path):
    class StableNameSchedule(FakeSchedule):
        def wrestler_stable_names(self, bout):
            return {"east": "East Stable", "west": "West Stable"}

    settings = default_settings()
    settings["overlay"]["slimhalf"]["show_stable_name"] = False
    settings["overlay"]["slimfull"]["show_stable_name"] = False
    settings["overlay"]["scoreboard"]["show_stable_name"] = True
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, settings)
    app = create_app(
        schedule=StableNameSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )

    state = app.test_client().get("/api/state").json

    assert state["stableNames"] == {
        "east": "East Stable",
        "west": "West Stable",
    }


def test_pre_match_state_counts_division_bouts_and_sanyaku_transition():
    jonokuchi_1 = make_bout(1, 10, 11)
    jonokuchi_1 = Bout(
        jonokuchi_1.match_no,
        jonokuchi_1.east,
        jonokuchi_1.west,
        "Jonokuchi",
    )
    jonokuchi_2 = Bout(2, jonokuchi_1.east, jonokuchi_1.west, "Jonokuchi")
    jonidan = Bout(1, jonokuchi_1.east, jonokuchi_1.west, "Jonidan")
    maegashira = Bout(
        1,
        BoutSide(20, "East 20", "Maegashira 1 East", BoutRecord(0, 0)),
        BoutSide(21, "West 21", "Maegashira 2 West", BoutRecord(0, 0)),
        "Makuuchi",
    )
    ozeki = Bout(
        2,
        BoutSide(22, "East 22", "Ozeki 1 East", BoutRecord(0, 0)),
        BoutSide(23, "West 23", "Maegashira 1 West", BoutRecord(0, 0)),
        "Makuuchi",
    )
    schedule = FakeSchedule([jonokuchi_1, jonokuchi_2, jonidan, maegashira, ozeki])
    app = create_app(schedule=schedule, test_config={"TESTING": True}, watch_config=False)
    controller = app.extensions["bout_controller"]

    first = controller.state()
    assert first["divisionNumber"] == 6
    assert first["divisionBoutNumber"] == 1
    assert first["divisionTotal"] == 2
    assert first["boutsUntilNextStage"] == 2

    controller.move(3)
    before_sanyaku = controller.state()
    assert before_sanyaku["divisionNumber"] == 1
    assert before_sanyaku["divisionBoutNumber"] == 1
    assert before_sanyaku["boutsUntilNextStage"] == 1

    controller.move(1)
    sanyaku = controller.state()
    assert sanyaku["boutsUntilNextStage"] is None


def test_division_setting_accepts_only_numeric_division_codes():
    settings = default_settings()
    assert settings["division"] == 0

    settings["division"] = 1
    assert validate_settings(settings)["division"] == 1

    settings["division"] = "all"
    with pytest.raises(SettingsError, match="division must"):
        validate_settings(settings)

    settings["division"] = "Jonokuchi"
    with pytest.raises(SettingsError, match="division must"):
        validate_settings(settings)

    settings["division"] = 7
    with pytest.raises(SettingsError, match="division must"):
        validate_settings(settings)


def test_lan_access_is_disabled_by_default_and_requires_a_boolean():
    settings = default_settings()
    assert settings["server"]["allow_lan_access"] is False

    settings["server"]["allow_lan_access"] = True
    assert validate_settings(settings)["server"]["allow_lan_access"] is True

    settings["server"]["allow_lan_access"] = "true"
    with pytest.raises(SettingsError, match="server.allow_lan_access"):
        validate_settings(settings)


@pytest.mark.parametrize(
    ("allow_lan_access", "expected_host"),
    [(False, "127.0.0.1"), (True, "0.0.0.0")],
)
def test_main_uses_lan_bind_host_only_when_enabled(
    monkeypatch, allow_lan_access, expected_host
):
    run_arguments = {}

    class FakeWatcher:
        def snapshot(self):
            return {
                "server": {
                    "host": "127.0.0.1",
                    "allow_lan_access": allow_lan_access,
                    "port": 5000,
                }
            }

    class FakeSocketIO:
        def run(self, app, **kwargs):
            run_arguments.update(kwargs)

    fake_app = SimpleNamespace(
        extensions={
            "settings_watcher": FakeWatcher(),
            "socketio": FakeSocketIO(),
        }
    )
    monkeypatch.setattr(app_module, "create_app", lambda **kwargs: fake_app)
    monkeypatch.setattr(app_module.sys, "argv", ["app.py"])

    app_module.main()

    assert run_arguments["host"] == expected_host
    assert run_arguments["port"] == 5000


def test_ocr_settings_validate_monitor_and_name_box_bounds():
    settings = default_settings()
    settings["ocr"]["monitor"] = 0
    with pytest.raises(SettingsError, match="ocr.monitor"):
        validate_settings(settings)

    settings = default_settings()
    settings["ocr"]["name_boxes"]["right"]["x"] = 1900
    with pytest.raises(SettingsError, match="must fit inside"):
        validate_settings(settings)


def test_ocr_name_boxes_use_pixels_relative_to_capture_region():
    settings = default_settings()
    settings["ocr"]["capture_regions"]["scoresheet"] = {
        "x": 100,
        "y": 50,
        "width": 800,
        "height": 400,
    }
    settings["ocr"]["name_boxes"] = {
        "left": {"x": 160, "y": 20, "width": 130, "height": 45},
        "right": {"x": 400, "y": 20, "width": 130, "height": 45},
    }

    validated = validate_settings(settings)

    assert validated["ocr"]["name_boxes"] == settings["ocr"]["name_boxes"]


def test_alternate_ocr_name_boxes_are_optional_and_bounded_by_capture():
    settings = default_settings()
    assert validate_settings(settings)["ocr"]["alternate_name_boxes"] is None

    settings["ocr"]["capture_regions"]["scoresheet"] = {
        "x": 100,
        "y": 50,
        "width": 800,
        "height": 400,
    }
    settings["ocr"]["name_boxes"] = {
        "left": {"x": 20, "y": 20, "width": 130, "height": 45},
        "right": {"x": 400, "y": 20, "width": 130, "height": 45},
    }
    settings["ocr"]["alternate_name_boxes"] = {
        "left": {"x": 20, "y": 200, "width": 130, "height": 45},
        "right": {"x": 400, "y": 200, "width": 130, "height": 45},
    }
    validated = validate_settings(settings)
    assert validated["ocr"]["alternate_name_boxes"] == settings["ocr"]["alternate_name_boxes"]

    settings["ocr"]["alternate_name_boxes"]["right"]["x"] = 700
    with pytest.raises(SettingsError, match="alternate_name_boxes.right must fit inside"):
        validate_settings(settings)


def test_capture_regions_must_be_nested_under_ocr():
    settings = default_settings()
    settings["capture_regions"] = {
        "scoresheet": {"x": 0, "y": 0, "width": 1920, "height": 1080}
    }

    with pytest.raises(SettingsError, match="Unknown settings key: capture_regions"):
        validate_settings(settings)


def test_percentage_name_boxes_are_rejected():
    settings = default_settings()
    settings["ocr"]["name_boxes"] = {
        "left": {"x": 15.5, "y": 3.5, "width": 13, "height": 4.5},
        "right": {"x": 34, "y": 3.5, "width": 13, "height": 4.5},
    }

    with pytest.raises(SettingsError, match="must be an integer"):
        validate_settings(settings)


def test_removed_pre_match_enabled_setting_is_rejected():
    settings = default_settings()
    settings["control"]["pre_match_enabled"] = False

    with pytest.raises(SettingsError, match="Unknown control setting: pre_match_enabled"):
        validate_settings(settings)


def test_overlay_dimensions_are_positive_integers_and_reject_position_settings():
    settings = default_settings()
    settings["overlay"]["scoreboard"] = {"width": 1200, "height": 180}
    settings["overlay"]["prematch"] = {"width": 1600, "height": 900, "opacity": 0.75}
    validated = validate_settings(settings)
    assert validated["overlay"]["scoreboard"]["width"] == 1200
    assert validated["overlay"]["prematch"]["opacity"] == 0.75

    settings["overlay"]["scoreboard"]["width"] = 0
    with pytest.raises(SettingsError, match="overlay.scoreboard.width"):
        validate_settings(settings)

    settings = default_settings()
    settings["overlay"]["scoreboard"]["x"] = 10
    with pytest.raises(SettingsError, match="Unknown overlay.scoreboard setting: x"):
        validate_settings(settings)

    settings = default_settings()
    settings["overlay"]["canvas"] = {"width": 1920, "height": 1080}
    with pytest.raises(SettingsError, match="Unknown overlay setting: canvas"):
        validate_settings(settings)


def test_slim_scoreboard_defaults_and_independent_overrides():
    settings = default_settings()
    assert settings["overlay"]["slimhalf"]["width"] == 960
    assert settings["overlay"]["slimhalf"]["height"] == 52
    assert settings["overlay"]["slimfull"]["width"] == 1920
    assert settings["overlay"]["slimfull"]["height"] == 52
    assert settings["overlay"]["slimhalf"]["show_upcoming_matches"] is False
    assert settings["overlay"]["slimfull"]["show_upcoming_matches"] is True
    assert settings["overlay"]["scoreboard"]["show_stable_name"] is True
    assert settings["overlay"]["slimhalf"]["show_stable_name"] is True
    assert settings["overlay"]["slimfull"]["show_stable_name"] is True
    assert settings["overlay"]["slimhalf"]["upcoming_matches_count"] == 2
    assert settings["overlay"]["slimfull"]["upcoming_matches_count"] == 2
    assert settings["overlay"]["slimhalf"]["opacity"] == 0.85
    assert settings["overlay"]["slimhalf"]["text_opacity"] == 0.85
    assert settings["overlay"]["slimfull"]["opacity"] == 0.85
    assert settings["overlay"]["slimfull"]["text_opacity"] == 0.85
    assert settings["overlay"]["slimfull"]["font_sizes"] == {
        "text": 30,
        "muted": 20,
        "accent": 22,
        "current_score": 24,
        "result": 24,
        "upcoming_matches": 22,
        "stable_name": 20,
    }
    assert settings["overlay"]["slimhalf"]["font_sizes"] == {
        "text": 30,
        "muted": 20,
        "accent": 22,
        "current_score": 24,
        "result": 24,
        "upcoming_matches": 22,
        "stable_name": 20,
    }
    assert settings["overlay"]["slimhalf"]["colors"]["stable_name"] == "#d4d8de"
    assert settings["overlay"]["slimfull"]["colors"]["stable_name"] == "#d4d8de"
    assert settings["overlay"]["slimhalf"]["font_sizes"]["upcoming_matches"] == 22
    assert settings["overlay"]["slimhalf"]["colors"]["upcoming_matches"] == "#ffffff"
    assert settings["overlay"]["slimfull"]["colors"]["upcoming_matches"] == "#ffffff"
    assert settings["overlay"]["slimhalf"]["show_current_score"] is True
    assert settings["overlay"]["slimfull"]["show_current_score"] is True
    assert settings["overlay"]["upcomingmatches"]["width"] == 1920
    assert settings["overlay"]["upcomingmatches"]["height"] == 36
    assert settings["overlay"]["upcomingmatches"]["match_count"] == 6
    assert settings["overlay"]["upcomingmatches"]["opacity"] == 0.85
    assert settings["overlay"]["upcomingmatches"]["text_opacity"] == 0.85
    assert settings["overlay"]["upcomingmatches"]["font_sizes"] == {
        "text": 22,
        "muted": 20,
        "accent": 20,
        "current_score": 20,
        "result": 20,
    }

    settings["overlay"]["slimhalf"]["width"] = 600
    settings["overlay"]["slimhalf"]["opacity"] = 0.7
    settings["overlay"]["slimhalf"]["show_current_score"] = False
    settings["overlay"]["slimfull"]["font_sizes"]["text"] = 20
    settings["overlay"]["slimfull"]["colors"]["accent"] = "#123456"
    settings["overlay"]["slimhalf"]["font_sizes"]["upcoming_matches"] = 21
    settings["overlay"]["slimhalf"]["colors"]["upcoming_matches"] = "#abcdef"
    settings["overlay"]["scoreboard"]["font_sizes"]["stable_name"] = 22
    settings["overlay"]["scoreboard"]["colors"]["stable_name"] = "#123abc"
    validated = validate_settings(settings)
    assert validated["overlay"]["slimhalf"]["width"] == 600
    assert validated["overlay"]["slimhalf"]["opacity"] == 0.7
    assert validated["overlay"]["slimhalf"]["show_current_score"] is False
    assert validated["overlay"]["slimfull"]["font_sizes"]["text"] == 20
    assert validated["overlay"]["slimfull"]["colors"]["accent"] == "#123456"
    assert validated["overlay"]["slimhalf"]["font_sizes"]["upcoming_matches"] == 21
    assert validated["overlay"]["slimhalf"]["colors"]["upcoming_matches"] == "#abcdef"
    assert validated["overlay"]["scoreboard"]["font_sizes"]["stable_name"] == 22
    assert validated["overlay"]["scoreboard"]["colors"]["stable_name"] == "#123abc"


@pytest.mark.parametrize("overlay_name", ["scoreboard", "slimhalf", "slimfull"])
@pytest.mark.parametrize("value", ["true", 1, None])
def test_show_stable_name_requires_boolean(overlay_name, value):
    settings = default_settings()
    settings["overlay"][overlay_name]["show_stable_name"] = value

    with pytest.raises(
        SettingsError,
        match=f"overlay.{overlay_name}.show_stable_name must be true or false",
    ):
        validate_settings(settings)


@pytest.mark.parametrize(
    ("overlay_name", "key", "value", "error"),
    [
        ("upcomingmatches", "match_count", 0, "match_count"),
        ("upcomingmatches", "match_count", True, "match_count"),
        ("slimhalf", "upcoming_matches_count", 0, "upcoming_matches_count"),
        ("slimfull", "upcoming_matches_count", 1.5, "upcoming_matches_count"),
        ("slimhalf", "show_upcoming_matches", "false", "show_upcoming_matches"),
    ],
)
def test_upcoming_match_options_are_validated(overlay_name, key, value, error):
    settings = default_settings()
    settings["overlay"][overlay_name][key] = value

    with pytest.raises(SettingsError, match=error):
        validate_settings(settings)


@pytest.mark.parametrize("overlay_name", ["slimhalf", "slimfull"])
@pytest.mark.parametrize("show_current_score", ["true", 1, None])
def test_slim_scoreboard_show_current_score_must_be_boolean(
    overlay_name, show_current_score
):
    settings = default_settings()
    settings["overlay"][overlay_name]["show_current_score"] = show_current_score

    with pytest.raises(
        SettingsError,
        match=f"overlay.{overlay_name}.show_current_score must be true or false",
    ):
        validate_settings(settings)


def test_scoreboard_and_prematch_font_size_defaults_and_overrides():
    settings = default_settings()
    assert settings["overlay"]["scoreboard"]["show_current_score"] is True
    assert settings["basho_id"] == "202609"
    assert settings["control"] == {
        "mode": "automated",
        "pre_match_salaried_seconds": 30,
        "pre_match_unsalaried_seconds": 20,
        "pre_match_pinned": False,
    }
    assert settings["overlay"]["control"]["font_sizes"] == {
        "text": 28,
        "muted": 36,
        "accent": 36,
        "current_score": 16,
        "result": 16,
    }
    assert settings["overlay"]["scoreboard"]["width"] == 993
    assert settings["overlay"]["scoreboard"]["height"] == 158
    assert settings["overlay"]["scoreboard"]["opacity"] == 0.85
    assert settings["overlay"]["scoreboard"]["text_opacity"] == 0.85
    assert settings["overlay"]["prematch"]["text_opacity"] == 1
    assert settings["overlay"]["scoreboard"]["font_sizes"] == {
        "text": 45,
        "muted": 36,
        "accent": 46,
        "current_score": 36,
        "result": 40,
        "stable_name": 22,
    }
    assert settings["overlay"]["scoreboard"]["colors"]["stable_name"] == "#d4d8de"
    assert settings["overlay"]["prematch"]["font_sizes"] == {
        "text": 80,
        "muted": 36,
        "accent": 54,
        "current_score": 38,
        "result": 60,
    }

    settings["overlay"]["scoreboard"]["font_sizes"]["text"] = 48.5
    settings["overlay"]["scoreboard"]["font_sizes"]["current_score"] = 32
    settings["overlay"]["scoreboard"]["colors"]["current_score"] = "#123456"
    settings["overlay"]["control"]["font_sizes"]["current_score"] = 20
    settings["overlay"]["control"]["font_sizes"]["text"] = 24
    settings["overlay"]["control"]["font_sizes"]["muted"] = 20
    settings["overlay"]["control"]["font_sizes"]["accent"] = 30
    settings["overlay"]["control"]["font_sizes"]["result"] = 14
    settings["overlay"]["control"]["colors"]["current_score"] = "#654321"
    settings["overlay"]["prematch"]["font_sizes"]["muted"] = 32
    settings["overlay"]["prematch"]["font_sizes"]["current_score"] = 42
    settings["overlay"]["prematch"]["colors"]["current_score"] = "#abcdef"
    settings["overlay"]["slimfull"]["font_sizes"]["stable_name"] = 19
    settings["overlay"]["slimfull"]["colors"]["stable_name"] = "#abcdef"
    validated = validate_settings(settings)
    assert validated["overlay"]["scoreboard"]["font_sizes"]["text"] == 48.5
    assert validated["overlay"]["scoreboard"]["font_sizes"]["current_score"] == 32
    assert validated["overlay"]["scoreboard"]["colors"]["current_score"] == "#123456"
    assert validated["overlay"]["control"]["font_sizes"]["current_score"] == 20
    assert validated["overlay"]["control"]["font_sizes"]["text"] == 24
    assert validated["overlay"]["control"]["font_sizes"]["muted"] == 20
    assert validated["overlay"]["control"]["font_sizes"]["accent"] == 30
    assert validated["overlay"]["control"]["font_sizes"]["result"] == 14
    assert validated["overlay"]["control"]["colors"]["current_score"] == "#654321"
    assert validated["overlay"]["prematch"]["font_sizes"]["muted"] == 32
    assert validated["overlay"]["prematch"]["font_sizes"]["current_score"] == 42
    assert validated["overlay"]["prematch"]["colors"]["current_score"] == "#abcdef"
    assert validated["overlay"]["slimfull"]["font_sizes"]["stable_name"] == 19
    assert validated["overlay"]["slimfull"]["colors"]["stable_name"] == "#abcdef"


@pytest.mark.parametrize(
    "show_current_score", ["true", 1, None]
)
def test_scoreboard_show_current_score_must_be_boolean(show_current_score):
    settings = default_settings()
    settings["overlay"]["scoreboard"]["show_current_score"] = show_current_score

    with pytest.raises(
        SettingsError,
        match="overlay.scoreboard.show_current_score must be true or false",
    ):
        validate_settings(settings)


@pytest.mark.parametrize(
    ("font_sizes", "error"),
    [
        ("invalid", "font_sizes must be an object"),
        ({"unknown": 24}, "Unknown overlay.scoreboard.font_sizes setting"),
        ({"text": 0}, "overlay.scoreboard.font_sizes.text"),
        ({"text": True}, "overlay.scoreboard.font_sizes.text"),
    ],
)
def test_overlay_font_sizes_reject_invalid_values(font_sizes, error):
    settings = default_settings()
    settings["overlay"]["scoreboard"]["font_sizes"] = font_sizes

    with pytest.raises(SettingsError, match=error):
        validate_settings(settings)


@pytest.mark.parametrize("opacity", [-0.1, 1.1, True, "0.5"])
def test_pre_match_opacity_rejects_values_outside_unit_interval(opacity):
    settings = default_settings()
    settings["overlay"]["prematch"]["opacity"] = opacity

    with pytest.raises(SettingsError, match="overlay.prematch.opacity"):
        validate_settings(settings)


@pytest.mark.parametrize("opacity", [-0.1, 1.1, True, "0.5"])
def test_scoreboard_opacity_rejects_values_outside_unit_interval(opacity):
    settings = default_settings()
    settings["overlay"]["scoreboard"]["opacity"] = opacity

    with pytest.raises(SettingsError, match="overlay.scoreboard.opacity"):
        validate_settings(settings)


@pytest.mark.parametrize("opacity", [0, 0.5, 1])
def test_pre_match_opacity_accepts_values_from_zero_through_one(opacity):
    settings = default_settings()
    settings["overlay"]["prematch"]["opacity"] = opacity

    assert validate_settings(settings)["overlay"]["prematch"]["opacity"] == opacity


@pytest.mark.parametrize("opacity", [0, 0.5, 1])
def test_scoreboard_opacity_accepts_values_from_zero_through_one(opacity):
    settings = default_settings()
    settings["overlay"]["scoreboard"]["opacity"] = opacity

    assert validate_settings(settings)["overlay"]["scoreboard"]["opacity"] == opacity


@pytest.mark.parametrize("opacity_key", ["opacity", "text_opacity"])
@pytest.mark.parametrize("opacity", [-0.1, 1.1, True, "0.5"])
@pytest.mark.parametrize(
    "overlay_name", ["scoreboard", "prematch", "slimhalf", "slimfull"]
)
def test_overlay_opacity_settings_reject_values_outside_unit_interval(
    overlay_name, opacity_key, opacity
):
    settings = default_settings()
    settings["overlay"][overlay_name][opacity_key] = opacity

    with pytest.raises(SettingsError, match=f"overlay.{overlay_name}.{opacity_key}"):
        validate_settings(settings)


@pytest.mark.parametrize(
    "overlay_name", ["scoreboard", "prematch", "slimhalf", "slimfull"]
)
@pytest.mark.parametrize("opacity", [0, 0.5, 1])
def test_overlay_text_opacity_accepts_values_from_zero_through_one(
    overlay_name, opacity
):
    settings = default_settings()
    settings["overlay"][overlay_name]["text_opacity"] = opacity

    assert (
        validate_settings(settings)["overlay"][overlay_name]["text_opacity"]
        == opacity
    )


def test_scoreboard_and_prematch_border_defaults():
    settings = default_settings()
    assert settings["overlay"]["scoreboard"]["border"] == {
        "width": 0,
        "color": "#ffffff38",
    }
    assert settings["overlay"]["prematch"]["border"] == {
        "width": 1,
        "color": "#ffffff33",
    }


def test_scoreboard_and_prematch_border_customization():
    settings = default_settings()
    settings["overlay"]["scoreboard"]["border"] = {
        "width": 3,
        "color": "#ff0000",
    }
    settings["overlay"]["prematch"]["border"] = {
        "thickness": 4,
        "color": "#00ff00",
    }
    validated = validate_settings(settings)
    assert validated["overlay"]["scoreboard"]["border"] == {
        "width": 3,
        "color": "#ff0000",
    }
    assert validated["overlay"]["prematch"]["border"] == {
        "width": 4,
        "color": "#00ff00",
    }


@pytest.mark.parametrize(
    "border_value",
    [
        {"width": 0},
        {"thickness": 0},
        {"enabled": False},
        False,
        0,
        "none",
        "off",
    ],
)
def test_overlay_border_can_be_turned_off_completely(border_value):
    settings = default_settings()
    settings["overlay"]["prematch"]["border"] = border_value
    validated = validate_settings(settings)
    assert validated["overlay"]["prematch"]["border"]["width"] == 0


def test_scoreboard_border_can_be_enabled_and_customized():
    settings = default_settings()
    settings["overlay"]["scoreboard"]["border"] = True
    validated = validate_settings(settings)
    assert validated["overlay"]["scoreboard"]["border"]["width"] == 1

    settings["overlay"]["scoreboard"]["border"] = 2
    validated = validate_settings(settings)
    assert validated["overlay"]["scoreboard"]["border"]["width"] == 2

    settings["overlay"]["scoreboard"]["border"] = "#ff00ff"
    validated = validate_settings(settings)
    assert validated["overlay"]["scoreboard"]["border"]["width"] == 1
    assert validated["overlay"]["scoreboard"]["border"]["color"] == "#ff00ff"


@pytest.mark.parametrize(
    ("border_setting", "error"),
    [
        ({"width": -1}, "overlay.scoreboard.border.width must be zero or greater"),
        ({"width": "two"}, "overlay.scoreboard.border.width must be a number"),
        ({"width": True}, "overlay.scoreboard.border.width must be a number"),
        ({"thickness": -2}, "overlay.scoreboard.border.width must be zero or greater"),
        ({"color": "blue"}, "overlay.scoreboard.border.color must be a hex color"),
        ({"enabled": "yes"}, "overlay.scoreboard.border.enabled must be true or false"),
        ({"unknown_key": 5}, "Unknown overlay.scoreboard.border setting: unknown_key"),
        ({"width": 2, "thickness": 3}, "overlay.scoreboard.border cannot have conflicting width and thickness"),
        ([1, 2], "overlay.scoreboard.border must be an object"),
    ],
)
def test_overlay_border_rejects_invalid_values(border_setting, error):
    settings = default_settings()
    settings["overlay"]["scoreboard"]["border"] = border_setting
    with pytest.raises(SettingsError, match=error):
        validate_settings(settings)


def test_overlay_font_and_colors_are_independent_and_validated():
    settings = default_settings()
    settings["overlay"]["control"]["font_family"] = '"Arial", sans-serif'
    settings["overlay"]["control"]["colors"]["text"] = "#123456"
    settings["overlay"]["control"]["colors"]["current_score"] = "#fedcba"
    settings["overlay"]["control"]["colors"]["background"] = "#234567"
    settings["overlay"]["prematch"]["colors"]["accent"] = "#abc"
    settings["overlay"]["prematch"]["colors"]["background"] = "#345678"
    settings["overlay"]["scoreboard"]["colors"]["result"] = "#abcdef"
    settings["overlay"]["scoreboard"]["colors"]["background"] = "#456789"

    validated = validate_settings(settings)

    assert validated["overlay"]["control"]["font_family"] == '"Arial", sans-serif'
    assert validated["overlay"]["control"]["colors"]["text"] == "#123456"
    assert validated["overlay"]["control"]["colors"]["current_score"] == "#fedcba"
    assert validated["overlay"]["control"]["colors"]["background"] == "#234567"
    assert validated["overlay"]["prematch"]["colors"]["accent"] == "#abc"
    assert validated["overlay"]["prematch"]["colors"]["background"] == "#345678"
    assert validated["overlay"]["scoreboard"]["colors"]["result"] == "#abcdef"
    assert validated["overlay"]["scoreboard"]["colors"]["background"] == "#456789"
    assert validated["overlay"]["scoreboard"]["colors"]["text"] == "#ffffff"


@pytest.mark.parametrize(
    ("setting", "value", "error"),
    [
        ("font_family", 'Arial; color: red', "font_family"),
        ("colors", "red", "colors must be an object"),
    ],
)
def test_overlay_font_settings_reject_invalid_values(setting, value, error):
    settings = default_settings()
    settings["overlay"]["scoreboard"][setting] = value

    with pytest.raises(SettingsError, match=error):
        validate_settings(settings)


def test_overlay_colors_reject_invalid_color_values():
    settings = default_settings()
    settings["overlay"]["prematch"]["colors"]["text"] = "red"

    with pytest.raises(SettingsError, match="overlay.prematch.colors.text"):
        validate_settings(settings)

    settings = default_settings()
    settings["overlay"]["scoreboard"]["colors"]["background"] = "blue"

    with pytest.raises(SettingsError, match="overlay.scoreboard.colors.background"):
        validate_settings(settings)


def test_numeric_division_is_converted_to_api_division_name(tmp_path, monkeypatch):
    settings = default_settings()
    settings["division"] = 3
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, settings)
    calls = []

    class CapturedSchedule:
        def __init__(self, client, basho_id, division, day):
            calls.append((basho_id, division, day))
            self.bouts = [make_bout(1, 10, 11)]

    monkeypatch.setattr("app.SumoApiClient", lambda **kwargs: object())
    monkeypatch.setattr("app.BashoSchedule", CapturedSchedule)

    create_app(config_path=settings_path, watch_config=False)

    assert calls == [("202609", "Makushita", 1)]


def test_division_jump_moves_to_first_bout_in_requested_division():
    bouts = []
    for index, division in enumerate(DIVISION_ORDER, start=1):
        bout = make_bout(index, index * 2, index * 2 + 1)
        bouts.append(Bout(index, bout.east, bout.west, division))
    app = create_app(
        schedule=FakeSchedule(bouts),
        test_config={"TESTING": True},
        watch_config=False,
    )
    client = app.test_client()

    response = client.post("/api/control/division", json={"division": 3})

    assert response.status_code == 200
    assert response.json["division"] == "Makushita"
    assert response.json["divisionNumber"] == 3
    assert response.json["index"] == DIVISION_ORDER.index("Makushita")


def test_division_jump_rejects_invalid_or_unloaded_division():
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        test_config={"TESTING": True},
        watch_config=False,
    )
    client = app.test_client()

    assert client.post("/api/control/division", json={"division": 0}).status_code == 400
    missing_division = client.post("/api/control/division", json={"division": 6})
    assert missing_division.status_code == 404


def write_config(path, settings):
    path.write_text(
        yaml.safe_dump(settings, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def test_initialize_config_file_creates_missing_config_from_python_defaults(tmp_path):
    config_path = tmp_path / "config.yaml"

    initialize_config_file(config_path, logging.getLogger())

    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == default_settings()


def test_manual_defaults_disable_ocr():
    settings = default_settings(enable_ocr=False)

    assert settings["control"]["mode"] == "manual"
    assert settings["ocr"]["enabled"] is False


def test_initialize_config_file_creates_manual_defaults_when_ocr_disabled(tmp_path):
    config_path = tmp_path / "config.yaml"

    initialize_config_file(config_path, logging.getLogger(), enable_ocr=False)

    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == default_settings(
        enable_ocr=False
    )


def test_initialize_config_file_adds_missing_values_without_replacing_user_values(
    tmp_path,
):
    base_settings = default_settings()
    config_path = tmp_path / "config.yaml"
    write_config(
        config_path,
        {
            "basho_id": "202609",
            "control": {"mode": "automated"},
            "overlay": {"scoreboard": {"width": 1200}},
        },
    )

    initialize_config_file(config_path, logging.getLogger())

    migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert migrated["basho_id"] == "202609"
    assert migrated["control"]["mode"] == "automated"
    assert migrated["control"]["pre_match_salaried_seconds"] == base_settings[
        "control"
    ]["pre_match_salaried_seconds"]
    assert migrated["api"]["timeout_seconds"] == base_settings["api"]["timeout_seconds"]
    assert migrated["overlay"]["scoreboard"]["width"] == 1200
    assert migrated["overlay"]["scoreboard"]["height"] == base_settings["overlay"][
        "scoreboard"
    ]["height"]


def test_initialize_config_file_leaves_up_to_date_config_text_unchanged(tmp_path):
    config_path = tmp_path / "config.yaml"
    base_settings = default_settings()
    config_text = "# Keep user comments.\n" + yaml.safe_dump(
        base_settings, allow_unicode=True, sort_keys=False
    )
    config_path.write_text(config_text, encoding="utf-8")

    initialize_config_file(config_path, logging.getLogger())

    assert config_path.read_text(encoding="utf-8") == config_text


def test_initialize_config_file_removes_only_explicitly_deprecated_values(tmp_path):
    config_path = tmp_path / "config.yaml"
    user_settings = default_settings()
    user_settings["control"]["old_setting"] = True
    write_config(config_path, user_settings)

    initialize_config_file(
        config_path,
        logging.getLogger(),
        deprecated_paths=(("control", "old_setting"),),
    )

    migrated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "old_setting" not in migrated["control"]

    user_settings["control"]["unknown_setting"] = True
    write_config(config_path, user_settings)
    with pytest.raises(SettingsError, match="Unknown control setting"):
        initialize_config_file(config_path, logging.getLogger())
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == user_settings


@pytest.mark.parametrize(
    ("enable_ocr", "expected_mode", "expected_ocr"),
    [(True, "automated", True), (False, "manual", False)],
)
def test_frozen_main_initializes_variant_defaults_and_leaves_custom_config(
    monkeypatch, tmp_path, enable_ocr, expected_mode, expected_ocr
):
    class FakeSocketIO:
        def run(self, *args, **kwargs):
            return None

    fake_app = SimpleNamespace(
        extensions={
            "settings_watcher": SimpleNamespace(snapshot=default_settings),
            "socketio": FakeSocketIO(),
        }
    )
    monkeypatch.setattr(app_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app_module.sys, "executable", str(tmp_path / "SumoOverlay.exe"))
    monkeypatch.setattr(app_module, "DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(app_module.sys, "argv", ["SumoOverlay.exe"])
    monkeypatch.setattr(app_module, "create_app", lambda **kwargs: fake_app)

    app_module.main(enable_ocr=enable_ocr)

    generated_settings = yaml.safe_load(
        (tmp_path / "config.yaml").read_text(encoding="utf-8")
    )
    assert generated_settings["control"]["mode"] == expected_mode
    assert generated_settings["ocr"]["enabled"] is expected_ocr

    custom_config_path = tmp_path / "custom.yaml"
    monkeypatch.setattr(
        app_module.sys,
        "argv",
        ["SumoOverlay.exe", "--config", str(custom_config_path)],
    )

    app_module.main(enable_ocr=False)

    assert not custom_config_path.exists()


def test_python_main_initializes_missing_default_config(monkeypatch, tmp_path):
    class FakeSocketIO:
        def run(self, *args, **kwargs):
            return None

    fake_app = SimpleNamespace(
        extensions={
            "settings_watcher": SimpleNamespace(snapshot=default_settings),
            "socketio": FakeSocketIO(),
        }
    )
    config_path = tmp_path / "config.yaml"
    monkeypatch.setattr(app_module.sys, "frozen", False, raising=False)
    monkeypatch.setattr(app_module, "DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.setattr(app_module.sys, "argv", ["app.py"])
    monkeypatch.setattr(app_module, "create_app", lambda **kwargs: fake_app)

    app_module.main(enable_ocr=False)

    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == default_settings(
        enable_ocr=False
    )


def test_ocr_match_selects_bout_and_broadcasts_overlay_updates(tmp_path):
    class OCRSchedule(FakeSchedule):
        def find_bout_index_by_kanji(self, left_name, right_name):
            if (left_name, right_name) in (
                ("大喜翔", "栃幸大"),
                ("栃幸大", "大喜翔"),
            ):
                return 1
            return None

    settings = default_settings()
    settings["control"]["mode"] = "automated"
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, settings)
    app = create_app(
        schedule=OCRSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )
    client = app.test_client()
    socket_client = app.extensions["socketio"].test_client(app)
    socket_client.get_received()

    scanner = app.extensions["ocr_scanner"]
    scanner.observe("大喜翔", "栃幸大", stable_readings=1)

    assert client.get("/api/state").json["index"] == 1
    assert client.get("/api/ocr/status").json["mode"] == "automated"
    events = socket_client.get_received()
    assert any(event["name"] == "bout_update" for event in events)
    assert any(event["name"] == "pre_match_update" for event in events)
    socket_client.disconnect()


def test_ocr_match_selects_bout_when_only_one_name_is_recognized(tmp_path):
    class OCRSchedule(FakeSchedule):
        def find_bout_index_by_kanji(self, left_name, right_name):
            return 1 if (left_name, right_name) == ("大喜翔", "") else None

    settings = default_settings()
    settings["control"]["mode"] = "automated"
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, settings)
    app = create_app(
        schedule=OCRSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )

    app.extensions["ocr_scanner"].observe("大喜翔", "", stable_readings=1)

    assert app.test_client().get("/api/state").json["index"] == 1


def test_ocr_match_uses_alternate_pair_when_it_matches_schedule(tmp_path):
    class OCRSchedule(FakeSchedule):
        def find_bout_index_by_kanji(self, left_name, right_name):
            return 1 if (left_name, right_name) == ("別左", "別右") else None

    settings = default_settings()
    settings["ocr"]["stable_readings"] = 1
    settings["ocr"]["alternate_name_boxes"] = {
        "left": {"x": 20, "y": 200, "width": 100, "height": 50},
        "right": {"x": 200, "y": 200, "width": 100, "height": 50},
    }
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, settings)
    app = create_app(
        schedule=OCRSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )

    scanner = app.extensions["ocr_scanner"]
    scanner._capture_and_read = lambda current_settings: (
        (("未読左", 0.9), ("未読右", 0.9)),
        (("別左", 0.9), ("別右", 0.9)),
    )
    on_match = scanner.on_match

    def stop_after_match(left_name, right_name):
        state = on_match(left_name, right_name)
        scanner._stop.set()
        return state

    scanner.on_match = stop_after_match
    scanner._run()

    assert app.test_client().get("/api/state").json["index"] == 1
    assert scanner.status()["state"] == "matched"


def test_ocr_retriggers_pre_match_for_the_already_selected_bout(tmp_path):
    class OCRSchedule(FakeSchedule):
        def find_bout_index_by_kanji(self, left_name, right_name):
            return 0 if (left_name, right_name) == ("大喜翔", "栃幸大") else None

    settings = default_settings()
    settings["control"]["mode"] = "automated"
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, settings)
    app = create_app(
        schedule=OCRSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )
    socket_client = app.extensions["socketio"].test_client(app)
    socket_client.get_received()

    app.extensions["ocr_scanner"].observe("大喜翔", "栃幸大", stable_readings=1)

    events = socket_client.get_received()
    assert not any(event["name"] == "bout_update" for event in events)
    assert any(event["name"] == "pre_match_update" for event in events)
    socket_client.disconnect()


def test_control_mode_is_persisted_to_yaml_config(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    settings["server"]["watch_interval_seconds"] = 0.1
    write_config(settings_path, settings)
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=True,
    )

    try:
        response = app.test_client().post("/api/control/mode", json={"mode": "automated"})
        saved_settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        assert response.status_code == 200
        assert response.json["settings"]["mode"] == "automated"
        assert response.json["settings"]["preMatchSalariedSeconds"] == 30
        assert response.json["settings"]["preMatchUnsalariedSeconds"] == 20
        assert saved_settings["control"]["mode"] == "automated"
        assert set(saved_settings["control"]) == {
            "mode",
            "pre_match_salaried_seconds",
            "pre_match_unsalaried_seconds",
            "pre_match_pinned",
        }
    finally:
        app.extensions["settings_watcher"].stop()


def test_basho_data_settings_update_a_subset_or_all_fields(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    settings["server"]["watch_interval_seconds"] = 0.1
    write_config(settings_path, settings)
    schedule_settings = []

    def schedule_factory(updated_settings):
        schedule_settings.append(
            (
                updated_settings["basho_id"],
                updated_settings["division"],
                updated_settings["day"],
            )
        )
        return FakeSchedule([make_bout(1, 10, 11)])

    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=True,
        schedule_factory=schedule_factory,
    )
    client = app.test_client()

    try:
        page = client.get("/control").get_data(as_text=True)
        assert 'id="data-settings"' in page
        assert 'id="data-basho-id"' in page
        assert 'id="data-division"' in page
        assert 'id="data-day"' in page

        day_response = client.post(
            "/api/control/data-settings", json={"day": 7}
        )
        assert day_response.status_code == 200
        assert day_response.json["settings"]["bashoId"] == "202609"
        assert day_response.json["settings"]["division"] == 0
        assert day_response.json["settings"]["day"] == 7
        assert schedule_settings[-1] == ("202609", 0, 7)

        two_fields_response = client.post(
            "/api/control/data-settings",
            json={"basho_id": "202607", "division": 3},
        )
        assert two_fields_response.status_code == 200
        assert two_fields_response.json["settings"]["bashoId"] == "202607"
        assert two_fields_response.json["settings"]["division"] == 3
        assert two_fields_response.json["settings"]["day"] == 7
        assert schedule_settings[-1] == ("202607", 3, 7)

        all_fields_response = client.post(
            "/api/control/data-settings",
            json={"basho_id": "202609", "division": 2, "day": 12},
        )
        assert all_fields_response.status_code == 200
        assert all_fields_response.json["settings"]["bashoId"] == "202609"
        assert all_fields_response.json["settings"]["division"] == 2
        assert all_fields_response.json["settings"]["day"] == 12
        assert schedule_settings[-1] == ("202609", 2, 12)

        saved_settings = yaml.safe_load(
            settings_path.read_text(encoding="utf-8")
        )
        assert saved_settings["basho_id"] == "202609"
        assert saved_settings["division"] == 2
        assert saved_settings["day"] == 12
    finally:
        app.extensions["settings_watcher"].stop()


def test_basho_data_settings_reject_invalid_values_without_saving(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    settings["server"]["watch_interval_seconds"] = 0.1
    write_config(settings_path, settings)
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=True,
    )

    try:
        response = app.test_client().post(
            "/api/control/data-settings", json={"day": 16}
        )

        assert response.status_code == 400
        assert "day must be an integer from 1 through 15" in response.json["error"]
        saved_settings = yaml.safe_load(
            settings_path.read_text(encoding="utf-8")
        )
        assert saved_settings["day"] == 1
    finally:
        app.extensions["settings_watcher"].stop()


def test_external_yaml_edit_hot_reloads_overlay_settings(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    settings["server"]["watch_interval_seconds"] = 0.05
    write_config(settings_path, settings)
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=True,
    )
    client = app.test_client()

    try:
        settings["control"]["mode"] = "automated"
        settings["overlay"]["scoreboard"]["width"] = 1600
        settings["overlay"]["scoreboard"]["colors"]["accent"] = "#ff00ff"
        settings["overlay"]["scoreboard"]["colors"]["background"] = "#112233"
        settings["overlay"]["prematch"]["height"] = 900
        settings["overlay"]["control"]["font_family"] = "Arial, sans-serif"
        settings["ocr"]["capture_regions"]["scoresheet"]["x"] = 120
        write_config(settings_path, settings)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = client.get("/api/settings").json
            if (
                response["settings"]["control"]["mode"] == "automated"
                and response["settings"]["overlay"]["scoreboard"]["width"] == 1600
                and response["settings"]["overlay"]["scoreboard"]["colors"]["accent"]
                == "#ff00ff"
            ):
                break
            time.sleep(0.05)
        assert response["settings"]["control"]["mode"] == "automated"
        assert response["settings"]["overlay"]["control"]["font_family"] == "Arial, sans-serif"
        assert client.get("/api/state").json["settings"]["overlay"]["scoreboard"]["width"] == 1600
        assert (
            client.get("/api/state").json["settings"]["overlay"]["scoreboard"]["colors"]["accent"]
            == "#ff00ff"
        )
        assert (
            client.get("/api/state").json["settings"]["overlay"]["scoreboard"]["colors"]["background"]
            == "#112233"
        )
        assert client.get("/api/pre-match-state").json["settings"]["overlay"]["prematch"]["height"] == 900
        assert client.get("/api/settings").json["settings"]["ocr"]["capture_regions"][
            "scoresheet"
        ]["x"] == 120
    finally:
        app.extensions["settings_watcher"].stop()


def test_invalid_hot_reload_keeps_last_good_configuration(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    settings["server"]["watch_interval_seconds"] = 0.05
    write_config(settings_path, settings)
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=True,
    )
    client = app.test_client()

    try:
        settings["control"]["mode"] = "not-a-mode"
        write_config(settings_path, settings)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = client.get("/api/settings").json
            if response["reloadError"]:
                break
            time.sleep(0.05)
        assert response["reloadError"]
        assert response["settings"]["control"]["mode"] == "automated"
    finally:
        app.extensions["settings_watcher"].stop()


def test_pre_match_toggle_is_independent_of_match_control_mode(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    settings["control"]["mode"] = "automated"
    write_config(settings_path, settings)
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=True,
    )

    try:
        response = app.test_client().post(
            "/api/control/pre-match",
            json={"salaried_seconds": 0, "unsalaried_seconds": 0},
        )
        saved_settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        assert response.status_code == 200
        assert response.json["settings"]["mode"] == "automated"
        assert response.json["settings"]["preMatchSalariedSeconds"] == 0
        assert response.json["settings"]["preMatchUnsalariedSeconds"] == 0
        assert saved_settings["control"]["mode"] == "automated"
        assert saved_settings["control"]["pre_match_salaried_seconds"] == 0
        assert saved_settings["control"]["pre_match_unsalaried_seconds"] == 0
    finally:
        app.extensions["settings_watcher"].stop()


def test_pre_match_pin_keeps_overlay_visible_until_unpinned(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    settings["control"]["pre_match_salaried_seconds"] = 0
    settings["control"]["pre_match_unsalaried_seconds"] = 0
    write_config(settings_path, settings)
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=True,
    )

    try:
        client = app.test_client()
        pinned = client.post("/api/control/pre-match", json={"pinned": True})
        assert pinned.status_code == 200
        assert pinned.json["settings"]["preMatchSalariedSeconds"] == 0
        assert pinned.json["settings"]["preMatchUnsalariedSeconds"] == 0
        assert pinned.json["settings"]["preMatchPinned"] is True
        saved_settings = yaml.safe_load(settings_path.read_text(encoding="utf-8"))
        assert saved_settings["control"]["pre_match_pinned"] is True

        unpinned = client.post("/api/control/pre-match", json={"pinned": False})
        assert unpinned.status_code == 200
        assert unpinned.json["settings"]["preMatchPinned"] is False
        assert unpinned.json["settings"]["preMatchSalariedSeconds"] == 0
        assert unpinned.json["settings"]["preMatchUnsalariedSeconds"] == 0
    finally:
        app.extensions["settings_watcher"].stop()


def test_pre_match_duration_settings_are_independent_per_division_group(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    settings["control"]["mode"] = "manual"
    write_config(settings_path, settings)
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=True,
    )

    try:
        client = app.test_client()
        off = client.post(
            "/api/control/pre-match", json={"salaried_seconds": 0}
        )
        assert off.status_code == 200
        assert off.json["settings"]["mode"] == "manual"
        assert off.json["settings"]["preMatchSalariedSeconds"] == 0
        assert off.json["settings"]["preMatchUnsalariedSeconds"] == 20

        on = client.post(
            "/api/control/pre-match",
            json={"salaried_seconds": 8, "unsalaried_seconds": 25},
        )
        assert on.status_code == 200
        assert on.json["settings"]["mode"] == "manual"
        assert on.json["settings"]["preMatchSalariedSeconds"] == 8
        assert on.json["settings"]["preMatchUnsalariedSeconds"] == 25
    finally:
        app.extensions["settings_watcher"].stop()


def test_invalid_pre_match_control_settings_are_rejected(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    write_config(settings_path, settings)
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=True,
    )

    try:
        client = app.test_client()
        assert client.post("/api/control/pre-match", json={"enabled": "off"}).status_code == 400
        assert client.post("/api/control/pre-match", json={"pinned": "yes"}).status_code == 400
        assert client.post(
            "/api/control/pre-match", json={"salaried_seconds": -1}
        ).status_code == 400
        assert client.post(
            "/api/control/pre-match", json={"unsalaried_seconds": "bad"}
        ).status_code == 400
        assert client.post("/api/control/pre-match", json={}).status_code == 400
    finally:
        app.extensions["settings_watcher"].stop()


def test_invalid_yaml_settings_is_rejected(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    settings["control"]["mode"] = "invalid"
    write_config(settings_path, settings)

    with pytest.raises(ValueError, match="control.mode"):
        create_app(
            schedule=FakeSchedule([make_bout(1, 10, 11)]),
            config_path=settings_path,
            watch_config=False,
        )


def test_malformed_yaml_config_reports_a_parse_error(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings_path.write_text("control:\n  mode: [invalid\n", encoding="utf-8")

    with pytest.raises(SettingsError, match=r"Invalid YAML .* line \d+, column \d+"):
        load_settings(settings_path)


def test_overlay_background_color_is_bound_in_templates_and_css():
    root = Path(__file__).resolve().parents[1]
    css = (root / "static" / "overlay.css").read_text(encoding="utf-8")
    assert "--overlay-background-color: #10141c;" in css
    assert "background: var(--overlay-background-color);" in css
    assert "var(--overlay-background-color)" in css

    control_html = (root / "templates" / "control.html").read_text(encoding="utf-8")
    assert "background: var(--overlay-background-color)" in control_html
    assert 'root.style.setProperty("--overlay-background-color", style.colors.background);' in control_html

    scoreboard_html = (root / "templates" / "scoreboard_overlay.html").read_text(encoding="utf-8")
    assert 'root.style.setProperty("--overlay-background-color", style.colors.background);' in scoreboard_html

    prematch_html = (root / "templates" / "prematch_overlay.html").read_text(encoding="utf-8")
    assert 'root.style.setProperty("--overlay-background-color", style.colors.background);' in prematch_html


def test_calibrate_page_renders_template(tmp_path):
    settings_path = tmp_path / "config.yaml"
    write_config(settings_path, default_settings())
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )
    client = app.test_client()
    response = client.get("/calibrate")
    assert response.status_code == 200
    assert b"OCR Calibration" in response.data
    assert b"OCR Test From Screenshot" in response.data
    assert b"Left name crop" in response.data
    assert b"Right name crop" in response.data
    assert b"Test OCR" in response.data
    assert b"Refresh Preview" not in response.data
    assert b"OCR Test Result (from screenshot)" not in response.data
    assert b'id="result-box"' not in response.data
    assert response.text.count('id="btn-test"') == 1
    assert b"runOcrPreview()" in response.data
    page = response.text
    assert page.index('class="coords-grid"') < page.index(
        'class="result-box ocr-preview"'
    )


def test_ocr_screenshot_endpoint(monkeypatch):
    import mss
    import mss.tools

    class FakeScreenshot:
        size = (10, 10)
        rgb = b"\x00\x00\x00" * 100

    class FakeMSS:
        monitors = [
            {"left": 0, "top": 0, "width": 100, "height": 100},
            {"left": 0, "top": 0, "width": 10, "height": 10},
        ]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def grab(self, monitor):
            return FakeScreenshot()

    monkeypatch.setattr(mss, "mss", FakeMSS)

    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        test_config={"TESTING": True},
        watch_config=False,
    )
    client = app.test_client()
    response = client.get("/api/ocr/screenshot")
    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert response.data.startswith(b"\x89PNG\r\n\x1a\n")


def test_ocr_screenshot_endpoint_invalid_monitor(monkeypatch):
    import mss

    class FakeMSS:
        monitors = [{"left": 0, "top": 0, "width": 100, "height": 100}]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(mss, "mss", FakeMSS)

    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        test_config={"TESTING": True},
        watch_config=False,
    )
    client = app.test_client()
    response = client.get("/api/ocr/screenshot")
    assert response.status_code == 400
    assert "unavailable" in response.json["error"]


def test_save_ocr_calibration_endpoint(tmp_path):
    settings_path = tmp_path / "config.yaml"
    settings = default_settings()
    write_config(settings_path, settings)
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=True,
    )

    try:
        client = app.test_client()
        response = client.post(
            "/api/ocr/calibration",
            json={
                "capture": {"x": 10, "y": 20, "width": 800, "height": 200},
                "left": {"x": 5, "y": 5, "width": 100, "height": 50},
                "right": {"x": 200, "y": 5, "width": 100, "height": 50},
                "alternate": {
                    "left": {"x": 10, "y": 120, "width": 120, "height": 50},
                    "right": {"x": 300, "y": 120, "width": 120, "height": 50},
                },
            },
        )
        assert response.status_code == 200
        saved_ocr = response.json["settings"]["ocr"]
        assert saved_ocr["capture_regions"]["scoresheet"]["x"] == 10
        assert saved_ocr["name_boxes"]["left"]["x"] == 5
        assert saved_ocr["name_boxes"]["right"]["x"] == 200
        assert saved_ocr["alternate_name_boxes"]["left"]["x"] == 10
        assert saved_ocr["alternate_name_boxes"]["right"]["y"] == 120

        # Without capture (nudge only updates name boxes)
        response2 = client.post(
            "/api/ocr/calibration",
            json={
                "left": {"x": 15, "y": 10, "width": 120, "height": 60},
                "right": {"x": 250, "y": 10, "width": 120, "height": 60},
            },
        )
        assert response2.status_code == 200
        saved_ocr2 = response2.json["settings"]["ocr"]
        assert saved_ocr2["capture_regions"]["scoresheet"]["x"] == 10
        assert saved_ocr2["name_boxes"]["left"]["x"] == 15
        assert saved_ocr2["name_boxes"]["right"]["x"] == 250
        assert saved_ocr2["alternate_name_boxes"]["right"]["x"] == 300
    finally:
        app.extensions["settings_watcher"].stop()


def test_control_page_uses_calibration_tool_for_box_positions():
    root = Path(__file__).resolve().parents[1]
    control_html = (root / "templates" / "control.html").read_text(encoding="utf-8")
    ocr_section = control_html.split(
        'id="ocr-automated-section"', maxsplit=1
    )[1].split("</section>", maxsplit=1)[0]

    assert 'href="/calibrate"' in ocr_section
    assert "Calibrate OCR Tool" in ocr_section
    preview_actions = ocr_section.split(
        'class="ocr-preview-actions"', maxsplit=1
    )[1].split("</div>", maxsplit=1)[0]
    assert preview_actions.index("Refresh Preview") < preview_actions.index(
        "Calibrate OCR Tool"
    )
    assert "save-box-positions" not in control_html
    assert "nudge-left-x" not in control_html
    assert "Select the winner to show the result on the overlays until the bout changes." not in control_html
    assert "West win shown until the bout changes." not in control_html
    assert "East win shown until the bout changes." not in control_html
    assert "Set duration to 0 to turn timed auto-show off." not in control_html
    assert control_html.index('class="pre-match-controls"') < control_html.index(
        'id="ocr-automated-section"'
    )


def test_ocr_preview_runs_only_when_requested():
    root = Path(__file__).resolve().parents[1]
    control_html = (root / "templates" / "control.html").read_text(encoding="utf-8")

    assert 'id="refresh-ocr-preview"' in control_html
    assert 'addEventListener("click", loadCropPreview)' in control_html
    assert "setInterval(loadCropPreview" not in control_html
    assert 'id="ocr-alt-left-img"' in control_html
    assert 'id="ocr-alt-right-img"' in control_html
    assert 'renderOcrCropPair("alt-", data.alternate)' in control_html


def test_calibration_page_supports_a_second_name_pair():
    root = Path(__file__).resolve().parents[1]
    calibration_html = (root / "templates" / "calibrate.html").read_text(
        encoding="utf-8"
    )

    assert 'value="alternate-left"' in calibration_html
    assert 'value="alternate-right"' in calibration_html
    assert 'id="alt-left-x"' in calibration_html
    assert 'id="alt-right-x"' in calibration_html
    assert 'id="preview-alt-left-img"' in calibration_html
    assert 'id="preview-alt-right-img"' in calibration_html
    assert 'alternate: alternate' in calibration_html


def test_ocr_test_endpoint_uses_buffered_screenshot_and_returns_images(monkeypatch):
    import numpy as np

    monkeypatch.setitem(sys.modules, "easyocr", ModuleType("easyocr"))

    class FakeReader:
        def readtext(self, crop, detail=1, paragraph=False):
            return [([0, 0], "テスト", 0.95)]

    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        test_config={"TESTING": True},
        watch_config=False,
    )
    scanner = app.extensions["ocr_scanner"]
    scanner._reader = FakeReader()

    fake_frame = np.zeros((100, 100, 3), dtype=np.uint8)
    scanner.set_last_screenshot(fake_frame)

    client = app.test_client()
    response = client.post(
        "/api/ocr/test",
        json={
            "capture": {"x": 10, "y": 10, "width": 80, "height": 80},
            "left": {"x": 0, "y": 0, "width": 30, "height": 30},
            "right": {"x": 40, "y": 0, "width": 30, "height": 30},
            "alternate": {
                "left": {"x": 0, "y": 40, "width": 30, "height": 30},
                "right": {"x": 40, "y": 40, "width": 30, "height": 30},
            },
        },
    )
    assert response.status_code == 200
    data = response.json
    assert data["left"] == "テスト"
    assert data["right"] == "テスト"
    assert data["leftConfidence"] == 0.95
    assert data["rightConfidence"] == 0.95
    assert data["leftImage"] != ""
    assert data["rightImage"] != ""
    assert data["alternate"]["left"] == "テスト"
    assert data["alternate"]["rightConfidence"] == 0.95
    assert data["alternate"]["leftImage"] != ""


def test_ocr_crop_preview_returns_both_pairs(monkeypatch):
    monkeypatch.setitem(sys.modules, "easyocr", ModuleType("easyocr"))
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        test_config={"TESTING": True},
        watch_config=False,
    )
    app.extensions["ocr_scanner"].capture_crops = lambda settings: (
        (("left", 0.9, b"left-image"), ("right", 0.8, b"right-image")),
        (("alt-left", 0.7, b"alt-left-image"), ("alt-right", 0.6, b"alt-right-image")),
    )

    response = app.test_client().get("/api/ocr/crop-preview")

    assert response.status_code == 200
    assert response.json["leftText"] == "left"
    assert response.json["rightConfidence"] == 0.8
    assert response.json["alternate"]["leftText"] == "alt-left"
    assert response.json["alternate"]["rightConfidence"] == 0.6


def test_unknown_ocr_capture_region_is_rejected():
    settings = default_settings()
    settings["ocr"]["capture_regions"] = {
        "unused_region": {"x": 50, "y": 60, "width": 1920, "height": 1080}
    }
    with pytest.raises(SettingsError, match="Unknown OCR capture region"):
        validate_settings(settings)
