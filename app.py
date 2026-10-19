"""Flask application and local control server for the Sumo OBS overlays."""

from __future__ import annotations

import argparse
import copy
import logging
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit
from jinja2 import ChoiceLoader, FileSystemLoader

from bout_state import BoutController
from config import DIVISION_NAMES, SettingsError, SettingsWatcher, load_settings
from ocr_scanner import OCRScanner
from sumo_data import BashoSchedule, SumoApiClient, SumoApiError


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.yaml")
DATA_SETTINGS = ("basho_id", "division", "day", "api")


def create_app(
    schedule: Any | None = None,
    config_path: str | Path | None = None,
    test_config: dict[str, Any] | None = None,
    watch_config: bool = True,
    schedule_factory: Callable[[dict[str, Any]], Any] | None = None,
    enable_ocr: bool = False,
    override_config_path: str | Path | None = None,
) -> Flask:
    app = Flask(__name__)
    if test_config:
        app.config.update(test_config)

    settings_path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    override_path = Path(override_config_path) if override_config_path else None
    settings = load_settings(settings_path, override_path)
    custom_templates_path = settings_path.parent / "custom_templates"
    built_in_templates_path = Path(__file__).parent / "templates"
    app.jinja_loader = ChoiceLoader(
        [FileSystemLoader(custom_templates_path), app.jinja_loader]
    )

    def validate_overlay_templates(new_settings: dict[str, Any]) -> None:
        for overlay_name, filename in new_settings["templates"].items():
            if not any(
                (directory / filename).is_file()
                for directory in (custom_templates_path, built_in_templates_path)
            ):
                raise SettingsError(
                    f"Template {filename!r} for {overlay_name} was not found in "
                    f"{custom_templates_path} or {built_in_templates_path}"
                )

    validate_overlay_templates(settings)

    def build_schedule(new_settings: dict[str, Any]) -> BashoSchedule:
        if schedule_factory is not None:
            return schedule_factory(new_settings)
        client = SumoApiClient(
            base_url=new_settings["api"]["base_url"],
            timeout=new_settings["api"]["timeout_seconds"],
        )
        return BashoSchedule(
            client,
            new_settings["basho_id"],
            (
                None
                if new_settings["division"] == 0
                else DIVISION_NAMES[new_settings["division"]]
            ),
            new_settings["day"],
        )

    if schedule is None:
        schedule = build_schedule(settings)

    controller = BoutController(schedule)
    socketio = SocketIO(app, async_mode="threading")
    app.extensions["bout_controller"] = controller
    app.extensions["sumo_schedule"] = schedule
    app.extensions["socketio"] = socketio
    app.extensions["settings_path"] = settings_path
    app.extensions["override_settings_path"] = override_path
    settings_lock = RLock()
    active_settings = settings
    watcher: SettingsWatcher | None = None
    result_lock = RLock()
    announced_winner: str | None = None

    def public_state(include_head_to_head: bool = False) -> dict[str, Any]:
        result = controller.state(include_head_to_head=include_head_to_head)
        with result_lock:
            winner = announced_winner
        if winner is not None:
            winner_side = result[winner]
            loser_side = result["west" if winner == "east" else "east"]
            winner_side["record"]["wins"] += 1
            loser_side["record"]["losses"] += 1
            result["announcedWinner"] = winner
        with settings_lock:
            current = copy.deepcopy(active_settings)
        result["settings"] = {
            "division": current["division"],
            "bashoId": current["basho_id"],
            "day": current["day"],
            "mode": current["control"]["mode"],
            "preMatchSalariedSeconds": current["control"][
                "pre_match_salaried_seconds"
            ],
            "preMatchUnsalariedSeconds": current["control"][
                "pre_match_unsalaried_seconds"
            ],
            "preMatchPinned": current["control"]["pre_match_pinned"],
            "overlay": current["overlay"],
        }
        return result

    def clear_announced_winner() -> None:
        nonlocal announced_winner
        with result_lock:
            announced_winner = None

    def broadcast_state(update_pre_match: bool) -> None:
        pre_match_state = None
        if update_pre_match:
            try:
                state = public_state(include_head_to_head=True)
                pre_match_state = state
            except SumoApiError as error:
                app.logger.error("Unable to update head-to-head display: %s", error)
                socketio.emit("pre_match_error", {"error": str(error)})
                state = public_state()
        else:
            state = public_state()
        socketio.emit("bout_update", state)
        if pre_match_state is not None:
            socketio.emit("pre_match_update", pre_match_state)

    def reload_settings(new_settings: dict[str, Any]) -> None:
        nonlocal active_settings
        validate_overlay_templates(new_settings)
        with settings_lock:
            previous = active_settings

        changed_data = any(previous[key] != new_settings[key] for key in DATA_SETTINGS)
        update_pre_match = (
            changed_data
            or any(
                previous["control"][key] != new_settings["control"][key]
                for key in (
                    "pre_match_salaried_seconds",
                    "pre_match_unsalaried_seconds",
                )
            )
            or previous["control"]["pre_match_pinned"]
            != new_settings["control"]["pre_match_pinned"]
        )
        if changed_data:
            try:
                replacement = build_schedule(new_settings)
            except SumoApiError as error:
                raise SettingsError(f"Could not load the selected basho data: {error}") from error
            controller.replace_schedule(replacement, reset_index=True)
            ocr_scanner.reset_detection()
            clear_announced_winner()

        with settings_lock:
            active_settings = copy.deepcopy(new_settings)
        app.extensions["sumo_schedule"] = controller.schedule
        broadcast_state(update_pre_match)

    def handle_ocr_match(left_name: str, right_name: str) -> dict[str, Any] | None:
        previous_index = controller.state()["index"]
        selected = controller.select_bout_by_kanji(left_name, right_name)
        if selected is None:
            return None
        changed = controller.state()["index"] != previous_index
        if changed:
            clear_announced_winner()
        pre_match_state = None
        try:
            state = public_state(include_head_to_head=True)
            pre_match_state = state
        except SumoApiError as error:
            app.logger.error("Unable to update head-to-head display after OCR: %s", error)
            socketio.emit("pre_match_error", {"error": str(error)})
            state = public_state()
        if changed:
            socketio.emit("bout_update", state)
        if pre_match_state is not None:
            socketio.emit("pre_match_update", pre_match_state)
        return state

    def current_settings() -> dict[str, Any]:
        with settings_lock:
            return copy.deepcopy(active_settings)

    ocr_scanner = OCRScanner(current_settings, handle_ocr_match, app.logger)
    app.extensions["ocr_scanner"] = ocr_scanner
    if enable_ocr:
        ocr_scanner.start()

    if watch_config:
        watcher = SettingsWatcher(
            settings_path,
            settings["server"]["watch_interval_seconds"],
            reload_settings,
            app.logger,
            override_path=override_path,
        )

    app.extensions["sumo_schedule"] = schedule
    app.extensions["settings_watcher"] = watcher

    @app.get("/overlay")
    @app.get("/pre-match-overlay")
    def pre_match_overlay() -> str:
        return render_template(current_settings()["templates"]["pre_match"])

    @app.get("/scoreboard-overlay")
    def scoreboard_overlay() -> str:
        return render_template(current_settings()["templates"]["main"])

    @app.get("/control")
    def control() -> str:
        return render_template("control.html")

    @app.get("/api/state")
    def get_state() -> Any:
        try:
            return jsonify(public_state())
        except SumoApiError as error:
            app.logger.error("Unable to build current bout state: %s", error)
            return jsonify({"error": str(error)}), 502

    @app.get("/api/pre-match-state")
    def get_pre_match_state() -> Any:
        try:
            return jsonify(public_state(include_head_to_head=True))
        except SumoApiError as error:
            app.logger.error("Unable to build head-to-head state: %s", error)
            return jsonify({"error": str(error)}), 502

    @app.get("/api/settings")
    def get_settings() -> Any:
        with settings_lock:
            current = copy.deepcopy(active_settings)
        initial_server = settings["server"]
        return jsonify(
            {
                "settings": current,
                "reloadError": watcher.last_error if watcher else None,
                "restartRequired": {
                    "host": current["server"]["host"] != initial_server["host"],
                    "port": current["server"]["port"] != initial_server["port"],
                },
            }
        )

    @app.get("/api/ocr/status")
    def get_ocr_status() -> Any:
        status = ocr_scanner.status()
        settings_snapshot = current_settings()
        status["mode"] = settings_snapshot["control"]["mode"]
        status["enabled"] = settings_snapshot["ocr"]["enabled"]
        return jsonify(status)

    @app.get("/api/lookup")
    def get_lookup() -> Any:
        try:
            return jsonify(getattr(controller.schedule, "kanji_lookup", {}))
        except SumoApiError as error:
            app.logger.error("Unable to build kanji lookup: %s", error)
            return jsonify({"error": str(error)}), 502

    def move_bout(offset: int) -> Any:
        old_index = controller.state()["index"]
        try:
            controller.move(offset)
            if controller.state()["index"] != old_index:
                clear_announced_winner()
            state = public_state()
        except SumoApiError as error:
            app.logger.error("Unable to update bout state: %s", error)
            return jsonify({"error": str(error)}), 502
        pre_match_state = None
        if state["index"] != old_index:
            try:
                state = public_state(include_head_to_head=True)
                pre_match_state = state
            except SumoApiError as error:
                app.logger.error("Unable to update head-to-head display: %s", error)
                socketio.emit("pre_match_error", {"error": str(error)})
        socketio.emit("bout_update", state)
        if pre_match_state is not None:
            socketio.emit("pre_match_update", pre_match_state)
        return jsonify(state)

    @app.post("/api/control/next")
    def next_bout() -> Any:
        return move_bout(1)

    @app.post("/api/control/prev")
    def previous_bout() -> Any:
        return move_bout(-1)

    @app.post("/api/control/division")
    def jump_to_division() -> Any:
        payload = request.get_json(silent=True)
        division = payload.get("division") if isinstance(payload, dict) else None
        if (
            isinstance(division, bool)
            or not isinstance(division, int)
            or division not in DIVISION_NAMES
        ):
            return jsonify({"error": "division must be a number from 1 through 6"}), 400
        try:
            old_index = controller.state()["index"]
            controller.jump_to_division(division)
            if controller.state()["index"] != old_index:
                clear_announced_winner()
            state = public_state()
        except ValueError as error:
            return jsonify({"error": str(error)}), 404
        except SumoApiError as error:
            app.logger.error("Unable to jump to division %s: %s", division, error)
            return jsonify({"error": str(error)}), 502
        pre_match_state = None
        if state["index"] != old_index:
            try:
                state = public_state(include_head_to_head=True)
                pre_match_state = state
            except SumoApiError as error:
                app.logger.error("Unable to update head-to-head display: %s", error)
                socketio.emit("pre_match_error", {"error": str(error)})
        socketio.emit("bout_update", state)
        if pre_match_state is not None:
            socketio.emit("pre_match_update", pre_match_state)
        return jsonify(state)

    @app.post("/api/control/mode")
    def set_mode() -> Any:
        payload = request.get_json(silent=True)
        mode = payload.get("mode") if isinstance(payload, dict) else None
        if mode not in ("manual", "automated"):
            return jsonify({"error": "mode must be 'manual' or 'automated'"}), 400
        if watcher is None:
            return jsonify({"error": "Live configuration reload is disabled"}), 503

        with settings_lock:
            updated_settings = copy.deepcopy(active_settings)
        updated_settings["control"]["mode"] = mode
        try:
            watcher.write(updated_settings)
            state = public_state()
        except (SettingsError, SumoApiError) as error:
            app.logger.error("Unable to update control mode: %s", error)
            return jsonify({"error": str(error)}), 500
        return jsonify(state)

    @app.post("/api/control/pre-match")
    def set_pre_match() -> Any:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "a JSON object is required"}), 400

        with settings_lock:
            updated_settings = copy.deepcopy(active_settings)
        if "pinned" in payload:
            if not isinstance(payload["pinned"], bool):
                return jsonify({"error": "pinned must be true or false"}), 400
            updated_settings["control"]["pre_match_pinned"] = payload["pinned"]
        duration_fields = {
            "salaried_seconds": "pre_match_salaried_seconds",
            "unsalaried_seconds": "pre_match_unsalaried_seconds",
        }
        for payload_key, setting_key in duration_fields.items():
            if payload_key in payload:
                seconds = payload[payload_key]
                if (
                    isinstance(seconds, bool)
                    or not isinstance(seconds, (int, float))
                    or seconds < 0
                ):
                    return jsonify({
                        "error": f"{payload_key} must be a number that is zero or greater"
                    }), 400
                updated_settings["control"][setting_key] = seconds
        if not any(
            key in payload for key in (*duration_fields, "pinned")
        ):
            return jsonify({
                "error": "provide salaried_seconds, unsalaried_seconds, and/or pinned"
            }), 400

        try:
            watcher.write(updated_settings)
            state = public_state()
        except (SettingsError, SumoApiError) as error:
            app.logger.error("Unable to update pre-match settings: %s", error)
            return jsonify({"error": str(error)}), 500
        return jsonify(state)

    def set_announced_winner(winner: Any) -> dict[str, Any]:
        nonlocal announced_winner
        if winner not in ("east", "west"):
            raise ValueError("winner must be 'east' or 'west'")
        with result_lock:
            announced_winner = winner
        return public_state()

    @app.post("/api/control/winner")
    def announce_winner_http() -> Any:
        payload = request.get_json(silent=True)
        winner = payload.get("winner") if isinstance(payload, dict) else None
        try:
            state = set_announced_winner(winner)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        socketio.emit("bout_result", state)
        return jsonify(state)

    @socketio.on("announce_winner")
    def announce_winner_socket(message: Any) -> None:
        winner = message.get("winner") if isinstance(message, dict) else None
        try:
            state = set_announced_winner(winner)
        except ValueError as error:
            emit("control_error", {"error": str(error)})
            return
        socketio.emit("bout_result", state)

    @socketio.on("connect")
    def send_initial_state() -> None:
        try:
            state = public_state(include_head_to_head=True)
            emit("bout_update", state)
            emit("pre_match_update", state)
        except SumoApiError as error:
            app.logger.error("Unable to send initial state with head-to-head: %s", error)
            emit("pre_match_error", {"error": str(error)})
            try:
                emit("bout_update", public_state())
            except SumoApiError as state_error:
                app.logger.error("Unable to send initial bout state: %s", state_error)
                emit("state_error", {"error": str(state_error)})

    @socketio.on("control")
    def socket_control(message: Any) -> None:
        if not isinstance(message, dict) or message.get("action") not in ("next", "prev"):
            emit("control_error", {"error": "action must be 'next' or 'prev'"})
            return
        try:
            old_index = controller.state()["index"]
            controller.move(1 if message["action"] == "next" else -1)
            if controller.state()["index"] != old_index:
                clear_announced_winner()
            state = public_state()
        except SumoApiError as error:
            app.logger.error("Unable to update bout state: %s", error)
            emit("control_error", {"error": str(error)})
            return
        pre_match_state = None
        if state["index"] != old_index:
            try:
                state = public_state(include_head_to_head=True)
                pre_match_state = state
            except SumoApiError as error:
                app.logger.error("Unable to update head-to-head display: %s", error)
                emit("pre_match_error", {"error": str(error)}, broadcast=True)
        emit("bout_update", state, broadcast=True)
        if pre_match_state is not None:
            emit("pre_match_update", pre_match_state, broadcast=True)

    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Sumo OBS overlay server")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to the YAML settings file (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--override-config",
        type=Path,
        help="Path to a partial YAML settings file applied over --config",
    )
    arguments = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    app = create_app(
        config_path=arguments.config,
        enable_ocr=True,
        override_config_path=arguments.override_config,
    )
    watcher: SettingsWatcher = app.extensions["settings_watcher"]
    settings = watcher.snapshot()
    socketio: SocketIO = app.extensions["socketio"]
    socketio.run(
        app,
        host=settings["server"]["host"],
        port=settings["server"]["port"],
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    main()
