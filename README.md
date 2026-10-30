# ScriptScan

**AI prescription data-entry assistant for pharmacies — v1 (MVP).**

ScriptScan reads a printed/faxed prescription image and produces the structured fields a
pharmacy assistant would otherwise re-type into HealthWATCH, with an honest **per-field
confidence**. Low-confidence fields are flagged **REVIEW** rather than silently guessed.
A benchmark mode proves, on your own de-identified faxes, that ScriptScan beats the
HealthWATCH "DE Assistant" on the hard, safety-critical fields.

> **The product replaces the TYPING, never the CHECKING.** The pharmacist still verifies
> every field. ScriptScan removes the redundant manual entry in the middle.

---

## ⚠ Privacy & safety (read first)

Prescriptions contain **Protected Health Information (PHI)**. This tool is built around
that fact:

- **Runs entirely locally** on your machine.
- **The only external network call** is to **Anthropic** (via the Claude Code CLI by
  default, or the API), solely to read the image into fields. No telemetry, no
  analytics, no other uploads.
- **No persistent storage of images or PHI by default.** Uploaded images are processed
  in memory and never written to disk.
- **De-identified input only.** Remove patient name, DOB, health card number, address,
  and phone before uploading. A persistent banner reminds you, and the model flags
  (`phi_detected`) if patient info still appears.
- **API key from environment only** (`ANTHROPIC_API_KEY`). Never hardcoded, printed, or
  persisted.
- **No silent miss.** Every field has a 0.0–1.0 confidence. Anything below the threshold
  (default **0.90**, high because this is safety-critical) is visually flagged REVIEW.

The only file ScriptScan writes is `benchmark_data.json`, and only when you explicitly
click "Record for benchmark." It holds the de-identified clinical field values you
record — never images. It is git-ignored.

---

## Setup

Requires **Python 3.11+**.

```bash
cd scriptscan
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Extraction backend

ScriptScan can read images two ways, selected by `SCRIPTSCAN_BACKEND`:

- **`cli` (default)** — drives your local **Claude Code CLI** (`claude -p`). **No API key
  needed**; it uses your existing Claude Code authentication. Requires the `claude` CLI on
  your PATH.
- **`api`** — calls the Anthropic Python SDK directly. Requires
  `export ANTHROPIC_API_KEY="sk-ant-..."` (environment only, never committed).

```bash
# Default — nothing to set; uses Claude Code:
#   (optionally) export SCRIPTSCAN_BACKEND=cli

# Override the model (defaults to claude-sonnet-4-6):
# export SCRIPTSCAN_MODEL="claude-sonnet-4-6"

# To use the API backend instead:
# export SCRIPTSCAN_BACKEND=api
# export ANTHROPIC_API_KEY="sk-ant-..."
```

> The CLI backend writes the uploaded image to a private (0700) temp directory only for
> the duration of the call — Claude Code's Read tool needs a file path — and deletes it
> immediately afterward. No PHI is persisted.

## Run

```bash
uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>.

1. Drag-drop (or pick) a **de-identified** PNG/JPG prescription image.
2. Click **Extract fields**.
3. Review the form: green ✓ fields met the confidence threshold; amber **REVIEW** fields
   did not — verify those against the fax.
4. Read the **Warnings** panel (look-alike/sound-alike drug risks are shown in red).
5. **Copy verified fields** for manual / copy-paste entry into HealthWATCH.
6. **Clear** wipes the image and results from memory.

---

## Local PHI masking (runs before the model call)

Patient identity never leaves the machine. Before any image is sent to Claude, a **local**
step (`masker.py`, Tesseract OCR on-device — no network) detects and blacks out the
patient-identifying region: **name, DOB, health-card/PHN, address, phone**. Only the
de-identified clinical image (drug, SIG, quantities, prescriber) is sent for extraction.

- On by default and **fail-closed**: if masking can't run (no Tesseract), extraction
  errors out rather than risk leaking PHI. Disable only for already-redacted input with
  `SCRIPTSCAN_MASK=off`.
- Needs the Tesseract binary: `brew install tesseract` (macOS).
- The UI shows a "🛡 Local PHI masking" banner and a **"Show the masked image sent to
  Claude"** preview so you can confirm the redaction.
- Verified on the `DummyRx/` real-format set: patient name/DOB/address removed, clinical +
  prescriber content preserved (see `FRAMEWORK_RESULTS.md`).

> Caveat: masking is heuristic OCR — it can miss an unlabeled token on a messy fax. The
> model's `phi_detected` flag and the masked-image preview are backstops; a pharmacist
> still reviews.

## Batch benchmark (whole folder at once)

Run the full pipeline (mask → extract → score) across a folder against a ground-truth JSON,
concurrently:

```bash
python batch_benchmark.py --images-dir DummyRx --truth DummyRx/ground_truth.json --workers 5
```

Prints per-image and aggregate accuracy, per-field breakdown, every miss, and the headline
hard-field number; writes results to `benchmark_data.json`. See `FRAMEWORK_RESULTS.md` for
a recorded run (95% overall, 90% hard, 0 wrong fields on the real-format dummies).

## Tests

Automated unit tests cover parsing, schema validation, error handling, confidence
flagging, and benchmark scoring (no network / no live model calls — the CLI backend is
mocked):

```bash
pip install -r requirements-dev.txt
python -m pytest        # 72 tests
```

What's covered:
- `tests/test_parsing.py` — defensive parsing never crashes; always returns all 13 fields
  (clean JSON, markdown fences, surrounding prose, nested/quoted braces, truncated, garbage).
- `tests/test_validation.py` — confidence clamped to [0,1], type coercion, legibility/warnings/PHI coercion.
- `tests/test_flagging.py` — `flag_fields` REVIEW logic and threshold boundaries.
- `tests/test_errors.py` — every backend failure raises a clean `ExtractionError`; the CLI
  temp dir (and image) is always deleted.
- `tests/test_scoring.py` — normalization (`1mg`==`1 MG`, `2026-Jun-18`==`2026-06-18`),
  numeric-aware fields (`90 Tablet(s)`==`90`), correct/near/wrong/missing, headline math.
- `tests/test_masker.py` — local PHI-masking detection rules (patient line / DOB /
  health-card masked; clinic header preserved; `"for 4 weeks"` not mistaken for a name).
- `tests/test_api.py` — endpoint validation and the mocked `/extract` + `/benchmark/score` flows.

## Benchmark harness — load a fax, score it

The fastest proof loop. Two ways to use it:

**Web UI** — open <http://127.0.0.1:8000/benchmark-ui>: drop a fax, type the correct
values into the *Ground truth* column, click **Run extraction & score** — the ScriptScan
column fills and you get per-field outcomes (correct/near/wrong/missing), overall + hard
accuracy, and a misses list. Save rows for the head-to-head report.

**CLI harness** — `benchmark_harness.py` runs extraction on an image, scores it against a
ground-truth JSON, and lists misses:

```bash
# Ground truth is a JSON of field -> correct value (see field names below):
python benchmark_harness.py run --image faxes/rx1.png --truth truth/rx1.json

# Record what HealthWATCH's DE Assistant produced, for the head-to-head:
python benchmark_harness.py set-de --image faxes/rx1.png --truth de/rx1.json

# Re-score a stored row without re-extracting:
python benchmark_harness.py score --image faxes/rx1.png

# Full head-to-head report (ScriptScan vs DE Assistant):
python benchmark_harness.py report
```

By default the harness extracts via direct import (no server needed). Pass
`--server http://127.0.0.1:8000` to use a running instance instead.

## Benchmark mode — the proof

The benchmark is the whole point: it measures field-level accuracy on **real,
de-identified** faxes against the incumbent.

For each test fax, record three sets of values:

1. **Ground truth** — the correct values, entered once by you.
2. **DE Assistant** — what HealthWATCH's built-in auto-fill produced (entered from your
   screen).
3. **ScriptScan** — what this tool produced.

You can record all three from the UI's **"Record for benchmark"** panel after extraction
(edit the form to match each source, enter the same filename key, and click the matching
Save button), or edit `benchmark_data.json` directly:

```json
{
  "fax_001.png": {
    "ground_truth": { "drug_name": "Ozempic", "drug_strength": "1 mg", "no_refills": "3" },
    "de_assistant": { "drug_name": null,       "drug_strength": null,   "no_refills": null },
    "scriptscan":   { "drug_name": "Ozempic",  "drug_strength": "1 mg", "no_refills": "3" }
  }
}
```

Then run the report:

```bash
python evaluate.py
```

It prints per-field and overall accuracy for ScriptScan vs the DE Assistant, the
head-to-head delta, the headline number on the hard fields, and every disagreement.
Comparison is tolerant (case-insensitive, trimmed, `1mg` == `1 MG`) and surfaces
near-misses. `python evaluate.py --json` emits machine-readable output.

**Headline output:**

> On the hard fields (drug, strength, SIG, refills, days supply), ScriptScan is correct
> X% vs the DE Assistant's Y%.

### Testing advice

- Test on your **messiest real faxes**, not clean ones — fax noise is the whole challenge.
- Keep every fax **de-identified**, every time.
- Watch the **look-alike/sound-alike** warnings — drug-name confusion is the highest-stakes
  failure. Catching it is a genuine safety feature.

---

## Project structure

```
main.py            FastAPI app + routes (serves the page, /extract, /benchmark)
extractor.py       Anthropic vision call, prompt, schema, defensive parsing/validation
evaluate.py        Benchmark / accuracy report (CLI)
static/index.html  Single-page UI
static/app.js      Frontend logic (vanilla JS, no build step)
static/style.css   Styling
requirements.txt   Dependencies
benchmark_data.json  (created on demand; git-ignored; de-identified values only)
```

## Extracted fields (HealthWATCH data-entry screen)

`prescriber_name`, `prescriber_license`, `drug_name`, `drug_strength`, `dosage_form`,
`sig_raw`, `sig_expanded`, `prescribed_qty`, `no_refills`, `tx_qty`, `days_supply`,
`dosage_interval`, `date_written`.

Each carries a `value` and a `confidence` (0.0–1.0). The model also returns
`overall_legibility` (high/medium/low), a `warnings` array, and `phi_detected`.

## Scope (v1)

**In:** reading printed/faxed prescriptions, field extraction with confidence, REVIEW
flagging, head-to-head benchmark.
**Out:** handwritten prescriptions (stay manual — the AI never guesses at illegible
handwriting), automatic write-back into HealthWATCH, billing/adjudication.
