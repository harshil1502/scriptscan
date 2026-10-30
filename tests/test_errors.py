"""Error-handling tests for the extraction backends.

All mocked — no real network or CLI calls. Verifies that every failure mode raises a
clean ExtractionError (never an uncaught crash) and that the CLI backend cleans up its
temp directory.
"""

import json
import os
import subprocess

import pytest

import extractor
from extractor import ExtractionError, extract_fields

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture(autouse=True)
def _disable_masking(monkeypatch):
    """These tests target backend dispatch/errors, not masking — and use fake image
    bytes that aren't decodable. Turn masking off so masking doesn't intercept them."""
    monkeypatch.setattr(extractor, "MASK_ENABLED", False)


def test_unsupported_media_type_raises():
    with pytest.raises(ExtractionError, match="Unsupported image type"):
        extract_fields(PNG_BYTES, "image/gif")


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setattr(extractor, "BACKEND", "bogus")
    with pytest.raises(ExtractionError, match="Unknown SCRIPTSCAN_BACKEND"):
        extract_fields(PNG_BYTES, "image/png")


def test_api_backend_without_key_raises(monkeypatch):
    monkeypatch.setattr(extractor, "BACKEND", "api")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ExtractionError, match="ANTHROPIC_API_KEY"):
        extract_fields(PNG_BYTES, "image/png")


def test_cli_backend_missing_binary_raises(monkeypatch):
    monkeypatch.setattr(extractor, "BACKEND", "cli")
    monkeypatch.setattr(extractor.shutil, "which", lambda _: None)
    with pytest.raises(ExtractionError, match="Claude Code CLI .* not found"):
        extract_fields(PNG_BYTES, "image/png")


def _fake_completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["claude"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_cli_success_parses_envelope_and_cleans_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(extractor, "BACKEND", "cli")
    monkeypatch.setattr(extractor.shutil, "which", lambda _: "/usr/bin/claude")

    envelope = {
        "is_error": False,
        "result": json.dumps(
            {
                "fields": {"drug_name": {"value": "Metformin", "confidence": 0.97}},
                "overall_legibility": "high",
                "warnings": [],
                "phi_detected": False,
            }
        ),
        "modelUsage": {"claude-sonnet-4-6": {}},
    }

    created_dirs = []
    real_mkdtemp = extractor.tempfile.mkdtemp

    def tracking_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        created_dirs.append(d)
        return d

    monkeypatch.setattr(extractor.tempfile, "mkdtemp", tracking_mkdtemp)
    monkeypatch.setattr(
        extractor.subprocess,
        "run",
        lambda *a, **k: _fake_completed(json.dumps(envelope)),
    )

    out = extract_fields(PNG_BYTES, "image/png")
    assert out["backend"] == "cli"
    assert out["result"]["fields"]["drug_name"]["value"] == "Metformin"
    # Temp dir (and the image in it) must be gone afterward — no PHI persisted.
    assert created_dirs and not os.path.exists(created_dirs[0])


def test_cli_nonzero_exit_raises_clean(monkeypatch):
    monkeypatch.setattr(extractor, "BACKEND", "cli")
    monkeypatch.setattr(extractor.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        extractor.subprocess,
        "run",
        lambda *a, **k: _fake_completed(stderr="boom", returncode=1),
    )
    with pytest.raises(ExtractionError, match="Claude Code CLI failed"):
        extract_fields(PNG_BYTES, "image/png")


def test_cli_is_error_envelope_raises(monkeypatch):
    monkeypatch.setattr(extractor, "BACKEND", "cli")
    monkeypatch.setattr(extractor.shutil, "which", lambda _: "/usr/bin/claude")
    env = json.dumps({"is_error": True, "subtype": "rate_limit", "result": ""})
    monkeypatch.setattr(
        extractor.subprocess, "run", lambda *a, **k: _fake_completed(env)
    )
    with pytest.raises(ExtractionError, match="reported an error"):
        extract_fields(PNG_BYTES, "image/png")


def test_cli_empty_result_raises(monkeypatch):
    monkeypatch.setattr(extractor, "BACKEND", "cli")
    monkeypatch.setattr(extractor.shutil, "which", lambda _: "/usr/bin/claude")
    env = json.dumps({"is_error": False, "result": "   "})
    monkeypatch.setattr(
        extractor.subprocess, "run", lambda *a, **k: _fake_completed(env)
    )
    with pytest.raises(ExtractionError, match="empty result"):
        extract_fields(PNG_BYTES, "image/png")


def test_cli_bad_envelope_json_raises(monkeypatch):
    monkeypatch.setattr(extractor, "BACKEND", "cli")
    monkeypatch.setattr(extractor.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        extractor.subprocess, "run", lambda *a, **k: _fake_completed("not json")
    )
    with pytest.raises(ExtractionError, match="parse Claude Code CLI output"):
        extract_fields(PNG_BYTES, "image/png")


def test_cli_timeout_raises_clean(monkeypatch):
    monkeypatch.setattr(extractor, "BACKEND", "cli")
    monkeypatch.setattr(extractor.shutil, "which", lambda _: "/usr/bin/claude")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(extractor.subprocess, "run", raise_timeout)
    with pytest.raises(ExtractionError, match="timed out"):
        extract_fields(PNG_BYTES, "image/png")
