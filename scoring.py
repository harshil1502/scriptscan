"""ScriptScan scoring — the single source of truth for benchmark comparison.

Shared by evaluate.py (the report), benchmark_harness.py (the harness), the /benchmark
endpoints, and the unit tests. Pure functions, no I/O, no network — easy to test.

A "field value" is compared tolerantly so that trivially-equivalent strings count as
correct (case-insensitive, trimmed, "1mg" == "1 MG"). Genuine differences are surfaced
as near-misses or outright misses.
"""

from __future__ import annotations

import re
from typing import Any

# All fields ScriptScan extracts, in HealthWATCH data-entry order.
ALL_FIELDS: list[str] = [
    "prescriber_name",
    "prescriber_license",
    "drug_name",
    "drug_strength",
    "dosage_form",
    "sig_raw",
    "sig_expanded",
    "prescribed_qty",
    "no_refills",
    "tx_qty",
    "days_supply",
    "dosage_interval",
    "date_written",
]

# The hard, safety-critical fields the incumbent DE Assistant fails at — the headline.
HARD_FIELDS: list[str] = [
    "drug_name",
    "drug_strength",
    "sig_raw",
    "no_refills",
    "days_supply",
]

# Outcomes a single (predicted, truth) comparison can yield.
CORRECT = "correct"
NEAR = "near"
WRONG = "wrong"
MISSING = "missing"
NO_TRUTH = "no_truth"


def normalize(value: Any) -> str:
    """Normalize a field value for tolerant comparison.

    - None / missing / "null" / "none" / "n/a" -> "" (treated as no value)
    - case-insensitive, trimmed, internal whitespace collapsed
    - a number joined to a following unit so "1 mg" == "1mg" == "1 MG"
    """
    if value is None:
        return ""
    s = str(value).strip().lower()
    if s in ("", "null", "none", "n/a"):
        return ""
    s = s.replace(",", " ")
    s = re.sub(r"\s+", " ", s)
    # Normalize month names to numbers so "2026-jun-18" == "2026-06-18".
    s = _normalize_months(s)
    # Collapse spacing around separators so "1 mg / 0.5 mL" == "1 mg/0.5 mL".
    s = re.sub(r"\s*([/\-])\s*", r"\1", s)
    # "1 mg" -> "1mg", "0.5 ml" -> "0.5ml" so spacing around units is irrelevant.
    s = re.sub(r"(\d(?:\.\d+)?)\s+([a-z]+)", r"\1\2", s)
    s = s.strip(" .")
    return s


_MONTHS = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}


def _normalize_months(s: str) -> str:
    """Map 3-letter (or longer) month names to two-digit numbers, as whole tokens."""
    return re.sub(
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
        lambda m: _MONTHS[m.group(1)],
        s,
    )


# Fields whose HealthWATCH value is fundamentally a number — the verbatim Rx text often
# carries a trailing unit/word ("90 Tablet(s)", "5 (Five)") that is the SAME value. For
# these, matching the leading number counts as correct.
NUMERIC_FIELDS = {"prescribed_qty", "tx_qty", "days_supply", "no_refills"}


def _leading_number(s: str) -> str | None:
    m = re.search(r"\d+(?:\.\d+)?", s)
    return m.group(0) if m else None


def compare(predicted: Any, truth: Any) -> str:
    """Return one of: 'correct', 'near', 'wrong', 'missing', 'no_truth'."""
    nt = normalize(truth)
    np = normalize(predicted)
    if nt == "":
        return NO_TRUTH  # no ground truth recorded; this cell cannot be scored
    if np == "":
        return MISSING
    if np == nt:
        return CORRECT
    # Near miss: one normalized value contains the other.
    if np in nt or nt in np:
        return NEAR
    return WRONG


def compare_field(predicted: Any, truth: Any, field: str) -> str:
    """Field-aware comparison. For numeric fields, a matching leading number is correct
    ("90 Tablet(s)" == "90"); otherwise falls back to the general compare()."""
    if field in NUMERIC_FIELDS:
        nt, np = normalize(truth), normalize(predicted)
        if nt == "":
            return NO_TRUTH
        if np == "":
            return MISSING
        tn, pn = _leading_number(nt), _leading_number(np)
        if tn is not None and pn is not None and float(tn) == float(pn):
            return CORRECT
    return compare(predicted, truth)


def score_pair(
    predicted: dict[str, Any], truth: dict[str, Any], fields: list[str] | None = None
) -> dict[str, Any]:
    """Score one predicted field-set against one ground-truth field-set.

    Returns per-field outcomes plus overall and hard-field accuracy and the misses list.
    Accuracy denominators count only fields that HAVE a ground-truth value.
    """
    fields = fields or ALL_FIELDS
    per_field: dict[str, Any] = {}
    misses: list[dict[str, Any]] = []

    for f in fields:
        t = truth.get(f)
        p = predicted.get(f)
        outcome = compare_field(p, t, f)
        per_field[f] = {
            "predicted": None if p is None else str(p),
            "truth": None if t is None else str(t),
            "outcome": outcome,
        }
        if outcome not in (CORRECT, NO_TRUTH):
            misses.append({"field": f, "predicted": p, "truth": t, "outcome": outcome})

    def acc(field_set: list[str]) -> dict[str, Any]:
        scored = [f for f in field_set if per_field[f]["outcome"] != NO_TRUTH]
        correct = [f for f in scored if per_field[f]["outcome"] == CORRECT]
        pct = (len(correct) / len(scored) * 100) if scored else None
        return {"correct": len(correct), "scored": len(scored), "accuracy": pct}

    return {
        "per_field": per_field,
        "overall": acc(fields),
        "hard": acc([f for f in HARD_FIELDS if f in fields]),
        "misses": misses,
    }


def score_dataset(data: dict[str, Any]) -> dict[str, Any]:
    """Score a full benchmark dataset.

    data = { filename: { "ground_truth": {...}, "scriptscan": {...}, "de_assistant": {...} } }

    Returns per-field and aggregate accuracy for each system vs ground truth, plus every
    disagreement, comparable head-to-head.
    """
    systems = ["scriptscan", "de_assistant"]
    tally = {
        sys_: {f: {"correct": 0, "scored": 0} for f in ALL_FIELDS} for sys_ in systems
    }
    disagreements: list[dict[str, Any]] = []

    for filename, row in sorted(data.items()):
        truth_fields = row.get("ground_truth") or {}
        for sys_ in systems:
            pred_fields = row.get(sys_) or {}
            for f in ALL_FIELDS:
                if normalize(truth_fields.get(f)) == "":
                    continue  # no ground truth for this field on this fax
                outcome = compare_field(pred_fields.get(f), truth_fields.get(f), f)
                tally[sys_][f]["scored"] += 1
                if outcome == CORRECT:
                    tally[sys_][f]["correct"] += 1
                else:
                    disagreements.append(
                        {
                            "filename": filename,
                            "system": sys_,
                            "field": f,
                            "predicted": pred_fields.get(f),
                            "truth": truth_fields.get(f),
                            "outcome": outcome,
                        }
                    )

    def aggregate(sys_: str, field_set: list[str]) -> dict[str, Any]:
        correct = sum(tally[sys_][f]["correct"] for f in field_set)
        scored = sum(tally[sys_][f]["scored"] for f in field_set)
        pct = (correct / scored * 100) if scored else None
        return {"correct": correct, "scored": scored, "accuracy": pct}

    summary: dict[str, Any] = {"per_field": {}, "disagreements": disagreements}
    for f in ALL_FIELDS:
        summary["per_field"][f] = {}
        for sys_ in systems:
            c = tally[sys_][f]
            pct = (c["correct"] / c["scored"] * 100) if c["scored"] else None
            summary["per_field"][f][sys_] = {**c, "accuracy": pct}
    for label, field_set in (("overall", ALL_FIELDS), ("hard", HARD_FIELDS)):
        summary[label] = {sys_: aggregate(sys_, field_set) for sys_ in systems}

    return summary
