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
        lambda current_settings: detections,
    )
    scanner._run()

    assert emitted == [expected]
    assert scanner.status()["state"] == "matched"
    assert scanner.status()["matchedBout"]["index"] == 4


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
