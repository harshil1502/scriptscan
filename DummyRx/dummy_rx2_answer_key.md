# Format-Matched Dummy Prescriptions — Answer Key

All data is **fabricated**. These dummies near-identically replicate your two real Rx formats so you can test ScriptScan meaningfully. Each comes in **clean** and **faxed** (degraded) versions — same content, score against the same answers below.

> **Format 1** = Thamesview-style letterhead fax (centered header + centered drug box + signature)
> **Format 2** = Kerubo-style EMR printout (Rx header + numbered RENEW Rx + labeled fields)

---

## FORMAT 1 (letterhead fax)

### F1_wegovy
- **Drug:** Ozempic
- **Strength/Form:** 0.5 mg/dose (Pen Injector)
- **SIG:** 0.25 mg 1 time weekly for 4 weeks
- **Qty:** 1 pen
- **Prescriber:** Dr. Helena Boardman (Riverbend Family Health Team)

### F1_humira
- **Drug:** Trulicity
- **Strength/Form:** 1.5 mg/0.5 mL (Pen Injector)
- **SIG:** 1.5 mg subcutaneously once weekly
- **Qty:** 4 pens
- **Prescriber:** Dr. Samuel Okonkwo (Northgate Medical Associates)

---

## FORMAT 2 (EMR printout)

### F2_pristiq
- **Drug:** PRISTIQ
- **Strength/Form:** 50 MG TABLET
- **SIG:** 1 Tablet(s) Once daily X 100 Day(s) starting on 2026-Jun-18
- **Note:** "06/26: Dose reduction" (tests if tool wrongly grabs the note as a field)
- **Refills:** 3 (Three)
- **Qty:** 100 Tablet(s) (One hundred)
- **Route:** Oral
- **DIN:** 02321092
- **Prescriber:** Dr. Angela K. Workman (047465)

### F2_atorvastatin
- **Drug:** ATORVASTATIN
- **Strength/Form:** 40 MG TABLET
- **SIG:** 1 Tablet(s) Once daily at bedtime X 90 Day(s) starting on 2026-Jun-18
- **Refills:** 5 (Five)
- **Qty:** 90 Tablet(s) (Ninety)
- **Route:** Oral
- **DIN:** 02356784
- **Prescriber:** Dr. Angela K. Workman (047465)

### F2_multidrug — EDGE CASE: two drugs on one Rx
- **Drug 1:** METFORMIN 850 MG TABLET — SIG: 1 Tablet(s) Twice daily with meals X 90 Day(s) — Refills: 5 — Qty: 180 Tablet(s) — DIN: 02229227
- **Drug 2:** RAMIPRIL 5 MG CAPSULE — SIG: 1 Capsule(s) Once daily X 90 Day(s) — Refills: 5 — Qty: 90 Capsule(s) — DIN: 02251010
- **Tests:** does the tool capture BOTH items, or silently drop the second?
- **Prescriber:** Dr. Angela K. Workman (047465)

---

## Scoring
For each image: run it through ScriptScan, compare each field to the key, mark ✅/❌. Tally the hard fields (drug, strength, sig, qty, refills) across all scrips → that's your accuracy %.

**Test progression:**
1. **Clean format-matched** → does it read your exact layouts correctly?
2. **Faxed format-matched** → can it read your layouts through fax noise? (closer to reality)
3. **Real de-identified faxes (at home, local)** → the true number that decides everything.

The faxed dummies are the most realistic test you can run safely — but the noise is still synthetic. A strong score here is encouraging; the real-fax number is still the one that counts.
