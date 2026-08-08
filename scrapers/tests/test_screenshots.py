"""Tests for the screenshot-share intake source.

Run from scrapers/ with `PYTHONPATH=.` so `sources.screenshots` imports the
same way run.py sees it. No network: `_extract` is monkeypatched.
"""

import hashlib
import os
from pathlib import Path

import pytest

from sources.screenshots import (
    build_signal,
    collect,
    parse_extraction,
    redact_handles,
)


# --- pure helpers -----------------------------------------------------------

def test_redact_handles_replaces_at_tokens():
    assert redact_handles("flooding by @jane.doe now") == "flooding by @[redacted] now"


def test_redact_handles_multiple():
    assert redact_handles("via @ Updates") == "via @ Updates"  # lone @ not a handle
    assert redact_handles("ping @team lead by @a.b.c") == "ping @[redacted] lead by @[redacted]"


def test_parse_extraction_well_formed():
    # A well-formed Gemini REST response: candidates[0].content.parts[0].text
    # holds the real JSON payload as a string.
    inner = '{"is_social_post": true, "text": "hi", "platform": "facebook"}'
    resp = {"candidates": [{"content": {"parts": [{"text": inner}]}}]}
    result = parse_extraction(resp)
    assert isinstance(result, dict)
    assert result["is_social_post"] is True
    assert result["text"] == "hi"

def test_parse_extraction_strips_json_fences():
    import json
    resp = {"candidates": [{"content": {"parts": [
        {"text": "```json\n{\"is_social_post\": true, \"text\": \"hi\"}\n```"}
    ]}}]}
    result = parse_extraction(resp)
    assert isinstance(result, dict)
    assert result["text"] == "hi"


def test_parse_extraction_empty_candidates():
    assert parse_extraction({"candidates": []}) is None


def test_parse_extraction_invalid_text():
    assert parse_extraction({"candidates": [{"content": {"parts": [
        {"text": "not json"}
    ]}}]}) is None


def test_parse_extraction_missing_parts_key():
    assert parse_extraction({"candidates": [{"content": {}}]}) is None


# --- build_signal -----------------------------------------------------------

VALID_SHA = "a" * 40


def test_build_signal_source_shape():
    ext = {
        "is_social_post": True,
        "platform": "Facebook",
        "text": "Road closed near the waterfront",
        "author_type": "individual",
        "posted_time_text": "2 hrs ago",
        "place_mentions": ["waterfront"],
    }
    sig = build_signal(ext, VALID_SHA, "post.png")
    assert sig["source"]["type"] == "screenshot"
    assert sig["source"]["name"] == "Screenshot · facebook"
    assert sig["source"]["collector"] == "screenshots"
    assert sig["source"]["local"] is True


def test_build_signal_redacts_handles_in_text():
    ext = {"is_social_post": True, "platform": "x", "text": "flooding by @jane.doe now"}
    sig = build_signal(ext, VALID_SHA, "f.png")
    assert "@[redacted]" in sig["text"]
    assert "@jane.doe" not in sig["text"]


def test_build_signal_external_id_is_sha1():
    ext = {"is_social_post": True, "platform": "x", "text": "hi"}
    sig = build_signal(ext, VALID_SHA, "f.png")
    assert sig["external_id"] == VALID_SHA


def test_build_signal_appends_new_place_mentions():
    ext = {
        "is_social_post": True,
        "platform": "facebook",
        "text": "Road is closed",
        "place_mentions": [" Oriental Parade", "Te Aro"],
    }
    sig = build_signal(ext, VALID_SHA, "f.png")
    assert "Oriental Parade" in sig["text"]
    assert "Te Aro" in sig["text"]
    assert "places mentioned" in sig["text"].lower()


def test_build_signal_does_not_duplicate_place_already_in_text():
    ext = {
        "is_social_post": True,
        "platform": "facebook",
        "text": "Flooding in Te Aro",
        "place_mentions": ["Te Aro", "Oriental Parade"],
    }
    sig = build_signal(ext, VALID_SHA, "f.png")
    # "Te Aro" already in text (case-insensitive) so not appended again
    assert sig["text"].count("Te Aro") == 1
    # "Oriental Parade" not in text so it is appended
    assert "Oriental Parade" in sig["text"]


def test_build_signal_raw_screenshot_true():
    ext = {"is_social_post": True, "platform": "x", "text": "hi"}
    sig = build_signal(ext, VALID_SHA, "f.png")
    assert sig["raw"]["screenshot"] is True
    assert sig["raw"]["platform"] == "x"
    assert sig["raw"]["filename"] == "f.png"


def test_build_signal_missing_platform_defaults_unknown():
    sig = build_signal({"is_social_post": True, "text": "hi"}, VALID_SHA, "f.png")
    assert sig["source"]["name"] == "Screenshot · unknown"
    assert sig["raw"]["platform"] == "unknown"


# --- collect() -------------------------------------------------------------

PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
    b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01"
    b"\x00\x05\xfe\xd4\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _FakeExtractOk:
    """Monkeypatchable fake _extract returning a valid extraction."""
    @staticmethod
    def __call__(image_bytes, mime, api_key):
        return {
            "is_social_post": True,
            "platform": "facebook",
            "text": "Flooding in Te Aro",
            "author_type": "individual",
            "posted_time_text": "just now",
            "place_mentions": [],
        }


def _write_png(path: Path, content: bytes = PNG_BYTES) -> None:
    path.write_bytes(content)


def test_collect_valid_file_moved_to_processed(monkeypatch, tmp_path):
    _write_png(tmp_path / "shot.png")
    monkeypatch.setattr("sources.screenshots._extract",
                        _FakeExtractOk(), raising=False)
    monkeypatch.setenv("SCREENSHOT_INBOX", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    signals = collect()
    assert len(signals) == 1
    assert (tmp_path / "processed" / "shot.png").exists()
    assert not (tmp_path / "shot.png").exists()


def test_collect_not_social_post_moved_to_skipped(monkeypatch, tmp_path):
    _write_png(tmp_path / "skip.png")
    def fake(image_bytes, mime, api_key):
        return {"is_social_post": False}
    monkeypatch.setattr("sources.screenshots._extract", fake, raising=False)
    monkeypatch.setenv("SCREENSHOT_INBOX", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    signals = collect()
    assert signals == []
    assert (tmp_path / "skipped" / "skip.png").exists()
    assert not (tmp_path / "skip.png").exists()


def test_collect_none_extraction_moved_to_failed(monkeypatch, tmp_path):
    _write_png(tmp_path / "bad.png")
    def fake(image_bytes, mime, api_key):
        return None
    monkeypatch.setattr("sources.screenshots._extract", fake, raising=False)
    monkeypatch.setenv("SCREENSHOT_INBOX", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    signals = collect()
    assert signals == []
    assert (tmp_path / "failed" / "bad.png").exists()
    assert not (tmp_path / "bad.png").exists()


def test_collect_no_api_key_does_not_move_files(monkeypatch, tmp_path):
    _write_png(tmp_path / "waiting.png")
    monkeypatch.setattr("sources.screenshots._extract", lambda *a: {}, raising=False)
    monkeypatch.setenv("SCREENSHOT_INBOX", str(tmp_path))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    signals = collect()
    assert signals == []
    assert (tmp_path / "waiting.png").exists()
    assert not (tmp_path / "processed").exists()
    assert not (tmp_path / "skipped").exists()
    assert not (tmp_path / "failed").exists()


def test_collect_ignores_non_image_files(monkeypatch, tmp_path):
    (tmp_path / "notes.txt").write_text("not an image")
    monkeypatch.setattr("sources.screenshots._extract", lambda *a: {}, raising=False)
    monkeypatch.setenv("SCREENSHOT_INBOX", str(tmp_path))
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    signals = collect()
    assert signals == []
    assert (tmp_path / "notes.txt").exists()
