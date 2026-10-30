"""Local PHI masking — strip the patient-identifying region BEFORE anything is sent
to Claude.

This is the backbone privacy step from the architecture: "patient identity never leaves
the building." OCR runs locally (Tesseract, on-device — no network), the patient-
identifying region is detected by pattern + keyword, and it is painted over with solid
black on a copy of the image. Only the masked image is handed to the extraction backend.

What is masked (patient identity) vs preserved (clinical + prescriber):
  MASK   — patient name line, DOB / birthdate, health-card / PHN numbers, and the
           patient's phone / postal / address block (anything below the prescriber
           header that looks like contact info).
  KEEP   — prescriber name, clinic address/phone/fax in the top header, and ALL the
           clinical content the tool must read (drug, strength, SIG, quantities, dates).

Returned info contains only categories and counts — never the PHI text itself, so logs
and API responses stay PHI-free.
"""

from __future__ import annotations

import io
import re
from typing import Any

# Fraction of image height treated as the prescriber/clinic header — contact info there
# (clinic phone/fax/postal) is preserved. Patient contact info sits below this band.
HEADER_FRACTION = 0.15

# Padding (px) added around each detected box so descenders/edges are fully covered.
BOX_PAD = 4

# Unambiguous patient-identity keywords. A whole OCR line containing one of these is an
# "anchor" for the patient block. Bare "for" is excluded (collides with "for 4 weeks");
# "for:" WITH a colon is a patient label ("For: Jane Doe") and is matched separately.
STRONG_KEYWORDS = re.compile(
    r"\b(patient|d\.?o\.?b\.?|dob|birth\s*date|birthdate|phn|hin|"
    r"health\s*card|hc#?|chart\s*#?|mrn)\b",
    re.IGNORECASE,
)

# "For:" / "Rx For:" — a patient-name label (colon required to avoid "for 4 weeks").
FOR_LABEL = re.compile(r"\bfor\s*:", re.IGNORECASE)

# A health-card / PHN style number (e.g. 1234-123-123, 1234 123 123, or 10 digits).
HEALTH_CARD = re.compile(r"\b(\d{4}[-\s]?\d{3}[-\s]?\d{3}|\d{10})\b")

# Canadian postal code (A1A 1A1).
POSTAL = re.compile(r"\b[A-Za-z]\d[A-Za-z]\s?\d[A-Za-z]\d\b")

# Phone / fax number.
PHONE = re.compile(r"(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")


class MaskingUnavailable(Exception):
    """Raised when masking is required but OCR cannot run (fail closed — never leak)."""


def _ocr(image) -> dict[str, Any]:
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as exc:  # pragma: no cover - import guard
        raise MaskingUnavailable("pytesseract is not installed") from exc
    try:
        return pytesseract.image_to_data(image, output_type=Output.DICT)
    except Exception as exc:  # tesseract binary missing / failed
        raise MaskingUnavailable(f"OCR failed ({exc.__class__.__name__})") from exc


def _group_lines(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Group OCR words into lines, keeping each line's text and bounding box."""
    lines: dict[tuple, dict[str, Any]] = {}
    n = len(data["text"])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        left, top = data["left"][i], data["top"][i]
        right, bottom = left + data["width"][i], top + data["height"][i]
        ln = lines.setdefault(
            key,
            {"words": [], "left": left, "top": top, "right": right, "bottom": bottom},
        )
        ln["words"].append(text)
        ln["left"] = min(ln["left"], left)
        ln["top"] = min(ln["top"], top)
        ln["right"] = max(ln["right"], right)
        ln["bottom"] = max(ln["bottom"], bottom)
    out = []
    for ln in lines.values():
        ln["text"] = " ".join(ln["words"])
        out.append(ln)
    return out


def _anchor_category(text: str) -> str | None:
    """Classify a line as a patient-identity anchor, or None if it is not one."""
    if HEALTH_CARD.search(text):
        return "health-card / PHN number"
    if re.search(r"\b(d\.?o\.?b\.?|dob|birth\s*date|birthdate)\b", text, re.IGNORECASE):
        return "date of birth"
    if re.search(
        r"\b(phn|hin|health\s*card|hc#?|chart\s*#?|mrn)\b", text, re.IGNORECASE
    ):
        return "health-card / PHN number"
    if STRONG_KEYWORDS.search(text) or FOR_LABEL.search(text):
        return "patient name / identity line"
    return None


def detect_phi_boxes(
    data: dict[str, Any], width: int, height: int
) -> list[dict[str, Any]]:
    """Pure detection logic (unit-tested): return boxes to mask + their category.

    Strategy: the patient-identifying block is a contiguous, left-aligned region
    bracketed by anchors (DOB / PHN / health-card / "Patient:" / "For:"). We mask every
    anchor line, plus any left-aligned line within a vertical band around the anchors
    (this catches the patient NAME and ADDRESS lines, which often carry no keyword of
    their own). Right-aligned content on the same rows (e.g. the Rx date) is preserved.

    Each box is {"box": (l, t, r, b), "category": str}.
    """
    lines = _group_lines(data)
    if not lines:
        return []

    header_cut = height * HEADER_FRACTION
    line_heights = sorted(ln["bottom"] - ln["top"] for ln in lines)
    lh = line_heights[len(line_heights) // 2] if line_heights else 12

    boxes: list[dict[str, Any]] = []
    taken: set[int] = set()

    def add(ln: dict[str, Any], category: str) -> None:
        if id(ln) in taken:
            return
        taken.add(id(ln))
        boxes.append(
            {
                "box": (ln["left"], ln["top"], ln["right"], ln["bottom"]),
                "category": category,
            }
        )

    # 1) Anchor lines (always masked, even inside the header band).
    anchors = []
    for ln in lines:
        cat = _anchor_category(ln["text"])
        if cat:
            anchors.append(ln)
            add(ln, cat)

    # 2) Patient band: left-aligned lines vertically adjacent to the anchors. This is
    #    where the bare name / address lines live.
    if anchors:
        band_top = min(a["top"] for a in anchors) - 1.5 * lh
        band_bottom = max(a["bottom"] for a in anchors) + 2.0 * lh
        for ln in lines:
            if id(ln) in taken:
                continue
            cy = (ln["top"] + ln["bottom"]) / 2
            left_aligned = ln["left"] < width * 0.6
            if band_top <= cy <= band_bottom and left_aligned:
                add(ln, "patient block (adjacent to identity)")

    # 3) Standalone patient contact (postal / phone) below the prescriber header.
    for ln in lines:
        if id(ln) in taken or ln["bottom"] <= header_cut:
            continue
        if POSTAL.search(ln["text"]) or PHONE.search(ln["text"]):
            add(ln, "patient contact (postal / phone)")

    return boxes


def mask_phi(image_bytes: bytes, media_type: str) -> tuple[bytes, dict[str, Any]]:
    """Detect and black out the patient-identifying region locally.

    Returns (masked_image_bytes, info). `info` has only categories/counts, never PHI text.
    Raises MaskingUnavailable if OCR cannot run (caller decides fail-open vs fail-closed).
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:  # pragma: no cover
        raise MaskingUnavailable("Pillow is not installed") from exc

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise ValueError(f"Could not read image ({exc.__class__.__name__})") from exc
    width, height = image.size

    data = _ocr(image)
    found = detect_phi_boxes(data, width, height)

    draw = ImageDraw.Draw(image)
    categories: dict[str, int] = {}
    for item in found:
        l, t, r, b = item["box"]
        draw.rectangle(
            [
                max(0, l - BOX_PAD),
                max(0, t - BOX_PAD),
                min(width, r + BOX_PAD),
                min(height, b + BOX_PAD),
            ],
            fill="black",
        )
        categories[item["category"]] = categories.get(item["category"], 0) + 1

    fmt = "PNG" if media_type == "image/png" else "JPEG"
    out = io.BytesIO()
    image.save(out, format=fmt)

    info = {
        "masked": True,
        "engine": "tesseract (local)",
        "regions_masked": len(found),
        "categories": categories,
    }
    return out.getvalue(), info
