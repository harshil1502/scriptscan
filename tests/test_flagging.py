"""Confidence-flagging tests — the 'no silent miss' guarantee."""

import extractor
from extractor import flag_fields


def _result(confidences):
    return {
        "fields": {
            f"field_{i}": {"value": "v", "confidence": c}
            for i, c in enumerate(confidences)
        }
    }


def test_below_threshold_is_flagged_review():
    r = flag_fields(_result([0.5, 0.95]), threshold=0.90)
    fs = list(r["fields"].values())
    assert fs[0]["review"] is True
    assert fs[1]["review"] is False
    assert r["flagged_count"] == 1
    assert r["threshold"] == 0.90


def test_value_exactly_at_threshold_is_not_flagged():
    # review = confidence < threshold, so exactly-at-threshold passes.
    r = flag_fields(_result([0.90]), threshold=0.90)
    assert list(r["fields"].values())[0]["review"] is False
    assert r["flagged_count"] == 0


def test_all_low_confidence_all_flagged():
    r = flag_fields(_result([0.1, 0.2, 0.0]), threshold=0.90)
    assert r["flagged_count"] == 3
    assert all(f["review"] for f in r["fields"].values())


def test_missing_confidence_treated_as_zero_and_flagged():
    r = flag_fields({"fields": {"x": {"value": None}}}, threshold=0.90)
    assert r["fields"]["x"]["review"] is True
    assert r["flagged_count"] == 1


def test_default_threshold_is_used_when_unspecified():
    r = flag_fields(_result([extractor.DEFAULT_THRESHOLD - 0.01]))
    assert r["flagged_count"] == 1


def test_custom_lower_threshold_flags_fewer():
    r = flag_fields(_result([0.6, 0.85]), threshold=0.5)
    assert r["flagged_count"] == 0
