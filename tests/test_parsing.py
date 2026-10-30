"""Defensive parsing tests for extractor.parse_model_output.

The parser must NEVER raise on model output and must always return the full 13-field
schema, no matter how malformed the input is.
"""

import json

import extractor
from extractor import FIELD_KEYS, parse_model_output

CLEAN = json.dumps(
    {
        "fields": {
            "drug_name": {"value": "Ozempic", "confidence": 0.95},
            "no_refills": {"value": "3", "confidence": 0.9},
        },
        "overall_legibility": "high",
        "warnings": ["LASA: Ozempic vs Ozurdex"],
        "phi_detected": False,
    }
)


def test_clean_json_parses_and_fills_all_fields():
    r = parse_model_output(CLEAN)
    assert r["fields"]["drug_name"]["value"] == "Ozempic"
    assert r["fields"]["drug_name"]["confidence"] == 0.95
    # Every field present even though the model only returned two.
    assert set(r["fields"].keys()) == set(FIELD_KEYS)
    assert r["fields"]["prescriber_name"]["value"] is None
    assert r["overall_legibility"] == "high"
    assert r["warnings"] == ["LASA: Ozempic vs Ozurdex"]
    assert r["phi_detected"] is False


def test_strips_markdown_fences_and_surrounding_prose():
    wrapped = "Sure, here you go:\n```json\n" + CLEAN + "\n```\nHope that helps!"
    r = parse_model_output(wrapped)
    assert r["fields"]["drug_name"]["value"] == "Ozempic"
    assert not r.get("_parse_error")


def test_bare_fences_without_lang():
    wrapped = "```\n" + CLEAN + "\n```"
    assert parse_model_output(wrapped)["fields"]["no_refills"]["value"] == "3"


def test_handles_braces_inside_string_values():
    payload = json.dumps(
        {
            "fields": {"sig_raw": {"value": "take 1 {with} food", "confidence": 0.8}},
            "overall_legibility": "medium",
            "warnings": [],
            "phi_detected": False,
        }
    )
    r = parse_model_output("prefix " + payload + " suffix")
    assert r["fields"]["sig_raw"]["value"] == "take 1 {with} food"


def test_total_garbage_never_raises_and_flags_everything():
    r = parse_model_output("the model refused and just wrote a sentence")
    assert r["_parse_error"] is True
    assert set(r["fields"].keys()) == set(FIELD_KEYS)
    assert all(f["confidence"] == 0.0 for f in r["fields"].values())
    assert all(f["value"] is None for f in r["fields"].values())
    assert r["overall_legibility"] == "low"


def test_empty_and_null_string_values_become_none():
    payload = json.dumps(
        {
            "fields": {
                "drug_name": {"value": "null", "confidence": 0.5},
                "drug_strength": {"value": "  ", "confidence": 0.5},
                "no_refills": {"value": "NULL", "confidence": 0.5},
            },
            "overall_legibility": "low",
            "warnings": [],
            "phi_detected": False,
        }
    )
    r = parse_model_output(payload)
    assert r["fields"]["drug_name"]["value"] is None
    assert r["fields"]["drug_strength"]["value"] is None
    assert r["fields"]["no_refills"]["value"] is None


def test_non_object_top_level_is_handled():
    r = parse_model_output("[1, 2, 3]")
    assert r["_parse_error"] is True
    assert set(r["fields"].keys()) == set(FIELD_KEYS)


def test_truncated_json_does_not_crash():
    r = parse_model_output('{"fields": {"drug_name": {"value": "Ozem')
    assert set(r["fields"].keys()) == set(FIELD_KEYS)


def test_numeric_values_coerced_to_string():
    payload = json.dumps(
        {
            "fields": {"no_refills": {"value": 3, "confidence": 0.9}},
            "overall_legibility": "high",
            "warnings": [],
            "phi_detected": False,
        }
    )
    r = parse_model_output(payload)
    assert r["fields"]["no_refills"]["value"] == "3"
