// ScriptScan benchmark harness UI. Load a fax, enter ground truth, run extraction,
// score per-field, list misses. Vanilla JS. Talks to the local server only.

"use strict";

const $ = (id) => document.getElementById(id);

const FIELD_LABELS = {
  prescriber_name: "Prescriber name",
  prescriber_license: "Prescriber license",
  drug_name: "Drug name",
  drug_strength: "Strength",
  dosage_form: "Dosage form",
  sig_raw: "SIG (as written)",
  sig_expanded: "SIG (plain English)",
  prescribed_qty: "Prescribed qty",
  no_refills: "Refills",
  tx_qty: "Treatment qty",
  days_supply: "Days supply",
  dosage_interval: "Dosage interval",
  date_written: "Date written",
};
const HARD = new Set(["drug_name", "drug_strength", "sig_raw", "no_refills", "days_supply"]);
const FIELDS = Object.keys(FIELD_LABELS);

let selectedFile = null;

function buildTable() {
  const body = $("bm-body");
  body.innerHTML = "";
  FIELDS.forEach((key) => {
    const tr = document.createElement("tr");
    tr.dataset.key = key;
    const star = HARD.has(key) ? " *" : "";
    tr.innerHTML = `
      <td class="fld">${FIELD_LABELS[key]}${star}</td>
      <td><input type="text" data-col="truth" /></td>
      <td><input type="text" data-col="scriptscan" readonly /></td>
      <td><input type="text" data-col="de" /></td>
      <td class="outcome"></td>`;
    body.appendChild(tr);
  });
}

function collectColumn(col) {
  const out = {};
  document.querySelectorAll(`#bm-body tr`).forEach((tr) => {
    const inp = tr.querySelector(`input[data-col="${col}"]`);
    out[tr.dataset.key] = inp.value.trim() || null;
  });
  return out;
}

function handleFile(file) {
  if (!file) return;
  if (!["image/png", "image/jpeg"].includes(file.type)) {
    showError("Please choose a PNG or JPG image.");
    return;
  }
  clearError();
  selectedFile = file;
  if (!$("bm-filename").value) $("bm-filename").value = file.name;
  const reader = new FileReader();
  reader.onload = (e) => {
    $("preview").src = e.target.result;
    $("preview-wrap").classList.remove("hidden");
    $("grid-section").classList.remove("hidden");
  };
  reader.readAsDataURL(file);
}

async function runAndScore() {
  if (!selectedFile) return showError("Choose an image first.");
  clearError();
  $("loading").classList.remove("hidden");
  $("run-btn").disabled = true;
  try {
    const form = new FormData();
    form.append("image", selectedFile);
    const res = await fetch("/extract", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok || data.error) {
      showError(data.error || data.detail || "Extraction failed.");
      return;
    }
    // Fill the ScriptScan column from extraction.
    const fields = data.result.fields || {};
    document.querySelectorAll("#bm-body tr").forEach((tr) => {
      const f = fields[tr.dataset.key] || {};
      tr.querySelector('input[data-col="scriptscan"]').value =
        f.value == null ? "" : f.value;
    });
    await score();
    flash("status-msg", "Extracted & scored");
  } catch (e) {
    showError("Could not reach the service: " + e.message);
  } finally {
    $("loading").classList.add("hidden");
    $("run-btn").disabled = false;
  }
}

async function score() {
  const truth = collectColumn("truth");
  const predicted = collectColumn("scriptscan");
  const res = await fetch("/benchmark/score", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ground_truth: truth, predicted }),
  });
  const r = await res.json();
  renderScore(r);
}

const SYM = { correct: "✓", near: "~", wrong: "✗", missing: "·", no_truth: "" };
function renderScore(r) {
  document.querySelectorAll("#bm-body tr").forEach((tr) => {
    const pf = r.per_field[tr.dataset.key];
    const cell = tr.querySelector(".outcome");
    cell.className = "outcome " + (pf ? pf.outcome : "");
    cell.textContent = pf && pf.outcome !== "no_truth" ? `${SYM[pf.outcome]} ${pf.outcome}` : "";
  });
  const ov = r.overall, hd = r.hard;
  const fmt = (a) => (a.accuracy == null ? "n/a" : `${a.accuracy.toFixed(0)}% (${a.correct}/${a.scored})`);
  let html = `<strong>Overall:</strong> ${fmt(ov)} &nbsp;·&nbsp; <strong>Hard fields (*):</strong> ${fmt(hd)}`;
  if (r.misses && r.misses.length) {
    html += `<h3>Misses (${r.misses.length})</h3><ul>`;
    r.misses.forEach((m) => {
      html += `<li><code>${m.field}</code>: got <em>${esc(m.predicted)}</em> — truth <em>${esc(m.truth)}</em> <span class="tag tag-review">${m.outcome}</span></li>`;
    });
    html += `</ul>`;
  } else {
    html += `<p>No misses against the recorded ground truth. 🎯</p>`;
  }
  const box = $("score-summary");
  box.innerHTML = html;
  box.classList.remove("hidden");
}

async function saveSource(source, col) {
  const filename = $("bm-filename").value.trim();
  if (!filename) return flash("status-msg", "Enter a filename key first");
  const res = await fetch("/benchmark/record", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, source, fields: collectColumn(col) }),
  });
  const r = await res.json();
  flash("status-msg", r.ok ? `Saved ${source}` : r.detail || "Save failed");
}

function esc(v) {
  if (v == null) return "∅";
  return String(v).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
function showError(m) { const b = $("error-box"); b.textContent = m; b.classList.remove("hidden"); }
function clearError() { $("error-box").classList.add("hidden"); }
function flash(id, m) { const e = $(id); e.textContent = m; setTimeout(() => (e.textContent = ""), 2500); }

function setupDropzone() {
  const dz = $("dropzone"), input = $("file-input");
  dz.addEventListener("click", () => input.click());
  input.addEventListener("change", () => handleFile(input.files[0]));
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("dragover"); }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("dragover"); }));
  dz.addEventListener("drop", (e) => handleFile(e.dataTransfer.files[0]));
}

document.addEventListener("DOMContentLoaded", () => {
  buildTable();
  setupDropzone();
  $("run-btn").addEventListener("click", runAndScore);
  $("save-truth-btn").addEventListener("click", () => saveSource("ground_truth", "truth"));
  $("save-de-btn").addEventListener("click", () => saveSource("de_assistant", "de"));
  // Re-score live as the user edits ground-truth values (debounced-ish).
  $("bm-body").addEventListener("input", (e) => {
    if (e.target.dataset.col === "truth" || e.target.dataset.col === "scriptscan") score();
  });
});
