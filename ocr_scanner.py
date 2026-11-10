"""CPU-based OCR capture and stable match-detection loop."""

from __future__ import annotations

import logging
from threading import Event, RLock, Thread
from typing import Any, Callable


class OCRScanner:
    def __init__(
        self,
        get_settings: Callable[[], dict[str, Any]],
        on_match: Callable[[str, str], dict[str, Any] | None],
        logger: logging.Logger,
        matches_bout: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.get_settings = get_settings
        self.on_match = on_match
        self.logger = logger
        self.matches_bout = matches_bout
        self._stop = Event()
        self._reset_requested = Event()
        self._lock = RLock()
        self._thread: Thread | None = None
        self._reader: Any | None = None
        self._candidate: tuple[str, str] | None = None
        self._candidate_count = 0
        self._last_emitted: tuple[str, str] | None = None
        self._last_screenshot: Any | None = None
        self._status: dict[str, Any] = {
            "state": "stopped",
            "left": "",
            "right": "",
            "leftConfidence": None,
            "rightConfidence": None,
            "error": None,
            "matchedBout": None,
            "lastAction": "No stable OCR match has been checked yet.",
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._run, name="sumo-ocr-scanner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._set_status(state="stopped")

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def reset_detection(self) -> None:
        self._reset_requested.set()

    def _reset_detection_if_requested(self) -> None:
        if self._reset_requested.is_set():
            self._candidate = None
            self._candidate_count = 0
            self._last_emitted = None
            self._reset_requested.clear()

    def _set_status(self, **values: Any) -> None:
        with self._lock:
            self._status.update(values)

    @staticmethod
    def normalize_text(text: str) -> str:
        return "".join(text.split())

    def observe(
        self,
        left: str,
        right: str,
        stable_readings: int,
    ) -> dict[str, Any] | None:
        self._reset_detection_if_requested()
        pair = (self.normalize_text(left), self.normalize_text(right))
        if not any(pair):
            self._candidate = None
            self._candidate_count = 0
            self._set_status(
                state="reading",
                left=pair[0],
                right=pair[1],
                leftConfidence=None,
                rightConfidence=None,
                error=None,
                matchedBout=None,
            )
            return None

        if pair == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = pair
            self._candidate_count = 1
        if self._candidate_count < stable_readings:
            self._set_status(
                state="confirming",
                left=pair[0],
                right=pair[1],
                leftConfidence=None,
                rightConfidence=None,
                error=None,
                matchedBout=None,
            )
            return None
        if pair == self._last_emitted:
            return None

        self._last_emitted = pair
        self._set_status(
            state="recognized",
            left=pair[0],
            right=pair[1],
            leftConfidence=None,
            rightConfidence=None,
            error=None,
        )
        try:
            state = self.on_match(*pair)
        except Exception as error:
            self.logger.exception("OCR match lookup failed")
            self._set_status(
                state="error",
                error=str(error),
                lastAction=f"OCR schedule lookup failed: {error}",
            )
            return None
        if state is None:
            self._set_status(
                state="unmatched",
                error="Recognized names were not found together in the loaded bout schedule.",
                matchedBout=None,
                lastAction=(
                    f"OCR read {pair[0] or 'unread'} vs {pair[1] or 'unread'}, "
                    "but could not identify one matching scheduled bout."
                ),
            )
            return None
        matched_bout = {
            "division": state.get("division"),
            "matchNo": state.get("matchNo"),
            "index": state.get("index"),
        }
        self._set_status(
            state="matched",
            error=None,
            matchedBout=matched_bout,
            lastAction=(
                f"Matched {pair[0] or pair[1]} to {matched_bout['division']} "
                f"bout {matched_bout['matchNo']}."
            ),
        )
        return state

    def _load_reader(self) -> Any:
        if self._reader is None:
            try:
                import easyocr
                import torch
            except ImportError as error:
                raise RuntimeError(
                    'OCR packages are missing. Install them with: '
                    r'.\.venv\Scripts\python.exe -m pip install -e ".[ocr]"'
                ) from error
            torch.set_num_threads(2)
            self._reader = easyocr.Reader(["ja"], gpu=False, verbose=False)
        return self._reader

    def set_last_screenshot(self, frame: Any) -> None:
        with self._lock:
            self._last_screenshot = frame

    def get_last_screenshot(self) -> Any | None:
        with self._lock:
            return self._last_screenshot

    def read_crops_from_frame(
        self,
        frame: Any,
        capture_region: dict[str, int],
        name_boxes: dict[str, dict[str, int]],
        frame_is_full_monitor: bool = True,
        alternate_name_boxes: dict[str, dict[str, int]] | None = None,
    ) -> tuple[
        tuple[tuple[str, float, bytes], tuple[str, float, bytes]],
        tuple[tuple[str, float, bytes], tuple[str, float, bytes]] | None,
    ]:
        """Run OCR on explicit name box coordinates from an existing image frame."""
        try:
            import mss.tools
        except ImportError as error:
            raise RuntimeError(
                'Screen-capture packages are missing. Install them with: '
                r'.\.venv\Scripts\python.exe -m pip install -e ".[ocr]"'
            ) from error

        if frame_is_full_monitor:
            cap_x = capture_region["x"]
            cap_y = capture_region["y"]
            cap_w = capture_region["width"]
            cap_h = capture_region["height"]
            sub_frame = frame[cap_y : cap_y + cap_h, cap_x : cap_x + cap_w]
        else:
            sub_frame = frame

        if sub_frame.size == 0:
            raise RuntimeError("Capture region produced an empty image frame.")

        reader = self._load_reader()

        def read_pair(
            pair_boxes: dict[str, dict[str, int]],
        ) -> tuple[tuple[str, float, bytes], tuple[str, float, bytes]]:
            detected: list[tuple[str, float, bytes]] = []
            for side in ("left", "right"):
                box = pair_boxes[side]
                left_px = box["x"]
                top_px = box["y"]
                right_px = left_px + box["width"]
                bottom_px = top_px + box["height"]
                crop = sub_frame[top_px:bottom_px, left_px:right_px]
                if crop.size == 0:
                    raise RuntimeError(
                        f"OCR {side} name box produced an empty image crop. "
                        f"Ensure {side} box ({left_px},{top_px} "
                        f"{box['width']}x{box['height']}) fits inside capture region "
                        f"({sub_frame.shape[1]}x{sub_frame.shape[0]})."
                    )
                results = reader.readtext(crop, detail=1, paragraph=False)
                readings = [
                    (self.normalize_text(text), float(confidence))
                    for _, text, confidence in results
                ]
                text, conf = (
                    max(readings, key=lambda reading: reading[1])
                    if readings
                    else ("", 0.0)
                )
                try:
                    png_bytes = mss.tools.to_png(
                        crop.tobytes(), (crop.shape[1], crop.shape[0])
                    )
                except Exception:
                    png_bytes = b""
                detected.append((text, conf, png_bytes or b""))
            return detected[0], detected[1]

        primary = read_pair(name_boxes)
        alternate = (
            read_pair(alternate_name_boxes) if alternate_name_boxes else None
        )
        return primary, alternate

    def capture_and_read_with_boxes(
        self,
        monitor_number: int,
        capture_region: dict[str, int],
        name_boxes: dict[str, dict[str, int]],
        alternate_name_boxes: dict[str, dict[str, int]] | None = None,
    ) -> tuple[
        tuple[tuple[str, float], tuple[str, float]],
        tuple[tuple[str, float], tuple[str, float]] | None,
    ]:
        """Capture and OCR using explicit coordinates instead of reading from settings."""
        try:
            import mss
            import numpy as np
        except ImportError as error:
            raise RuntimeError(
                'Screen-capture packages are missing. Install them with: '
                r'.\.venv\Scripts\python.exe -m pip install -e ".[ocr]"'
            ) from error

        with mss.mss() as capture:
            monitors = capture.monitors
            if monitor_number >= len(monitors):
                raise RuntimeError(
                    f"OCR monitor {monitor_number} is unavailable; this computer has "
                    f"{max(0, len(monitors) - 1)} configured monitor(s)."
                )
            monitor = monitors[monitor_number]
            if (
                capture_region["x"] + capture_region["width"] > monitor["width"]
                or capture_region["y"] + capture_region["height"] > monitor["height"]
            ):
                raise RuntimeError(
                    "capture_region must fit inside the selected monitor."
                )
            screenshot = capture.grab(
                {
                    "left": monitor["left"] + capture_region["x"],
                    "top": monitor["top"] + capture_region["y"],
                    "width": capture_region["width"],
                    "height": capture_region["height"],
                }
            )
            frame = np.frombuffer(screenshot.rgb, dtype=np.uint8).reshape(
                screenshot.height, screenshot.width, 3
            )

        primary, alternate = self.read_crops_from_frame(
            frame,
            capture_region,
            name_boxes,
            frame_is_full_monitor=False,
            alternate_name_boxes=alternate_name_boxes,
        )
        primary_text = (
            (primary[0][0], primary[0][1]),
            (primary[1][0], primary[1][1]),
        )
        alternate_text = (
            (
                (alternate[0][0], alternate[0][1]),
                (alternate[1][0], alternate[1][1]),
            )
            if alternate is not None
            else None
        )
        return primary_text, alternate_text

    def capture_crops(
        self, settings: dict[str, Any]
    ) -> tuple[
        tuple[tuple[str, float, bytes], tuple[str, float, bytes]],
        tuple[tuple[str, float, bytes], tuple[str, float, bytes]] | None,
    ]:
        """Capture each configured name-pair crop from one scoresheet frame."""
        try:
            import mss
            import numpy as np
        except ImportError as error:
            raise RuntimeError(
                'Screen-capture packages are missing. Install them with: '
                r'.\.venv\Scripts\python.exe -m pip install -e ".[ocr]"'
            ) from error

        ocr_settings = settings["ocr"]
        capture_region = ocr_settings["capture_regions"]["scoresheet"]
        with mss.mss() as capture:
            monitors = capture.monitors
            monitor_number = ocr_settings["monitor"]
            if monitor_number >= len(monitors):
                raise RuntimeError(
                    f"OCR monitor {monitor_number} is unavailable; this computer has "
                    f"{max(0, len(monitors) - 1)} configured monitor(s)."
                )
            monitor = monitors[monitor_number]
            screenshot = capture.grab(
                {
                    "left": monitor["left"] + capture_region["x"],
                    "top": monitor["top"] + capture_region["y"],
                    "width": capture_region["width"],
                    "height": capture_region["height"],
                }
            )
            frame = np.frombuffer(screenshot.rgb, dtype=np.uint8).reshape(
                screenshot.height, screenshot.width, 3
            )

        return self.read_crops_from_frame(
            frame,
            capture_region,
            ocr_settings["name_boxes"],
            frame_is_full_monitor=False,
            alternate_name_boxes=ocr_settings.get("alternate_name_boxes"),
        )

    def _capture_and_read(
        self, settings: dict[str, Any]
    ) -> tuple[
        tuple[tuple[str, float], tuple[str, float]],
        tuple[tuple[str, float], tuple[str, float]] | None,
    ]:
        try:
            import mss
            import numpy as np
        except ImportError as error:
            raise RuntimeError(
                'Screen-capture packages are missing. Install them with: '
                r'.\.venv\Scripts\python.exe -m pip install -e ".[ocr]"'
            ) from error

        ocr_settings = settings["ocr"]
        capture_region = ocr_settings["capture_regions"]["scoresheet"]
        with mss.mss() as capture:
            monitors = capture.monitors
            monitor_number = ocr_settings["monitor"]
            if monitor_number >= len(monitors):
                raise RuntimeError(
                    f"OCR monitor {monitor_number} is unavailable; this computer has "
                    f"{max(0, len(monitors) - 1)} configured monitor(s)."
                )
            monitor = monitors[monitor_number]
            if (
                capture_region["x"] + capture_region["width"] > monitor["width"]
                or capture_region["y"] + capture_region["height"] > monitor["height"]
            ):
                raise RuntimeError(
                    "ocr.capture_regions.scoresheet must fit inside the selected OCR monitor."
                )
            screenshot = capture.grab(
                {
                    "left": monitor["left"] + capture_region["x"],
                    "top": monitor["top"] + capture_region["y"],
                    "width": capture_region["width"],
                    "height": capture_region["height"],
                }
            )
            frame = np.frombuffer(screenshot.rgb, dtype=np.uint8).reshape(
                screenshot.height, screenshot.width, 3
            )

        reader = self._load_reader()

        def read_pair(
            name_boxes: dict[str, dict[str, int]]
        ) -> tuple[tuple[str, float], tuple[str, float]]:
            detected: list[tuple[str, float]] = []
            for side in ("left", "right"):
                box = name_boxes[side]
                left = box["x"]
                top = box["y"]
                right = left + box["width"]
                bottom = top + box["height"]
                crop = frame[top:bottom, left:right]
                if crop.size == 0:
                    raise RuntimeError(
                        f"OCR {side} name box produced an empty image crop."
                    )
                results = reader.readtext(crop, detail=1, paragraph=False)
                readings = [
                    (self.normalize_text(text), float(confidence))
                    for _, text, confidence in results
                ]
                detected.append(
                    max(readings, key=lambda result: result[1])
                    if readings
                    else ("", 0.0)
                )
            return detected[0], detected[1]

        primary = read_pair(ocr_settings["name_boxes"])
        alternate_boxes = ocr_settings.get("alternate_name_boxes")
        alternate = read_pair(alternate_boxes) if alternate_boxes else None
        return primary, alternate

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._reset_detection_if_requested()
                settings = self.get_settings()
                mode = settings["control"]["mode"]
                ocr_settings = settings["ocr"]
                if not ocr_settings["enabled"]:
                    self._candidate = None
                    self._candidate_count = 0
                    self._last_emitted = None
                    self._set_status(state="disabled", error=None, matchedBout=None)
                    self._stop.wait(min(1, ocr_settings["interval_seconds"]))
                    continue
                if mode != "automated":
                    self._candidate = None
                    self._candidate_count = 0
                    self._last_emitted = None
                    self._set_status(state="manual", error=None, matchedBout=None)
                    self._stop.wait(min(1, ocr_settings["interval_seconds"]))
                    continue

                primary, alternate = self._capture_and_read(settings)
                minimum_confidence = ocr_settings["minimum_confidence"]

                def trusted_pair(
                    pair: tuple[tuple[str, float], tuple[str, float]],
                ) -> tuple[str, str]:
                    left_reading, right_reading = pair
                    return (
                        left_reading[0]
                        if left_reading[1] >= minimum_confidence
                        else "",
                        right_reading[0]
                        if right_reading[1] >= minimum_confidence
                        else "",
                    )

                primary_names = trusted_pair(primary)
                if alternate is not None and self.matches_bout is not None:
                    alternate_names = trusted_pair(alternate)
                    if not self.matches_bout(*primary_names) and self.matches_bout(
                        *alternate_names
                    ):
                        primary = alternate
                        primary_names = alternate_names

                left, right = primary
                if (
                    left[1] < minimum_confidence
                    and right[1] < minimum_confidence
                ):
                    self._candidate = None
                    self._candidate_count = 0
                    self._set_status(
                        state="low-confidence",
                        left=left[0],
                        right=right[0],
                        leftConfidence=left[1],
                        rightConfidence=right[1],
                        error=None,
                        matchedBout=None,
                        lastAction=(
                            "OCR read "
                            f"{left[0] or 'unread'} ({left[1]:.0%}) vs "
                            f"{right[0] or 'unread'} ({right[1]:.0%}); "
                            "both names were below the confidence threshold."
                        ),
                    )
                else:
                    self.observe(
                        *primary_names,
                        ocr_settings["stable_readings"],
                    )
                self._stop.wait(ocr_settings["interval_seconds"])
            except Exception as error:
                message = str(error)
                with self._lock:
                    should_log = self._status.get("error") != message
                if should_log:
                    self.logger.exception("OCR scanner is unavailable: %s", error)
                self._set_status(
                    state="error",
                    error=message,
                    lastAction=f"OCR scan failed: {message}",
                )
                self._stop.wait(2)
