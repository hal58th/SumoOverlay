"""YAML settings loading, validation, and hot-reload support."""

from __future__ import annotations

import copy
import logging
import math
import os
import re
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Callable

import yaml


DIVISION_NAMES = {
    1: "Makuuchi",
    2: "Juryo",
    3: "Makushita",
    4: "Sandanme",
    5: "Jonidan",
    6: "Jonokuchi",
}
ALL_DIVISIONS = 0
DEFAULT_OVERLAY_STYLE = {
    "font_family": '"Segoe UI", Arial, sans-serif',
    "colors": {
        "text": "#ffffff",
        "muted": "#d4d8de",
        "accent": "#e8bd68",
        "result": "#ff4b4b",
    },
}

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
        "pre_match_salaried_seconds": 10,
        "pre_match_unsalaried_seconds": 10,
        "pre_match_pinned": False,
    },
    "ocr": {
        "enabled": True,
        "monitor": 1,
        "interval_seconds": 1,
        "stable_readings": 2,
        "minimum_confidence": 0.25,
        "capture_regions": {
            "abema_scoresheet": {
                "x": 0,
                "y": 0,
                "width": 1920,
                "height": 1080,
            },
        },
        "name_boxes": {
            "left": {"x": 298, "y": 38, "width": 250, "height": 49},
            "right": {"x": 653, "y": 38, "width": 250, "height": 49},
        },
    },
    "overlay": {
        "control": copy.deepcopy(DEFAULT_OVERLAY_STYLE),
        "scoreboard": {
            "width": 1582,
            "height": 174,
            **copy.deepcopy(DEFAULT_OVERLAY_STYLE),
        },
        "prematch": {
            "width": 1840,
            "height": 1000,
            "opacity": 0.5,
            **copy.deepcopy(DEFAULT_OVERLAY_STYLE),
        },
    },
    "templates": {
        "scoreboard": "scoreboard_overlay.html",
        "prematch": "prematch_overlay.html",
    },
}


class SettingsError(ValueError):
    """Raised when the YAML settings file is missing or invalid."""


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


def _validate_overlay_style(section: dict[str, Any], label: str) -> dict[str, Any]:
    font_family = section.get("font_family")
    if (
        not isinstance(font_family, str)
        or not font_family
        or len(font_family) > 200
        or re.fullmatch(r"""[A-Za-z0-9 ,'"_-]+""", font_family) is None
    ):
        raise SettingsError(
            f"{label}.font_family must be a CSS font-family list"
        )

    colors = section.get("colors")
    if not isinstance(colors, dict):
        raise SettingsError(f"{label}.colors must be an object")
    default_colors = DEFAULT_OVERLAY_STYLE["colors"]
    unknown_colors = set(colors) - set(default_colors)
    if unknown_colors:
        raise SettingsError(f"Unknown {label}.colors setting: {sorted(unknown_colors)[0]}")
    colors = {**default_colors, **colors}
    for name, value in colors.items():
        if not isinstance(value, str) or re.fullmatch(
            r"#[0-9A-Fa-f]{3,4}|#[0-9A-Fa-f]{6}|#[0-9A-Fa-f]{8}", value
        ) is None:
            raise SettingsError(f"{label}.colors.{name} must be a hex color")
    section["font_family"] = font_family
    section["colors"] = colors
    return section


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
        raise SettingsError("The settings file must contain a YAML mapping")

    settings = default_settings()
    for key, value in raw.items():
        if key not in settings:
            raise SettingsError(f"Unknown settings key: {key}")
        settings[key] = value

    basho_id = settings.get("basho_id")
    if not isinstance(basho_id, str) or len(basho_id) != 6 or not basho_id.isdigit():
        raise SettingsError("basho_id must be six digits, for example 202505")

    division = settings.get("division")
    if (
        isinstance(division, bool)
        or not isinstance(division, int)
        or division != ALL_DIVISIONS and division not in DIVISION_NAMES
    ):
        raise SettingsError("division must be 0 for all or a number from 1 through 6")

    day = settings.get("day")
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 15:
        raise SettingsError("day must be an integer from 1 through 15")

    api = settings.get("api")
    if not isinstance(api, dict):
        raise SettingsError("api must be a YAML mapping")
    api = {**default_settings()["api"], **api}
    if not isinstance(api["base_url"], str) or not api["base_url"].startswith(("http://", "https://")):
        raise SettingsError("api.base_url must be an HTTP or HTTPS URL")
    api["timeout_seconds"] = _positive_number(api["timeout_seconds"], "api.timeout_seconds")
    settings["api"] = api

    server = settings.get("server")
    if not isinstance(server, dict):
        raise SettingsError("server must be a YAML mapping")
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
        raise SettingsError("control must be a YAML mapping")
    unknown_control_keys = set(control) - set(default_settings()["control"])
    if unknown_control_keys:
        raise SettingsError(
            f"Unknown control setting: {sorted(unknown_control_keys)[0]}"
        )
    control = {**default_settings()["control"], **control}
    if control["mode"] not in ("manual", "automated"):
        raise SettingsError("control.mode must be 'manual' or 'automated'")
    if not isinstance(control["pre_match_pinned"], bool):
        raise SettingsError("control.pre_match_pinned must be true or false")
    for group in ("salaried", "unsalaried"):
        setting = f"pre_match_{group}_seconds"
        control[setting] = _positive_number(
            control[setting], f"control.{setting}", allow_zero=True
        )
    settings["control"] = control

    ocr = settings.get("ocr")
    if not isinstance(ocr, dict):
        raise SettingsError("ocr must be a YAML mapping")
    unknown_ocr_keys = set(ocr) - set(default_settings()["ocr"])
    if unknown_ocr_keys:
        raise SettingsError(f"Unknown OCR setting: {sorted(unknown_ocr_keys)[0]}")
    ocr = {**default_settings()["ocr"], **ocr}
    capture_regions = ocr.get("capture_regions")
    if not isinstance(capture_regions, dict):
        raise SettingsError("ocr.capture_regions must be a YAML mapping")
    if "abema_scoresheet" not in capture_regions:
        raise SettingsError("ocr.capture_regions.abema_scoresheet is required")
    capture_region = _validate_bounds(
        capture_regions["abema_scoresheet"], "ocr.capture_regions.abema_scoresheet"
    )
    ocr["capture_regions"] = {"abema_scoresheet": capture_region}
    if not isinstance(ocr["enabled"], bool):
        raise SettingsError("ocr.enabled must be true or false")
    if (
        isinstance(ocr["monitor"], bool)
        or not isinstance(ocr["monitor"], int)
        or ocr["monitor"] < 1
    ):
        raise SettingsError("ocr.monitor must be a monitor number of 1 or greater")
    ocr["interval_seconds"] = _positive_number(
        ocr["interval_seconds"], "ocr.interval_seconds"
    )
    if (
        isinstance(ocr["stable_readings"], bool)
        or not isinstance(ocr["stable_readings"], int)
        or ocr["stable_readings"] < 1
    ):
        raise SettingsError("ocr.stable_readings must be an integer of 1 or greater")
    confidence = ocr["minimum_confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise SettingsError("ocr.minimum_confidence must be a number from 0 through 1")
    boxes = ocr["name_boxes"]
    if not isinstance(boxes, dict) or not all(
        isinstance(boxes.get(side), dict) for side in ("left", "right")
    ):
        raise SettingsError("ocr.name_boxes must contain left and right pixel rectangles")
    normalized_boxes: dict[str, dict[str, int]] = {}
    for side in ("left", "right"):
        normalized_boxes[side] = _validate_bounds(
            boxes[side], f"ocr.name_boxes.{side}"
        )
        box = normalized_boxes[side]
        if (
            box["x"] + box["width"] > capture_region["width"]
            or box["y"] + box["height"] > capture_region["height"]
        ):
            raise SettingsError(
                f"ocr.name_boxes.{side} must fit inside ocr.capture_regions.abema_scoresheet"
            )
    ocr["name_boxes"] = normalized_boxes
    settings["ocr"] = ocr

    overlay = settings.get("overlay")
    if not isinstance(overlay, dict):
        raise SettingsError("overlay must be a YAML mapping")
    unknown_overlay_keys = set(overlay) - {"control", "scoreboard", "prematch"}
    if unknown_overlay_keys:
        raise SettingsError(
            f"Unknown overlay setting: {sorted(unknown_overlay_keys)[0]}"
        )
    overlay_defaults = default_settings()["overlay"]
    for key in ("control", "scoreboard", "prematch"):
        section = overlay.get(key, {})
        if not isinstance(section, dict):
            raise SettingsError(f"overlay.{key} must be an object")
        allowed_keys = {"font_family", "colors"}
        if key != "control":
            allowed_keys.update({"width", "height"})
        if key == "prematch":
            allowed_keys.add("opacity")
        unknown_keys = set(section) - allowed_keys
        if unknown_keys:
            raise SettingsError(
                f"Unknown overlay.{key} setting: {sorted(unknown_keys)[0]}"
            )
        if "colors" in section and not isinstance(section["colors"], dict):
            raise SettingsError(f"overlay.{key}.colors must be an object")
    overlay = {
        key: {
            **overlay_defaults[key],
            **overlay.get(key, {}),
            "colors": {
                **overlay_defaults[key]["colors"],
                **overlay.get(key, {}).get("colors", {}),
            },
        }
        for key in ("control", "scoreboard", "prematch")
    }
    for key in ("control", "scoreboard", "prematch"):
        overlay[key] = _validate_overlay_style(overlay[key], f"overlay.{key}")
    for key in ("scoreboard", "prematch"):
        for dimension in ("width", "height"):
            value = overlay[key].get(dimension)
            overlay[key][dimension] = _positive_number(
                value, f"overlay.{key}.{dimension}"
            )
            if not isinstance(overlay[key][dimension], int):
                raise SettingsError(f"overlay.{key}.{dimension} must be an integer")
    opacity = overlay["prematch"]["opacity"]
    if (
        isinstance(opacity, bool)
        or not isinstance(opacity, (int, float))
        or not math.isfinite(opacity)
        or not 0 <= opacity <= 1
    ):
        raise SettingsError("overlay.prematch.opacity must be a number from 0 through 1")
    settings["overlay"] = overlay

    templates = settings.get("templates")
    if not isinstance(templates, dict):
        raise SettingsError("templates must be a YAML mapping")
    unknown_template_keys = set(templates) - {"scoreboard", "prematch"}
    if unknown_template_keys:
        raise SettingsError(f"Unknown template setting: {sorted(unknown_template_keys)[0]}")
    templates = {**default_settings()["templates"], **templates}
    for key, value in templates.items():
        if (
            not isinstance(value, str)
            or not value
            or Path(value).name != value
            or Path(value).suffix.lower() != ".html"
        ):
            raise SettingsError(f"templates.{key} must be an HTML filename without a path")
    settings["templates"] = templates

    return settings


def load_settings(path: Path, override_path: Path | None = None) -> dict[str, Any]:
    settings = _read_settings_file(path)
    overrides = _read_settings_file(override_path) if override_path is not None else {}
    return validate_settings(_merge_settings(settings, overrides))


def _read_settings_file(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as config_file:
            raw = yaml.safe_load(config_file)
    except FileNotFoundError as error:
        raise SettingsError(
            f"Settings file not found: {path}. Create it as a YAML settings file; see README.md."
        ) from error
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        location = (
            f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        )
        problem = getattr(error, "problem", None) or str(error)
        raise SettingsError(f"Invalid YAML in {path}{location}: {problem}") from error
    except UnicodeDecodeError as error:
        raise SettingsError(f"Settings file {path} must be UTF-8 encoded YAML") from error
    except OSError as error:
        raise SettingsError(f"Unable to read settings file {path}: {error}") from error
    if not isinstance(raw, dict):
        raise SettingsError(f"Settings file {path} must contain a YAML mapping")
    return raw


def _merge_settings(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_settings(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _contains_path(settings: dict[str, Any], path: tuple[str, ...]) -> bool:
    current: Any = settings
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return False
        current = current[key]
    return True


def _set_path(settings: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    current = settings
    for key in path[:-1]:
        child = current.get(key)
        if not isinstance(child, dict):
            child = {}
            current[key] = child
        current = child
    current[path[-1]] = copy.deepcopy(value)


def _changed_paths(
    previous: dict[str, Any], updated: dict[str, Any], prefix: tuple[str, ...] = ()
) -> list[tuple[str, ...]]:
    changed: list[tuple[str, ...]] = []
    for key in previous.keys() | updated.keys():
        old_value = previous.get(key)
        new_value = updated.get(key)
        path = (*prefix, key)
        if isinstance(old_value, dict) and isinstance(new_value, dict):
            changed.extend(_changed_paths(old_value, new_value, path))
        elif old_value != new_value:
            changed.append(path)
    return changed


def _write_settings_file(path: Path, settings: dict[str, Any], logger: logging.Logger) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as config_file:
            yaml.safe_dump(
                settings,
                config_file,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )
            config_file.flush()
            os.fsync(config_file.fileno())
        os.replace(temporary, path)
    except OSError as error:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.exception("Unable to remove temporary settings file %s", temporary)
        raise SettingsError(f"Unable to save settings file {path}: {error}") from error


def _settings_file_signature(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError as error:
        raise SettingsError(f"Unable to inspect settings file {path}: {error}") from error
    return stat.st_mtime_ns, stat.st_size


class SettingsWatcher:
    def __init__(
        self,
        path: Path,
        interval_seconds: float,
        on_change: Callable[[dict[str, Any]], None],
        logger: logging.Logger,
        override_path: Path | None = None,
    ) -> None:
        self.path = path
        self.override_path = override_path
        self.interval_seconds = interval_seconds
        self.on_change = on_change
        self.logger = logger
        self._stop = Event()
        self._lock = RLock()
        self._base_raw = _read_settings_file(path)
        self._override_raw = (
            _read_settings_file(override_path) if override_path is not None else {}
        )
        self._settings = validate_settings(
            _merge_settings(self._base_raw, self._override_raw)
        )
        self._signature = self._file_signature()
        self._last_error: str | None = None
        self._thread = Thread(target=self._watch, name="settings-watcher", daemon=True)
        self._thread.start()

    def _file_signature(self) -> tuple[tuple[int, int], ...]:
        paths = (self.path,) if self.override_path is None else (
            self.path,
            self.override_path,
        )
        return tuple(_settings_file_signature(path) for path in paths)

    def _load_layers(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        base_raw = _read_settings_file(self.path)
        override_raw = (
            _read_settings_file(self.override_path)
            if self.override_path is not None
            else {}
        )
        settings = validate_settings(_merge_settings(base_raw, override_raw))
        return base_raw, override_raw, settings

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._settings)

    @property
    def last_error(self) -> str | None:
        with self._lock:
            return self._last_error

    def write(self, settings: dict[str, Any]) -> dict[str, Any]:
        validated = validate_settings(settings)
        if self.override_path is None:
            _write_settings_file(self.path, validated, self.logger)
            base_raw, override_raw = validated, {}
        else:
            with self._lock:
                previous = copy.deepcopy(self._settings)
                base_raw = copy.deepcopy(self._base_raw)
                override_raw = copy.deepcopy(self._override_raw)
            base_changed = False
            override_changed = False
            for path in _changed_paths(previous, validated):
                if _contains_path(override_raw, path):
                    _set_path(override_raw, path, self._get_path(validated, path))
                    override_changed = True
                else:
                    _set_path(base_raw, path, self._get_path(validated, path))
                    base_changed = True
            validated = validate_settings(_merge_settings(base_raw, override_raw))
            if override_changed:
                _write_settings_file(self.override_path, override_raw, self.logger)
            if base_changed:
                _write_settings_file(self.path, base_raw, self.logger)
        self.apply(validated)
        with self._lock:
            self._base_raw = base_raw
            self._override_raw = override_raw
        self._signature = self._file_signature()
        return validated

    @staticmethod
    def _get_path(settings: dict[str, Any], path: tuple[str, ...]) -> Any:
        current: Any = settings
        for key in path:
            current = current[key]
        return current

    def apply(self, settings: dict[str, Any]) -> None:
        validated = validate_settings(settings)
        self.on_change(validated)
        with self._lock:
            self._settings = validated
            self._last_error = None
            self.interval_seconds = validated["server"]["watch_interval_seconds"]

    def _watch(self) -> None:
        while not self._stop.wait(float(self.interval_seconds)):
            try:
                signature = self._file_signature()
                if signature == self._signature:
                    continue
                base_raw, override_raw, settings = self._load_layers()
                self.apply(settings)
                with self._lock:
                    self._base_raw = base_raw
                    self._override_raw = override_raw
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
