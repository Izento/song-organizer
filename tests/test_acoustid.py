# pylint: disable=import-error

import sys
from types import SimpleNamespace

from renamer import acoustid


def test_lookup_uses_precomputed_fingerprint(monkeypatch):
    calls = {}

    def fake_lookup(api_key, fingerprint, duration, meta):
        calls["lookup"] = (api_key, fingerprint, duration, meta)
        return {"status": "ok"}

    fake_client = SimpleNamespace(
        lookup=fake_lookup,
        parse_lookup_result=lambda response: iter(
            [(0.92, "recording-id", "Track title", "Artist")]
        ),
        WebServiceError=RuntimeError,
        FingerprintGenerationError=RuntimeError,
        NoBackendError=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "acoustid", fake_client)
    monkeypatch.setattr(acoustid, "resolve_fpcalc", lambda: "fpcalc.exe")
    monkeypatch.setattr(
        acoustid,
        "fingerprint_file_details",
        lambda path: ("precomputed-fingerprint", 123.4, None),
    )
    monkeypatch.setattr(acoustid, "_load_cache", lambda: {})
    monkeypatch.setattr(acoustid, "_save_cache", lambda: None)
    monkeypatch.setattr(acoustid, "_file_key", lambda path: "test-key")

    result = acoustid.lookup("song.mp3", "api-key")

    assert result["artist"] == "Artist"
    assert result["title"] == "Track title"
    assert result["recording_id"] == "recording-id"
    assert calls["lookup"] == (
        "api-key",
        "precomputed-fingerprint",
        123.4,
        ["recordings"],
    )
