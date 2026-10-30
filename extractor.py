"""ScriptScan extractor — vision extraction, prompt, and defensive parsing.

Two interchangeable extraction backends (select with SCRIPTSCAN_BACKEND):
  - "cli"  (default): drives the local **Claude Code CLI** (`claude -p`). No API key
           needed — it uses your existing Claude Code auth. The image is read by Claude
           Code's own Read tool.
  - "api": the Anthropic Python SDK directly (needs ANTHROPIC_API_KEY).

PRIVACY / SAFETY NOTES (read before editing):
- The ONLY external network call this module makes is to Anthropic (via the Claude Code
  CLI or the SDK), for the sole purpose of reading a prescription image into structured
  fields. No telemetry, no analytics, no other uploads.
- The CLI backend writes the image to a 0700 temp directory ONLY for the duration of the
  call (Claude Code's Read tool needs a file path) and deletes it in a finally block.
  The API backend never touches disk. Neither backend persists PHI.
- No secret is hardcoded, printed, logged, or persisted. The API key (api backend) comes
  from the environment only.
- Confidence scores are genuine per-field certainties. The prompt instructs the model
  to be conservative: unclear handwriting / ambiguous abbreviations / cut-off text must
  produce LOW confidence rather than a confident guess. "No silent miss" is the product.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

# Backend: "cli" (Claude Code CLI, default) or "api" (Anthropic SDK).
BACKEND = os.environ.get("SCRIPTSCAN_BACKEND", "cli").strip().lower()

# Model is configurable via env var; defaults to the value mandated by the PRD.
DEFAULT_MODEL = "claude-sonnet-4-6"
MODEL = os.environ.get("SCRIPTSCAN_MODEL", DEFAULT_MODEL)

# Timeout (seconds) for a single CLI extraction call.
CLI_TIMEOUT = int(os.environ.get("SCRIPTSCAN_CLI_TIMEOUT", "180"))

# Confidence threshold below which a field is flagged for human review. High by default
# because this is safety-critical: better to flag than to present an uncertain value as
# certain ("no silent miss").
DEFAULT_THRESHOLD = float(os.environ.get("SCRIPTSCAN_THRESHOLD", "0.90"))

# Local PHI masking. ON by default and FAIL-CLOSED: the patient-identifying region is
# blacked out locally before the image is sent to Claude. If masking is enabled but OCR
# is unavailable, extraction errors out rather than risk leaking PHI. Set
# SCRIPTSCAN_MASK=off to bypass (NOT recommended — only for fully de-identified inputs).
MASK_ENABLED = os.environ.get("SCRIPTSCAN_MASK", "on").strip().lower() != "off"

# The exact HealthWATCH data-entry fields, in display order. Used to validate and to
# guarantee a complete, well-formed response even when the model omits something.
FIELD_KEYS: tuple[str, ...] = (
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
)

SYSTEM_PROMPT = """You are a meticulous pharmacy data-entry assistant. You are reading a \
single prescription (typically a printed or faxed image) so its details can be entered \
into the HealthWATCH pharmacy management system's data-entry screen.

Your job is to read the prescription and return the structured field values with an \
HONEST per-field confidence. You are a safety-critical tool: a confidently wrong value \
is the single worst outcome. It is far better to flag a field for human review than to \
guess.

Return ONLY a single JSON object. No prose. No markdown code fences. Exactly this schema:

{
  "fields": {
    "prescriber_name":    { "value": "string|null", "confidence": 0.0 },
    "prescriber_license": { "value": "string|null", "confidence": 0.0 },
    "drug_name":          { "value": "string|null", "confidence": 0.0 },
    "drug_strength":      { "value": "string|null", "confidence": 0.0 },
    "dosage_form":        { "value": "string|null", "confidence": 0.0 },
    "sig_raw":            { "value": "string|null", "confidence": 0.0 },
    "sig_expanded":       { "value": "string|null", "confidence": 0.0 },
    "prescribed_qty":     { "value": "string|null", "confidence": 0.0 },
    "no_refills":         { "value": "string|null", "confidence": 0.0 },
    "tx_qty":             { "value": "string|null", "confidence": 0.0 },
    "days_supply":        { "value": "string|null", "confidence": 0.0 },
    "dosage_interval":    { "value": "string|null", "confidence": 0.0 },
    "date_written":       { "value": "string|null", "confidence": 0.0 }
  },
  "overall_legibility": "high|medium|low",
  "warnings": ["array of strings"],
  "phi_detected": true
}

FIELD MEANINGS AND RULES:
- prescriber_name: the prescribing physician/practitioner's name.
- prescriber_license: prescriber license/registration number (e.g. CPSO #), if shown.
- drug_name: the medication name exactly as written. Look-alike/sound-alike (LASA) drug \
names are the highest-stakes error — if the name is ambiguous, lower confidence and add a \
warning naming the candidates.
- drug_strength: e.g. "10 mg", "1 mg/0.5 mL".
- dosage_form: e.g. "tablet", "injection", "capsule", "solution".
- sig_raw: the directions exactly as written/abbreviated (e.g. "INJ 1 MG SC ONCE WEEKLY").
- sig_expanded: the same directions in plain English (e.g. "Inject 1 milligram \
subcutaneously once weekly").
- prescribed_qty: quantity prescribed.
- no_refills: number of refills authorized.
- tx_qty: treatment/total quantity to dispense if specified.
- days_supply: days the prescription covers.
- dosage_interval: dosing frequency/interval (e.g. "once weekly", "BID").
- date_written: date the prescription was written.

CONFIDENCE:
- confidence is a genuine 0.0-1.0 certainty for EACH field independently.
- Be conservative. Unclear handwriting, ambiguous abbreviations, smudges, or cut-off \
text -> LOW confidence (well below 0.9). Crisp printed text you can read unambiguously \
-> high confidence.
- If a field is not present on the prescription, set value to null and confidence to 0.0.
- A field you DERIVE rather than read (e.g. days_supply computed from quantity and sig) \
MUST get a lowered confidence to show it was inferred, AND a warning noting the inference.

WARNINGS (array of short strings) must call out:
- Look-alike/sound-alike (LASA) drug-name risk, with the candidate names.
- Ambiguous or unusual dosing.
- Any field that was inferred/derived rather than read directly.
- Anything cut off or illegible.

phi_detected: set true if any patient-identifying information (patient name, date of \
birth, health card number, address, phone) is still visible in the image. This tool \
requires de-identified input. When phi_detected is true, you MUST also add a warning \
that NAMES the specific PHI categories you saw (e.g. "Residual PHI: patient name, date \
of birth, health card number visible — re-redact before processing"). Do not quote the \
PHI values themselves; name the categories only.

Return the JSON object and nothing else."""


def _empty_field() -> dict[str, Any]:
    return {"value": None, "confidence": 0.0}


def _coerce_confidence(raw: Any) -> float:
    """Clamp confidence into [0.0, 1.0]; treat unparseable values as 0.0 (= flag it)."""
    try:
        c = float(raw)
    except (TypeError, ValueError):
        return 0.0
    # NaN/inf must be treated as "no confidence" -> 0.0 (flag for review). NaN slips past
    # the < / > comparisons below (both are False for NaN), which would defeat the REVIEW
    # flag and also break JSON serialization (Starlette rejects NaN). Reject it up front.
    if not math.isfinite(c):
        return 0.0
    if c < 0.0:
        return 0.0
    if c > 1.0:
        return 1.0
    return c


def _strip_fences(text: str) -> str:
    """Remove stray markdown code fences the model may add despite instructions."""
    text = text.strip()
    # ```json ... ``` or ``` ... ```
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        return fence.group(1).strip()
    return text


def _extract_json_object(text: str) -> str:
    """Best-effort: return the substring from the first '{' to its matching '}'.

    Guards against leading/trailing prose if the model ignores instructions.
    """
    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text[start:]


def parse_model_output(raw_text: str) -> dict[str, Any]:
    """Defensively parse the model's text into the validated ScriptScan schema.

    Never raises on malformed model output. On unrecoverable failure, returns a result
    with a clear warning and everything flagged for review (all confidences 0.0).
    """
    cleaned = _extract_json_object(_strip_fences(raw_text))
    try:
        data = json.loads(cleaned)
        if not isinstance(data, dict):
            raise ValueError("top-level JSON is not an object")
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "fields": {k: _empty_field() for k in FIELD_KEYS},
            "overall_legibility": "low",
            "warnings": [
                "Could not parse the model response as JSON — every field is flagged "
                f"for manual review. ({exc})"
            ],
            "phi_detected": False,
            "_parse_error": True,
        }

    return _validate_schema(data)


def _validate_schema(data: dict[str, Any]) -> dict[str, Any]:
    """Force the parsed object into the exact schema, filling/clamping defensively."""
    raw_fields = data.get("fields")
    if not isinstance(raw_fields, dict):
        raw_fields = {}

    fields: dict[str, Any] = {}
    for key in FIELD_KEYS:
        entry = raw_fields.get(key)
        if not isinstance(entry, dict):
            fields[key] = _empty_field()
            continue
        value = entry.get("value")
        # Normalize empty strings / "null" text to a real null.
        if isinstance(value, str):
            value = value.strip()
            if value == "" or value.lower() == "null":
                value = None
        elif value is not None and not isinstance(value, (int, float)):
            value = str(value)
        fields[key] = {
            "value": value if value is None else str(value),
            "confidence": _coerce_confidence(entry.get("confidence")),
        }

    legibility = data.get("overall_legibility")
    if legibility not in ("high", "medium", "low"):
        legibility = "low"

    warnings = data.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    warnings = [str(w) for w in warnings if str(w).strip()]

    phi = bool(data.get("phi_detected", False))

    return {
        "fields": fields,
        "overall_legibility": legibility,
        "warnings": warnings,
        "phi_detected": phi,
    }


def flag_fields(
    result: dict[str, Any], threshold: float = DEFAULT_THRESHOLD
) -> dict[str, Any]:
    """Annotate each field with review=True when confidence < threshold.

    "No silent miss": any field the model is not confident about is flagged for human
    review rather than presented as certain. Returns the same result dict, mutated, with
    a top-level "flagged_count" added. Pure/deterministic — unit-tested.
    """
    flagged = 0
    fields = result.get("fields", {})
    for entry in fields.values():
        conf = entry.get("confidence", 0.0) or 0.0
        review = conf < threshold
        entry["review"] = review
        if review:
            flagged += 1
    result["flagged_count"] = flagged
    result["threshold"] = threshold
    return result


class ExtractionError(Exception):
    """Raised for a clean, user-facing error (e.g. missing API key, API failure)."""


USER_PROMPT = (
    "Read this prescription and return the JSON object described in your "
    "instructions. JSON only — no prose, no markdown fences."
)


def extract_fields(image_bytes: bytes, media_type: str) -> dict[str, Any]:
    """Read a de-identified prescription image and return the validated ScriptScan
    result plus the raw model text. Dispatches to the configured backend.

    Raises ExtractionError with a clean, user-facing message on any failure.
    """
    if media_type not in ("image/png", "image/jpeg"):
        raise ExtractionError(f"Unsupported image type: {media_type}. Use PNG or JPG.")

    # Mask the patient-identifying region LOCALLY before the image reaches Claude.
    # Fail-closed: if masking is on but OCR can't run, error out rather than leak PHI.
    if MASK_ENABLED:
        import base64

        import masker

        try:
            image_bytes, mask_info = masker.mask_phi(image_bytes, media_type)
        except masker.MaskingUnavailable as exc:
            raise ExtractionError(
                f"PHI masking is required but unavailable: {exc}. Install Tesseract + "
                "pytesseract, or set SCRIPTSCAN_MASK=off only for already-redacted input."
            ) from exc
        except ValueError as exc:
            raise ExtractionError(str(exc)) from exc
        # Masked image is PHI-free; return it so the UI can show what was sent to Claude.
        mask_info["preview"] = f"data:{media_type};base64," + base64.standard_b64encode(
            image_bytes
        ).decode("ascii")
    else:
        mask_info = {"masked": False, "regions_masked": 0}

    if BACKEND == "api":
        outcome = _extract_via_api(image_bytes, media_type)
    elif BACKEND == "cli":
        outcome = _extract_via_cli(image_bytes, media_type)
    else:
        raise ExtractionError(
            f"Unknown SCRIPTSCAN_BACKEND={BACKEND!r}. Use 'cli' or 'api'."
        )

    outcome["mask_info"] = mask_info
    return outcome


def _extract_via_cli(image_bytes: bytes, media_type: str) -> dict[str, Any]:
    """Drive the local Claude Code CLI (`claude -p`) to read the image and return JSON.

    Uses the user's existing Claude Code auth — no API key required. The image is
    written to a private temp dir for the duration of the call and deleted afterward.
    """
    claude_bin = shutil.which("claude")
    if not claude_bin:
        raise ExtractionError(
            "Claude Code CLI ('claude') not found on PATH. Install it, or set "
            "SCRIPTSCAN_BACKEND=api to use the Anthropic SDK instead."
        )

    ext = ".png" if media_type == "image/png" else ".jpg"
    # 0700 temp dir; both dir and file removed in finally — nothing persists.
    tmp_dir = tempfile.mkdtemp(prefix="scriptscan_")
    img_path = os.path.join(tmp_dir, f"rx{ext}")
    try:
        with open(img_path, "wb") as fh:
            fh.write(image_bytes)

        cmd = [
            claude_bin,
            "-p",
            f"Read the image file at {img_path} and extract the prescription. {USER_PROMPT}",
            "--append-system-prompt",
            SYSTEM_PROMPT,
            "--allowedTools",
            "Read",
            "--disallowedTools",
            "Bash Edit Write WebSearch WebFetch",
            "--add-dir",
            tmp_dir,
            "--output-format",
            "json",
        ]
        if MODEL:
            cmd += ["--model", MODEL]

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=CLI_TIMEOUT,
                cwd=tmp_dir,
            )
        except subprocess.TimeoutExpired as exc:
            raise ExtractionError(
                f"Claude Code CLI timed out after {CLI_TIMEOUT}s."
            ) from exc

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = detail[-1] if detail else f"exit code {proc.returncode}"
            raise ExtractionError(f"Claude Code CLI failed: {tail}")

        # The CLI's JSON envelope carries the model's answer in the "result" field.
        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise ExtractionError(
                "Could not parse Claude Code CLI output envelope."
            ) from exc

        if envelope.get("is_error"):
            raise ExtractionError(
                f"Claude Code CLI reported an error: {envelope.get('subtype', 'unknown')}"
            )

        raw_text = envelope.get("result", "")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ExtractionError("Claude Code CLI returned an empty result.")

        model_used = MODEL
        usage = envelope.get("modelUsage")
        if isinstance(usage, dict) and usage:
            model_used = next(iter(usage.keys()), MODEL)

        parsed = parse_model_output(raw_text)
        return {
            "raw": raw_text,
            "result": parsed,
            "model": model_used,
            "backend": "cli",
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _extract_via_api(image_bytes: bytes, media_type: str) -> dict[str, Any]:
    """Read the image via the Anthropic Python SDK directly. Requires ANTHROPIC_API_KEY."""
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ExtractionError(
            "ANTHROPIC_API_KEY is not set. Export it, or use SCRIPTSCAN_BACKEND=cli."
        )

    import base64

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")

    client = anthropic.Anthropic(api_key=api_key)

    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": USER_PROMPT},
                    ],
                }
            ],
        )
    except anthropic.APIError as exc:
        # Surface a clean message; do not leak the key or internal details.
        raise ExtractionError(f"Anthropic API error: {exc.__class__.__name__}") from exc

    raw_text = "".join(
        block.text
        for block in message.content
        if getattr(block, "type", None) == "text"
    )
    parsed = parse_model_output(raw_text)
    return {"raw": raw_text, "result": parsed, "model": MODEL, "backend": "api"}
