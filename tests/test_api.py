"""API-level tests using FastAPI's TestClient. Extraction is mocked — no live calls."""

import io

import pytest
from fastapi.testclient import TestClient

import extractor
import main

client = TestClient(main.app)


def test_config_reports_backend():
    r = client.get("/config")
    assert r.status_code == 200
    body = r.json()
    assert body["backend"] in ("cli", "api")
    assert len(body["field_keys"]) == 13
    assert "api_key" not in body  # never leak the key


def test_index_served():
    assert client.get("/").status_code == 200


def test_extract_rejects_non_image():
    r = client.post("/extract", files={"image": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 400


def test_extract_rejects_empty():
    r = client.post("/extract", files={"image": ("x.png", b"", "image/png")})
    assert r.status_code == 400


def test_extract_success_is_mocked_and_flagged(monkeypatch):
    def fake_extract(image_bytes, media_type):
        return {
            "raw": "{}",
            "result": {
                "fields": {
                    k: {"value": "v", "confidence": 0.95 if k == "drug_name" else 0.5}
                    for k in extractor.FIELD_KEYS
                },
                "overall_legibility": "high",
                "warnings": [],
                "phi_detected": False,
            },
            "model": "test",
            "backend": "cli",
        }

    monkeypatch.setattr(main.extractor, "extract_fields", fake_extract)
    r = client.post("/extract", files={"image": ("x.png", b"\x89PNG1234", "image/png")})
    assert r.status_code == 200
    body = r.json()
    # Server-side flagging applied.
    assert body["result"]["fields"]["drug_name"]["review"] is False
    assert body["result"]["fields"]["no_refills"]["review"] is True
    assert body["result"]["flagged_count"] == 12


def test_extract_error_returns_clean_502(monkeypatch):
    def boom(image_bytes, media_type):
        raise extractor.ExtractionError("nope")

    monkeypatch.setattr(main.extractor, "extract_fields", boom)
    r = client.post("/extract", files={"image": ("x.png", b"\x89PNG1234", "image/png")})
    assert r.status_code == 502
    assert r.json()["error"] == "nope"


def test_benchmark_score_endpoint():
    r = client.post(
        "/benchmark/score",
        json={
            "ground_truth": {"drug_name": "Ozempic", "no_refills": "3"},
            "predicted": {"drug_name": "ozempic", "no_refills": "5"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["per_field"]["drug_name"]["outcome"] == "correct"
    assert body["per_field"]["no_refills"]["outcome"] == "wrong"
    assert body["overall"]["scored"] == 2


def test_benchmark_score_rejects_bad_payload():
    r = client.post("/benchmark/score", json={"ground_truth": "x", "predicted": {}})
    assert r.status_code == 400


def test_benchmark_record_validates_source():
    r = client.post(
        "/benchmark/record",
        json={"filename": "f.png", "source": "bogus", "fields": {}},
    )
    assert r.status_code == 400
