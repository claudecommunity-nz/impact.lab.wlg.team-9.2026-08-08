"""Tests for the screenshot-share intake source.

Run from scrapers/ with `PYTHONPATH=.` so `sources.screenshots` imports the
same way run.py sees it. No network: `_extract` is monkeypatched.
"""

import hashlib
import os
from pathlib import Path

import pytest

from sources.screenshots import store
from sources.screenshots import (
    build_signal,
    describe,
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


# Stand-ins for the SDK's response objects. parse_extraction reads them with
# getattr, so anything with the right attribute names will do — which keeps
# these tests independent of the SDK's model classes.

class _Block:
    def __init__(self, text, type="text"):
        self.text = text
        self.type = type


class _Message:
    def __init__(self, content=(), stop_reason="end_turn", stop_details=None):
        self.content = list(content)
        self.stop_reason = stop_reason
        self.stop_details = stop_details


class _StopDetails:
    def __init__(self, category):
        self.category = category


def test_parse_extraction_well_formed():
    # Structured outputs put the JSON straight in the first text block.
    inner = '{"is_social_post": true, "text": "hi", "platform": "facebook"}'
    result = parse_extraction(_Message([_Block(inner)]))
    assert isinstance(result, dict)
    assert result["is_social_post"] is True
    assert result["text"] == "hi"


def test_parse_extraction_skips_non_text_blocks():
    # Thinking is on by default, so a thinking block can precede the answer.
    inner = '{"is_social_post": true, "text": "hi"}'
    message = _Message([_Block("reasoning", type="thinking"), _Block(inner)])
    assert parse_extraction(message)["text"] == "hi"


def test_parse_extraction_refusal_returns_none():
    # A safety classifier declining returns a normal 200 with no usable
    # content — reading content[0] here would be the bug this guards.
    message = _Message([], stop_reason="refusal", stop_details=_StopDetails("cyber"))
    assert parse_extraction(message) is None


def test_parse_extraction_refusal_without_details():
    assert parse_extraction(_Message([], stop_reason="refusal")) is None


def test_parse_extraction_truncated_returns_none():
    # Truncated JSON would parse as invalid anyway, but stop_reason says so
    # first and more cheaply.
    message = _Message([_Block('{"is_social_post": tr')], stop_reason="max_tokens")
    assert parse_extraction(message) is None


def test_parse_extraction_empty_content():
    assert parse_extraction(_Message([])) is None


def test_parse_extraction_invalid_json():
    assert parse_extraction(_Message([_Block("not json")])) is None


def test_parse_extraction_json_but_not_an_object():
    assert parse_extraction(_Message([_Block('["a", "b"]')])) is None


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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    signals = collect()
    assert len(signals) == 1
    # Stored under the content hash, which is also the signal's external_id.
    sha1_hex = hashlib.sha1(PNG_BYTES).hexdigest()
    assert (tmp_path / "processed" / f"{sha1_hex}.png").exists()
    assert not (tmp_path / "shot.png").exists()
    assert signals[0]["external_id"] == sha1_hex


def test_collect_signal_points_at_the_stored_image(monkeypatch, tmp_path):
    _write_png(tmp_path / "shot.png")
    monkeypatch.setattr("sources.screenshots._extract",
                        _FakeExtractOk(), raising=False)
    monkeypatch.setenv("SCREENSHOT_INBOX", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    sha1_hex = hashlib.sha1(PNG_BYTES).hexdigest()
    media = collect()[0]["media"]
    assert media == [{"type": "image", "url": f"/screenshots/{sha1_hex}.png"}]


def test_collect_two_files_same_name_do_not_collide(monkeypatch, tmp_path):
    # Two different images can arrive under the same name from different
    # phones. On local disk a plain rename would silently overwrite; over a
    # network filesystem it can fail and leave the file to be re-processed
    # (and re-charged) every 20 seconds.
    (tmp_path / "IMG_1234.png").write_bytes(PNG_BYTES)
    monkeypatch.setattr("sources.screenshots._extract",
                        lambda *a: {"is_social_post": False}, raising=False)
    monkeypatch.setenv("SCREENSHOT_INBOX", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    collect()

    (tmp_path / "IMG_1234.png").write_bytes(PNG_BYTES + b"different")
    collect()

    skipped = sorted(p.name for p in (tmp_path / "skipped").iterdir())
    assert skipped == ["IMG_1234-2.png", "IMG_1234.png"]
    assert not (tmp_path / "IMG_1234.png").exists()


def test_collect_not_social_post_moved_to_skipped(monkeypatch, tmp_path):
    _write_png(tmp_path / "skip.png")
    def fake(image_bytes, mime, api_key):
        return {"is_social_post": False}
    monkeypatch.setattr("sources.screenshots._extract", fake, raising=False)
    monkeypatch.setenv("SCREENSHOT_INBOX", str(tmp_path))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    signals = collect()
    assert signals == []
    assert (tmp_path / "failed" / "bad.png").exists()
    assert not (tmp_path / "bad.png").exists()


def test_collect_no_api_key_does_not_move_files(monkeypatch, tmp_path):
    _write_png(tmp_path / "waiting.png")
    monkeypatch.setattr("sources.screenshots._extract", lambda *a: {}, raising=False)
    monkeypatch.setenv("SCREENSHOT_INBOX", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    signals = collect()
    assert signals == []
    assert (tmp_path / "notes.txt").exists()


# --- storage backend --------------------------------------------------------

def test_safe_name_strips_unsafe_characters():
    # Camera rolls and share sheets produce names with spaces and colons.
    assert store.safe_name("Screenshot 2026-08-08 at 14:32:01.png") \
        == "Screenshot_2026-08-08_at_14_32_01.png"


def test_safe_name_never_returns_a_traversal_or_empty_string():
    # The name comes from whatever a stranger called their upload, and it is
    # used to build a path and a URL.
    assert "/" not in store.safe_name("../../etc/passwd")
    assert not store.safe_name("...").startswith(".")
    assert store.safe_name("") == "screenshot"


def test_open_inbox_defaults_to_a_directory(monkeypatch):
    monkeypatch.delenv("SCREENSHOT_BLOB_URL", raising=False)
    monkeypatch.delenv("SCREENSHOT_BLOB_SAS", raising=False)
    monkeypatch.setenv("SCREENSHOT_INBOX", "/inbox")
    inbox = store.open_inbox()
    assert isinstance(inbox, store.LocalInbox)
    assert inbox.kind == "directory"


def test_open_inbox_uses_blob_when_url_and_sas_are_set(monkeypatch):
    monkeypatch.setenv("SCREENSHOT_BLOB_URL", "https://acct.blob.core.windows.net/screenshots")
    monkeypatch.setenv("SCREENSHOT_BLOB_SAS", "?sv=2021-08-06&sig=abc")
    inbox = store.open_inbox()
    assert isinstance(inbox, store.BlobInbox)
    assert inbox.kind == "azure-blob"
    # The SAS is a credential: it must not be in the value the dashboard shows.
    assert "sig=" not in inbox.location


def test_open_inbox_falls_back_when_the_sas_is_missing(monkeypatch):
    # A blob URL with no SAS is a misconfiguration, not a request for local
    # storage — in Azure the local inbox is empty and nobody can write to it.
    monkeypatch.setenv("SCREENSHOT_BLOB_URL", "https://acct.blob.core.windows.net/screenshots")
    monkeypatch.delenv("SCREENSHOT_BLOB_SAS", raising=False)
    assert isinstance(store.open_inbox(), store.LocalInbox)


def test_blob_pending_reads_names_out_of_the_list_xml():
    xml = """<?xml version="1.0"?>
    <EnumerationResults ContainerName="screenshots">
      <Blobs>
        <Blob><Name>inbox/one.png</Name></Blob>
        <Blob><Name>inbox/two.jpg</Name></Blob>
        <Blob><Name>inbox/notes.txt</Name></Blob>
        <Blob><Name>inbox/nested/three.png</Name></Blob>
        <Blob><Name>processed/old.png</Name></Blob>
      </Blobs>
    </EnumerationResults>"""

    class _Resp:
        ok = True
        text = xml

    inbox = store.BlobInbox("https://acct.blob.core.windows.net/screenshots", "sv=x")
    with_stub = lambda *a, **k: _Resp()
    import sources.screenshots.store as store_module
    original = store_module.requests.get
    store_module.requests.get = with_stub
    try:
        names = inbox.pending({".png", ".jpg", ".jpeg", ".webp"}, 10)
    finally:
        store_module.requests.get = original

    # Leaf names only, extension-filtered, and never anything outside inbox/.
    assert names == ["one.png", "two.jpg"]


def test_blob_pending_returns_empty_on_a_failed_list():
    class _Resp:
        ok = False
        status_code = 403
        text = ""

    inbox = store.BlobInbox("https://acct.blob.core.windows.net/screenshots", "sv=x")
    import sources.screenshots.store as store_module
    original = store_module.requests.get
    store_module.requests.get = lambda *a, **k: _Resp()
    try:
        assert inbox.pending({".png"}, 10) == []
    finally:
        store_module.requests.get = original


def test_describe_never_leaks_the_credential(monkeypatch):
    monkeypatch.setenv("SCREENSHOT_BLOB_URL", "https://acct.blob.core.windows.net/screenshots")
    monkeypatch.setenv("SCREENSHOT_BLOB_SAS", "?sv=2021-08-06&sig=SECRET")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-SECRET")
    described = describe()
    assert "SECRET" not in repr(described)
    assert described["storage"] == "azure-blob"
    assert described["configured"] is True
