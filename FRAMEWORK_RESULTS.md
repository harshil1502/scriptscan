# ScriptScan — Framework Benchmark Results (real-format dummies)

Run of the **full pipeline** (local PHI mask → Claude Sonnet 4.6 extraction → tolerant
scoring) over the `DummyRx/` set: 5 fabricated prescriptions in the user's two real
formats (Thamesview-style letterhead fax + Kerubo-style EMR printout), each in **clean**
and **faxed/degraded** versions. Ground truth: `DummyRx/ground_truth.json` (from the
answer key). Reproduce with:

```bash
python batch_benchmark.py --images-dir DummyRx --truth DummyRx/ground_truth.json
```

## Headline

| Metric | Result |
|---|---|
| **Hard fields** (drug, strength, SIG, qty, refills) | **90%** (36/40) |
| **Overall** (all scored fields) | **95%** (80/84) |
| **Fields actually *wrong*** | **0** |
| drug_name / drug_strength / dosage_form | **100%** (10/10 each) |
| prescriber_name / prescriber_license | **100%** |
| PHI masked locally before model call | **10/10** (phi_detected False) |

Every non-exact field is a **near-match** (the model returned a more complete value, e.g.
`100 Tablet(s)` for qty `100`, or a slightly longer SIG string) — **not a single field was
read incorrectly**.

## Per-image

| Image | Overall | Hard |
|---|---|---|
| F1_humira (Trulicity) | 100% | 100% |
| F1_wegovy (Ozempic) | 100% | 100% |
| F2_atorvastatin | 100% | 100% |
| F2_pristiq | 91% | 80% |
| F2_multidrug | 88% | 75% |
| faxed_F1_humira | 100% | 100% |
| faxed_F1_wegovy | 100% | 100% |
| faxed_F2_atorvastatin | 100% | 100% |
| faxed_F2_pristiq | 91% | 80% |
| faxed_F2_multidrug | 88% | 75% |

Fax noise caused **no degradation** — faxed scores match their clean counterparts.

## Edge cases (from the answer key)

- **Multi-drug Rx (`F2_multidrug`) — handled safely.** The single-drug schema returns
  Drug 1 (Metformin), and the model emits a prominent warning naming Drug 2 in full:
  *"MULTI-DRUG PRESCRIPTION … Drug 2 is RAMIPRIL 5 MG CAPSULE … requires a separate
  data-entry record."* It does **not** silently drop the second drug (the exact risk the
  answer key probed). The UI now highlights multi-drug warnings in red. The 75% "hard"
  here only reflects scoring Drug 1's SIG as a near-match; nothing was misread.
- **The "06/26: Dose reduction" note (`F2_pristiq`)** was correctly treated as a note, not
  pulled into a structured field.
- **Decoy filenames** (`F1_wegovy` is actually Ozempic, `F1_humira` is Trulicity) — the
  model read the actual image content, unaffected.

## Local PHI masking (verified on all 10)

OCR-based on-device masking (Tesseract) blacked out the patient block — name, DOB,
health-card/PHN, address, phone — before the image reached Claude. Manual OCR re-check of
the masked outputs found **no patient name / DOB / address surviving**. The clinic header
address is intentionally preserved (it is the prescriber's, not PHI).

## Honest caveats

- These are **synthetic** dummies. A strong score here is encouraging; the real
  de-identified-fax number (run locally) is the one that decides everything.
- Masking is heuristic OCR — it could miss an unlabeled PHI token on a messy real fax.
  The model's `phi_detected` flag and the masked-image preview remain as backstops.
- SIG is long free-text; "near" matches there are expected and acceptable (a pharmacist
  verifies it regardless).
