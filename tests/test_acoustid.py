# pylint: disable=import-error

import sys
from types import SimpleNamespace

from renamer import acoustid


def test_lookup_uses_precomputed_fingerprint(monkeypatch):
    calls = {}

    def fake_lookup(api_key, fingerprint, duration, meta):
        calls["lookup"] = (api_key, fingerprint, duration, meta)
        return {
            "status": "ok",
            "results": [
                {
                    "score": 0.92,
                    "recordings": [
                        {
                            "id": "recording-id",
                            "title": "Track title",
                            "artists": [{"name": "Artist"}],
                            "sources": 12,
                        }
                    ],
                }
            ],
        }

    fake_client = SimpleNamespace(
        lookup=fake_lookup,
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
        ["recordings", "sources"],
    )


def test_lookup_selects_most_supported_recording(monkeypatch):
    response = {
        "status": "ok",
        "results": [
            {
                "score": 0.99,
                "recordings": [
                    {
                        "id": "wrong-recording",
                        "title": "Ski Mask Way",
                        "artists": [{"name": "50 Cent"}],
                        "sources": 2,
                    },
                    {
                        "id": "correct-recording",
                        "title": "Build You Up",
                        "artists": [{"name": "50 Cent feat. Jamie Foxx"}],
                        "sources": 1444,
                    },
                ],
            }
        ],
    }
    fake_client = SimpleNamespace(
        lookup=lambda *_args, **_kwargs: response,
        WebServiceError=RuntimeError,
        FingerprintGenerationError=RuntimeError,
        NoBackendError=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "acoustid", fake_client)
    monkeypatch.setattr(acoustid, "resolve_fpcalc", lambda: "fpcalc.exe")
    monkeypatch.setattr(
        acoustid,
        "fingerprint_file_details",
        lambda path: ("precomputed-fingerprint", 175.0, None),
    )
    monkeypatch.setattr(acoustid, "_load_cache", lambda: {})
    monkeypatch.setattr(acoustid, "_save_cache", lambda: None)
    monkeypatch.setattr(acoustid, "_file_key", lambda path: "test-key")

    result = acoustid.lookup(
        "song.mp3",
        "api-key",
    )

    assert result["recording_id"] == "correct-recording"
    assert result["title"] == "Build You Up"
    assert result["artist"] == "50 Cent"
    assert result["feat_artists"] == ["Jamie Foxx"]
    assert result["sources"] == 1444


def test_lookup_prefers_exact_filename_title_in_conflicting_match(monkeypatch):
    response = {
        "status": "ok",
        "results": [
            {
                "score": 0.99,
                "recordings": [
                    {
                        "id": "wrong-recording",
                        "title": "Pac's Life",
                        "artists": [{"name": "2Pac"}],
                        "sources": 2,
                    },
                    {
                        "id": "version-recording",
                        "title": "Troublesome '96",
                        "artists": [{"name": "2Pac"}],
                        "sources": 2,
                    },
                    {
                        "id": "correct-recording",
                        "title": "Troublesome",
                        "artists": [{"name": "2Pac"}],
                        "sources": 1,
                    },
                ],
            }
        ],
    }
    fake_client = SimpleNamespace(
        lookup=lambda *_args, **_kwargs: response,
        WebServiceError=RuntimeError,
        FingerprintGenerationError=RuntimeError,
        NoBackendError=RuntimeError,
    )
    monkeypatch.setitem(sys.modules, "acoustid", fake_client)
    monkeypatch.setattr(acoustid, "resolve_fpcalc", lambda: "fpcalc.exe")
    monkeypatch.setattr(
        acoustid,
        "fingerprint_file_details",
        lambda path: ("precomputed-fingerprint", 327.0, None),
    )
    monkeypatch.setattr(acoustid, "_load_cache", lambda: {})
    monkeypatch.setattr(acoustid, "_save_cache", lambda: None)
    monkeypatch.setattr(acoustid, "_file_key", lambda path: "test-key")

    result = acoustid.lookup(
        "2Pac - Troublesome.mp3",
        "api-key",
    )

    assert result["recording_id"] == "correct-recording"
    assert result["title"] == "Troublesome"
