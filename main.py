"""ScriptScan — local FastAPI app for AI-assisted pharmacy prescription data entry.

PRIVACY POSTURE (the backbone of this tool):
- Runs entirely locally on the user's machine.
- Handles Protected Health Information (PHI). The ONLY permitted external network call is
  to the Anthropic API for image extraction (see extractor.py). No telemetry, no
  analytics, no other uploads.
- No persistent storage of images or PHI by default. Uploaded images are processed in
  memory and never written to disk. The benchmark store (see /benchmark) holds only the
  de-identified field values the user explicitly chooses to record.
- The product replaces the manual TYPING step. The pharmacist still verifies everything.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

import extractor
import scoring

APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

# Benchmark data is the ONLY thing this app persists, and only on explicit user action.
# It contains de-identified clinical field values keyed by filename — never images.
BENCHMARK_FILE = APP_DIR / "benchmark_data.json"

MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB guard

app = FastAPI(title="ScriptScan", version="1.0")


@app.get("/")
def index() -> Any:
    from fastapi.responses import FileResponse

    return FileResponse(STATIC_DIR / "index.html")


@app.get("/config")
def config() -> dict[str, Any]:
    """Expose non-secret runtime config to the frontend. Never exposes the API key."""
    backend = extractor.BACKEND
    if backend == "cli":
        backend_ready = shutil.which("claude") is not None
        backend_hint = (
            "Using Claude Code CLI (no API key needed)."
            if backend_ready
            else "Claude Code CLI not found on PATH."
        )
    else:
        backend_ready = bool(os.environ.get("ANTHROPIC_API_KEY"))
        backend_hint = (
            "Using Anthropic API." if backend_ready else "ANTHROPIC_API_KEY not set."
        )
    return {
        "model": extractor.MODEL,
        "backend": backend,
        "backend_ready": backend_ready,
        "backend_hint": backend_hint,
        "default_threshold": 0.90,
        "field_keys": list(extractor.FIELD_KEYS),
    }


@app.post("/extract")
async def extract(image: UploadFile = File(...)) -> Any:
    media_type = image.content_type or ""
    if media_type not in ("image/png", "image/jpeg"):
        raise HTTPException(
            status_code=400,
            detail="Please upload a PNG or JPG image.",
        )

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty image upload.")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=400, detail="Image too large (max 20 MB).")

    try:
        # extract_fields blocks (subprocess / network); run it off the event loop so
        # concurrent users aren't serialized behind one another.
        outcome = await run_in_threadpool(
            extractor.extract_fields, image_bytes, media_type
        )
    except extractor.ExtractionError as exc:
        # Clean, user-facing error — never crash the UI.
        return JSONResponse(status_code=502, content={"error": str(exc)})

    # Annotate fields with review flags server-side (the frontend also flags using its
    # own threshold control; this keeps the API self-describing for the harness/tests).
    extractor.flag_fields(outcome.get("result", {}))

    # image_bytes goes out of scope here; nothing is persisted.
    return outcome


# ----------------------------------------------------------------------------------
# Benchmark store: ground truth vs DE Assistant vs ScriptScan, keyed by filename.
# Opt-in only — written when the user records a row. Holds de-identified values only.
# ----------------------------------------------------------------------------------


def _load_benchmark() -> dict[str, Any]:
    if not BENCHMARK_FILE.exists():
        return {}
    try:
        return json.loads(BENCHMARK_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_benchmark(data: dict[str, Any]) -> None:
    BENCHMARK_FILE.write_text(json.dumps(data, indent=2))


@app.get("/benchmark")
def benchmark_all() -> dict[str, Any]:
    return _load_benchmark()


@app.post("/benchmark/record")
async def benchmark_record(payload: dict[str, Any]) -> dict[str, Any]:
    """Record one benchmark row.

    payload = {
      "filename": "fax_001.png",
      "source": "ground_truth" | "de_assistant" | "scriptscan",
      "fields": { field_key: value, ... }
    }
    """
    filename = str(payload.get("filename", "")).strip()
    source = str(payload.get("source", "")).strip()
    fields = payload.get("fields")

    if not filename:
        raise HTTPException(status_code=400, detail="filename is required.")
    if source not in ("ground_truth", "de_assistant", "scriptscan"):
        raise HTTPException(
            status_code=400,
            detail="source must be ground_truth, de_assistant, or scriptscan.",
        )
    if not isinstance(fields, dict):
        raise HTTPException(status_code=400, detail="fields must be an object.")

    # Keep only known field keys, coerced to strings/null.
    clean_fields = {}
    for key in extractor.FIELD_KEYS:
        if key in fields:
            v = fields[key]
            clean_fields[key] = None if v is None else str(v)

    data = _load_benchmark()
    row = data.setdefault(filename, {})
    row[source] = clean_fields
    _save_benchmark(data)
    return {"ok": True, "filename": filename, "source": source}


@app.post("/benchmark/score")
async def benchmark_score(payload: dict[str, Any]) -> dict[str, Any]:
    """Score a predicted field-set against a ground-truth field-set.

    payload = { "ground_truth": {field: value}, "predicted": {field: value} }
    Returns per-field outcomes, overall + hard-field accuracy, and the misses list.
    """
    truth = payload.get("ground_truth")
    predicted = payload.get("predicted")
    if not isinstance(truth, dict) or not isinstance(predicted, dict):
        raise HTTPException(
            status_code=400,
            detail="ground_truth and predicted must both be objects.",
        )
    return scoring.score_pair(predicted, truth)


@app.get("/benchmark-ui")
def benchmark_ui() -> Any:
    from fastapi.responses import FileResponse

    return FileResponse(STATIC_DIR / "benchmark.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
