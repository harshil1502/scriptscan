// ScriptScan frontend. Vanilla JS, no framework, no build step.
// Everything runs against the local FastAPI server. The image lives only in this
// page's memory and the in-flight request; "Clear" wipes it.

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

let selectedFile = null;
let lastResult = null;

// ---- Config / startup -------------------------------------------------------

async function loadConfig() {
  try {
    const res = await fetch("/config");
    const cfg = await res.json();
    const backendName = cfg.backend === "cli" ? "Claude Code CLI" : "Anthropic API";
    $("model-badge").textContent = `${backendName} · ${cfg.model}`;
    $("threshold").value = cfg.default_threshold;
    if (!cfg.backend_ready) {
      const warn = $("api-key-warning");
      warn.textContent = cfg.backend_hint + " Extraction will not work until this is resolved.";
      warn.classList.remove("hidden");
    }
  } catch (e) {
    // Non-fatal; the server may not be reachable yet.
  }
}

// ---- Upload handling --------------------------------------------------------

function handleFile(file) {
  if (!file) return;
  if (!["image/png", "image/jpeg"].includes(file.type)) {
    showError("Please choose a PNG or JPG image.");
    return;
  }
  clearError();
  selectedFile = file;
  const reader = new FileReader();
  reader.onload = (e) => {
    $("preview").src = e.target.result;
    $("preview-wrap").classList.remove("hidden");
  };
  reader.readAsDataURL(file);
}

function setupDropzone() {
  const dz = $("dropzone");
  const input = $("file-input");

  dz.addEventListener("click", () => input.click());
  dz.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      input.click();
    }
  });
  input.addEventListener("change", () => handleFile(input.files[0]));

  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.add("dragover");
    })
  );
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => {
      e.preventDefault();
      dz.classList.remove("dragover");
    })
  );
  dz.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    handleFile(file);
  });
}

// ---- Extraction -------------------------------------------------------------

async function extract() {
  if (!selectedFile) {
    showError("Choose an image first.");
    return;
  }
  clearError();
  $("results-section").classList.add("hidden");
  $("loading").classList.remove("hidden");
  $("extract-btn").disabled = true;

  try {
    const form = new FormData();
    form.append("image", selectedFile);
    const res = await fetch("/extract", { method: "POST", body: form });
    const data = await res.json();

    if (!res.ok || data.error) {
      showError(data.error || data.detail || "Extraction failed.");
      return;
    }
    lastResult = data.result;
    renderMaskStatus(data.mask_info);
    renderResults(data.result, data.raw);
  } catch (e) {
    showError("Could not reach the extraction service: " + e.message);
  } finally {
    $("loading").classList.add("hidden");
    $("extract-btn").disabled = false;
  }
}

// ---- Rendering --------------------------------------------------------------

function renderMaskStatus(info) {
  const box = $("mask-status");
  const previewBlock = $("mask-preview-block");
  if (!info || !info.masked) {
    box.classList.add("hidden");
    previewBlock.classList.add("hidden");
    return;
  }
  const cats = info.categories || {};
  const parts = Object.keys(cats).map((c) => `${cats[c]}× ${c}`);
  const detail = parts.length ? " — " + parts.join(", ") : "";
  box.textContent =
    `🛡 Local PHI masking (${info.engine}): blacked out ${info.regions_masked} ` +
    `region(s) on this machine before sending to Claude${detail}.`;
  box.classList.remove("hidden");

  if (info.preview) {
    $("mask-preview").src = info.preview;
    previewBlock.classList.remove("hidden");
  } else {
    previewBlock.classList.add("hidden");
  }
}

function renderResults(result, raw) {
  const threshold = parseFloat($("threshold").value) || 0.9;

  // PHI warning
  $("phi-warning").classList.toggle("hidden", !result.phi_detected);

  // Legibility badge
  const badge = $("legibility-badge");
  badge.textContent = "Legibility: " + result.overall_legibility;
  badge.className = "badge " + result.overall_legibility;

  // Warnings
  const warnPanel = $("warnings-panel");
  const warnList = $("warnings-list");
  warnList.innerHTML = "";
  if (result.warnings && result.warnings.length) {
    warnPanel.classList.remove("hidden");
    result.warnings.forEach((w) => {
      const li = document.createElement("li");
      li.textContent = w;
      // Emphasize the highest-stakes risks in red: look-alike/sound-alike drug
      // confusion, and multi-drug prescriptions (so the 2nd drug is never dropped).
      if (/look[- ]?alike|sound[- ]?alike|lasa|confus|multi[- ]?drug|second drug|two (separate )?drug/i.test(w)) {
        li.classList.add("lasa");
      }
      warnList.appendChild(li);
    });
  } else {
    warnPanel.classList.add("hidden");
  }

  // Fields
  const formEl = $("results-form");
  formEl.innerHTML = "";
  Object.keys(FIELD_LABELS).forEach((key) => {
    const entry = result.fields[key] || { value: null, confidence: 0 };
    const conf = entry.confidence || 0;
    const isReview = conf < threshold;

    const wrap = document.createElement("div");
    wrap.className = "field" + (isReview ? " review" : "");

    const label = document.createElement("div");
    label.className = "field-label";
    const nameSpan = document.createElement("span");
    nameSpan.className = "name";
    nameSpan.textContent = FIELD_LABELS[key];
    const tag = document.createElement("span");
    if (isReview) {
      tag.className = "tag tag-review";
      tag.textContent = "REVIEW " + Math.round(conf * 100) + "%";
    } else {
      tag.className = "tag tag-ok";
      tag.textContent = "✓ " + Math.round(conf * 100) + "%";
    }
    label.appendChild(nameSpan);
    label.appendChild(tag);

    const input = document.createElement("input");
    input.type = "text";
    input.value = entry.value == null ? "" : entry.value;
    input.dataset.key = key;
    if (isReview) input.placeholder = "Needs review — verify against the fax";

    wrap.appendChild(label);
    wrap.appendChild(input);
    formEl.appendChild(wrap);
  });

  $("raw-output").textContent = raw || "";
  $("results-section").classList.remove("hidden");
}

// ---- Copy / clear -----------------------------------------------------------

function collectFields() {
  const out = {};
  document.querySelectorAll("#results-form input").forEach((inp) => {
    out[inp.dataset.key] = inp.value.trim() || null;
  });
  return out;
}

function copyVerified() {
  const fields = collectFields();
  const lines = Object.keys(FIELD_LABELS).map(
    (k) => `${FIELD_LABELS[k]}: ${fields[k] || ""}`
  );
  const text = lines.join("\n");
  navigator.clipboard.writeText(text).then(
    () => flash("copy-feedback", "Copied to clipboard"),
    () => flash("copy-feedback", "Copy failed — select and copy manually")
  );
}

function clearAll() {
  selectedFile = null;
  lastResult = null;
  $("file-input").value = "";
  $("preview").src = "";
  $("preview-wrap").classList.add("hidden");
  $("results-section").classList.add("hidden");
  clearError();
}

// ---- Benchmark recording ----------------------------------------------------

async function recordBenchmark(source) {
  const filename = $("bm-filename").value.trim();
  if (!filename) {
    flash("bm-feedback", "Enter a filename key first", true);
    return;
  }
  try {
    const res = await fetch("/benchmark/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, source, fields: collectFields() }),
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      flash("bm-feedback", `Saved "${filename}" as ${source}`);
    } else {
      flash("bm-feedback", data.detail || "Save failed", true);
    }
  } catch (e) {
    flash("bm-feedback", "Save failed: " + e.message, true);
  }
}

// ---- Helpers ----------------------------------------------------------------

function showError(msg) {
  const box = $("error-box");
  box.textContent = msg;
  box.classList.remove("hidden");
}
function clearError() {
  $("error-box").classList.add("hidden");
}
function flash(id, msg) {
  const el = $(id);
  el.textContent = msg;
  setTimeout(() => (el.textContent = ""), 2500);
}

// ---- Wire up ----------------------------------------------------------------

document.addEventListener("DOMContentLoaded", () => {
  loadConfig();
  setupDropzone();
  $("extract-btn").addEventListener("click", extract);
  $("clear-btn").addEventListener("click", clearAll);
  $("copy-btn").addEventListener("click", copyVerified);
  document.querySelectorAll("#benchmark-block [data-source]").forEach((btn) =>
    btn.addEventListener("click", () => recordBenchmark(btn.dataset.source))
  );
});
