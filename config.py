"""JSON settings loading, validation, and hot-reload support."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Callable


DIVISION_NAMES = {
    1: "Makuuchi",
    2: "Juryo",
    3: "Makushita",
    4: "Sandanme",
    5: "Jonidan",
    6: "Jonokuchi",
}
ALL_DIVISIONS = 0

DEFAULT_SETTINGS: dict[str, Any] = {
    "basho_id": "202505",
    "division": ALL_DIVISIONS,
    "day": 1,
    "api": {"base_url": "https://sumo-api.com/api", "timeout_seconds": 15},
    "server": {
        "host": "127.0.0.1",
        "port": 5000,
        "watch_interval_seconds": 1,
    },
    "control": {
        "mode": "manual",
        "pre_match_seconds": 10,
        "pre_match_pinned": False,
    },
    "overlay": {
        "canvas": {"width": 1920, "height": 1080},
        "main": {"x": 16, "y": 12, "width": 640, "height": 88},
        "pre_match": {"x": 40, "y": 40, "width": 1840, "height": 1000},
    },
    "capture_regions": {
        "abema_scoresheet": {"x": 0, "y": 0, "width": 1920, "height": 1080},
    },
}


class SettingsError(ValueError):
    """Raised when the JSON settings file is missing or invalid."""


def default_settings() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_SETTINGS)


def _positive_number(value: Any, label: str, *, allow_zero: bool = False) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsError(f"{label} must be a number")
    if not math.isfinite(value):
        raise SettingsError(f"{label} must be finite")
    invalid = value < 0 if allow_zero else value <= 0
    if invalid:
        comparison = "zero or greater" if allow_zero else "greater than zero"
        raise SettingsError(f"{label} must be {comparison}")
    return value


def _validate_bounds(bounds: Any, label: str) -> dict[str, int]:
    if not isinstance(bounds, dict):
        raise SettingsError(f"{label} must be an object with x, y, width, and height")
    result: dict[str, int] = {}
    for key in ("x", "y", "width", "height"):
        value = bounds.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsError(f"{label}.{key} must be an integer")
        result[key] = value
    if result["x"] < 0 or result["y"] < 0:
        raise SettingsError(f"{label}.x and {label}.y must be zero or greater")
    if result["width"] <= 0 or result["height"] <= 0:
        raise SettingsError(f"{label}.width and {label}.height must be greater than zero")
    return result


def validate_settings(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise SettingsError("The settings file must contain a JSON object")

    settings = default_settings()
    for key, value in raw.items():
        if key not in settings:
            raise SettingsError(f"Unknown settings key: {key}")
        settings[key] = value

    basho_id = settings.get("basho_id")
    if not isinstance(basho_id, str) or len(basho_id) != 6 or not basho_id.isdigit():
        raise SettingsError("basho_id must be six digits, for example 202505")

    division = settings.get("division")
    if isinstance(division, bool):
        raise SettingsError("division must be 0 for all or a number from 1 through 6")
    if division == "all":
        division = ALL_DIVISIONS
        settings["division"] = division
    elif isinstance(division, str):
        matching_numbers = [
            number for number, name in DIVISION_NAMES.items() if name == division
        ]
        if not matching_numbers:
            raise SettingsError(
                "division must be 0 for all or a number from 1 through 6"
            )
        division = matching_numbers[0]
        settings["division"] = division
    elif (
        not isinstance(division, int)
        or division != ALL_DIVISIONS and division not in DIVISION_NAMES
    ):
        raise SettingsError("division must be 0 for all or a number from 1 through 6")

    day = settings.get("day")
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 15:
        raise SettingsError("day must be an integer from 1 through 15")

    api = settings.get("api")
    if not isinstance(api, dict):
        raise SettingsError("api must be a JSON object")
    api = {**default_settings()["api"], **api}
    if not isinstance(api["base_url"], str) or not api["base_url"].startswith(("http://", "https://")):
        raise SettingsError("api.base_url must be an HTTP or HTTPS URL")
    api["timeout_seconds"] = _positive_number(api["timeout_seconds"], "api.timeout_seconds")
    settings["api"] = api

    server = settings.get("server")
    if not isinstance(server, dict):
        raise SettingsError("server must be a JSON object")
    server = {**default_settings()["server"], **server}
    if not isinstance(server["host"], str) or not server["host"]:
        raise SettingsError("server.host must be a non-empty string")
    port = server["port"]
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise SettingsError("server.port must be an integer from 1 through 65535")
    server["watch_interval_seconds"] = _positive_number(
        server["watch_interval_seconds"], "server.watch_interval_seconds"
    )
    settings["server"] = server

    control = settings.get("control")
    if not isinstance(control, dict):
        raise SettingsError("control must be a JSON object")
    control = {**default_settings()["control"], **control}
    control.pop("pre_match_enabled", None)
    if control["mode"] not in ("manual", "automated"):
        raise SettingsError("control.mode must be 'manual' or 'automated'")
    if not isinstance(control["pre_match_pinned"], bool):
        raise SettingsError("control.pre_match_pinned must be true or false")
    control["pre_match_seconds"] = _positive_number(
        control["pre_match_seconds"], "control.pre_match_seconds", allow_zero=True
    )
    settings["control"] = control

    overlay = settings.get("overlay")
    if not isinstance(overlay, dict) or any(
        not isinstance(overlay.get(key, {}), dict) for key in ("canvas", "main", "pre_match")
    ):
        raise SettingsError("overlay must be a JSON object")
    overlay_defaults = default_settings()["overlay"]
    overlay = {
        "canvas": {**overlay_defaults["canvas"], **overlay.get("canvas", {})},
        "main": {**overlay_defaults["main"], **overlay.get("main", {})},
        "pre_match": {**overlay_defaults["pre_match"], **overlay.get("pre_match", {})},
    }
    for key, value in overlay["canvas"].items():
        overlay["canvas"][key] = _positive_number(value, f"overlay.canvas.{key}")
        if not isinstance(overlay["canvas"][key], int):
            raise SettingsError(f"overlay.canvas.{key} must be an integer")
    for key in ("main", "pre_match"):
        overlay[key] = _validate_bounds(overlay[key], f"overlay.{key}")
        if (
            overlay[key]["x"] + overlay[key]["width"] > overlay["canvas"]["width"]
            or overlay[key]["y"] + overlay[key]["height"] > overlay["canvas"]["height"]
        ):
            raise SettingsError(f"overlay.{key} must fit inside overlay.canvas")
    settings["overlay"] = overlay

    capture_regions = settings.get("capture_regions")
    if not isinstance(capture_regions, dict):
        raise SettingsError("capture_regions must be a JSON object")
    if "abema_scoresheet" not in capture_regions:
        raise SettingsError("capture_regions.abema_scoresheet is required")
    settings["capture_regions"] = {
        "abema_scoresheet": _validate_bounds(
            capture_regions["abema_scoresheet"], "capture_regions.abema_scoresheet"
        )
    }
    return settings


def load_settings(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as config_file:
            raw = json.load(config_file)
    except FileNotFoundError as error:
        raise SettingsError(
            f"Settings file not found: {path}. Create it as a JSON settings file; see README.md."
        ) from error
    except json.JSONDecodeError as error:
        raise SettingsError(
            f"Invalid JSON in {path} at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error
    except UnicodeDecodeError as error:
        raise SettingsError(f"Settings file {path} must be UTF-8 encoded JSON") from error
    except OSError as error:
        raise SettingsError(f"Unable to read settings file {path}: {error}") from error
    return validate_settings(raw)


class SettingsWatcher:
    def __init__(
        self,
        path: Path,
        interval_seconds: float,
        on_change: Callable[[dict[str, Any]], None],
        logger: logging.Logger,
    ) -> None:
        self.path = path
        self.interval_seconds = interval_seconds
        self.on_change = on_change
        self.logger = logger
        self._stop = Event()
        self._lock = RLock()
        self._settings = load_settings(path)
        self._signature = self._file_signature()
        self._last_error: str | None = None
        self._thread = Thread(target=self._watch, name="settings-watcher", daemon=True)
        self._thread.start()

    def _file_signature(self) -> tuple[int, int]:
        try:
            stat = self.path.stat()
        except OSError as error:
            raise SettingsError(f"Unable to inspect settings file {self.path}: {error}") from error
        return stat.st_mtime_ns, stat.st_size

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._settings)

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def write(self, settings: dict[str, Any]) -> dict[str, Any]:
        validated = validate_settings(settings)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as config_file:
                json.dump(validated, config_file, ensure_ascii=False, indent=2)
                config_file.write("\n")
                config_file.flush()
                os.fsync(config_file.fileno())
            os.replace(temporary, self.path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                self.logger.exception("Unable to remove temporary settings file %s", temporary)
            raise SettingsError(f"Unable to save settings file {self.path}: {error}") from error

        self.apply(validated)
        self._signature = self._file_signature()
        return validated

    def apply(self, settings: dict[str, Any]) -> None:
        validated = validate_settings(settings)
        self.on_change(validated)
        with self._lock:
            self._settings = validated
            self._last_error = None
            self.interval_seconds = validated["server"]["watch_interval_seconds"]

    def _watch(self) -> None:
        while not self._stop.wait(float(self.interval_seconds)):
            if self._signature is None and not self.path.exists():
                continue
            try:
                signature = self._file_signature()
                if signature == self._signature:
                    continue
                settings = load_settings(self.path)
                self.apply(settings)
                self._signature = signature
            except (SettingsError, OSError, ValueError) as error:
                try:
                    self._signature = self._file_signature()
                except SettingsError:
                    self._signature = None
                with self._lock:
                    self._last_error = str(error)
                self.logger.error("Settings reload failed; keeping the last valid settings: %s", error)

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=max(1.0, float(self.interval_seconds) + 0.5))
