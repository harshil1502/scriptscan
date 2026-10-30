#!/usr/bin/env python3
"""ScriptScan benchmark harness.

The integrated proof loop: load a real (de-identified) fax image, run the extraction,
compare against ground truth you enter once, and get a per-field accuracy score plus a
list of every miss. Results accumulate in benchmark_data.json so `evaluate.py` can give
the head-to-head report against the DE Assistant.

Runs entirely locally. The extraction uses whatever backend extractor.py is configured
for (Claude Code CLI by default — no API key needed). No PHI is persisted: only the
clinical field values you record are written to benchmark_data.json (never images).

Ground-truth input format (JSON), keyed by the ScriptScan field names:

    {
      "drug_name": "APO-SEMAGLUTIDE 1MG/DOSE",
      "drug_strength": "1 mg/dose",
      "sig_raw": "INJ 1 MG SC ONCE WEEKLY",
      "prescribed_qty": "1",
      "no_refills": "10",
      "days_supply": "28"
    }

Field names: prescriber_name, prescriber_license, drug_name, drug_strength, dosage_form,
sig_raw, sig_expanded, prescribed_qty, no_refills, tx_qty, days_supply, dosage_interval,
date_written.

Usage:
    # Run extraction on a fax and score it against a ground-truth JSON:
    python benchmark_harness.py run --image faxes/rx1.png --truth truth/rx1.json

    # Run extraction only and save its values (no truth yet):
    python benchmark_harness.py run --image faxes/rx1.png

    # Set/replace ground truth for an image without re-running extraction:
    python benchmark_harness.py set-truth --image faxes/rx1.png --truth truth/rx1.json

    # Record what the DE Assistant produced (from your HealthWATCH screen):
    python benchmark_harness.py set-de --image faxes/rx1.png --truth de/rx1.json

    # Re-score a stored row (no extraction) and list misses:
    python benchmark_harness.py score --image faxes/rx1.png

    # Full head-to-head report across everything recorded:
    python benchmark_harness.py report
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import scoring

BENCHMARK_FILE = Path(__file__).resolve().parent / "benchmark_data.json"
MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


# ----------------------------------------------------------------------------------
# Store helpers
# ----------------------------------------------------------------------------------


def load_store(path: Path = BENCHMARK_FILE) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}


def save_store(data: dict[str, Any], path: Path = BENCHMARK_FILE) -> None:
    path.write_text(json.dumps(data, indent=2))


def clean_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """Keep only known field keys; coerce to string|null."""
    out = {}
    for k in scoring.ALL_FIELDS:
        if k in fields:
            v = fields[k]
            out[k] = None if v is None else str(v)
    return out


def load_truth_file(p: str) -> dict[str, Any]:
    raw = json.loads(Path(p).read_text())
    if not isinstance(raw, dict):
        raise ValueError("truth file must be a JSON object of field -> value")
    return clean_fields(raw)


# ----------------------------------------------------------------------------------
# Extraction (direct import by default; optional running server via --server)
# ----------------------------------------------------------------------------------


def run_extraction(image_path: Path, server: str | None) -> dict[str, Any]:
    media = MEDIA.get(image_path.suffix.lower())
    if not media:
        raise SystemExit(f"Unsupported image type: {image_path.suffix} (use PNG/JPG)")
    image_bytes = image_path.read_bytes()

    if server:
        import urllib.request

        boundary = "----scriptscanbenchmark"
        body = (
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="image"; filename="{image_path.name}"\r\n'
                f"Content-Type: {media}\r\n\r\n"
            ).encode()
            + image_bytes
            + f"\r\n--{boundary}--\r\n".encode()
        )
        req = urllib.request.Request(
            server.rstrip("/") + "/extract",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.loads(resp.read())
        if "error" in payload:
            raise SystemExit(f"Extraction error: {payload['error']}")
        return payload

    # Direct import — no server needed.
    import extractor

    try:
        return extractor.extract_fields(image_bytes, media)
    except extractor.ExtractionError as exc:
        raise SystemExit(f"Extraction error: {exc}")


def fields_from_result(envelope: dict[str, Any]) -> dict[str, Any]:
    """Pull {field: value} out of an /extract envelope ({raw, result, model, backend})."""
    inner = envelope.get("result", envelope)
    return {k: v.get("value") for k, v in inner.get("fields", {}).items()}


# ----------------------------------------------------------------------------------
# Reporting one image
# ----------------------------------------------------------------------------------


def print_pair_report(filename: str, predicted: dict, truth: dict) -> None:
    res = scoring.score_pair(predicted, truth)
    print(f"\n  Per-field accuracy for {filename}")
    print("  " + "-" * 64)
    print(f"  {'Field':<20}{'Outcome':<10}{'Predicted':<18}{'Truth':<18}")
    print("  " + "-" * 64)
    sym = {"correct": "✓", "near": "~", "wrong": "✗", "missing": "·", "no_truth": " "}
    for f in scoring.ALL_FIELDS:
        pf = res["per_field"][f]
        if pf["outcome"] == "no_truth":
            continue
        p = (pf["predicted"] or "")[:16]
        t = (pf["truth"] or "")[:16]
        mark = " *" if f in scoring.HARD_FIELDS else "  "
        print(f"  {f:<18}{mark}{sym[pf['outcome']]} {pf['outcome']:<8}{p:<18}{t:<18}")
    print("  " + "-" * 64)
    ov, hd = res["overall"], res["hard"]
    ov_s = (
        f"{ov['accuracy']:.0f}% ({ov['correct']}/{ov['scored']})"
        if ov["accuracy"] is not None
        else "n/a"
    )
    hd_s = (
        f"{hd['accuracy']:.0f}% ({hd['correct']}/{hd['scored']})"
        if hd["accuracy"] is not None
        else "n/a"
    )
    print(f"  Overall: {ov_s}    Hard fields (*): {hd_s}")
    if res["misses"]:
        print(f"\n  Misses ({len(res['misses'])}):")
        for m in res["misses"]:
            print(
                f"    {m['field']}: got {m['predicted']!r} "
                f"(truth {m['truth']!r}) — {m['outcome']}"
            )
    else:
        print("\n  No misses against the recorded ground truth. 🎯")
    print()


# ----------------------------------------------------------------------------------
# Commands
# ----------------------------------------------------------------------------------


def cmd_run(args) -> int:
    image_path = Path(args.image)
    if not image_path.exists():
        print(f"No such image: {image_path}", file=sys.stderr)
        return 1

    print(f"Running extraction on {image_path.name} ...")
    result = run_extraction(image_path, args.server)
    backend = result.get("backend", "?")
    model = result.get("model", "?")
    predicted = fields_from_result(result)
    print(f"Extraction done (backend={backend}, model={model}).")

    store = load_store()
    row = store.setdefault(image_path.name, {})
    row["scriptscan"] = clean_fields(predicted)

    truth = None
    if args.truth:
        truth = load_truth_file(args.truth)
        row["ground_truth"] = truth
    elif "ground_truth" in row:
        truth = row["ground_truth"]
    save_store(store)

    if truth:
        print_pair_report(image_path.name, predicted, truth)
    else:
        print(
            "\n  Saved ScriptScan output. No ground truth yet — add it with:\n"
            f"    python benchmark_harness.py set-truth --image {args.image} --truth <truth.json>\n"
        )
    return 0


def cmd_set_truth(args) -> int:
    return _set_source(args, "ground_truth")


def cmd_set_de(args) -> int:
    return _set_source(args, "de_assistant")


def _set_source(args, source: str) -> int:
    image_name = Path(args.image).name
    truth = load_truth_file(args.truth)
    store = load_store()
    store.setdefault(image_name, {})[source] = truth
    save_store(store)
    print(f"Recorded {source} for {image_name} ({len(truth)} fields).")
    if source == "ground_truth" and "scriptscan" in store[image_name]:
        print_pair_report(image_name, store[image_name]["scriptscan"], truth)
    return 0


def cmd_score(args) -> int:
    image_name = Path(args.image).name
    store = load_store()
    row = store.get(image_name)
    if not row:
        print(f"No recorded data for {image_name}.", file=sys.stderr)
        return 1
    truth = row.get("ground_truth")
    pred = row.get("scriptscan")
    if not truth:
        print(f"No ground truth recorded for {image_name}.", file=sys.stderr)
        return 1
    if not pred:
        print(
            f"No ScriptScan extraction recorded for {image_name}. Run it first.",
            file=sys.stderr,
        )
        return 1
    print_pair_report(image_name, pred, truth)
    return 0


def cmd_report(args) -> int:
    import evaluate

    path = Path(args.file)
    if not path.exists():
        print(f"No benchmark file at {path}.", file=sys.stderr)
        return 1
    summary = scoring.score_dataset(load_store(path))
    evaluate.print_report(summary, str(path))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="ScriptScan benchmark harness")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="run extraction on an image and score it")
    p_run.add_argument("--image", required=True)
    p_run.add_argument("--truth", help="ground-truth JSON file")
    p_run.add_argument(
        "--server", help="use a running server URL instead of direct import"
    )
    p_run.set_defaults(func=cmd_run)

    p_t = sub.add_parser("set-truth", help="set ground truth for an image")
    p_t.add_argument("--image", required=True)
    p_t.add_argument("--truth", required=True)
    p_t.set_defaults(func=cmd_set_truth)

    p_d = sub.add_parser("set-de", help="record DE Assistant values for an image")
    p_d.add_argument("--image", required=True)
    p_d.add_argument("--truth", required=True, help="DE Assistant values JSON file")
    p_d.set_defaults(func=cmd_set_de)

    p_s = sub.add_parser("score", help="re-score a stored row and list misses")
    p_s.add_argument("--image", required=True)
    p_s.set_defaults(func=cmd_score)

    p_r = sub.add_parser("report", help="full head-to-head report")
    p_r.add_argument("--file", default=str(BENCHMARK_FILE))
    p_r.set_defaults(func=cmd_report)
    return ap


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
