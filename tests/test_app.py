import json
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from app import create_app
from config import SettingsError, default_settings, load_settings, validate_settings
from sumo_data import DIVISION_ORDER, Bout, BoutRecord, BoutSide, SumoApiError


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

    assert client.get("/overlay").status_code == 200
    assert client.get("/pre-match-overlay").status_code == 200
    main_overlay = client.get("/main-overlay")
    assert main_overlay.status_code == 200
    assert 'Makuuchi: 1' in main_overlay.text
    assert "return number ? `Division ${number}` : \"Division\";" in main_overlay.text
    assert "fitNameToWidth" not in main_overlay.text
    assert "state.settings.overlay.main.width}px" in main_overlay.text
    assert "state.settings.overlay.main.height}px" in main_overlay.text
    main_styles = client.get("/static/overlay.css").text.split(
        ".pre-match-card {", maxsplit=1
    )[0]
    assert "width: 1582px;" not in main_styles
    assert "height: 174px;" not in main_styles
    assert "font-size: 90px;" in main_styles
    assert "line-height: 1.25;" in main_styles
    assert "font-size: 72px;" in main_styles
    assert "font-size: 56px;" in main_styles
    main_strip_styles = main_styles.split(".main-strip {", maxsplit=1)[1].split(
        "}", maxsplit=1
    )[0]
    assert "background: #10141c;" in main_strip_styles
    all_styles = client.get("/static/overlay.css").text
    assert "clamp(" not in all_styles
    assert "vw" not in all_styles
    assert "vh" not in all_styles
    pre_match_html = client.get("/overlay").text
    assert (
        "const { width, height, opacity } = state.settings.overlay.pre_match;"
        in pre_match_html
    )
    assert 'preMatchOverlay.style.width = `${width}px`;' in pre_match_html
    assert "preMatchOverlay.style.setProperty(\"--pre-match-opacity\", String(opacity));" in pre_match_html
    assert "fitNameToWidth" not in pre_match_html
    assert "rgb(20 25 34 / var(--pre-match-opacity, 0.5))" in all_styles
    assert "rgb(37 42 51 / var(--pre-match-opacity, 0.5))" in all_styles
    control = client.get("/control")
    assert control.status_code == 200
    assert 'id="bouts-until-next-stage"' in control.text
    assert 'class="rank-record"' in control.text
    assert '`${state.boutsUntilNextStage} ${boutWord} until ${target}`' in control.text
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
    settings["templates"]["pre_match"] = "custom_pre_match.html"
    settings_path = tmp_path / "config.json"
    write_config(settings_path, settings)
    custom_template_dir = tmp_path / "custom_templates"
    custom_template_dir.mkdir()
    (custom_template_dir / "custom_pre_match.html").write_text(
        "<main>Custom pre-match overlay</main>", encoding="utf-8"
    )
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )
    client = app.test_client()

    assert b"Custom pre-match overlay" in client.get("/overlay").data
    assert b"Makuuchi: 1" in client.get("/main-overlay").data
    assert b"Sumo Overlay Controls" in client.get("/control").data


def test_partial_override_config_merges_nested_values(tmp_path):
    settings_path = tmp_path / "config.json"
    override_path = tmp_path / "config.firefox.json"
    settings = default_settings()
    settings["overlay"]["main"]["height"] = 200
    write_config(settings_path, settings)
    write_config(
        override_path,
        {"ocr": {"monitor": 2}, "overlay": {"main": {"width": 1400}}},
    )

    effective = load_settings(settings_path, override_path)

    assert effective["ocr"]["monitor"] == 2
    assert effective["ocr"]["interval_seconds"] == settings["ocr"]["interval_seconds"]
    assert effective["overlay"]["main"] == {
        **settings["overlay"]["main"],
        "width": 1400,
    }


def test_control_updates_persist_to_the_layer_that_defines_the_setting(tmp_path):
    settings_path = tmp_path / "config.json"
    override_path = tmp_path / "config.firefox.json"
    write_config(settings_path, default_settings())
    write_config(override_path, {"control": {"pre_match_seconds": 30}})
    app = create_app(
        schedule=FakeSchedule([make_bout(1, 10, 11)]),
        config_path=settings_path,
        override_config_path=override_path,
        test_config={"TESTING": True},
        watch_config=True,
    )

    try:
        response = app.test_client().post(
            "/api/control/pre-match", json={"seconds": 45}
        )

        assert response.status_code == 200
        assert response.json["settings"]["preMatchSeconds"] == 45
        assert json.loads(override_path.read_text(encoding="utf-8")) == {
            "control": {"pre_match_seconds": 45}
        }
        assert json.loads(settings_path.read_text(encoding="utf-8"))["control"][
            "pre_match_seconds"
        ] == 10
    finally:
        app.extensions["settings_watcher"].stop()


def test_override_config_changes_are_hot_reloaded(tmp_path):
    settings_path = tmp_path / "config.json"
    override_path = tmp_path / "config.firefox.json"
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
    settings["templates"]["pre_match"] = "../outside.html"

    with pytest.raises(SettingsError, match="HTML filename without a path"):
        validate_settings(settings)

    settings = default_settings()
    settings["templates"]["pre_match"] = "missing_overlay.html"
    settings_path = tmp_path / "config.json"
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
    settings["capture_regions"]["abema_scoresheet"] = {
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
    settings["overlay"]["main"] = {"width": 1200, "height": 180}
    settings["overlay"]["pre_match"] = {"width": 1600, "height": 900, "opacity": 0.75}
    validated = validate_settings(settings)
    assert validated["overlay"] == settings["overlay"]

    settings["overlay"]["main"]["width"] = 0
    with pytest.raises(SettingsError, match="overlay.main.width"):
        validate_settings(settings)

    settings = default_settings()
    settings["overlay"]["main"]["x"] = 10
    with pytest.raises(SettingsError, match="Unknown overlay.main setting: x"):
        validate_settings(settings)

    settings = default_settings()
    settings["overlay"]["canvas"] = {"width": 1920, "height": 1080}
    with pytest.raises(SettingsError, match="Unknown overlay setting: canvas"):
        validate_settings(settings)


@pytest.mark.parametrize("opacity", [-0.1, 1.1, True, "0.5"])
def test_pre_match_opacity_rejects_values_outside_unit_interval(opacity):
    settings = default_settings()
    settings["overlay"]["pre_match"]["opacity"] = opacity

    with pytest.raises(SettingsError, match="overlay.pre_match.opacity"):
        validate_settings(settings)


@pytest.mark.parametrize("opacity", [0, 0.5, 1])
def test_pre_match_opacity_accepts_values_from_zero_through_one(opacity):
    settings = default_settings()
    settings["overlay"]["pre_match"]["opacity"] = opacity

    assert validate_settings(settings)["overlay"]["pre_match"]["opacity"] == opacity


def test_numeric_division_is_converted_to_api_division_name(tmp_path, monkeypatch):
    settings = default_settings()
    settings["division"] = 3
    settings_path = tmp_path / "config.json"
    write_config(settings_path, settings)
    calls = []

    class CapturedSchedule:
        def __init__(self, client, basho_id, division, day):
            calls.append((basho_id, division, day))
            self.bouts = [make_bout(1, 10, 11)]

    monkeypatch.setattr("app.SumoApiClient", lambda **kwargs: object())
    monkeypatch.setattr("app.BashoSchedule", CapturedSchedule)

    create_app(config_path=settings_path, watch_config=False)

    assert calls == [("202505", "Makushita", 1)]


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
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")


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
    settings_path = tmp_path / "config.json"
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
    settings_path = tmp_path / "config.json"
    write_config(settings_path, settings)
    app = create_app(
        schedule=OCRSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)]),
        config_path=settings_path,
        test_config={"TESTING": True},
        watch_config=False,
    )

    app.extensions["ocr_scanner"].observe("大喜翔", "", stable_readings=1)

    assert app.test_client().get("/api/state").json["index"] == 1


def test_ocr_retriggers_pre_match_for_the_already_selected_bout(tmp_path):
    class OCRSchedule(FakeSchedule):
        def find_bout_index_by_kanji(self, left_name, right_name):
            return 0 if (left_name, right_name) == ("大喜翔", "栃幸大") else None

    settings = default_settings()
    settings["control"]["mode"] = "automated"
    settings_path = tmp_path / "config.json"
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


def test_control_mode_is_persisted_to_json_config(tmp_path):
    settings_path = tmp_path / "config.json"
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
        saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert response.status_code == 200
        assert response.json["settings"]["mode"] == "automated"
        assert response.json["settings"]["preMatchEnabled"] is True
        assert response.json["settings"]["preMatchSeconds"] == 10
        assert saved_settings["control"]["mode"] == "automated"
        assert set(saved_settings["control"]) == {
            "mode",
            "pre_match_seconds",
            "pre_match_pinned",
        }
    finally:
        app.extensions["settings_watcher"].stop()


def test_external_json_edit_hot_reloads_overlay_settings(tmp_path):
    settings_path = tmp_path / "config.json"
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
        settings["overlay"]["main"]["width"] = 1600
        settings["overlay"]["pre_match"]["height"] = 900
        settings["capture_regions"]["abema_scoresheet"]["x"] = 120
        write_config(settings_path, settings)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = client.get("/api/settings").json
            if (
                response["settings"]["control"]["mode"] == "automated"
                and response["settings"]["overlay"]["main"]["width"] == 1600
            ):
                break
            time.sleep(0.05)
        assert response["settings"]["control"]["mode"] == "automated"
        assert client.get("/api/state").json["settings"]["overlay"]["main"]["width"] == 1600
        assert client.get("/api/pre-match-state").json["settings"]["overlay"]["pre_match"]["height"] == 900
        assert client.get("/api/settings").json["settings"]["capture_regions"][
            "abema_scoresheet"
        ]["x"] == 120
    finally:
        app.extensions["settings_watcher"].stop()


def test_invalid_hot_reload_keeps_last_good_configuration(tmp_path):
    settings_path = tmp_path / "config.json"
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
        assert response["settings"]["control"]["mode"] == "manual"
    finally:
        app.extensions["settings_watcher"].stop()


def test_pre_match_toggle_is_independent_of_match_control_mode(tmp_path):
    settings_path = tmp_path / "config.json"
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
            json={"seconds": 0},
        )
        saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert response.status_code == 200
        assert response.json["settings"]["mode"] == "automated"
        assert response.json["settings"]["preMatchEnabled"] is False
        assert saved_settings["control"]["mode"] == "automated"
        assert saved_settings["control"]["pre_match_seconds"] == 0
    finally:
        app.extensions["settings_watcher"].stop()


def test_pre_match_pin_keeps_overlay_visible_until_unpinned(tmp_path):
    settings_path = tmp_path / "config.json"
    settings = default_settings()
    settings["control"]["pre_match_seconds"] = 0
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
        assert pinned.json["settings"]["preMatchEnabled"] is False
        assert pinned.json["settings"]["preMatchPinned"] is True
        saved_settings = json.loads(settings_path.read_text(encoding="utf-8"))
        assert saved_settings["control"]["pre_match_pinned"] is True

        unpinned = client.post("/api/control/pre-match", json={"pinned": False})
        assert unpinned.status_code == 200
        assert unpinned.json["settings"]["preMatchPinned"] is False
        assert unpinned.json["settings"]["preMatchEnabled"] is False
    finally:
        app.extensions["settings_watcher"].stop()


def test_zero_pre_match_duration_turns_overlay_off_and_positive_value_enables_it(tmp_path):
    settings_path = tmp_path / "config.json"
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
        off = client.post("/api/control/pre-match", json={"seconds": 0})
        assert off.status_code == 200
        assert off.json["settings"]["mode"] == "manual"
        assert off.json["settings"]["preMatchSeconds"] == 0
        assert off.json["settings"]["preMatchEnabled"] is False

        on = client.post("/api/control/pre-match", json={"seconds": 8})
        assert on.status_code == 200
        assert on.json["settings"]["mode"] == "manual"
        assert on.json["settings"]["preMatchSeconds"] == 8
        assert on.json["settings"]["preMatchEnabled"] is True
    finally:
        app.extensions["settings_watcher"].stop()


def test_invalid_pre_match_control_settings_are_rejected(tmp_path):
    settings_path = tmp_path / "config.json"
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
        assert client.post("/api/control/pre-match", json={"seconds": -1}).status_code == 400
        assert client.post("/api/control/pre-match", json={}).status_code == 400
    finally:
        app.extensions["settings_watcher"].stop()


def test_invalid_json_config_is_rejected(tmp_path):
    settings_path = tmp_path / "config.json"
    settings = default_settings()
    settings["control"]["mode"] = "invalid"
    write_config(settings_path, settings)

    with pytest.raises(ValueError, match="control.mode"):
        create_app(
            schedule=FakeSchedule([make_bout(1, 10, 11)]),
            config_path=settings_path,
            watch_config=False,
        )
