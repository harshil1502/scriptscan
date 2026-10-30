"""Tests for local PHI masking detection logic (detect_phi_boxes is pure — no OCR)."""

import masker
from masker import detect_phi_boxes


def _ocr_data(lines):
    """Build a pytesseract-style data dict from (text, left, top, w, h) word tuples,
    grouping each input line as its own OCR line."""
    keys = [
        "text",
        "left",
        "top",
        "width",
        "height",
        "block_num",
        "par_num",
        "line_num",
    ]
    data = {k: [] for k in keys}
    for line_idx, words in enumerate(lines):
        for text, left, top, w, h in words:
            data["text"].append(text)
            data["left"].append(left)
            data["top"].append(top)
            data["width"].append(w)
            data["height"].append(h)
            data["block_num"].append(1)
            data["par_num"].append(1)
            data["line_num"].append(line_idx)
    return data


W, H = 900, 650  # header band = top 97px


def _categories(boxes):
    return {b["category"] for b in boxes}


def test_patient_keyword_line_is_masked():
    data = _ocr_data(
        [
            [
                ("Patient:", 40, 200, 80, 18),
                ("Jane", 130, 200, 50, 18),
                ("Doe", 190, 200, 40, 18),
            ]
        ]
    )
    boxes = detect_phi_boxes(data, W, H)
    assert len(boxes) == 1
    assert boxes[0]["category"] == "patient name / identity line"


def test_for_colon_label_is_anchor():
    data = _ocr_data(
        [
            [
                ("For:", 40, 200, 50, 18),
                ("Jordan", 100, 200, 70, 18),
                ("Maplewood", 180, 200, 110, 18),
            ]
        ]
    )
    boxes = detect_phi_boxes(data, W, H)
    assert boxes and boxes[0]["category"] == "patient name / identity line"


def test_dob_line_is_masked():
    data = _ocr_data([[("DOB:", 40, 230, 50, 18), ("1975-04-05", 100, 230, 110, 18)]])
    assert _categories(detect_phi_boxes(data, W, H)) == {"date of birth"}


def test_name_line_adjacent_to_dob_masked_by_band():
    # The patient NAME line carries no keyword but sits just above the DOB anchor.
    data = _ocr_data(
        [
            [("Jordan", 40, 210, 70, 18), ("Maplewood", 120, 210, 110, 18)],
            [("DOB:", 40, 240, 50, 18), ("1995-07-15", 100, 240, 110, 18)],
        ]
    )
    cats = _categories(detect_phi_boxes(data, W, H))
    assert "date of birth" in cats
    assert "patient block (adjacent to identity)" in cats


def test_health_card_number_masked_below_header():
    data = _ocr_data([[("1234-123-123", 40, 300, 130, 18)]])
    boxes = detect_phi_boxes(data, W, H)
    assert boxes and boxes[0]["category"] == "health-card / PHN number"


def test_clinical_line_with_for_is_not_masked():
    # "for 4 weeks" must NOT trigger masking ("for" is excluded as a keyword).
    data = _ocr_data(
        [
            [
                ("1", 40, 300, 12, 18),
                ("mg", 60, 300, 24, 18),
                ("weekly", 100, 300, 60, 18),
                ("for", 170, 300, 30, 18),
                ("4", 210, 300, 12, 18),
                ("weeks", 230, 300, 50, 18),
            ]
        ]
    )
    assert detect_phi_boxes(data, W, H) == []


def test_clinic_phone_in_header_is_preserved():
    # A phone in the top header band (clinic) must not be masked.
    data = _ocr_data([[("Phone:", 40, 30, 60, 18), ("519-351-6464", 110, 30, 120, 18)]])
    assert detect_phi_boxes(data, W, H) == []


def test_patient_phone_below_header_is_masked():
    data = _ocr_data([[("905-555-1212", 40, 250, 120, 18)]])
    boxes = detect_phi_boxes(data, W, H)
    assert boxes and boxes[0]["category"] == "patient contact (postal / phone)"


def test_patient_postal_below_header_is_masked():
    data = _ocr_data(
        [
            [
                ("Chatham", 40, 260, 80, 18),
                ("ON", 130, 260, 24, 18),
                ("N7L", 160, 260, 36, 18),
                ("1S1", 200, 260, 36, 18),
            ]
        ]
    )
    boxes = detect_phi_boxes(data, W, H)
    assert boxes and boxes[0]["category"] == "patient contact (postal / phone)"


def test_clinical_content_preserved():
    data = _ocr_data(
        [
            [("Drug:", 40, 300, 50, 18), ("PRISTIQ", 100, 300, 90, 18)],
            [
                ("50", 40, 330, 24, 18),
                ("MG", 70, 330, 30, 18),
                ("TABLET", 110, 330, 70, 18),
            ],
            [("Refills:", 40, 360, 70, 18), ("3", 120, 360, 12, 18)],
        ]
    )
    assert detect_phi_boxes(data, W, H) == []


def test_invalid_image_raises_valueerror():
    import pytest

    with pytest.raises(ValueError, match="Could not read image"):
        masker.mask_phi(b"not an image", "image/png")
