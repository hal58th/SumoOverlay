from dataclasses import dataclass

import pytest

from app import create_app
from sumo_data import Bout, BoutRecord, BoutSide, SumoApiError


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

    def refresh(self):
        self.refresh_count += 1
        if self.fail_refresh:
            raise SumoApiError("API unavailable")
        return self.bouts


def test_manual_controls_update_state_and_emit_websocket_event():
    schedule = FakeSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)])
    app = create_app(schedule=schedule, config={"TESTING": True})
    client = app.test_client()
    socket_client = app.extensions["socketio"].test_client(app)
    socket_client.get_received()

    response = client.post("/api/control/next")

    assert response.status_code == 200
    assert response.json["matchNo"] == 2
    assert response.json["index"] == 1
    assert schedule.refresh_count == 1
    events = socket_client.get_received()
    assert any(event["name"] == "bout_update" for event in events)
    socket_client.disconnect()


def test_failed_refresh_keeps_state_and_returns_explicit_error():
    schedule = FakeSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)], fail_refresh=True)
    app = create_app(schedule=schedule, config={"TESTING": True})
    client = app.test_client()

    response = client.post("/api/control/next")

    assert response.status_code == 502
    assert response.json == {"error": "API unavailable"}
    assert client.get("/api/state").json["index"] == 0


def test_socket_control_surfaces_refresh_error():
    schedule = FakeSchedule([make_bout(1, 10, 11), make_bout(2, 12, 13)], fail_refresh=True)
    app = create_app(schedule=schedule, config={"TESTING": True})
    socket_client = app.extensions["socketio"].test_client(app)
    socket_client.get_received()

    socket_client.emit("control", {"action": "next"})

    events = socket_client.get_received()
    assert any(event["name"] == "control_error" for event in events)
    assert app.extensions["bout_controller"].state()["index"] == 0
    socket_client.disconnect()


def test_pages_and_kanji_lookup_are_available():
    schedule = FakeSchedule([make_bout(1, 10, 11)], kanji_lookup={"栃大海": "Tochitaikai"})
    app = create_app(schedule=schedule, config={"TESTING": True})
    client = app.test_client()

    assert client.get("/overlay").status_code == 200
    assert client.get("/control").status_code == 200
    assert client.get("/api/lookup").json == {"栃大海": "Tochitaikai"}


def test_invalid_basho_configuration_fails_before_startup():
    with pytest.raises(ValueError, match="SUMO_BASHO"):
        create_app(config={"SUMO_BASHO": "invalid"})
