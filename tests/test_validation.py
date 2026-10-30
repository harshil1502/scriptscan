"""Schema validation / coercion tests (confidence clamping, type coercion)."""

import json

from extractor import parse_model_output


def _wrap(fields, legibility="high", warnings=None, phi=False):
    return json.dumps(
        {
            "fields": fields,
            "overall_legibility": legibility,
            "warnings": warnings if warnings is not None else [],
            "phi_detected": phi,
        }
    )


def test_confidence_clamped_into_unit_interval():
    r = parse_model_output(
        _wrap(
            {
                "drug_name": {"value": "X", "confidence": 5},
                "no_refills": {"value": "3", "confidence": -1},
                "drug_strength": {"value": "Y", "confidence": 0.5},
            }
        )
    )
    assert r["fields"]["drug_name"]["confidence"] == 1.0
    assert r["fields"]["no_refills"]["confidence"] == 0.0
    assert r["fields"]["drug_strength"]["confidence"] == 0.5


def test_unparseable_confidence_becomes_zero():
    r = parse_model_output(_wrap({"drug_name": {"value": "X", "confidence": "high"}}))
    assert r["fields"]["drug_name"]["confidence"] == 0.0


def test_nan_confidence_becomes_zero_not_leaked():
    # Regression: NaN slips past < / > clamps; must be coerced to 0.0 so it can't
    # defeat the REVIEW flag or break JSON serialization (Starlette rejects NaN).
    r = parse_model_output(
        '{"fields": {"drug_name": {"value": "X", "confidence": NaN}}}'
    )
    c = r["fields"]["drug_name"]["confidence"]
    assert c == 0.0
    import math

    assert math.isfinite(c)


def test_infinity_confidence_clamped():
    r = parse_model_output(
        '{"fields": {"drug_name": {"value": "X", "confidence": Infinity}}}'
    )
    assert r["fields"]["drug_name"]["confidence"] == 0.0


def test_missing_confidence_defaults_to_zero():
    r = parse_model_output(_wrap({"drug_name": {"value": "X"}}))
    assert r["fields"]["drug_name"]["confidence"] == 0.0


def test_non_dict_field_entry_is_replaced_with_empty():
    r = parse_model_output(_wrap({"drug_name": "just a string"}))
    assert r["fields"]["drug_name"]["value"] is None
    assert r["fields"]["drug_name"]["confidence"] == 0.0


def test_invalid_legibility_coerced_to_low():
    r = parse_model_output(_wrap({}, legibility="excellent"))
    assert r["overall_legibility"] == "low"


def test_warnings_non_list_coerced_to_empty():
    r = parse_model_output(_wrap({}, warnings="not a list"))
    assert r["warnings"] == []


def test_warnings_items_stringified_and_blanks_dropped():
    r = parse_model_output(_wrap({}, warnings=["real", "", "  ", 42]))
    assert r["warnings"] == ["real", "42"]


def test_phi_detected_coerced_to_bool():
    assert parse_model_output(_wrap({}, phi=1))["phi_detected"] is True
    assert parse_model_output(_wrap({}, phi=0))["phi_detected"] is False
