"""Scoring tests — normalization, per-field comparison, dataset aggregation, headline."""

import scoring
from scoring import compare, normalize, score_dataset, score_pair


# --- normalize ---------------------------------------------------------------


def test_normalize_unit_spacing_equivalence():
    assert normalize("1mg") == normalize("1 MG") == normalize("1 mg")


def test_normalize_case_and_whitespace():
    assert normalize("  Ozempic  ") == normalize("ozempic")
    assert normalize("INJ   1  MG") == normalize("inj 1 mg")


def test_normalize_treats_nullish_as_empty():
    for v in (None, "", "null", "NONE", "n/a", "  "):
        assert normalize(v) == ""


def test_normalize_collapses_spacing_around_separators():
    # Regression: "1 mg / 0.5 mL" must equal "1 mg/0.5 mL" (identical strength).
    assert normalize("1 mg / 0.5 mL") == normalize("1 mg/0.5 mL")
    assert compare("1 mg / 0.5 mL", "1 mg/0.5 mL") == "correct"


def test_normalize_month_names_match_numeric_dates():
    # Regression: "2026-Jun-18" must equal "2026-06-18" (same date).
    assert compare("2026-06-18", "2026-Jun-18") == "correct"
    assert compare("2026-June-18", "2026-06-18") == "correct"


def test_numeric_field_ignores_trailing_unit_words():
    from scoring import compare_field

    # HealthWATCH qty/refills are numbers; "90 Tablet(s)" captures qty 90.
    assert compare_field("90 Tablet(s)", "90", "prescribed_qty") == "correct"
    assert compare_field("5 (Five)", "5", "no_refills") == "correct"
    assert (
        compare_field("100 Tablet(s) (One hundred)", "100", "prescribed_qty")
        == "correct"
    )
    # But a genuinely different number is still wrong.
    assert compare_field("90", "180", "prescribed_qty") == "wrong"
    # Non-numeric fields keep strict behavior (drug name unit words matter).
    assert compare_field("Metformin XR", "Metformin", "drug_name") == "near"


# --- compare -----------------------------------------------------------------


def test_compare_correct_with_unit_normalization():
    assert compare("1 mg", "1mg") == "correct"


def test_compare_missing_when_no_prediction():
    assert compare(None, "Ozempic") == "missing"
    assert compare("", "Ozempic") == "missing"


def test_compare_no_truth_when_no_ground_truth():
    assert compare("anything", None) == "no_truth"


def test_compare_near_on_substring():
    assert compare("Metformin", "Metformin HCl") == "near"


def test_compare_wrong_on_mismatch():
    assert compare("Lamisil", "Lamictal") == "wrong"


# --- score_pair --------------------------------------------------------------


def test_score_pair_accuracy_and_misses():
    truth = {
        "drug_name": "Ozempic",
        "drug_strength": "1 mg",
        "no_refills": "3",
        "days_supply": "28",
        "prescriber_name": "Dr. Chen",
    }
    predicted = {
        "drug_name": "Ozempic",
        "drug_strength": "1mg",  # correct after normalization
        "no_refills": "5",  # wrong
        "days_supply": None,  # missing
        "prescriber_name": "Dr. Chen",
    }
    res = score_pair(predicted, truth)
    assert res["per_field"]["drug_strength"]["outcome"] == "correct"
    assert res["per_field"]["no_refills"]["outcome"] == "wrong"
    assert res["per_field"]["days_supply"]["outcome"] == "missing"
    # 5 fields have truth; 3 correct -> 60%
    assert round(res["overall"]["accuracy"]) == 60
    miss_fields = {m["field"] for m in res["misses"]}
    assert miss_fields == {"no_refills", "days_supply"}


def test_score_pair_no_truth_excluded_from_denominator():
    res = score_pair({"drug_name": "X"}, {"drug_name": "X"})
    # Only drug_name has truth; everything else is no_truth and not scored.
    assert res["overall"]["scored"] == 1
    assert res["overall"]["accuracy"] == 100.0


def test_score_pair_empty_truth_gives_none_accuracy():
    res = score_pair({"drug_name": "X"}, {})
    assert res["overall"]["scored"] == 0
    assert res["overall"]["accuracy"] is None  # no division by zero


# --- score_dataset (head-to-head headline) -----------------------------------


def test_dataset_headline_scriptscan_beats_de_assistant():
    data = {
        "rx1.png": {
            "ground_truth": {
                "drug_name": "Ozempic",
                "drug_strength": "1 mg",
                "sig_raw": "INJ 1 MG SC ONCE WEEKLY",
                "no_refills": "3",
                "days_supply": "28",
            },
            "scriptscan": {
                "drug_name": "Ozempic",
                "drug_strength": "1mg",
                "sig_raw": "inj 1 mg sc once weekly",
                "no_refills": "3",
                "days_supply": "28",
            },
            "de_assistant": {
                "drug_name": None,
                "drug_strength": None,
                "sig_raw": None,
                "no_refills": None,
                "days_supply": None,
            },
        }
    }
    s = score_dataset(data)
    assert s["hard"]["scriptscan"]["accuracy"] == 100.0
    assert s["hard"]["de_assistant"]["accuracy"] == 0.0
    # All 5 DE Assistant hard fields are disagreements (misses).
    de_misses = [d for d in s["disagreements"] if d["system"] == "de_assistant"]
    assert len(de_misses) == 5


def test_dataset_empty_is_safe():
    s = score_dataset({})
    assert s["overall"]["scriptscan"]["accuracy"] is None
    assert s["disagreements"] == []


def test_hard_fields_subset_of_all_fields():
    assert set(scoring.HARD_FIELDS).issubset(set(scoring.ALL_FIELDS))
