#!/usr/bin/env python3
"""Batch benchmark — run the full ScriptScan framework over a folder of faxes.

For every image found under --images-dir whose basename has an entry in the
--truth ground-truth JSON, this runs the FULL pipeline (local PHI mask -> extract ->
score) and reports per-image and aggregate accuracy, with per-field breakdown and a
list of every miss. Results are also written to benchmark_data.json.

Extractions run concurrently (--workers) since each is a slow model call.

Usage:
    python batch_benchmark.py --images-dir DummyRx --truth DummyRx/ground_truth.json
    python batch_benchmark.py --images-dir DummyRx --truth DummyRx/ground_truth.json --workers 5
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import extractor
import scoring

MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def find_images(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in MEDIA and p.is_file():
            out.append(p)
    return out


def run_one(path: Path, truth: dict[str, Any]) -> dict[str, Any]:
    media = MEDIA[path.suffix.lower()]
    try:
        env = extractor.extract_fields(path.read_bytes(), media)
    except extractor.ExtractionError as exc:
        return {"name": path.name, "error": str(exc)}
    fields = {k: v.get("value") for k, v in env["result"]["fields"].items()}
    score = scoring.score_pair(fields, truth)
    return {
        "name": path.name,
        "predicted": fields,
        "score": score,
        "mask_info": {
            k: v for k, v in env.get("mask_info", {}).items() if k != "preview"
        },
        "warnings": env["result"].get("warnings", []),
        "phi_detected": env["result"].get("phi_detected"),
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum correct/scored across all images, overall and per hard-field set + per field."""
    agg = {f: {"correct": 0, "scored": 0} for f in scoring.ALL_FIELDS}
    for r in results:
        if "score" not in r:
            continue
        for f, pf in r["score"]["per_field"].items():
            if pf["outcome"] == "no_truth":
                continue
            agg[f]["scored"] += 1
            if pf["outcome"] == "correct":
                agg[f]["correct"] += 1

    def acc(field_set):
        c = sum(agg[f]["correct"] for f in field_set)
        s = sum(agg[f]["scored"] for f in field_set)
        return {"correct": c, "scored": s, "accuracy": (c / s * 100) if s else None}

    return {
        "per_field": agg,
        "overall": acc(scoring.ALL_FIELDS),
        "hard": acc(scoring.HARD_FIELDS),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="ScriptScan batch benchmark")
    ap.add_argument("--images-dir", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--store", default="benchmark_data.json")
    args = ap.parse_args()

    truth_all = json.loads(Path(args.truth).read_text())
    images = [p for p in find_images(Path(args.images_dir)) if p.name in truth_all]
    if not images:
        print("No images with matching ground truth found.", file=sys.stderr)
        return 1

    print(
        f"Running full pipeline (mask -> extract -> score) on {len(images)} images "
        f"with {args.workers} workers...\n"
    )

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, p, truth_all[p.name]): p for p in images}
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            if "error" in r:
                print(f"  [ERROR] {r['name']}: {r['error']}")
            else:
                s = r["score"]
                ov = s["overall"]["accuracy"]
                hd = s["hard"]["accuracy"]
                masked = r["mask_info"].get("regions_masked", 0)
                print(
                    f"  [done] {r['name']:30} overall={_p(ov)} hard={_p(hd)} "
                    f"masked={masked} phi={r['phi_detected']}"
                )

    results.sort(key=lambda r: r["name"])

    # Persist to benchmark store (scriptscan + ground_truth per filename).
    store = {}
    if Path(args.store).exists():
        try:
            store = json.loads(Path(args.store).read_text())
        except json.JSONDecodeError:
            store = {}
    for r in results:
        if "predicted" in r:
            row = store.setdefault(r["name"], {})
            row["scriptscan"] = {
                k: (None if v is None else str(v)) for k, v in r["predicted"].items()
            }
            row["ground_truth"] = truth_all[r["name"]]
    Path(args.store).write_text(json.dumps(store, indent=2))

    print_report(results)
    return 0


def _p(v) -> str:
    return f"{v:.0f}%" if v is not None else "n/a"


def print_report(results: list[dict[str, Any]]) -> None:
    agg = aggregate(results)
    print("\n" + "=" * 70)
    print("  ScriptScan — full framework benchmark on real dummy formats")
    print("=" * 70)

    print(f"\n  {'Image':32}{'Overall':>10}{'Hard':>8}{'Masked':>8}{'PHI':>6}")
    print("  " + "-" * 62)
    for r in results:
        if "error" in r:
            print(f"  {r['name']:32}{'ERROR':>10}")
            continue
        s = r["score"]
        print(
            f"  {r['name']:32}{_p(s['overall']['accuracy']):>10}"
            f"{_p(s['hard']['accuracy']):>8}"
            f"{r['mask_info'].get('regions_masked', 0):>8}{str(r['phi_detected']):>6}"
        )

    print("  " + "-" * 62)
    print(
        f"  {'AGGREGATE':32}{_p(agg['overall']['accuracy']):>10}{_p(agg['hard']['accuracy']):>8}"
    )

    print("\n  Per-field accuracy (across all images):")
    for f in scoring.ALL_FIELDS:
        a = agg["per_field"][f]
        if a["scored"] == 0:
            continue
        mark = " *" if f in scoring.HARD_FIELDS else "  "
        pct = a["correct"] / a["scored"] * 100
        print(f"    {f:20}{mark} {pct:5.0f}%  ({a['correct']}/{a['scored']})")

    print("\n  Misses:")
    any_miss = False
    for r in results:
        if "score" not in r:
            continue
        for m in r["score"]["misses"]:
            any_miss = True
            print(
                f"    {r['name']:30} {m['field']:16} got {str(m['predicted'])[:24]!r:26} "
                f"truth {str(m['truth'])[:24]!r} [{m['outcome']}]"
            )
    if not any_miss:
        print("    none 🎯")

    hd = agg["hard"]
    if hd["accuracy"] is not None:
        print("\n  " + "=" * 62)
        print(
            f"  HEADLINE: across these formats, ScriptScan got the hard fields "
            f"(drug, strength, SIG, qty, refills)\n  correct {hd['accuracy']:.0f}% "
            f"({hd['correct']}/{hd['scored']})."
        )
        print("  " + "=" * 62)


if __name__ == "__main__":
    raise SystemExit(main())
