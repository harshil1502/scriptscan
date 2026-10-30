#!/usr/bin/env python3
"""ScriptScan benchmark / accuracy report.

The benchmark IS the proof: it compares, per field, how often ScriptScan and the
HealthWATCH "DE Assistant" match the human-entered ground truth — on the same set of
de-identified faxes.

Reads benchmark_data.json (written by the benchmark harness/UI, or by hand). Structure:

  {
    "fax_001.png": {
      "ground_truth": { "drug_name": "Ozempic", "drug_strength": "1 mg", ... },
      "de_assistant": { "drug_name": null,       "drug_strength": null,   ... },
      "scriptscan":   { "drug_name": "Ozempic",  "drug_strength": "1 mg", ... }
    },
    ...
  }

Scoring lives in scoring.py (shared with the harness, endpoints, and tests). Runs
entirely locally. No network. No PHI required — clinical field values only.

Usage:
    python evaluate.py                     # report on benchmark_data.json
    python evaluate.py --file other.json   # report on a different file
    python evaluate.py --json              # machine-readable JSON output
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import scoring
from scoring import ALL_FIELDS, HARD_FIELDS


def fmt_pct(v) -> str:
    return f"{v:5.1f}%" if v is not None else "   n/a"


def print_report(summary: dict, source: str) -> None:
    ss = summary["overall"]["scriptscan"]
    de = summary["overall"]["de_assistant"]
    hss = summary["hard"]["scriptscan"]
    hde = summary["hard"]["de_assistant"]

    print()
    print("=" * 68)
    print(f"  ScriptScan benchmark report  ·  {source}")
    print("=" * 68)

    if ss["scored"] == 0:
        print(
            "\n  No scorable cells found. Record some ground-truth values first\n"
            "  (via the benchmark harness/UI or by editing the JSON).\n"
        )
        return

    print(f"\n  {'Field':<20}{'ScriptScan':>14}{'DE Assistant':>16}{'Delta':>10}")
    print("  " + "-" * 60)
    for f in ALL_FIELDS:
        pf = summary["per_field"][f]
        if pf["scriptscan"]["scored"] == 0:
            continue
        s_acc = pf["scriptscan"]["accuracy"]
        d_acc = pf["de_assistant"]["accuracy"]
        delta = (s_acc - d_acc) if (s_acc is not None and d_acc is not None) else None
        delta_s = f"{delta:+5.1f}" if delta is not None else "  n/a"
        marker = " *" if f in HARD_FIELDS else "  "
        print(f"  {f:<18}{marker}{fmt_pct(s_acc):>12}{fmt_pct(d_acc):>16}{delta_s:>10}")

    print("  " + "-" * 60)
    print(
        f"  {'OVERALL':<20}{fmt_pct(ss['accuracy']):>14}{fmt_pct(de['accuracy']):>16}"
    )
    print(
        f"  {'HARD FIELDS (*)':<20}{fmt_pct(hss['accuracy']):>14}{fmt_pct(hde['accuracy']):>16}"
    )

    if hss["accuracy"] is not None and hde["accuracy"] is not None:
        print("\n  " + "=" * 60)
        print(
            f"  HEADLINE: On the hard fields (drug, strength, SIG, refills,\n"
            f"  days supply), ScriptScan is correct {hss['accuracy']:.0f}% "
            f"vs the DE Assistant's {hde['accuracy']:.0f}%."
        )
        print("  " + "=" * 60)

    dis = summary["disagreements"]
    if dis:
        print(f"\n  Disagreements ({len(dis)} cells where a system != ground truth):\n")
        for d in dis:
            print(
                f"    [{d['system']:<12}] {d['filename']} · {d['field']}: "
                f"got {d['predicted']!r} (truth {d['truth']!r}) — {d['outcome']}"
            )
    print()


def main() -> int:
    ap = argparse.ArgumentParser(description="ScriptScan benchmark report")
    ap.add_argument("--file", default="benchmark_data.json", help="benchmark JSON path")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"No benchmark file at {path}. Record some rows first.", file=sys.stderr)
        return 1

    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        print(f"Could not parse {path}: {exc}", file=sys.stderr)
        return 1

    summary = scoring.score_dataset(data)

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print_report(summary, str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
