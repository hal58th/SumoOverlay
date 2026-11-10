import logging
import sys
from types import SimpleNamespace

import pytest

from ocr_scanner import OCRScanner


def test_scanner_requires_stable_reads_and_emits_a_match_once(caplog):
    emitted = []
    scanner = OCRScanner(
        lambda: {},
        lambda left, right: emitted.append((left, right))
        or {"index": 3, "division": "Makuuchi", "matchNo": 4},
        logging.getLogger("test-ocr"),
    )

    assert scanner.observe(" 大喜翔 ", "栃幸大", stable_readings=2) is None
    assert scanner.status()["state"] == "confirming"
    state = scanner.observe("大喜翔", " 栃幸大 ", stable_readings=2)
    assert state == {"index": 3, "division": "Makuuchi", "matchNo": 4}
    assert scanner.status()["state"] == "matched"
    assert scanner.status()["lastAction"] == "Matched 大喜翔 to Makuuchi bout 4."
    assert emitted == [("大喜翔", "栃幸大")]

    assert scanner.observe("大喜翔", "栃幸大", stable_readings=2) is None
    assert scanner.status()["state"] == "matched"
    assert emitted == [("大喜翔", "栃幸大")]


def test_scanner_can_detect_the_same_names_after_a_detection_reset():
    emitted = []
    scanner = OCRScanner(
        lambda: {},
        lambda left, right: emitted.append((left, right)) or {"index": 0},
        logging.getLogger("test-ocr"),
    )

    scanner.observe("大喜翔", "栃幸大", stable_readings=1)
    scanner.reset_detection()
    scanner.observe("大喜翔", "栃幸大", stable_readings=1)

    assert emitted == [("大喜翔", "栃幸大"), ("大喜翔", "栃幸大")]


def test_scanner_resets_stability_when_names_change_or_disappear():
    emitted = []
    scanner = OCRScanner(
        lambda: {},
        lambda left, right: emitted.append((left, right)) or {"index": 1},
        logging.getLogger("test-ocr"),
    )

    scanner.observe("大喜翔", "栃幸大", stable_readings=2)
    scanner.observe("藤天晴", "夢道鵬", stable_readings=2)
    scanner.observe("", "夢道鵬", stable_readings=2)
    scanner.observe("藤天晴", "夢道鵬", stable_readings=2)

    assert emitted == []
    assert scanner.status()["state"] == "confirming"


@pytest.mark.parametrize(
    ("detections", "expected"),
    [
        ((("大喜翔", 0.8), ("読めない", 0.2)), ("大喜翔", "")),
        ((("読めない", 0.2), ("栃幸大", 0.8)), ("", "栃幸大")),
    ],
)
def test_scanner_accepts_either_name_when_only_one_side_meets_confidence(
    monkeypatch, detections, expected
):
    emitted = []
    settings = {
        "control": {"mode": "automated"},
        "ocr": {
            "enabled": True,
            "stable_readings": 1,
            "minimum_confidence": 0.5,
            "interval_seconds": 0.01,
        },
    }

    def on_match(left, right):
        emitted.append((left, right))
        scanner._stop.set()
        return {"index": 4}

    scanner = OCRScanner(lambda: settings, on_match, logging.getLogger("test-ocr"))
    monkeypatch.setattr(
        scanner,
        "_capture_and_read",
        lambda current_settings: (detections, None),
    )
    scanner._run()

    assert emitted == [expected]
    assert scanner.status()["state"] == "matched"
    assert scanner.status()["matchedBout"]["index"] == 4


@pytest.mark.parametrize(
    ("matched_pairs", "expected"),
    [
        ({("alternate-left", "alternate-right")}, ("alternate-left", "alternate-right")),
        (
            {
                ("primary-left", "primary-right"),
                ("alternate-left", "alternate-right"),
            },
            ("primary-left", "primary-right"),
        ),
        (set(), ("primary-left", "primary-right")),
    ],
)
def test_scanner_uses_schedule_match_to_select_name_pair(matched_pairs, expected):
    settings = {
        "control": {"mode": "automated"},
        "ocr": {
            "enabled": True,
            "stable_readings": 1,
            "minimum_confidence": 0.5,
            "interval_seconds": 0.01,
        },
    }
    primary = (("primary-left", 0.9), ("primary-right", 0.9))
    alternate = (("alternate-left", 0.9), ("alternate-right", 0.9))
    emitted = []
    scanner = OCRScanner(
        lambda: settings,
        lambda left, right: None,
        logging.getLogger("test-ocr-pair-selection"),
        matches_bout=lambda left, right: (left, right) in matched_pairs,
    )
    scanner._capture_and_read = lambda current_settings: (primary, alternate)

    def on_match(left, right):
        emitted.append((left, right))
        scanner._stop.set()
        if (left, right) in matched_pairs:
            return {"index": 2, "division": "Makuuchi", "matchNo": 3}
        return None

    scanner.on_match = on_match

    scanner._run()

    assert emitted == [expected]
    expected_state = "matched" if expected in matched_pairs else "unmatched"
    assert scanner.status()["state"] == expected_state


def test_capture_reads_both_pairs_from_one_scoresheet_frame(monkeypatch):
    import mss

    class FakeScreenshot:
        width = 40
        height = 20
        rgb = b"\x00" * (width * height * 3)

    class FakeMSS:
        monitors = [
            {"left": 0, "top": 0, "width": 100, "height": 100},
            {"left": 0, "top": 0, "width": 100, "height": 100},
        ]

        def __init__(self):
            self.grabs = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def grab(self, region):
            self.grabs.append(region)
            return FakeScreenshot()

    fake_capture = FakeMSS()
    monkeypatch.setattr(mss, "mss", lambda: fake_capture)

    class FakeReader:
        def __init__(self):
            self.read_count = 0

        def readtext(self, crop, detail=1, paragraph=False):
            self.read_count += 1
            return [([0, 0], f"box-{self.read_count}", 0.9)]

    scanner = OCRScanner(lambda: {}, lambda left, right: None, logging.getLogger("test-ocr"))
    scanner._reader = FakeReader()
    settings = {
        "ocr": {
            "monitor": 1,
            "capture_regions": {
                "scoresheet": {"x": 5, "y": 10, "width": 40, "height": 20}
            },
            "name_boxes": {
                "left": {"x": 0, "y": 0, "width": 10, "height": 10},
                "right": {"x": 20, "y": 0, "width": 10, "height": 10},
            },
            "alternate_name_boxes": {
                "left": {"x": 0, "y": 10, "width": 10, "height": 10},
                "right": {"x": 20, "y": 10, "width": 10, "height": 10},
            },
        }
    }

    primary, alternate = scanner._capture_and_read(settings)

    assert primary == (("box-1", 0.9), ("box-2", 0.9))
    assert alternate == (("box-3", 0.9), ("box-4", 0.9))
    assert len(fake_capture.grabs) == 1


def test_scanner_reports_unmatched_names_and_callback_errors(caplog):
    logger = logging.getLogger("test-ocr-errors")
    unmatched = OCRScanner(lambda: {}, lambda left, right: None, logger)

    unmatched.observe("大喜翔", "栃幸大", stable_readings=1)
    assert unmatched.status()["state"] == "unmatched"
    assert unmatched.status()["matchedBout"] is None
    last_action = unmatched.status()["lastAction"]
    unmatched.observe("", "", stable_readings=1)
    assert unmatched.status()["lastAction"] == last_action

    def fail_lookup(left, right):
        raise RuntimeError("lookup failed")

    failed = OCRScanner(lambda: {}, fail_lookup, logger)
    with caplog.at_level(logging.ERROR, logger="test-ocr-errors"):
        failed.observe("大喜翔", "栃幸大", stable_readings=1)

    assert failed.status()["state"] == "error"
    assert failed.status()["error"] == "lookup failed"
    assert "OCR match lookup failed" in caplog.text


def test_reader_is_cpu_only_and_limits_torch_threads(monkeypatch):
    reader_options = {}
    thread_limits = []
    expected_reader = object()

    def make_reader(languages, **options):
        reader_options["languages"] = languages
        reader_options.update(options)
        return expected_reader

    monkeypatch.setitem(sys.modules, "easyocr", SimpleNamespace(Reader=make_reader))
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(set_num_threads=thread_limits.append),
    )
    scanner = OCRScanner(lambda: {}, lambda left, right: None, logging.getLogger("test-ocr"))

    assert scanner._load_reader() is expected_reader
    assert reader_options == {"languages": ["ja"], "gpu": False, "verbose": False}
    assert thread_limits == [2]
