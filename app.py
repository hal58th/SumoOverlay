"""Flask application and local control server for the Sumo OBS overlay."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping

from flask import Flask, jsonify, render_template
from flask_socketio import SocketIO, emit

from bout_state import BoutController
from sumo_data import BashoSchedule, SumoApiClient, SumoApiError


DIVISIONS = ("Makuuchi", "Juryo", "Makushita", "Sandanme", "Jonidan", "Jonokuchi")


def _validate_settings(app: Flask) -> tuple[str, str, int]:
    basho_id = app.config["SUMO_BASHO"]
    division = app.config["SUMO_DIVISION"]
    day = app.config["SUMO_DAY"]
    if not isinstance(basho_id, str) or re.fullmatch(r"\d{6}", basho_id) is None:
        raise ValueError("SUMO_BASHO must be a six-digit basho ID such as 202505")
    if division not in DIVISIONS:
        raise ValueError(f"SUMO_DIVISION must be one of: {', '.join(DIVISIONS)}")
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 15:
        raise ValueError("SUMO_DAY must be an integer from 1 through 15")
    return basho_id, division, day


def _environment_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error


def _environment_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error


def create_app(
    schedule: Any | None = None,
    config: Mapping[str, Any] | None = None,
) -> Flask:
    app = Flask(__name__)
    app.config.from_mapping(
        SUMO_BASHO=os.environ.get("SUMO_BASHO", ""),
        SUMO_DIVISION=os.environ.get("SUMO_DIVISION", "Makuuchi"),
        SUMO_DAY=_environment_int("SUMO_DAY", 1),
        SUMO_API_BASE_URL=os.environ.get("SUMO_API_BASE_URL", "https://sumo-api.com/api"),
        SUMO_API_TIMEOUT=_environment_float("SUMO_API_TIMEOUT", 10.0),
        HOST=os.environ.get("HOST", "127.0.0.1"),
        PORT=_environment_int("PORT", 5000),
        SECRET_KEY=os.environ.get("SUMO_SECRET_KEY", "local-sumo-overlay"),
    )
    if config:
        app.config.update(config)

    if schedule is None:
        basho_id, division, day = _validate_settings(app)
        client = SumoApiClient(
            base_url=app.config["SUMO_API_BASE_URL"],
            timeout=app.config["SUMO_API_TIMEOUT"],
        )
        schedule = BashoSchedule(client, basho_id, division, day)

    controller = BoutController(schedule)
    socketio = SocketIO(app, async_mode="threading")
    app.extensions["bout_controller"] = controller
    app.extensions["sumo_schedule"] = schedule
    app.extensions["socketio"] = socketio

    @app.get("/overlay")
    def overlay() -> str:
        return render_template("overlay.html")

    @app.get("/control")
    def control() -> str:
        return render_template("control.html")

    @app.get("/api/state")
    def get_state() -> Any:
        return jsonify(controller.state())

    @app.get("/api/lookup")
    def get_lookup() -> Any:
        try:
            return jsonify(getattr(schedule, "kanji_lookup", {}))
        except SumoApiError as error:
            app.logger.error("Unable to build kanji lookup: %s", error)
            return jsonify({"error": str(error)}), 502

    def move_bout(offset: int) -> Any:
        try:
            state = controller.move(offset)
        except SumoApiError as error:
            app.logger.error("Unable to update bout state: %s", error)
            return jsonify({"error": str(error)}), 502
        socketio.emit("bout_update", state)
        return jsonify(state)

    @app.post("/api/control/next")
    def next_bout() -> Any:
        return move_bout(1)

    @app.post("/api/control/prev")
    def previous_bout() -> Any:
        return move_bout(-1)

    @socketio.on("connect")
    def send_initial_state() -> None:
        emit("bout_update", controller.state())

    @socketio.on("control")
    def socket_control(message: Any) -> None:
        if not isinstance(message, dict) or message.get("action") not in ("next", "prev"):
            emit("control_error", {"error": "action must be 'next' or 'prev'"})
            return
        try:
            state = controller.move(1 if message["action"] == "next" else -1)
        except SumoApiError as error:
            app.logger.error("Unable to update bout state: %s", error)
            emit("control_error", {"error": str(error)})
            return
        emit("bout_update", state, broadcast=True)

    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = create_app()
    socketio: SocketIO = app.extensions["socketio"]
    socketio.run(
        app,
        host=app.config["HOST"],
        port=app.config["PORT"],
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    main()
